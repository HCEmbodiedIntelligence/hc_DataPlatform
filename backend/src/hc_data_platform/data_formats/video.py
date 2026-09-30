"""Inspect locally supplied RGB/depth media with bounded decode memory."""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .errors import format_error


@contextmanager
def video_process(command: list[str], *, encode: bool = False) -> Iterator[subprocess.Popen[bytes]]:
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
                raise format_error(
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


def inspect_video(
    path: Path, *, frame_count: int, fps: float
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
        raise format_error("EXPORT_VIDEO_INVALID", "MP4 FPS/frame count does not match Parquet.")
    if width * height * 3 > 64 * 1024**2:
        raise format_error(
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
    with video_process(command) as decoder:
        assert decoder.stdout is not None
        for _ in range(frame_count):
            frame = decoder.stdout.read(width * height * 3)
            if len(frame) != width * height * 3:
                raise format_error(
                    "EXPORT_MEDIA_FRAMES_MISSING",
                    "Exported MP4 contains a damaged or missing frame.",
                )
            pixels = np.frombuffer(frame, dtype=np.uint8).reshape(-1, 3).astype(np.float64) / 255
            total += pixels.sum(axis=0)
            square += np.square(pixels).sum(axis=0)
            minimum = np.minimum(minimum, pixels.min(axis=0))
            maximum = np.maximum(maximum, pixels.max(axis=0))
        if decoder.stdout.read(1):
            raise format_error(
                "EXPORT_MEDIA_FRAME_COUNT_MISMATCH", "Exported MP4 contains extra frames."
            )
    mean = total / (frame_count * width * height)
    std = np.sqrt(np.maximum(0, square / (frame_count * width * height) - np.square(mean)))
    stats = {
        key: value.reshape(3, 1, 1).tolist()
        for key, value in {"min": minimum, "max": maximum, "mean": mean, "std": std}.items()
    }
    stats["count"] = [frame_count]
    feature: dict[str, Any] = {
        "dtype": "video",
        "shape": [height, width, 3],
        "names": ["height", "width", "channels"],
        "info": {
            "video.fps": fps,
            "video.codec": stream["codec_name"],
            "video.pix_fmt": stream["pix_fmt"],
            "is_depth_map": False,
            "has_audio": False,
        },
    }
    return feature, stats


def inspect_depth_video(
    path: Path,
    frame_count: int,
    fps: float | Fraction,
    info: dict[str, Any],
    *,
    masks: Iterator[NDArray[np.bool_]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    import av

    from .holobrain_depth import DepthProfile, dequantize

    total = square = 0.0
    low, high = float("inf"), float("-inf")
    count = samples = 0
    profile = DepthProfile(
        depth_min=info["video.depth_min"],
        depth_max=info["video.depth_max"],
        shift=info["video.shift"],
        use_log=info["video.use_log"],
    )
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        if (
            stream.codec_context.pix_fmt != "gray12le"
            or stream.codec_context.name != "hevc"
            or stream.average_rate != Fraction(str(fps))
        ):
            raise format_error("EXPORT_DEPTH_FORMAT_INVALID", "Depth video codec/FPS changed.")
        for index, frame in enumerate(container.decode(video=0)):
            if (
                frame.pts is None
                or frame.time_base is None
                or abs(float(frame.pts * frame.time_base) - index / fps) > 1e-6
            ):
                raise format_error(
                    "EXPORT_DEPTH_TIME_INVALID", "Depth PTS differs from the dataset grid."
                )
            pixels = dequantize(
                frame.to_ndarray(format="gray12le"), profile, output_unit=info["depth_unit"]
            ).astype(np.float64)
            if masks is not None:
                valid = next(masks, None)
                if valid is None or valid.shape != pixels.shape:
                    raise ValueError("depth statistics require a mask for every frame")
                pixels = pixels[valid]
            total += pixels.sum()
            square += np.square(pixels).sum()
            if pixels.size:
                low, high = min(low, float(pixels.min())), max(high, float(pixels.max()))
            samples += pixels.size
            count += 1
        height, width = stream.height, stream.width
    if count != frame_count:
        raise format_error(
            "EXPORT_DEPTH_FRAME_COUNT_INVALID", "Depth frame count differs from data rows."
        )
    if masks is not None and next(masks, None) is not None:
        raise ValueError("depth statistics have extra masks")
    if count == 0:
        raise ValueError("depth statistics require a nonempty stream")
    mean = total / samples if samples else 0.0
    stats: dict[str, Any] = (
        {
            k: [[[float(v)]]]
            for k, v in {
                "min": low,
                "max": high,
                "mean": mean,
                "std": np.sqrt(max(0, square / samples - mean * mean)),
            }.items()
        }
        if samples
        else {}
    )
    if samples:
        stats["count"] = [count]
    feature: dict[str, Any] = {
        "dtype": "video",
        "shape": [height, width, 1],
        "names": ["height", "width", "channels"],
        "info": {**info, "video.fps": fps, "video.height": height, "video.width": width},
    }
    if masks is not None:
        feature["info"]["hc.stats_valid_pixels"] = samples
        if not samples:
            # No finite placeholder is a measured min/max/mean/std. Official
            # readers can read the video without that feature's statistics;
            # normalization consumers must handle their explicit absence.
            feature["info"]["hc.stats_status"] = "NO_VALID_PIXELS"
    return feature, stats
