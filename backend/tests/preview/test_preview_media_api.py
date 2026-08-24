from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.preview.adapters import FFmpegHlsEncoder, FilePreviewMediaReader
from hc_data_platform.preview.memory import (
    HmacUrlSigner,
    InMemoryExclusionReader,
    InMemoryPreviewCache,
    InMemoryStepReader,
)
from hc_data_platform.preview.models import (
    EncodedPreviewArtifactV1,
    EncodingProfileV1,
    PreviewFrameV1,
    PreviewRequestV1,
    ViewMode,
)
from hc_data_platform.preview.router import router
from hc_data_platform.preview.service import PreviewService

NOW = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)


class LocalHlsEncoder:
    def __init__(self, media_root: Path) -> None:
        self._media_root = media_root

    def encode(
        self,
        *,
        cache_key: str,
        request: PreviewRequestV1,
        frames: object,
    ) -> EncodedPreviewArtifactV1:
        del request, frames
        directory = self._media_root / cache_key
        directory.mkdir(parents=True)
        playlist = directory / "index.m3u8"
        playlist.write_text(
            "#EXTM3U\n"
            "#EXT-X-VERSION:7\n"
            '#EXT-X-MAP:URI="init.mp4"\n'
            "#EXTINF:1.0,\n"
            "segment_00000.m4s\n"
            "#EXT-X-ENDLIST\n",
            encoding="utf-8",
        )
        (directory / "init.mp4").write_bytes(b"init-blob")
        (directory / "segment_00000.m4s").write_bytes(b"abcdef")
        return EncodedPreviewArtifactV1(
            artifact_uri=playlist.as_uri(),
            duration_seconds=1,
            frame_count=1,
        )


@pytest.fixture
def media_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, PreviewService, HmacUrlSigner, str]]:
    import hc_data_platform.preview.router as preview_router

    signer = HmacUrlSigner(b"preview-media-test-secret")
    cache = InMemoryPreviewCache()
    service = PreviewService(
        step_reader=InMemoryStepReader(
            [
                PreviewFrameV1(
                    rollout_id="rollout-1",
                    step_index=0,
                    timestamp_ns=0,
                    image_ref=b"synthetic-frame",
                )
            ]
        ),
        exclusions=InMemoryExclusionReader(),
        encoder=LocalHlsEncoder(tmp_path / "media"),
        cache=cache,
        signer=signer,
        media_reader=FilePreviewMediaReader(tmp_path / "media"),
        clock=lambda: NOW,
    )
    descriptor = service.create(
        PreviewRequestV1(
            project_id="project-1",
            dataset_id="dataset-1",
            rollout_id="rollout-1",
            lance_version="v1",
            annotation_revision=1,
            camera_id="front",
            view_mode=ViewMode.ORIGINAL,
            frequency_hz=1,
        )
    )
    monkeypatch.setattr(preview_router, "_service", service)
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(ProblemException)
    async def problem_handler(_: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.problem.status,
            content=exc.problem.model_dump(mode="json", exclude_none=True),
            media_type="application/problem+json",
        )

    with TestClient(app) as client:
        yield client, service, signer, descriptor.session_id


def test_signed_playlist_rewrites_hls_members_and_serves_media_ranges(
    media_api: tuple[TestClient, PreviewService, HmacUrlSigner, str],
    tmp_path: Path,
) -> None:
    client, service, _, session_id = media_api
    descriptor = service.get(session_id)

    assert descriptor.playlist_url.startswith(
        f"/api/v1/previews/sessions/{session_id}/media/index.m3u8?"
    )
    assert "preview.invalid" not in descriptor.playlist_url
    assert "file:" not in descriptor.playlist_url

    playlist = client.get(descriptor.playlist_url)

    assert playlist.status_code == 200
    assert playlist.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert playlist.headers["cache-control"] == "no-store"
    assert str(tmp_path) not in playlist.text
    init = re.search(r'URI="(?P<url>/api/v1/[^\"]+)"', playlist.text)
    segment = re.search(r"(?P<url>/api/v1/previews/.+/segment_00000\.m4s\?[^\n]+)", playlist.text)
    assert init is not None
    assert segment is not None

    init_response = client.get(init.group("url"), headers={"Range": "bytes=1-3"})
    segment_response = client.get(segment.group("url"), headers={"Range": "bytes=1-3"})

    assert init_response.status_code == 206
    assert init_response.content == b"nit"
    assert init_response.headers["accept-ranges"] == "bytes"
    assert init_response.headers["content-range"] == "bytes 1-3/9"
    assert segment_response.status_code == 206
    assert segment_response.content == b"bcd"
    assert segment_response.headers["content-range"] == "bytes 1-3/6"

    invalid_range = client.get(segment.group("url"), headers={"Range": "bytes=99-100"})
    multiple_ranges = client.get(segment.group("url"), headers={"Range": "bytes=0-1,3-4"})
    assert invalid_range.status_code == multiple_ranges.status_code == 416
    assert invalid_range.headers["content-type"].startswith("application/problem+json")
    assert invalid_range.json()["code"] == "PREVIEW_RANGE_NOT_SATISFIABLE"


def test_media_capability_cannot_be_reused_for_another_asset_or_after_expiry(
    media_api: tuple[TestClient, PreviewService, HmacUrlSigner, str],
) -> None:
    client, service, signer, session_id = media_api
    descriptor = service.get(session_id)
    tampered = descriptor.playlist_url.replace("index.m3u8", "init.mp4")

    denied = client.get(tampered)
    assert denied.status_code == 403
    assert denied.json()["code"] == "PREVIEW_MEDIA_FORBIDDEN"

    record = service.resolve_media(
        session_id=session_id,
        asset_name="index.m3u8",
        expires=int((NOW + timedelta(minutes=15)).timestamp()),
        signature=descriptor.playlist_url.rsplit("sig=", maxsplit=1)[1],
    )[0]
    expired = signer.sign(
        session_id=session_id,
        artifact_uri=record.artifact.artifact_uri,
        asset_name="index.m3u8",
        expires_at=NOW - timedelta(seconds=1),
    )

    expired_response = client.get(expired)
    assert expired_response.status_code == 403
    assert expired_response.json()["code"] == "PREVIEW_MEDIA_FORBIDDEN"


def test_signed_missing_asset_returns_safe_not_found(
    media_api: tuple[TestClient, PreviewService, HmacUrlSigner, str],
) -> None:
    client, service, signer, session_id = media_api
    descriptor = service.get(session_id)
    record = service.resolve_media(
        session_id=session_id,
        asset_name="index.m3u8",
        expires=int((NOW + timedelta(minutes=15)).timestamp()),
        signature=descriptor.playlist_url.rsplit("sig=", maxsplit=1)[1],
    )[0]
    missing = signer.sign(
        session_id=session_id,
        artifact_uri=record.artifact.artifact_uri,
        asset_name="segment_00001.m4s",
        expires_at=NOW + timedelta(minutes=1),
    )

    response = client.get(missing)

    assert response.status_code == 404
    assert response.json()["code"] == "PREVIEW_MEDIA_NOT_FOUND"
    assert "file:" not in response.text


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="FFmpeg runtime dependency is unavailable"
)
def test_real_ffmpeg_hls_is_streamed_through_the_signed_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hc_data_platform.preview.router as preview_router

    source = tmp_path / "source"
    source.mkdir()
    frames: list[PreviewFrameV1] = []
    for index in range(4):
        image = source / f"frame-{index}.ppm"
        image.write_bytes(
            b"P6\n32 24\n255\n" + bytes((index * 30, 40, 200 - index * 20)) * (32 * 24)
        )
        frames.append(
            PreviewFrameV1(
                rollout_id="rollout-1",
                step_index=index,
                timestamp_ns=index * 250_000_000,
                image_ref=str(image),
            )
        )
    media_root = tmp_path / "media"
    service = PreviewService(
        step_reader=InMemoryStepReader(frames),
        exclusions=InMemoryExclusionReader(),
        encoder=FFmpegHlsEncoder(media_root),
        cache=InMemoryPreviewCache(),
        signer=HmacUrlSigner(b"preview-real-ffmpeg-test-secret"),
        media_reader=FilePreviewMediaReader(media_root),
        clock=lambda: NOW,
    )
    descriptor = service.create(
        PreviewRequestV1(
            project_id="project-1",
            dataset_id="dataset-1",
            rollout_id="rollout-1",
            lance_version="v1",
            annotation_revision=1,
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
    )
    monkeypatch.setattr(preview_router, "_service", service)
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(ProblemException)
    async def problem_handler(_: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.problem.status,
            content=exc.problem.model_dump(mode="json", exclude_none=True),
            media_type="application/problem+json",
        )

    with TestClient(app) as client:
        playlist = client.get(descriptor.playlist_url)
        segment = re.search(
            r"(?P<url>/api/v1/previews/.+/segment_[0-9]{5}\.m4s\?[^\n]+)", playlist.text
        )
        assert playlist.status_code == 200
        assert segment is not None
        streamed = client.get(segment.group("url"), headers={"Range": "bytes=0-31"})

    assert streamed.status_code == 206
    assert streamed.headers["content-type"].startswith("video/iso.segment")
    assert re.fullmatch(r"bytes 0-31/[1-9][0-9]*", streamed.headers["content-range"])
    assert len(streamed.content) == 32
