from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.preview.memory import (
    HmacUrlSigner,
    InMemoryExclusionReader,
    InMemoryPreviewArtifactStore,
    InMemoryPreviewRepository,
    InMemoryStepReader,
)
from hc_data_platform.preview.models import (
    EncodedPreviewArtifactV1,
    PreviewDescriptorV1,
    PreviewFrameV1,
    PreviewPendingDescriptorV1,
    PreviewRequestV1,
    PreviewScopeV1,
)
from hc_data_platform.preview.router import router
from hc_data_platform.preview.service import (
    PreviewControlPlaneService,
    PreviewGenerationService,
)

NOW = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
SCOPE = PreviewScopeV1(
    organization_id="organization-1",
    project_id="project-1",
    region_code="cn-test",
)


class LocalHlsEncoder:
    def __init__(self, media_root: Path) -> None:
        self._media_root = media_root

    def encode(
        self,
        *,
        cache_key: str,
        request: PreviewRequestV1,
        frames: object,
        cancelled=None,  # type: ignore[no-untyped-def]
    ) -> EncodedPreviewArtifactV1:
        del request, cancelled
        frame_count = sum(1 for _ in frames)  # type: ignore[union-attr]
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
            frame_count=frame_count,
        )

    def cleanup(self, encoded: EncodedPreviewArtifactV1) -> None:
        del encoded


@pytest.fixture
def media_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, PreviewControlPlaneService, InMemoryPreviewArtifactStore, str]]:
    import hc_data_platform.preview.router as preview_router

    repository = InMemoryPreviewRepository()
    store = InMemoryPreviewArtifactStore()
    signer = HmacUrlSigner(b"preview-media-test-secret")
    control = PreviewControlPlaneService(
        repository=repository,
        store=store,
        signer=signer,
        clock=lambda: NOW,
    )
    generation = PreviewGenerationService(
        step_reader=InMemoryStepReader(
            [
                PreviewFrameV1(
                    rollout_id="rollout-1",
                    step_index=0,
                    timestamp_ns=0,
                    image_ref=b"\xff\xd8synthetic-frame\xff\xd9",
                )
            ]
        ),
        exclusions=InMemoryExclusionReader(),
        encoder=LocalHlsEncoder(tmp_path / "media"),
        repository=repository,
        store=store,
        clock=lambda: NOW,
    )
    request = PreviewRequestV1(
        project_id="project-1",
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        lance_version="v1",
        camera_id="front",
        frequency_hz=1,
    )
    pending = asyncio.run(control.create_session(SCOPE, request))
    assert isinstance(pending, PreviewPendingDescriptorV1)
    generation.generate(SCOPE, request, job_id=pending.job_id)
    descriptor = asyncio.run(control.create_session(SCOPE, request))
    assert isinstance(descriptor, PreviewDescriptorV1)

    monkeypatch.setattr(preview_router, "_service", control)
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
        yield client, control, store, descriptor.session_id


def test_api_serves_only_small_playlist_and_members_use_direct_object_urls(
    media_api: tuple[TestClient, PreviewControlPlaneService, InMemoryPreviewArtifactStore, str],
) -> None:
    client, control, _, session_id = media_api
    descriptor = control.get_session(SCOPE, session_id)

    playlist = client.get(descriptor.playlist_url)

    assert playlist.status_code == 200
    assert playlist.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert playlist.headers["cache-control"] == "no-store"
    members = re.findall(r'https://objects\.invalid/[^"\n]+', playlist.text)
    assert len(members) == 2
    assert all("derived/previews/project-1/" in member for member in members)
    assert all("/api/v1/previews/" not in member for member in members)


def test_playlist_capability_rejects_tampering(
    media_api: tuple[TestClient, PreviewControlPlaneService, InMemoryPreviewArtifactStore, str],
) -> None:
    client, control, _, session_id = media_api
    descriptor = control.get_session(SCOPE, session_id)

    denied = client.get(descriptor.playlist_url.replace("sig=", "sig=0"))

    assert denied.status_code == 422 or denied.status_code == 403
    assert "playlist_key" not in denied.text


def test_committed_playlist_cannot_reference_an_object_outside_its_manifest(
    media_api: tuple[TestClient, PreviewControlPlaneService, InMemoryPreviewArtifactStore, str],
) -> None:
    client, control, store, session_id = media_api
    descriptor = control.get_session(SCOPE, session_id)
    artifact = store.objects
    playlist_key = next(key for key in artifact if key.endswith("/index.m3u8"))
    artifact[playlist_key] = artifact[playlist_key].replace(
        b"segment_00000.m4s", b"segment_99999.m4s"
    )

    response = client.get(descriptor.playlist_url)

    assert response.status_code == 409
    assert response.json()["code"] == "PREVIEW_MANIFEST_INVALID"
