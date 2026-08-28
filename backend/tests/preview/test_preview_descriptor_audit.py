from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.preview.audit import InMemoryPreviewAuditRecorder
from hc_data_platform.preview.memory import (
    HmacUrlSigner,
    InMemoryExclusionReader,
    InMemoryMediaEncoder,
    InMemoryPreviewArtifactStore,
    InMemoryPreviewRepository,
    InMemoryStepReader,
)
from hc_data_platform.preview.models import (
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
from hc_data_platform.security import AuthContext


def _payload() -> dict[str, object]:
    return {
        "project_id": "project-a",
        "dataset_id": "dataset-a",
        "rollout_id": "rollout-a",
        "lance_version": "v1",
        "annotation_revision": 1,
        "camera_id": "front",
        "view_mode": "original",
        "frequency_hz": 2,
    }


def _service() -> PreviewControlPlaneService:
    repository = InMemoryPreviewRepository()
    store = InMemoryPreviewArtifactStore()
    def clock() -> datetime:
        return datetime(2026, 8, 20, tzinfo=timezone.utc)
    control = PreviewControlPlaneService(
        repository=repository,
        store=store,
        signer=HmacUrlSigner(b"preview-audit-test-secret"),
        clock=clock,
    )
    generation = PreviewGenerationService(
        step_reader=InMemoryStepReader(
            [
                PreviewFrameV1(
                    rollout_id="rollout-a",
                    step_index=0,
                    timestamp_ns=0,
                    image_ref=b"\xff\xd8preview-audit-frame\xff\xd9",
                )
            ]
        ),
        exclusions=InMemoryExclusionReader(),
        encoder=InMemoryMediaEncoder(),
        repository=repository,
        store=store,
        clock=clock,
    )
    request = PreviewRequestV1.model_validate(_payload())
    scope = PreviewScopeV1(
        organization_id="organization-a",
        project_id="project-a",
        region_code="cn-test",
    )
    pending = asyncio.run(control.create_session(scope, request))
    assert isinstance(pending, PreviewPendingDescriptorV1)
    generation.generate(
        scope,
        request.model_copy(update={"annotation_revision": 0}),
        job_id=pending.job_id,
    )
    return control


@pytest.fixture
def audited_preview_api(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, InMemoryPreviewAuditRecorder]]:
    import hc_data_platform.preview.router as preview_router

    recorder = InMemoryPreviewAuditRecorder()
    monkeypatch.setattr(preview_router, "_service", _service())
    monkeypatch.setattr(preview_router, "_audit_recorder", recorder)
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def install_verified_auth(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.auth_context = AuthContext(
            subject_id="preview-auditor",
            project_ids=frozenset({"project-a"}),
            region_codes=frozenset({"cn-test"}),
            roles=frozenset({"uploader"}),
            organization_ids=frozenset({"organization-a"}),
            organization_scope_triples=frozenset(
                {("organization-a", "project-a", "cn-test")}
            ),
        )
        return await call_next(request)

    @app.exception_handler(ProblemException)
    async def problem_handler(_: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.problem.status,
            content=exc.problem.model_dump(mode="json", exclude_none=True),
            media_type="application/problem+json",
        )

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, recorder


def test_authenticated_descriptor_create_and_refresh_are_redacted_and_audited(
    audited_preview_api: tuple[TestClient, InMemoryPreviewAuditRecorder],
) -> None:
    client, recorder = audited_preview_api

    created = client.post(
        "/api/v1/previews/sessions",
        json=_payload(),
        headers={
            "X-Organization-Id": "organization-a",
            "X-Region-Code": "cn-test",
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    refreshed = client.get(
        f"/api/v1/previews/sessions/{session_id}?project_id=project-a",
        headers={
            "X-Organization-Id": "organization-a",
            "X-Region-Code": "cn-test",
        },
    )
    assert refreshed.status_code == 200

    assert [(event.operation, event.session_id) for event in recorder.events] == [
        ("CREATED", session_id),
        ("REFRESHED", session_id),
    ]
    for event in recorder.events:
        assert event.project_id == "project-a"
        assert event.region_code == "cn-test"
        assert event.actor_id == "preview-auditor"
        assert event.dataset_id == "dataset-a"
        assert event.rollout_id == "rollout-a"
        assert event.camera_id == "front"
        assert event.view_mode == "original"
        assert event.grant_expires_at.tzinfo is not None
        assert event.request_id


def test_preview_descriptor_is_not_returned_when_the_audit_ledger_fails(
    audited_preview_api: tuple[TestClient, InMemoryPreviewAuditRecorder],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = audited_preview_api

    class FailingRecorder:
        def append_descriptor_issue(self, event: object) -> None:
            del event
            raise RuntimeError("audit ledger unavailable")

    import hc_data_platform.preview.router as preview_router

    monkeypatch.setattr(preview_router, "_audit_recorder", FailingRecorder())
    response = client.post(
        "/api/v1/previews/sessions",
        json=_payload(),
        headers={
            "X-Organization-Id": "organization-a",
            "X-Region-Code": "cn-test",
        },
    )

    assert response.status_code == 500
    assert "playlist_url" not in response.text
