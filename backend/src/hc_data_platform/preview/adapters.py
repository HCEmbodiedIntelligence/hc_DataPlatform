"""Adapters from BE-08/09 contracts and a production FFmpeg media encoder."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse

from hc_data_platform.annotation.ports import (
    EffectiveExclusionPort as AnnotationEffectiveExclusionPort,
)
from hc_data_platform.lance_catalog.models import StepRecord, StepWindow
from hc_data_platform.lance_catalog.ports import StepReaderPort as LanceStepReaderPort

from .models import (
    EncodedPreviewArtifactV1,
    PreviewCacheRecordV1,
    PreviewFrameV1,
    PreviewRequestV1,
    RenderFrameV1,
    StepRangeV1,
)


class PreviewAdapterError(RuntimeError):
    """A provider value cannot be represented by the preview contract."""


class PreviewEncodingError(RuntimeError):
    """FFmpeg failed before a complete preview artifact could be committed."""


class CommandRunner(Protocol):
    def __call__(self, command: Sequence[str]) -> None: ...


ImageRefResolver = Callable[[object], str | bytes | None]


class FilePreviewCache:
    """Process-independent TTL metadata cache paired with atomic FFmpeg directories."""

    _SAFE_ID = re.compile(r"^[0-9a-f-]{32,64}$")

    def __init__(self, root: Path) -> None:
        self._root = root

    def get(self, cache_key: str, *, now: datetime) -> PreviewCacheRecordV1 | None:
        return self._read(self._record_path(cache_key), now=now)

    def get_by_session(self, session_id: str, *, now: datetime) -> PreviewCacheRecordV1 | None:
        if self._SAFE_ID.fullmatch(session_id) is None:
            return None
        pointer = self._root / "sessions" / session_id
        try:
            cache_key = pointer.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        return self.get(cache_key, now=now)

    def put(self, record: PreviewCacheRecordV1) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        sessions = self._root / "sessions"
        sessions.mkdir(exist_ok=True)
        self._atomic_write(self._record_path(record.cache_key), record.model_dump_json())
        self._atomic_write(sessions / record.session_id, record.cache_key)

    def _record_path(self, cache_key: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{64}", cache_key) is None:
            raise ValueError("cache key must be a lowercase SHA-256 digest")
        return self._root / f"{cache_key}.json"

    @staticmethod
    def _read(path: Path, *, now: datetime) -> PreviewCacheRecordV1 | None:
        try:
            record = PreviewCacheRecordV1.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if record.cache_expires_at <= now:
            return None
        return record

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name)


def _default_image_ref(value: object) -> str | bytes | None:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str) and value:
        return value
    if isinstance(value, os.PathLike):
        return str(value)
    return None


def _parse_lance_version(value: str) -> int:
    normalized = value[1:] if value.lower().startswith("v") else value
    try:
        version = int(normalized)
    except ValueError as exc:
        raise PreviewAdapterError(
            f"lance_version must be a positive integer or v-prefixed integer, got {value!r}"
        ) from exc
    if version < 1:
        raise PreviewAdapterError("lance_version must identify a committed version")
    return version


class LanceStepReaderAdapter:
    """Converts BE-08 StepWindow/StepRecord values into preview image frames."""

    def __init__(
        self,
        source: LanceStepReaderPort,
        *,
        page_size: int = 4_096,
        image_ref_resolver: ImageRefResolver = _default_image_ref,
    ) -> None:
        if page_size < 1:
            raise ValueError("page_size must be positive")
        self._source = source
        self._page_size = page_size
        self._image_ref_resolver = image_ref_resolver

    def read_steps(
        self,
        *,
        project_id: str,
        dataset_id: str,
        rollout_id: str,
        lance_version: str,
        camera_id: str,
        start_step: int | None,
        end_step: int | None,
    ) -> Sequence[PreviewFrameV1]:
        version = _parse_lance_version(lance_version)
        first_step = 0 if start_step is None else start_step
        records = self._read_records(
            project_id=project_id,
            dataset_id=dataset_id,
            rollout_id=rollout_id,
            version=version,
            start_step=first_step,
            end_step=end_step,
        )
        return tuple(self._to_preview_frame(record, camera_id=camera_id) for record in records)

    def _read_records(
        self,
        *,
        project_id: str,
        dataset_id: str,
        rollout_id: str,
        version: int,
        start_step: int,
        end_step: int | None,
    ) -> tuple[StepRecord, ...]:
        if end_step is not None:
            window = self._source.read_steps(
                dataset_id,
                rollout_id,
                start_step,
                end_step,
                version=version,
                project_id=project_id,
            )
            self._validate_window(
                window,
                project_id=project_id,
                dataset_id=dataset_id,
                version=version,
                rollout_id=rollout_id,
            )
            return window.steps

        records: list[StepRecord] = []
        cursor = start_step
        while True:
            window = self._source.read_steps(
                dataset_id,
                rollout_id,
                cursor,
                cursor + self._page_size,
                version=version,
                project_id=project_id,
            )
            self._validate_window(
                window,
                project_id=project_id,
                dataset_id=dataset_id,
                version=version,
                rollout_id=rollout_id,
            )
            if not window.steps:
                return tuple(records)
            records.extend(window.steps)
            cursor += self._page_size

    @staticmethod
    def _validate_window(
        window: StepWindow,
        *,
        project_id: str,
        dataset_id: str,
        version: int,
        rollout_id: str,
    ) -> None:
        if (
            window.project_id != project_id
            or window.dataset_id != dataset_id
            or window.dataset_version != version
            or window.rollout_id != rollout_id
        ):
            raise PreviewAdapterError("BE-08 returned a StepWindow for a different source identity")

    def _to_preview_frame(self, record: StepRecord, *, camera_id: str) -> PreviewFrameV1:
        modality_key = self._camera_modality_key(record, camera_id)
        value = record.modalities.get(modality_key)
        image_ref = self._image_ref_resolver(value)
        camera_valid = record.valid.get(modality_key, image_ref is not None)

        invalid_reason: str | None = None
        if not record.sample_valid:
            invalid_reason = "source sample is invalid"
        elif not camera_valid:
            invalid_reason = f"{modality_key} is invalid"
        elif image_ref is None:
            invalid_reason = f"{modality_key} image reference is missing or unsupported"

        source_timestamps = record.source_timestamps_ns.get(modality_key, ())
        source_timestamp = (
            None
            if not source_timestamps
            else min(source_timestamps, key=lambda value: abs(value - record.timestamp_ns))
        )
        return PreviewFrameV1(
            rollout_id=record.rollout_id,
            step_index=record.step_index,
            timestamp_ns=record.timestamp_ns,
            source_timestamp_ns=source_timestamp,
            image_ref=image_ref,
            valid=invalid_reason is None,
            invalid_reason=invalid_reason,
        )

    @staticmethod
    def _camera_modality_key(record: StepRecord, camera_id: str) -> str:
        candidates = (camera_id, f"camera.{camera_id}")
        return next((key for key in candidates if key in record.modalities), candidates[-1])


class AnnotationExclusionAdapter:
    """Resolves a rollout to a task and consumes BE-09 immutable effective exclusions."""

    def __init__(
        self,
        source: AnnotationEffectiveExclusionPort,
    ) -> None:
        self._source = source

    def effective_ranges(
        self,
        *,
        project_id: str,
        rollout_id: str,
        annotation_revision: int,
    ) -> Sequence[StepRangeV1]:
        exclusions = self._source.effective_ranges(
            project_id=project_id,
            rollout_id=rollout_id,
            annotation_revision=annotation_revision,
        )
        return tuple(
            StepRangeV1(start_step=item.start_step, end_step=item.end_step) for item in exclusions
        )


def _subprocess_runner(command: Sequence[str]) -> None:
    try:
        subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise PreviewEncodingError(f"required executable is unavailable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "unknown FFmpeg failure").strip()
        raise PreviewEncodingError(detail) from exc


class FFmpegHlsEncoder:
    """Writes H.264 HLS with CMAF/fMP4 segments, then atomically publishes the directory."""

    _SAFE_CACHE_KEY = re.compile(r"^[0-9a-f]{64}$")

    def __init__(
        self,
        cache_root: Path,
        *,
        ffmpeg_binary: str = "ffmpeg",
        runner: CommandRunner = _subprocess_runner,
        allowed_remote_schemes: Sequence[str] = ("http", "https"),
        max_embedded_image_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if max_embedded_image_bytes < 1:
            raise ValueError("max_embedded_image_bytes must be positive")
        self._cache_root = cache_root
        self._ffmpeg_binary = ffmpeg_binary
        self._runner = runner
        self._allowed_remote_schemes = frozenset(allowed_remote_schemes)
        self._max_embedded_image_bytes = max_embedded_image_bytes

    def encode(
        self,
        *,
        cache_key: str,
        request: PreviewRequestV1,
        frames: Sequence[RenderFrameV1],
    ) -> EncodedPreviewArtifactV1:
        if self._SAFE_CACHE_KEY.fullmatch(cache_key) is None:
            raise ValueError("cache_key must be a lowercase SHA-256 digest")
        self._cache_root.mkdir(parents=True, exist_ok=True)
        final_dir = self._cache_root / cache_key
        final_playlist = final_dir / "index.m3u8"
        if final_playlist.is_file():
            return self._artifact(final_playlist, request=request, frame_count=len(frames))
        if final_dir.exists():
            raise PreviewEncodingError("preview cache target exists without a committed playlist")

        temporary_dir = Path(tempfile.mkdtemp(prefix=f".{cache_key}.", dir=self._cache_root))
        try:
            self._encode_in_directory(temporary_dir, request=request, frames=frames)
            if not (temporary_dir / "index.m3u8").is_file():
                raise PreviewEncodingError("FFmpeg completed without producing index.m3u8")
            try:
                temporary_dir.rename(final_dir)
            except OSError as exc:
                if not final_playlist.is_file():
                    raise PreviewEncodingError("could not atomically commit preview cache") from exc
            return self._artifact(final_playlist, request=request, frame_count=len(frames))
        finally:
            if temporary_dir.exists():
                shutil.rmtree(temporary_dir)

    def _encode_in_directory(
        self,
        directory: Path,
        *,
        request: PreviewRequestV1,
        frames: Sequence[RenderFrameV1],
    ) -> None:
        if not frames:
            self._write_empty_playlist(directory / "index.m3u8")
            return

        frame_directory = directory / "frames"
        frame_directory.mkdir()
        for number, frame in enumerate(frames):
            target = frame_directory / f"frame_{number:09d}.ppm"
            if frame.placeholder is not None:
                self._write_placeholder(
                    target,
                    width=request.encoding_profile.width,
                    height=request.encoding_profile.height,
                )
            else:
                if frame.image_ref is None:
                    raise PreviewEncodingError("render frame has neither image_ref nor placeholder")
                self._normalize_source_image(
                    frame.image_ref,
                    target,
                    request=request,
                    excluded=frame.excluded,
                )

        profile = request.encoding_profile
        keyframe_interval = max(1, round(request.frequency_hz * profile.segment_duration_seconds))
        self._runner(
            [
                self._ffmpeg_binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-framerate",
                str(request.frequency_hz),
                "-start_number",
                "0",
                "-i",
                str(frame_directory / "frame_%09d.ppm"),
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                profile.preset,
                "-b:v",
                f"{profile.video_bitrate_kbps}k",
                "-pix_fmt",
                profile.pixel_format,
                "-g",
                str(keyframe_interval),
                "-keyint_min",
                str(keyframe_interval),
                "-sc_threshold",
                "0",
                "-force_key_frames",
                f"expr:gte(t,n_forced*{profile.segment_duration_seconds})",
                "-f",
                "hls",
                "-hls_time",
                str(profile.segment_duration_seconds),
                "-hls_playlist_type",
                "vod",
                "-hls_segment_type",
                "fmp4",
                "-hls_fmp4_init_filename",
                "init.mp4",
                "-hls_segment_filename",
                str(directory / "segment_%05d.m4s"),
                "-hls_flags",
                "independent_segments",
                str(directory / "index.m3u8"),
            ]
        )

    def _normalize_source_image(
        self,
        image_ref: str | bytes,
        target: Path,
        *,
        request: PreviewRequestV1,
        excluded: bool,
    ) -> None:
        temporary_source: Path | None = None
        if isinstance(image_ref, bytes):
            if len(image_ref) > self._max_embedded_image_bytes:
                raise PreviewEncodingError("embedded image exceeds the configured size limit")
            descriptor, name = tempfile.mkstemp(
                prefix=f".{target.stem}.", suffix=".source-image", dir=target.parent
            )
            temporary_source = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(image_ref)
            source = str(temporary_source)
        else:
            source = self._ffmpeg_input(image_ref)
        profile = request.encoding_profile
        filters = [
            (f"scale={profile.width}:{profile.height}:force_original_aspect_ratio=decrease"),
            f"pad={profile.width}:{profile.height}:(ow-iw)/2:(oh-ih)/2:color=black",
            "setsar=1",
        ]
        if excluded:
            filters.append("drawbox=x=0:y=0:w=iw:h=ih:color=red@0.85:t=12")
        try:
            self._runner(
                [
                    self._ffmpeg_binary,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-y",
                    "-i",
                    source,
                    "-frames:v",
                    "1",
                    "-vf",
                    ",".join(filters),
                    "-pix_fmt",
                    "rgb24",
                    str(target),
                ]
            )
        finally:
            if temporary_source is not None:
                temporary_source.unlink(missing_ok=True)

    def _ffmpeg_input(self, image_ref: str) -> str:
        parsed = urlparse(image_ref)
        if not parsed.scheme:
            return image_ref
        if parsed.scheme == "file":
            if parsed.netloc not in ("", "localhost"):
                raise PreviewEncodingError("file image references cannot target a remote host")
            return unquote(parsed.path)
        if parsed.scheme in self._allowed_remote_schemes:
            return image_ref
        raise PreviewEncodingError(f"unsupported image reference scheme: {parsed.scheme}")

    @staticmethod
    def _write_placeholder(target: Path, *, width: int, height: int) -> None:
        """A conspicuous magenta checkerboard that cannot be mistaken for source imagery."""

        header = f"P6\n{width} {height}\n255\n".encode("ascii")
        rows = bytearray()
        block_size = max(8, min(width, height) // 12)
        colors = (b"\xb0\x00\xb0", b"\x30\x00\x30")
        for y in range(height):
            rows.extend(
                b"".join(colors[((x // block_size) + (y // block_size)) % 2] for x in range(width))
            )
        target.write_bytes(header + rows)

    @staticmethod
    def _write_empty_playlist(target: Path) -> None:
        target.write_text(
            "#EXTM3U\n"
            "#EXT-X-VERSION:7\n"
            "#EXT-X-TARGETDURATION:1\n"
            "#EXT-X-MEDIA-SEQUENCE:0\n"
            "#EXT-X-PLAYLIST-TYPE:VOD\n"
            "#EXT-X-ENDLIST\n",
            encoding="utf-8",
        )

    @staticmethod
    def _artifact(
        playlist: Path,
        *,
        request: PreviewRequestV1,
        frame_count: int,
    ) -> EncodedPreviewArtifactV1:
        return EncodedPreviewArtifactV1(
            artifact_uri=playlist.resolve().as_uri(),
            duration_seconds=frame_count / request.frequency_hz,
            frame_count=frame_count,
        )
