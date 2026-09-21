from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from PIL import Image, ImageDraw

from hc_data_platform.ingest.ports import ObjectStoragePort

from .models import (
    AlignedMediaEncodingProfileV1,
    AlignedMediaGenerationRequestV1,
    EncodedAlignedMediaV1,
)
from .ports import AlignedFrameV1
from .profiles import DEFAULT_ALIGNED_MEDIA_PROFILES, AlignedMediaProfileCatalog


class AlignedMediaEncodingError(RuntimeError):
    code = "ALIGNED_MEDIA_ENCODING_FAILED"


class AlignedMediaGenerationCancelled(AlignedMediaEncodingError):
    code = "ALIGNED_MEDIA_GENERATION_CANCELLED"


class FFmpegMp4Encoder:
    """Materialize immutable H.264 MP4 from JPEG staging or one localized source MP4."""

    _SAFE_KEY = re.compile(r"^[0-9a-f]{64}$")

    def __init__(
        self,
        staging_root: Path,
        *,
        ffmpeg_binary: str = "ffmpeg",
        ffprobe_binary: str = "ffprobe",
        process_factory: Callable[..., Any] = subprocess.Popen,
        probe_runner: Callable[..., Any] = subprocess.run,
        profiles: AlignedMediaProfileCatalog = DEFAULT_ALIGNED_MEDIA_PROFILES,
        max_frame_bytes: int = 64 * 1024 * 1024,
        ffmpeg_threads: int = 2,
        process_timeout_seconds: float = 3_600,
        raw_storage: ObjectStoragePort | None = None,
    ) -> None:
        if max_frame_bytes < 1 or ffmpeg_threads < 1 or process_timeout_seconds <= 0:
            raise ValueError("aligned media encoder limits must be positive")
        self._root = staging_root.resolve()
        self._ffmpeg = ffmpeg_binary
        self._ffprobe = ffprobe_binary
        self._process_factory = process_factory
        self._probe_runner = probe_runner
        self._profiles = profiles
        self._max_frame_bytes = max_frame_bytes
        self._ffmpeg_threads = ffmpeg_threads
        self._timeout = process_timeout_seconds
        self._raw_storage = raw_storage
        self._placeholder_cache: dict[tuple[int, int], bytes] = {}

    def encode(
        self,
        *,
        artifact_key: str,
        request: AlignedMediaGenerationRequestV1,
        frames: Iterable[AlignedFrameV1],
        cancelled: Callable[[], bool] | None = None,
    ) -> EncodedAlignedMediaV1:
        if self._SAFE_KEY.fullmatch(artifact_key) is None:
            raise ValueError("artifact_key must be a lowercase SHA-256 digest")
        profile = self._profiles.get(request.profile_id)
        self._root.mkdir(parents=True, exist_ok=True)
        final_dir = self._root / artifact_key
        final_file = final_dir / "media.mp4"
        if final_file.is_file():
            return self._probe(final_file, expected_frames=request.alignment.row_count)
        if final_dir.exists():
            raise AlignedMediaEncodingError("aligned media staging target is incomplete")

        temporary = Path(tempfile.mkdtemp(prefix=f".{artifact_key}.", dir=self._root))
        try:
            output = temporary / "media.mp4"
            if request.mp4_source is None:
                self._encode_file(
                    output,
                    profile=profile,
                    frames=frames,
                    cancelled=cancelled,
                )
            else:
                self._encode_mp4_source(
                    output,
                    request=request,
                    profile=profile,
                    cancelled=cancelled,
                )
            encoded = self._probe(output, expected_frames=request.alignment.row_count)
            try:
                temporary.rename(final_dir)
            except OSError as exc:
                if not final_file.is_file():
                    raise AlignedMediaEncodingError(
                        "could not atomically commit aligned media staging"
                    ) from exc
            return encoded.model_copy(update={"file_uri": final_file.resolve().as_uri()})
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _encode_mp4_source(
        self,
        output: Path,
        *,
        request: AlignedMediaGenerationRequestV1,
        profile: AlignedMediaEncodingProfileV1,
        cancelled: Callable[[], bool] | None,
    ) -> None:
        source = request.mp4_source
        if source is None:
            raise RuntimeError("MP4 source descriptor is missing")
        storage = self._raw_storage
        if storage is None:
            raise AlignedMediaEncodingError("raw object storage is not configured for MP4 cuts")
        localized = output.parent / "source.mp4"
        digest = hashlib.sha256()
        written = 0
        try:
            with localized.open("wb") as stream:
                for chunk in storage.read_chunks(source.object_key):
                    if cancelled is not None and cancelled():
                        raise AlignedMediaGenerationCancelled(
                            "aligned media generation was cancelled"
                        )
                    if not isinstance(chunk, bytes):
                        raise TypeError("raw object storage returned non-bytes")
                    written += len(chunk)
                    if written > source.size_bytes:
                        raise AlignedMediaEncodingError(
                            "localized MP4 exceeded its immutable size receipt"
                        )
                    digest.update(chunk)
                    stream.write(chunk)
            if written != source.size_bytes or digest.hexdigest() != source.content_sha256:
                raise AlignedMediaEncodingError(
                    "localized MP4 differs from its immutable object receipt"
                )
            source_probe = self._probe_source(localized)
            expected_frames = request.alignment.row_count
            if self._can_stream_copy(source_probe, source=source, expected_frames=expected_frames):
                command = [
                    self._ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(localized),
                    "-map",
                    "0:v:0",
                    "-an",
                    "-c:v",
                    "copy",
                    "-frames:v",
                    str(expected_frames),
                    "-video_track_timescale",
                    str(profile.fps),
                    "-movflags",
                    "+faststart",
                    str(output),
                ]
                decision = "STREAM_COPY"
            else:
                start = source.start_offset_ns / 1_000_000_000
                end = source.end_offset_ns / 1_000_000_000
                filters = (
                    f"trim=start={start:.9f}:end={end:.9f},setpts=PTS-STARTPTS,"
                    f"fps=fps={profile.fps}:start_time=0:round=near,"
                    "scale=iw:ih:out_range=tv,"
                    f"format={profile.pixel_format},setparams=range=limited"
                )
                command = [
                    self._ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(localized),
                    "-map",
                    "0:v:0",
                    "-an",
                    "-vf",
                    filters,
                    "-c:v",
                    "libx264",
                    "-preset",
                    profile.preset,
                    "-crf",
                    str(profile.crf),
                    "-pix_fmt",
                    profile.pixel_format,
                    "-g",
                    str(profile.gop_frames),
                    "-keyint_min",
                    str(profile.gop_frames),
                    "-sc_threshold",
                    "0",
                    "-bf",
                    "0",
                    "-threads",
                    str(self._ffmpeg_threads),
                    "-frames:v",
                    str(expected_frames),
                    "-r",
                    str(profile.fps),
                    "-fps_mode",
                    "cfr",
                    "-video_track_timescale",
                    str(profile.fps),
                    "-movflags",
                    "+faststart",
                    str(output),
                ]
                decision = "TRANSCODE"
            self._run_ffmpeg(command, cancelled=cancelled)
            (output.parent / "encode-facts.json").write_text(
                json.dumps(
                    {
                        "decision": decision,
                        "frame_count": expected_frames,
                        "first_timestamp_ns": (
                            source.capture_start_timestamp_ns + source.start_offset_ns
                        ),
                        "localized_source_bytes": written,
                        "placeholder_count": 0,
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        finally:
            with suppress(OSError):
                localized.unlink()

    def _run_ffmpeg(
        self,
        command: list[str],
        *,
        cancelled: Callable[[], bool] | None,
    ) -> None:
        try:
            process = self._process_factory(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise AlignedMediaEncodingError(
                f"required executable is unavailable: {self._ffmpeg}"
            ) from exc
        deadline = time.monotonic() + self._timeout
        try:
            while True:
                if cancelled is not None and cancelled():
                    raise AlignedMediaGenerationCancelled("aligned media generation was cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, self._timeout)
                try:
                    _stdout, stderr = process.communicate(timeout=min(1.0, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode != 0:
                detail = bytes(stderr or b"").decode(errors="replace").strip()
                raise AlignedMediaEncodingError(detail or "unknown FFmpeg failure")
        except BaseException as exc:
            with suppress(Exception):
                process.kill()
            with suppress(Exception):
                process.communicate(timeout=5)
            if isinstance(exc, subprocess.TimeoutExpired):
                raise AlignedMediaEncodingError("aligned media encoding timed out") from exc
            raise

    def _probe_source(self, path: Path) -> dict[str, Any]:
        try:
            result = self._probe_runner(
                [
                    self._ffprobe,
                    "-v",
                    "error",
                    "-count_frames",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    (
                        "stream=codec_name,pix_fmt,width,height,avg_frame_rate,r_frame_rate,"
                        "time_base,start_time,duration,nb_read_frames"
                    ),
                    "-of",
                    "json",
                    str(path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise AlignedMediaEncodingError("ffprobe could not inspect the source MP4") from exc
        payload = json.loads(result.stdout)
        streams = payload.get("streams")
        if not isinstance(streams, list) or len(streams) != 1:
            raise AlignedMediaEncodingError("source MP4 must contain exactly one video stream")
        try:
            first = self._probe_runner(
                [
                    self._ffprobe,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-read_intervals",
                    "0%+#1",
                    "-show_entries",
                    "frame=key_frame,best_effort_timestamp_time",
                    "-of",
                    "json",
                    str(path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise AlignedMediaEncodingError(
                "ffprobe could not inspect the first source frame"
            ) from exc
        first_frames = json.loads(first.stdout).get("frames")
        if not isinstance(first_frames, list) or len(first_frames) != 1:
            raise AlignedMediaEncodingError("source MP4 has no first video frame")
        result = dict(streams[0])
        result["_first_key_frame"] = first_frames[0].get("key_frame")
        result["_first_pts"] = first_frames[0].get("best_effort_timestamp_time")
        return result

    @staticmethod
    def _can_stream_copy(
        probe: dict[str, Any],
        *,
        source: Any,
        expected_frames: int,
    ) -> bool:
        if source.start_offset_ns != 0:
            return False
        try:
            frames = int(probe.get("nb_read_frames") or 0)
            duration_ns = round(float(probe.get("duration") or 0) * 1_000_000_000)
        except (TypeError, ValueError):
            return False
        return (
            probe.get("codec_name") == "h264"
            and source.configured_codec.lower() == "h264"
            and source.configured_fps == 30
            and source.configured_time_base_numerator == 1
            and source.configured_time_base_denominator == 30
            and probe.get("pix_fmt") == "yuv420p"
            and probe.get("avg_frame_rate") == "30/1"
            and probe.get("r_frame_rate") == "30/1"
            and probe.get("time_base") == "1/30"
            and float(probe.get("start_time") or 0) == 0
            and int(probe.get("_first_key_frame") or 0) == 1
            and float(probe.get("_first_pts") or -1) == 0
            and frames == expected_frames
            and abs(duration_ns - source.end_offset_ns) <= 1
        )

    def _encode_file(
        self,
        output: Path,
        *,
        profile: AlignedMediaEncodingProfileV1,
        frames: Iterable[AlignedFrameV1],
        cancelled: Callable[[], bool] | None,
    ) -> None:
        iterator = iter(frames)
        try:
            first = next(iterator)
        except StopIteration as exc:
            raise AlignedMediaEncodingError("aligned media cannot encode an empty rollout") from exc
        if first.step_index != 0:
            raise AlignedMediaEncodingError("aligned media frames must be contiguous from zero")
        first_timestamp_ns = int(first.timestamp_ns)
        leading_placeholders = 0
        pending = first
        if profile.resolution_policy == "preserve":
            while not pending.valid or pending.image is None:
                leading_placeholders += 1
                if cancelled is not None and cancelled():
                    raise AlignedMediaGenerationCancelled("aligned media generation was cancelled")
                try:
                    pending = next(iterator)
                except StopIteration as exc:
                    raise AlignedMediaEncodingError(
                        "preserve resolution requires at least one valid camera frame"
                    ) from exc
                if pending.step_index != leading_placeholders:
                    raise AlignedMediaEncodingError(
                        "aligned media frames must be contiguous from zero"
                    )
            width, height = self._output_dimensions(
                self._valid_frame_bytes(pending),
                profile,
            )
        else:
            width, height = self._output_dimensions(b"", profile)
        # JPEG is full-range. Convert the samples as well as their range metadata;
        # setparams alone can darken the decoded MP4 on newer FFmpeg versions.
        filters = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:out_range=tv,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
            f"format={profile.pixel_format},setparams=range=limited"
        )
        command = [
            self._ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-framerate",
            str(profile.fps),
            "-i",
            "pipe:0",
            "-an",
            "-vf",
            filters,
            "-c:v",
            "libx264",
            "-preset",
            profile.preset,
            "-crf",
            str(profile.crf),
            "-pix_fmt",
            profile.pixel_format,
            "-g",
            str(profile.gop_frames),
            "-keyint_min",
            str(profile.gop_frames),
            "-sc_threshold",
            "0",
            "-bf",
            "0",
            "-threads",
            str(self._ffmpeg_threads),
            "-r",
            str(profile.fps),
            "-fps_mode",
            "cfr",
            "-video_track_timescale",
            str(profile.fps),
            "-movflags",
            "+faststart",
            str(output),
        ]
        try:
            process = self._process_factory(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise AlignedMediaEncodingError(
                f"required executable is unavailable: {self._ffmpeg}"
            ) from exc
        if process.stdin is None:
            process.kill()
            raise AlignedMediaEncodingError("FFmpeg stdin pipe was not created")
        stdin = process.stdin
        frame_count = 0
        placeholder_count = 0
        try:
            if leading_placeholders:
                placeholder = self._placeholder(width, height)
                for _index in range(leading_placeholders):
                    stdin.write(placeholder)
                frame_count = leading_placeholders
                placeholder_count = leading_placeholders
            content, is_placeholder = self._frame_bytes(
                pending,
                width=width,
                height=height,
            )
            stdin.write(content)
            placeholder_count += int(is_placeholder)
            frame_count += 1
            for frame in iterator:
                if cancelled is not None and cancelled():
                    raise AlignedMediaGenerationCancelled("aligned media generation was cancelled")
                if frame.step_index != frame_count:
                    raise AlignedMediaEncodingError(
                        "aligned media frames must be contiguous from zero"
                    )
                content, is_placeholder = self._frame_bytes(
                    frame,
                    width=width,
                    height=height,
                )
                placeholder_count += int(is_placeholder)
                stdin.write(content)
                frame_count += 1
            stdin.close()
            process.stdin = None
            deadline = time.monotonic() + self._timeout
            stderr = b""
            while True:
                if cancelled is not None and cancelled():
                    raise AlignedMediaGenerationCancelled("aligned media generation was cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, self._timeout)
                try:
                    _, stderr = process.communicate(timeout=min(1.0, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode != 0:
                detail = bytes(stderr or b"").decode(errors="replace").strip()
                raise AlignedMediaEncodingError(detail or "unknown FFmpeg failure")
        except BaseException as exc:
            with suppress(Exception):
                stdin.close()
            with suppress(Exception):
                process.kill()
            with suppress(Exception):
                process.communicate(timeout=5)
            if isinstance(exc, subprocess.TimeoutExpired):
                raise AlignedMediaEncodingError("aligned media encoding timed out") from exc
            if isinstance(exc, BrokenPipeError):
                stderr = process.stderr.read() if process.stderr is not None else b""
                detail = bytes(stderr or b"").decode(errors="replace").strip()
                raise AlignedMediaEncodingError(detail or "FFmpeg closed its input pipe") from exc
            raise
        (output.parent / "encode-facts.json").write_text(
            json.dumps(
                {
                    "frame_count": frame_count,
                    "first_timestamp_ns": first_timestamp_ns,
                    "placeholder_count": placeholder_count,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def _probe(self, path: Path, *, expected_frames: int) -> EncodedAlignedMediaV1:
        facts_path = path.parent / "encode-facts.json"
        facts = json.loads(facts_path.read_text(encoding="utf-8")) if facts_path.is_file() else {}
        result = self._probe_runner(
            [
                self._ffprobe,
                "-v",
                "error",
                "-count_frames",
                "-show_entries",
                (
                    "stream=index,codec_type,codec_name,pix_fmt,width,height,avg_frame_rate,"
                    "r_frame_rate,time_base,start_time,nb_read_frames:format=duration"
                ),
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        payload = json.loads(result.stdout)
        streams = payload.get("streams")
        if not isinstance(streams, list):
            raise AlignedMediaEncodingError("ffprobe returned no stream inventory")
        video = [item for item in streams if item.get("codec_type") == "video"]
        audio = [item for item in streams if item.get("codec_type") == "audio"]
        if len(video) != 1 or audio:
            raise AlignedMediaEncodingError("canonical media must contain one video and no audio")
        stream = video[0]
        frame_count = int(stream.get("nb_read_frames") or facts.get("frame_count", 0))
        if frame_count != expected_frames:
            raise AlignedMediaEncodingError("MP4 frame count does not match aligned staging")
        if (
            stream.get("codec_name") != "h264"
            or stream.get("pix_fmt") != "yuv420p"
            or stream.get("avg_frame_rate") != "30/1"
            or stream.get("r_frame_rate") != "30/1"
            or stream.get("time_base") != "1/30"
            or float(stream.get("start_time") or 0) != 0
        ):
            raise AlignedMediaEncodingError(
                "MP4 codec, pixel format, or frame rate is invalid: "
                f"codec={stream.get('codec_name')!r}, "
                f"pixel_format={stream.get('pix_fmt')!r}, "
                f"average_frame_rate={stream.get('avg_frame_rate')!r}"
            )
        duration = float(payload.get("format", {}).get("duration", 0))
        expected_duration = frame_count / 30
        if abs(duration - expected_duration) > 1 / 30:
            raise AlignedMediaEncodingError("MP4 duration does not match aligned timeline")
        first_frame = self._probe_runner(
            [
                self._ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-read_intervals",
                "0%+#1",
                "-show_entries",
                "frame=key_frame,best_effort_timestamp",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        first_payload = json.loads(first_frame.stdout)
        frames = first_payload.get("frames")
        if (
            not isinstance(frames, list)
            or len(frames) != 1
            or int(frames[0].get("key_frame", 0)) != 1
            or int(frames[0].get("best_effort_timestamp", -1)) != 0
        ):
            raise AlignedMediaEncodingError(
                "canonical MP4 must start at PTS 0 on an independently decodable keyframe"
            )
        with path.open("rb") as stream_file:
            header = stream_file.read(min(path.stat().st_size, 2 * 1024 * 1024))
        moov = header.find(b"moov")
        mdat = header.find(b"mdat")
        if moov < 0 or (mdat >= 0 and moov > mdat):
            raise AlignedMediaEncodingError("canonical MP4 is missing faststart atom ordering")
        return EncodedAlignedMediaV1(
            file_uri=path.resolve().as_uri(),
            frame_count=frame_count,
            duration_seconds=expected_duration,
            width=int(stream["width"]),
            height=int(stream["height"]),
            placeholder_count=int(facts.get("placeholder_count", 0)),
            first_timestamp_ns=int(facts.get("first_timestamp_ns", 0)),
        )

    def _frame_bytes(
        self,
        frame: AlignedFrameV1,
        *,
        width: int,
        height: int,
    ) -> tuple[bytes, bool]:
        if not frame.valid or frame.image is None:
            return self._placeholder(width, height), True
        return self._valid_frame_bytes(frame), False

    def _valid_frame_bytes(self, frame: AlignedFrameV1) -> bytes:
        if not frame.valid or frame.image is None:
            raise AlignedMediaEncodingError("camera frame is not valid image content")
        content = bytes(frame.image)
        if len(content) > self._max_frame_bytes:
            raise AlignedMediaEncodingError("camera frame exceeds the configured byte limit")
        if (
            len(content) < 4
            or not content.startswith(b"\xff\xd8")
            or not content.endswith(b"\xff\xd9")
        ):
            raise AlignedMediaEncodingError("camera frame must be a complete JPEG")
        return content

    @staticmethod
    def _output_dimensions(
        first_frame: bytes,
        profile: AlignedMediaEncodingProfileV1,
    ) -> tuple[int, int]:
        if profile.resolution_policy == "fit":
            assert profile.max_width is not None and profile.max_height is not None
            return profile.max_width, profile.max_height
        with Image.open(BytesIO(first_frame)) as image:
            width = image.width - image.width % 2
            height = image.height - image.height % 2
        if width < 2 or height < 2:
            raise AlignedMediaEncodingError("camera frame dimensions are invalid")
        return width, height

    def _placeholder(self, width: int, height: int) -> bytes:
        cached = self._placeholder_cache.get((width, height))
        if cached is not None:
            return cached
        block = max(8, min(width, height) // 12)
        image = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(image)
        for y in range(0, height, block):
            for x in range(0, width, block):
                color = (176, 0, 176) if (x // block + y // block) % 2 == 0 else (48, 0, 48)
                draw.rectangle(
                    (
                        x,
                        y,
                        min(x + block - 1, width - 1),
                        min(y + block - 1, height - 1),
                    ),
                    fill=color,
                )
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=90, subsampling=0, optimize=False)
        content = buffer.getvalue()
        self._placeholder_cache[(width, height)] = content
        return content

    def cleanup(self, encoded: EncodedAlignedMediaV1) -> None:
        parsed = urlparse(encoded.file_uri)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            return
        path = Path(unquote(parsed.path)).resolve()
        if path.name != "media.mp4" or self._root not in path.parents:
            raise AlignedMediaEncodingError("refusing to clean unsafe media staging")
        if self._SAFE_KEY.fullmatch(path.parent.name) is None:
            raise AlignedMediaEncodingError("refusing to clean unkeyed media staging")
        if path.parent.exists():
            shutil.rmtree(path.parent)
