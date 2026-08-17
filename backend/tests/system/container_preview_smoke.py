"""Run current preview source against FFmpeg shipped in a runtime image."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

from hc_data_platform.preview.adapters import FFmpegHlsEncoder, PreviewEncodingError
from hc_data_platform.preview.models import (
    EncodingProfileV1,
    PlaceholderDescriptorV1,
    PreviewRequestV1,
    RenderFrameV1,
    ViewMode,
)
from hc_data_platform.preview.service import preview_cache_key


def _request() -> PreviewRequestV1:
    return PreviewRequestV1(
        project_id="be12",
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        lance_version="7",
        annotation_revision=3,
        camera_id="front",
        view_mode=ViewMode.ORIGINAL,
        frequency_hz=4,
        encoding_profile=EncodingProfileV1(
            width=64,
            height=48,
            video_bitrate_kbps=128,
            segment_duration_seconds=0.5,
            preset="ultrafast",
        ),
    )


def _write_ppm(path: Path, index: int) -> None:
    width, height = 32, 24
    color = bytes((index * 30, 40, 200 - index * 20))
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode() + color * width * height)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="be12-preview-") as directory:
        root = Path(directory)
        source = root / "source"
        source.mkdir()
        paths = [source / f"frame-{index}.ppm" for index in range(6)]
        for index, path in enumerate(paths):
            _write_ppm(path, index)

        request = _request()
        frames = [
            RenderFrameV1(
                playback_frame=index,
                step_index=index,
                timestamp_ns=index * 250_000_000,
                image_ref=str(path),
            )
            for index, path in enumerate(paths[:5])
        ]
        frames[2] = frames[2].model_copy(update={"excluded": True})
        frames.append(
            RenderFrameV1(
                playback_frame=5,
                step_index=5,
                timestamp_ns=1_250_000_000,
                placeholder=PlaceholderDescriptorV1(
                    invalid_reason="camera decode failed",
                    playback_frame=5,
                    step_index=5,
                ),
            )
        )
        artifact = FFmpegHlsEncoder(root / "cache").encode(
            cache_key=preview_cache_key(request),
            request=request,
            frames=frames,
        )
        playlist = Path(unquote(urlparse(artifact.artifact_uri).path))
        playlist_text = playlist.read_text(encoding="utf-8")
        if '#EXT-X-MAP:URI="init.mp4"' not in playlist_text:
            raise AssertionError("CMAF initialization segment is missing")
        if not list(playlist.parent.glob("segment_*.m4s")):
            raise AssertionError("CMAF media segments are missing")
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_name,width,height",
                "-of",
                "json",
                str(playlist),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        stream = json.loads(probe.stdout)["streams"][0]
        if stream != {"codec_name": "h264", "width": 64, "height": 48}:
            raise AssertionError(f"unexpected preview stream: {stream}")

        failure_root = root / "failure-cache"
        missing_frame = RenderFrameV1(
            playback_frame=0,
            step_index=0,
            timestamp_ns=0,
            image_ref=str(root / "does-not-exist.png"),
        )
        try:
            FFmpegHlsEncoder(failure_root).encode(
                cache_key=preview_cache_key(request),
                request=request,
                frames=(missing_frame,),
            )
        except PreviewEncodingError:
            pass
        else:
            raise AssertionError("missing input unexpectedly encoded")
        if failure_root.exists() and list(failure_root.iterdir()):
            raise AssertionError("failed encode left a committed cache artifact")

        print(
            json.dumps(
                {
                    "passed": True,
                    "codec": stream,
                    "frame_count": artifact.frame_count,
                    "duration_seconds": artifact.duration_seconds,
                    "cmaf_segments": len(list(playlist.parent.glob("segment_*.m4s"))),
                    "failed_encode_left_artifacts": False,
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
