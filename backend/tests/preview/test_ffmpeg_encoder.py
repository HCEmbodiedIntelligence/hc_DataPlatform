from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from hc_data_platform.preview.adapters import FFmpegHlsEncoder, PreviewEncodingError
from hc_data_platform.preview.models import (
    EncodingProfileV1,
    PlaceholderDescriptorV1,
    PreviewRequestV1,
    RenderFrameV1,
    ViewMode,
)
from hc_data_platform.preview.service import preview_cache_key


def preview_request() -> PreviewRequestV1:
    return PreviewRequestV1(
        project_id="project-1",
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


def _ppm(path: Path, *, red: int, green: int, blue: int) -> None:
    width, height = 32, 24
    path.write_bytes(
        f"P6\n{width} {height}\n255\n".encode("ascii") + bytes((red, green, blue)) * width * height
    )


def _render_frames(paths: Sequence[Path]) -> tuple[RenderFrameV1, ...]:
    return tuple(
        RenderFrameV1(
            playback_frame=index,
            step_index=index,
            timestamp_ns=index * 250_000_000,
            image_ref=str(path),
        )
        for index, path in enumerate(paths)
    )


def test_ffmpeg_failure_does_not_commit_a_partial_cache_directory(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []

    def fail(command: Sequence[str]) -> None:
        commands.append(tuple(command))
        raise PreviewEncodingError("intentional failure")

    request = preview_request()
    cache_key = preview_cache_key(request)
    encoder = FFmpegHlsEncoder(tmp_path, runner=fail)
    placeholder = PlaceholderDescriptorV1(
        invalid_reason="camera decode failed",
        playback_frame=0,
        step_index=0,
    )
    frame = RenderFrameV1(
        playback_frame=0,
        step_index=0,
        timestamp_ns=0,
        placeholder=placeholder,
    )

    with pytest.raises(PreviewEncodingError, match="intentional failure"):
        encoder.encode(cache_key=cache_key, request=request, frames=(frame,))

    assert commands
    assert all(isinstance(command, tuple) for command in commands)
    assert not (tmp_path / cache_key).exists()
    assert list(tmp_path.iterdir()) == []


def test_embedded_lance_image_is_materialized_only_for_the_decode_call(tmp_path: Path) -> None:
    embedded = b"P6\n1 1\n255\n\xff\x00\x00"
    observed_sources: list[bytes] = []

    def runner(command: Sequence[str]) -> None:
        target = Path(command[-1])
        if "-frames:v" in command:
            source = Path(command[command.index("-i") + 1])
            observed_sources.append(source.read_bytes())
            _ppm(target, red=255, green=0, blue=0)
        else:
            target.write_text("#EXTM3U\n#EXT-X-ENDLIST\n", encoding="utf-8")

    request = preview_request()
    cache_key = preview_cache_key(request)
    artifact = FFmpegHlsEncoder(tmp_path, runner=runner).encode(
        cache_key=cache_key,
        request=request,
        frames=(
            RenderFrameV1(
                playback_frame=0,
                step_index=0,
                timestamp_ns=0,
                image_ref=embedded,
            ),
        ),
    )

    assert observed_sources == [embedded]
    assert artifact.frame_count == 1
    assert list((tmp_path / cache_key / "frames").glob("*.source-image")) == []


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg/FFprobe runtime dependency is unavailable",
)
def test_ffmpeg_generates_probeable_hls_with_cmaf_segments(tmp_path: Path) -> None:
    source_directory = tmp_path / "source"
    source_directory.mkdir()
    source_paths = [source_directory / f"frame-{index}.ppm" for index in range(6)]
    for index, source_path in enumerate(source_paths):
        _ppm(source_path, red=index * 30, green=40, blue=200 - index * 20)

    request = preview_request()
    cache_root = tmp_path / "cache"
    render_frames = list(_render_frames(source_paths[:5]))
    render_frames[2] = render_frames[2].model_copy(update={"excluded": True})
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
    artifact = FFmpegHlsEncoder(cache_root).encode(
        cache_key=preview_cache_key(request),
        request=request,
        frames=render_frames,
    )
    playlist = Path(unquote(urlparse(artifact.artifact_uri).path))
    playlist_text = playlist.read_text(encoding="utf-8")

    assert artifact.frame_count == 6
    assert artifact.duration_seconds == 1.5
    assert '#EXT-X-MAP:URI="init.mp4"' in playlist_text
    assert (playlist.parent / "init.mp4").is_file()
    assert list(playlist.parent.glob("segment_*.m4s"))

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
    assert stream == {"codec_name": "h264", "width": 64, "height": 48}


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="FFmpeg runtime dependency is unavailable",
)
def test_real_ffmpeg_decode_failure_leaves_no_committed_artifact(tmp_path: Path) -> None:
    request = preview_request()
    cache_key = preview_cache_key(request)
    cache_root = tmp_path / "cache"
    frame = RenderFrameV1(
        playback_frame=0,
        step_index=0,
        timestamp_ns=0,
        image_ref=str(tmp_path / "does-not-exist.png"),
    )

    with pytest.raises(PreviewEncodingError):
        FFmpegHlsEncoder(cache_root).encode(
            cache_key=cache_key,
            request=request,
            frames=(frame,),
        )

    assert not (cache_root / cache_key).exists()
    assert list(cache_root.iterdir()) == []
