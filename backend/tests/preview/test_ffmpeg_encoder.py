from __future__ import annotations

import json
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pytest
from PIL import Image

from hc_data_platform.preview.adapters import (
    FFmpegHlsEncoder,
    PreviewEncodingError,
    PreviewGenerationCancelled,
)
from hc_data_platform.preview.models import (
    PlaceholderDescriptorV1,
    PreviewRequestV1,
    RenderFrameV1,
)
from hc_data_platform.preview.profiles import PreviewProfileCatalog
from hc_data_platform.preview.service import preview_artifact_key


def preview_request() -> PreviewRequestV1:
    return PreviewRequestV1(
        project_id="project-1",
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        lance_version="7",
        camera_id="front",
        profile_id="quad-h264-360p-v1",
        frequency_hz=4,
    )


def jpeg(color: tuple[int, int, int] = (200, 40, 20)) -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 24), color).save(output, "JPEG")
    return output.getvalue()


class Sink:
    def __init__(self) -> None:
        self.content = bytearray()

    def write(self, value: bytes) -> int:
        self.content.extend(value)
        return len(value)

    def close(self) -> None:
        return None


class SuccessfulProcess:
    def __init__(self, command: list[str], **_: Any) -> None:
        self.command = command
        self.stdin: Sink | None = Sink()
        self.stderr = None
        self.returncode = 0
        target = Path(command[-1])
        target.write_text(
            '#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:1,\nsegment_00000.m4s\n',
            encoding="utf-8",
        )
        (target.parent / "init.mp4").write_bytes(b"init")
        (target.parent / "segment_00000.m4s").write_bytes(b"segment")

    def communicate(self, timeout: float) -> tuple[bytes, bytes]:
        assert timeout > 0
        return b"", b""

    def kill(self) -> None:
        self.returncode = -9


def test_one_hundred_frames_use_one_h264_process_and_no_frame_directory(
    tmp_path: Path,
) -> None:
    processes: list[SuccessfulProcess] = []

    def process_factory(command: list[str], **kwargs: Any) -> SuccessfulProcess:
        process = SuccessfulProcess(command, **kwargs)
        processes.append(process)
        return process

    request = preview_request()
    artifact = FFmpegHlsEncoder(
        tmp_path,
        process_factory=process_factory,
        profiles=PreviewProfileCatalog(("quad-h264-360p-v1",)),
    ).encode(
        cache_key=preview_artifact_key(request),
        request=request,
        frames=(
            RenderFrameV1(
                playback_frame=index,
                step_index=index,
                timestamp_ns=index * 250_000_000,
                image_ref=jpeg(),
            )
            for index in range(100)
        ),
    )

    assert len(processes) == 1
    command = processes[0].command
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-threads") + 1] == "2"
    assert "libvpx-vp9" not in command
    assert command[command.index("-vf") + 1].startswith("scale=640:360:")
    assert processes[0].stdin is None
    assert artifact.frame_count == 100
    assert not (tmp_path / preview_artifact_key(request) / "frames").exists()


def test_placeholder_jpeg_is_precomputed_and_reused(tmp_path: Path) -> None:
    encoder = FFmpegHlsEncoder(
        tmp_path,
        process_factory=SuccessfulProcess,
        profiles=PreviewProfileCatalog(("quad-h264-360p-v1",)),
    )
    first = encoder._placeholder(width=640, height=360)
    second = encoder._placeholder(width=640, height=360)

    assert first is second
    assert first.startswith(b"\xff\xd8") and first.endswith(b"\xff\xd9")


def test_cancellation_kills_ffmpeg_and_removes_partial_staging(tmp_path: Path) -> None:
    processes: list[SuccessfulProcess] = []

    def process_factory(command: list[str], **kwargs: Any) -> SuccessfulProcess:
        process = SuccessfulProcess(command, **kwargs)
        processes.append(process)
        return process

    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    request = preview_request()
    cache_key = preview_artifact_key(request)
    with pytest.raises(PreviewGenerationCancelled):
        FFmpegHlsEncoder(
            tmp_path,
            process_factory=process_factory,
            profiles=PreviewProfileCatalog(("quad-h264-360p-v1",)),
        ).encode(
            cache_key=cache_key,
            request=request,
            frames=(
                RenderFrameV1(
                    playback_frame=index,
                    step_index=index,
                    timestamp_ns=index * 250_000_000,
                    image_ref=jpeg(),
                )
                for index in range(2)
            ),
            cancelled=cancelled,
        )

    assert len(processes) == 1
    assert processes[0].returncode == -9
    assert not (tmp_path / cache_key).exists()
    assert not tuple(tmp_path.glob(f".{cache_key}.*"))


def test_oversized_file_is_rejected_before_reading_or_committing(tmp_path: Path) -> None:
    source = tmp_path / "too-large.jpg"
    source.write_bytes(jpeg() + b"x" * 2_048)
    request = preview_request()
    cache_root = tmp_path / "cache"
    frame = RenderFrameV1(
        playback_frame=0,
        step_index=0,
        timestamp_ns=0,
        image_ref=str(source),
    )

    with pytest.raises(PreviewEncodingError, match="size limit"):
        FFmpegHlsEncoder(
            cache_root,
            process_factory=SuccessfulProcess,
            profiles=PreviewProfileCatalog(("quad-h264-360p-v1",)),
            max_embedded_image_bytes=1_024,
        ).encode(
            cache_key=preview_artifact_key(request),
            request=request,
            frames=(frame,),
        )

    assert not (cache_root / preview_artifact_key(request)).exists()


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg/FFprobe runtime dependency is unavailable",
)
def test_real_ffmpeg_generates_probeable_h264_hls(tmp_path: Path) -> None:
    request = preview_request()
    encoder = FFmpegHlsEncoder(
        tmp_path / "cache",
        profiles=PreviewProfileCatalog(("quad-h264-360p-v1",)),
        ffmpeg_threads=1,
    )
    render_frames = [
        RenderFrameV1(
            playback_frame=index,
            step_index=index,
            timestamp_ns=index * 250_000_000,
            image_ref=jpeg((index * 30, 40, 200 - index * 20)),
        )
        for index in range(5)
    ]
    render_frames.append(
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

    artifact = encoder.encode(
        cache_key=preview_artifact_key(request),
        request=request,
        frames=render_frames,
    )
    playlist = Path(unquote(urlparse(artifact.artifact_uri).path))

    assert artifact.frame_count == 6
    assert '#EXT-X-MAP:URI="init.mp4"' in playlist.read_text(encoding="utf-8")
    assert list(playlist.parent.glob("segment_*.m4s"))
    assert not (playlist.parent / "frames").exists()

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
    assert json.loads(probe.stdout)["streams"][0] == {
        "codec_name": "h264",
        "width": 640,
        "height": 360,
    }


def test_cleanup_removes_only_the_exact_keyed_staging_directory(tmp_path: Path) -> None:
    request = preview_request()
    encoder = FFmpegHlsEncoder(
        tmp_path,
        process_factory=SuccessfulProcess,
        profiles=PreviewProfileCatalog(("quad-h264-360p-v1",)),
    )
    artifact = encoder.encode(
        cache_key=preview_artifact_key(request),
        request=request,
        frames=(
            RenderFrameV1(
                playback_frame=0,
                step_index=0,
                timestamp_ns=0,
                image_ref=jpeg(),
            ),
        ),
    )

    encoder.cleanup(artifact)

    assert not Path(unquote(urlparse(artifact.artifact_uri).path)).parent.exists()
