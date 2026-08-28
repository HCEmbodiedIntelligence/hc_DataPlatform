"""Adapters from BE-08/09 contracts and a production FFmpeg media encoder."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from datetime import datetime
from io import BytesIO
from itertools import chain
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

from PIL import Image, ImageDraw

from hc_data_platform.annotation.ports import (
    EffectiveExclusionPort as AnnotationEffectiveExclusionPort,
)
from hc_data_platform.lance_catalog.models import StepRecord, StepWindow
from hc_data_platform.lance_catalog.ports import StepReaderPort as LanceStepReaderPort

from .metrics import FFMPEG_PROCESS, PREVIEW_OBJECT_GET, PREVIEW_SOURCE_BYTES_READ
from .models import (
    EncodedPreviewArtifactV1,
    PreviewCacheRecordV1,
    PreviewFrameV1,
    PreviewRequestV1,
    RenderFrameV1,
    StepRangeV1,
)
from .profiles import DEFAULT_PREVIEW_PROFILES, PreviewProfileCatalog


class PreviewAdapterError(RuntimeError):
    """A provider value cannot be represented by the preview contract."""


class PreviewEncodingError(RuntimeError):
    """FFmpeg failed before a complete preview artifact could be committed."""


class PreviewGenerationCancelled(PreviewEncodingError):
    """The Temporal activity asked the encoder to terminate its subprocess."""


ImageRefResolver = Callable[[object], str | bytes | None]


class S3ImageClient(Protocol):
    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]: ...


class S3ImageRefResolver:
    """Materialize a configured-bucket ``s3://`` image reference for FFmpeg.

    Lance rows may contain either embedded image bytes, local/HTTP references for
    development, or immutable source objects in the deployment's data bucket.
    Only the configured bucket is eligible for S3 resolution, which prevents a
    catalog value from turning preview generation into a cross-bucket reader.
    Invalid or unavailable source imagery is represented as the existing explicit
    placeholder path rather than exposing object-store errors to the browser.
    """

    def __init__(
        self, client: S3ImageClient, bucket: str, *, max_object_bytes: int = 64 * 1024 * 1024
    ) -> None:
        if not bucket.strip():
            raise ValueError("bucket must not be empty")
        if max_object_bytes < 1:
            raise ValueError("max_object_bytes must be positive")
        self._client = client
        self._bucket = bucket
        self._max_object_bytes = max_object_bytes

    def __call__(self, value: object) -> str | bytes | None:
        if not isinstance(value, str):
            return _default_image_ref(value)
        parsed = urlparse(value)
        if parsed.scheme != "s3":
            return _default_image_ref(value)
        if (
            parsed.netloc != self._bucket
            or not parsed.path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            return None
        key = unquote(parsed.path).lstrip("/")
        if not key:
            return None
        try:
            response = self._get_object(key)
            raw_length = response.get("ContentLength")
            if isinstance(raw_length, int) and raw_length > self._max_object_bytes:
                return None
            body = response.get("Body")
            if body is None or not hasattr(body, "read") or not hasattr(body, "close"):
                return None
            try:
                data = bytes(body.read(self._max_object_bytes + 1))
            finally:
                body.close()
        except Exception:
            return None
        return data if len(data) <= self._max_object_bytes else None

    def _get_object(self, key: str) -> dict[str, object]:
        PREVIEW_OBJECT_GET.inc()
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        if not isinstance(response, dict):
            raise TypeError("S3 get_object response must be a mapping")
        return response


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


class FilePreviewMediaReader:
    """Safely opens only committed HLS assets beneath the configured preview cache root."""

    _SAFE_ASSET = re.compile(r"^(?:index\.m3u8|init\.mp4|segment_[0-9]{5}\.m4s)$")
    _SAFE_CACHE_KEY = re.compile(r"^[0-9a-f]{64}$")

    def __init__(self, media_root: Path) -> None:
        self._media_root = media_root.resolve()

    def resolve(self, record: PreviewCacheRecordV1, *, asset_name: str) -> Path:
        if self._SAFE_ASSET.fullmatch(asset_name) is None:
            raise FileNotFoundError
        if self._SAFE_CACHE_KEY.fullmatch(record.cache_key) is None:
            raise FileNotFoundError
        parsed = urlparse(record.artifact.artifact_uri)
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            raise FileNotFoundError
        artifact = Path(unquote(parsed.path)).resolve()
        expected_playlist = self._media_root / record.cache_key / "index.m3u8"
        if artifact != expected_playlist:
            raise FileNotFoundError
        candidate = (expected_playlist.parent / asset_name).resolve()
        if candidate.parent != expected_playlist.parent or not candidate.is_file():
            raise FileNotFoundError
        return candidate


def _default_image_ref(value: object) -> str | bytes | None:
    if isinstance(value, (bytes, bytearray, memoryview)):
        encoded = bytes(value)
        return encoded or None
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
        image_prefetch: int = 16,
        image_fetch_workers: int = 8,
    ) -> None:
        if page_size < 1:
            raise ValueError("page_size must be positive")
        if image_prefetch < 1:
            raise ValueError("image_prefetch must be positive")
        if image_fetch_workers < 1:
            raise ValueError("image_fetch_workers must be positive")
        self._source = source
        self._page_size = page_size
        self._image_ref_resolver = image_ref_resolver
        self._image_prefetch = image_prefetch
        self._image_fetch_workers = min(image_fetch_workers, image_prefetch)

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
    ) -> Iterable[PreviewFrameV1]:
        version = _parse_lance_version(lance_version)
        first_step = 0 if start_step is None else start_step
        return self._prefetched_frames(
            self._read_records(
                project_id=project_id,
                dataset_id=dataset_id,
                rollout_id=rollout_id,
                version=version,
                start_step=first_step,
                end_step=end_step,
            ),
            camera_id=camera_id,
        )

    def _prefetched_frames(
        self, records: Iterable[StepRecord], *, camera_id: str
    ) -> Iterator[PreviewFrameV1]:
        """Resolve image references concurrently without losing order or backpressure."""

        source = iter(records)
        pending: deque[Future[PreviewFrameV1]] = deque()
        with ThreadPoolExecutor(
            max_workers=self._image_fetch_workers,
            thread_name_prefix="preview-image-fetch",
        ) as executor:

            def submit_one() -> bool:
                try:
                    record = next(source)
                except StopIteration:
                    return False
                pending.append(
                    executor.submit(self._to_preview_frame, record, camera_id=camera_id)
                )
                return True

            while len(pending) < self._image_prefetch and submit_one():
                pass
            while pending:
                frame = pending.popleft().result()
                submit_one()
                yield frame

    def _read_records(
        self,
        *,
        project_id: str,
        dataset_id: str,
        rollout_id: str,
        version: int,
        start_step: int,
        end_step: int | None,
    ) -> Iterator[StepRecord]:
        cursor = start_step
        while end_step is None or cursor < end_step:
            page_end = cursor + self._page_size
            if end_step is not None:
                page_end = min(page_end, end_step)
            window = self._source.read_steps(
                dataset_id,
                rollout_id,
                cursor,
                page_end,
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
                return
            yield from window.steps
            cursor = page_end

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
        if image_ref is None:
            invalid_reason = f"{modality_key} image reference is missing or unsupported"
        elif not camera_valid:
            invalid_reason = f"{modality_key} is invalid"

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


class FFmpegHlsEncoder:
    """Stream camera JPEGs through one FFmpeg process into bounded HLS staging."""

    _SAFE_CACHE_KEY = re.compile(r"^[0-9a-f]{64}$")

    def __init__(
        self,
        cache_root: Path,
        *,
        ffmpeg_binary: str = "ffmpeg",
        process_factory: Callable[..., Any] = subprocess.Popen,
        profiles: PreviewProfileCatalog = DEFAULT_PREVIEW_PROFILES,
        max_embedded_image_bytes: int = 64 * 1024 * 1024,
        ffmpeg_threads: int = 2,
        process_timeout_seconds: float = 3_600,
    ) -> None:
        if max_embedded_image_bytes < 1:
            raise ValueError("max_embedded_image_bytes must be positive")
        if ffmpeg_threads < 1:
            raise ValueError("ffmpeg_threads must be positive")
        if process_timeout_seconds <= 0:
            raise ValueError("process_timeout_seconds must be positive")
        self._cache_root = cache_root
        self._ffmpeg_binary = ffmpeg_binary
        self._process_factory = process_factory
        self._profiles = profiles
        self._max_embedded_image_bytes = max_embedded_image_bytes
        self._ffmpeg_threads = ffmpeg_threads
        self._process_timeout_seconds = process_timeout_seconds
        self._placeholder_cache: dict[tuple[int, int], bytes] = {}

    def encode(
        self,
        *,
        cache_key: str,
        request: PreviewRequestV1,
        frames: Iterable[RenderFrameV1],
        cancelled: Callable[[], bool] | None = None,
    ) -> EncodedPreviewArtifactV1:
        if self._SAFE_CACHE_KEY.fullmatch(cache_key) is None:
            raise ValueError("cache_key must be a lowercase SHA-256 digest")
        profile = self._profiles.get(request.profile_id)
        self._cache_root.mkdir(parents=True, exist_ok=True)
        final_dir = self._cache_root / cache_key
        final_playlist = final_dir / "index.m3u8"
        if final_playlist.is_file():
            frame_count = sum(1 for _ in frames)
            return self._artifact(final_playlist, request=request, frame_count=frame_count)
        if final_dir.exists():
            raise PreviewEncodingError("preview cache target exists without a committed playlist")

        temporary_dir = Path(tempfile.mkdtemp(prefix=f".{cache_key}.", dir=self._cache_root))
        try:
            frame_count = self._encode_in_directory(
                temporary_dir,
                request=request,
                profile=profile,
                frames=frames,
                cancelled=cancelled,
            )
            if not (temporary_dir / "index.m3u8").is_file():
                raise PreviewEncodingError("FFmpeg completed without producing index.m3u8")
            try:
                temporary_dir.rename(final_dir)
            except OSError as exc:
                if not final_playlist.is_file():
                    raise PreviewEncodingError("could not atomically commit preview cache") from exc
            return self._artifact(final_playlist, request=request, frame_count=frame_count)
        finally:
            if temporary_dir.exists():
                shutil.rmtree(temporary_dir)

    def _encode_in_directory(
        self,
        directory: Path,
        *,
        request: PreviewRequestV1,
        profile: object,
        frames: Iterable[RenderFrameV1],
        cancelled: Callable[[], bool] | None,
    ) -> int:
        from .models import EncodingProfileV1

        resolved_profile = EncodingProfileV1.model_validate(profile)
        iterator = iter(frames)
        try:
            first = next(iterator)
        except StopIteration:
            self._write_empty_playlist(directory / "index.m3u8")
            return 0

        keyframe_interval = max(
            1, round(request.frequency_hz * resolved_profile.segment_duration_seconds)
        )
        filters = (
            f"scale={resolved_profile.width}:{resolved_profile.height}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={resolved_profile.width}:{resolved_profile.height}:"
            "(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
            f"format={resolved_profile.pixel_format}"
        )
        command = [
                self._ffmpeg_binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-framerate",
                str(request.frequency_hz),
                "-i",
                "pipe:0",
                "-an",
                "-vf",
                filters,
                "-c:v",
                "libx264",
                "-preset",
                resolved_profile.preset,
                "-sc_threshold",
                "0",
                "-bf",
                "0",
                "-threads",
                str(self._ffmpeg_threads),
                "-b:v",
                f"{resolved_profile.video_bitrate_kbps}k",
                "-pix_fmt",
                resolved_profile.pixel_format,
                "-g",
                str(keyframe_interval),
                "-keyint_min",
                str(keyframe_interval),
                "-force_key_frames",
                f"expr:gte(t,n_forced*{resolved_profile.segment_duration_seconds})",
                "-f",
                "hls",
                "-hls_time",
                str(resolved_profile.segment_duration_seconds),
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
        try:
            FFMPEG_PROCESS.inc()
            process = self._process_factory(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise PreviewEncodingError(
                f"required executable is unavailable: {self._ffmpeg_binary}"
            ) from exc
        stdin = process.stdin
        if stdin is None:
            process.kill()
            raise PreviewEncodingError("FFmpeg stdin pipe was not created")
        frame_count = 0
        try:
            for frame in chain((first,), iterator):
                if cancelled is not None and cancelled():
                    raise PreviewGenerationCancelled(
                        "FFmpeg preview generation was cancelled"
                    )
                content = self._frame_bytes(
                    frame,
                    width=resolved_profile.width,
                    height=resolved_profile.height,
                )
                stdin.write(content)
                PREVIEW_SOURCE_BYTES_READ.inc(len(content))
                frame_count += 1
            stdin.close()
            process.stdin = None
            deadline = time.monotonic() + self._process_timeout_seconds
            stderr = b""
            while True:
                if cancelled is not None and cancelled():
                    raise PreviewGenerationCancelled(
                        "FFmpeg preview generation was cancelled"
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(
                        command, self._process_timeout_seconds
                    )
                try:
                    _, stderr = process.communicate(timeout=min(1.0, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode != 0:
                detail = bytes(stderr or b"").decode("utf-8", errors="replace").strip()
                raise PreviewEncodingError(detail or "unknown FFmpeg failure")
        except BaseException as exc:
            with suppress(Exception):
                stdin.close()
            with suppress(Exception):
                process.kill()
            with suppress(Exception):
                process.communicate(timeout=5)
            if isinstance(exc, subprocess.TimeoutExpired):
                raise PreviewEncodingError("FFmpeg preview generation timed out") from exc
            if isinstance(exc, BrokenPipeError):
                stderr = process.stderr.read() if process.stderr is not None else b""
                detail = bytes(stderr or b"").decode("utf-8", errors="replace").strip()
                raise PreviewEncodingError(detail or "FFmpeg closed the input pipe") from exc
            raise
        return frame_count

    def _frame_bytes(self, frame: RenderFrameV1, *, width: int, height: int) -> bytes:
        if frame.placeholder is not None:
            return self._placeholder(width=width, height=height)
        if frame.image_ref is None:
            raise PreviewEncodingError("render frame has neither image_ref nor placeholder")
        if isinstance(frame.image_ref, bytes):
            content = frame.image_ref
        else:
            source = Path(self._local_image_path(frame.image_ref))
            try:
                size = source.stat().st_size
            except OSError as exc:
                raise PreviewEncodingError("preview source image is unavailable") from exc
            if size > self._max_embedded_image_bytes:
                raise PreviewEncodingError("embedded image exceeds the configured size limit")
            try:
                with source.open("rb") as stream:
                    content = stream.read(self._max_embedded_image_bytes + 1)
            except OSError as exc:
                raise PreviewEncodingError("preview source image is unavailable") from exc
        if len(content) > self._max_embedded_image_bytes:
            raise PreviewEncodingError("embedded image exceeds the configured size limit")
        if (
            len(content) < 4
            or not content.startswith(b"\xff\xd8")
            or not content.endswith(b"\xff\xd9")
        ):
            raise PreviewEncodingError("preview source image must be a complete JPEG frame")
        return content

    def cleanup(self, encoded: EncodedPreviewArtifactV1) -> None:
        """Remove only this worker's committed staging directory after publication."""

        parsed = urlparse(encoded.artifact_uri)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            return
        playlist = Path(unquote(parsed.path)).resolve()
        root = self._cache_root.resolve()
        if playlist.name != "index.m3u8" or root not in playlist.parents:
            raise PreviewEncodingError("refusing to clean an unsafe preview staging path")
        if self._SAFE_CACHE_KEY.fullmatch(playlist.parent.name) is None:
            raise PreviewEncodingError("refusing to clean an unkeyed preview staging path")
        if playlist.parent.exists():
            shutil.rmtree(playlist.parent)

    @staticmethod
    def _local_image_path(image_ref: str) -> str:
        parsed = urlparse(image_ref)
        if not parsed.scheme:
            return image_ref
        if parsed.scheme == "file":
            if parsed.netloc not in ("", "localhost"):
                raise PreviewEncodingError("file image references cannot target a remote host")
            return unquote(parsed.path)
        raise PreviewEncodingError(f"unsupported image reference scheme: {parsed.scheme}")

    def _placeholder(self, *, width: int, height: int) -> bytes:
        """A conspicuous magenta checkerboard that cannot be mistaken for source imagery."""

        cached = self._placeholder_cache.get((width, height))
        if cached is not None:
            return cached
        block_size = max(8, min(width, height) // 12)
        colors = ((176, 0, 176), (48, 0, 48))
        image = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(image)
        for y in range(0, height, block_size):
            for x in range(0, width, block_size):
                draw.rectangle(
                    (x, y, min(x + block_size - 1, width - 1), min(y + block_size - 1, height - 1)),
                    fill=colors[((x // block_size) + (y // block_size)) % 2],
                )
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=90, subsampling=0, optimize=False)
        content = buffer.getvalue()
        self._placeholder_cache[(width, height)] = content
        return content

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
