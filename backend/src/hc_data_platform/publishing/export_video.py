"""Materialize selected camera frames into portable, bounded-memory MP4 shards."""

from __future__ import annotations

import hashlib
import itertools
import json
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from hc_data_platform.aligned_media.models import AlignedMediaFrameReferenceV1

from .export_assets import ExportAssetsPort, ExportVideoSource, export_error
from .models import PublishedDatasetManifestV1, PublishedRolloutV1


@contextmanager
def _process(command: list[str], *, encode: bool = False) -> Iterator[subprocess.Popen[bytes]]:
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if encode else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL if encode else subprocess.PIPE,
            stderr=errors,
        )
        try:
            yield process
            if encode and process.stdin:
                process.stdin.close()
            if process.wait(timeout=1800):
                errors.seek(0)
                raise export_error(
                    "EXPORT_VIDEO_INVALID", errors.read()[-1000:].decode(errors="replace")
                )
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stdout:
                process.stdout.close()
            if process.stdin and not process.stdin.closed:
                process.stdin.close()


def _time(reference: AlignedMediaFrameReferenceV1) -> float:
    return reference.pts * reference.pts_time_base_numerator / reference.pts_time_base_denominator


@lru_cache(maxsize=1)
def _filter_file_option() -> str:
    # FFmpeg 8 removed filter_script in favor of its general option-file syntax.
    help_text = subprocess.run(
        ["ffmpeg", "-h", "full"], capture_output=True, check=True, timeout=30
    ).stdout
    return "-filter_script:v" if b"-filter_script" in help_text else "-/filter:v"


def _localize(assets: ExportAssetsPort, source: ExportVideoSource, root: Path) -> Path:
    path = root / f"{source.sha256}.mp4"
    if path.is_file():
        return path
    digest, size = hashlib.sha256(), 0
    with path.open("wb") as output:
        for chunk in assets.read_video(source):
            size += len(chunk)
            if size > source.size:
                raise export_error("EXPORT_SOURCE_HASH_MISMATCH", "Video exceeds its frozen size.")
            output.write(chunk)
            digest.update(chunk)
    if size != source.size or digest.hexdigest() != source.sha256:
        raise export_error(
            "EXPORT_SOURCE_HASH_MISMATCH", "Video differs from its immutable receipt."
        )
    return path


def _selected_frames(
    source: ExportVideoSource,
    path: Path,
    references: Sequence[AlignedMediaFrameReferenceV1],
    root: Path,
) -> Iterator[bytes]:
    unique: list[AlignedMediaFrameReferenceV1] = []
    counts: list[int] = []
    for reference in references:
        timestamp = _time(reference)
        if not source.start_seconds - 1e-5 <= timestamp < source.end_seconds + 1e-5:
            raise export_error(
                "EXPORT_MEDIA_RANGE_INVALID", "Camera frame is outside its frozen Episode."
            )
        if unique and timestamp < _time(unique[-1]) - 1e-6:
            raise export_error(
                "EXPORT_MEDIA_TIME_ORDER_INVALID", "Camera frame timestamps run backwards."
            )
        if unique and reference.frame_index == unique[-1].frame_index:
            if abs(timestamp - _time(unique[-1])) > 1e-6:
                raise export_error(
                    "EXPORT_MEDIA_TIME_ORDER_INVALID", "Repeated frame has conflicting PTS."
                )
            counts[-1] += 1
        else:
            unique.append(reference)
            counts.append(1)
    ranges: list[tuple[AlignedMediaFrameReferenceV1, AlignedMediaFrameReferenceV1]] = []
    for reference in unique:
        if ranges and reference.frame_index == ranges[-1][1].frame_index + 1:
            ranges[-1] = (ranges[-1][0], reference)
        else:
            ranges.append((reference, reference))
    expression = "+".join(
        f"between(t,{_time(first) - 1e-6:.9f},{_time(last) + 1e-6:.9f})" for first, last in ranges
    )
    # A filter file avoids argv limits on heavily edited Episodes. copyts keeps
    # source PTS after seeking; frame_index/fps is never used as a source clock.
    filter_path = root / "selection.filter"
    filter_path.write_text(f"select='{expression}'", encoding="utf-8")
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-threads",
        "1",
        "-copyts",
        "-ss",
        f"{max(0, _time(unique[0]) - 0.1):.9f}",
        "-t",
        f"{_time(unique[-1]) - max(0, _time(unique[0]) - 0.1) + 0.1:.9f}",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        _filter_file_option(),
        str(filter_path),
        "-threads",
        "1",
        "-fps_mode",
        "passthrough",
        "-pix_fmt",
        "rgb24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    size = source.width * source.height * 3
    if size > 64 * 1024**2:
        raise export_error(
            "EXPORT_MEDIA_DIMENSIONS_INVALID", "Camera frame exceeds the decode limit."
        )
    with _process(command) as decoder:
        assert decoder.stdout is not None
        for count in counts:
            frame = decoder.stdout.read(size)
            if len(frame) != size:
                raise export_error(
                    "EXPORT_MEDIA_FRAMES_MISSING",
                    "Selected camera frames could not all be decoded.",
                )
            for _ in range(count):
                yield frame
        if decoder.stdout.read(1):
            raise export_error(
                "EXPORT_MEDIA_FRAME_COUNT_MISMATCH",
                "Camera PTS selection includes unexpected frames.",
            )


def inspect_video(
    path: Path, *, frame_count: int, fps: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    width, height = int(stream["width"]), int(stream["height"])
    numerator, denominator = map(int, stream["avg_frame_rate"].split("/"))
    if numerator != fps * denominator or int(stream.get("nb_frames", -1)) != frame_count:
        raise export_error("EXPORT_VIDEO_INVALID", "MP4 FPS/frame count does not match Parquet.")
    if width * height * 3 > 64 * 1024**2:
        raise export_error(
            "EXPORT_MEDIA_DIMENSIONS_INVALID", "Camera frame exceeds the decode limit."
        )
    total, square = np.zeros(3), np.zeros(3)
    minimum, maximum = np.ones(3), np.zeros(3)
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-threads",
        "1",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-an",
        "-threads",
        "1",
        "-fps_mode",
        "passthrough",
        "-pix_fmt",
        "rgb24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    with _process(command) as decoder:
        assert decoder.stdout is not None
        for _ in range(frame_count):
            frame = decoder.stdout.read(width * height * 3)
            if len(frame) != width * height * 3:
                raise export_error(
                    "EXPORT_MEDIA_FRAMES_MISSING",
                    "Exported MP4 contains a damaged or missing frame.",
                )
            pixels = np.frombuffer(frame, dtype=np.uint8).reshape(-1, 3).astype(np.float64) / 255
            total += pixels.sum(axis=0)
            square += np.square(pixels).sum(axis=0)
            minimum = np.minimum(minimum, pixels.min(axis=0))
            maximum = np.maximum(maximum, pixels.max(axis=0))
        if decoder.stdout.read(1):
            raise export_error(
                "EXPORT_MEDIA_FRAME_COUNT_MISMATCH", "Exported MP4 contains extra frames."
            )
    mean = total / (frame_count * width * height)
    std = np.sqrt(np.maximum(0, square / (frame_count * width * height) - np.square(mean)))
    stats = {
        key: value.reshape(3, 1, 1).tolist()
        for key, value in {"min": minimum, "max": maximum, "mean": mean, "std": std}.items()
    }
    stats["count"] = [frame_count]
    feature = {
        "dtype": "video",
        "shape": [height, width, 3],
        "names": ["height", "width", "channels"],
        "info": {
            "video.fps": fps,
            "video.codec": stream["codec_name"],
            "video.pix_fmt": stream["pix_fmt"],
            "video.is_depth_map": False,
            "has_audio": False,
        },
    }
    return feature, stats


def materialize_video(
    assets: ExportAssetsPort,
    manifest: PublishedDatasetManifestV1,
    rollout: PublishedRolloutV1,
    references: Sequence[AlignedMediaFrameReferenceV1],
    output: Path,
    *,
    fps: int,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    receipts: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="hc-export-video-") as name:
        root = Path(name)
        first = assets.video_source(manifest, rollout, references[0])
        command = [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{first.width}x{first.height}",
            "-r",
            str(fps),
            "-i",
            "pipe:0",
            "-an",
            "-map_metadata",
            "-1",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "1",
            "-g",
            str(fps),
            "-bf",
            "0",
            "-sc_threshold",
            "0",
            "-fflags",
            "+bitexact",
            "-flags:v",
            "+bitexact",
            "-video_track_timescale",
            str(fps),
            "-movflags",
            "+faststart",
            str(output),
        ]
        with _process(command, encode=True) as encoder:
            assert encoder.stdin is not None
            for _, group in itertools.groupby(references, key=lambda ref: ref.artifact_id):
                selected = list(group)
                source = assets.video_source(manifest, rollout, selected[0])
                if (source.width, source.height) != (first.width, first.height):
                    raise export_error(
                        "EXPORT_MEDIA_DIMENSIONS_INVALID",
                        "Camera dimensions changed within an Episode.",
                    )
                if any(
                    ref.object_key != source.object_key
                    or ref.camera_id != selected[0].camera_id
                    or ref.alignment_version != selected[0].alignment_version
                    for ref in selected
                ):
                    raise export_error(
                        "EXPORT_ASSET_SCOPE_MISMATCH",
                        "Camera references disagree with their media receipt.",
                    )
                path = _localize(assets, source, root)
                receipts.append(
                    {
                        "artifact_id": selected[0].artifact_id,
                        "source_sha256": source.sha256,
                        "source_size": source.size,
                        "frame_count": len(selected),
                    }
                )
                for frame in _selected_frames(source, path, selected, root):
                    encoder.stdin.write(frame)
        feature, stats = inspect_video(output, frame_count=len(references), fps=fps)
    return feature, stats, receipts
