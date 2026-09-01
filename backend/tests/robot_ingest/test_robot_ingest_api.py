from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.ports import InMemoryObjectStorage, crc64_ecma
from hc_data_platform.robot_ingest.models import (
    CreateRobotIngestIdentity,
    IssueCredentialCommand,
    RobotIngestUploadManifest,
    UploadTarget,
)
from hc_data_platform.robot_ingest.repository import InMemoryRobotIngestRepository
from hc_data_platform.robot_ingest.router import configure_robot_ingest, router
from hc_data_platform.robot_ingest.service import RobotIngestService
from hc_data_platform.security.auth import AuthContext

NOW = datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
ORG = "api-org"
PROJECT = "api-project"
REGION = "api-region"
TASK = "api-task-global-id"
ROBOT = "api-robot"


def _auth() -> AuthContext:
    capabilities = {
        (ORG, PROJECT, "ingest_source.read"),
        (ORG, PROJECT, "ingest_source.manage"),
    }
    return AuthContext(
        subject_id="api-admin",
        organization_ids=frozenset({ORG}),
        project_ids=frozenset({PROJECT}),
        region_codes=frozenset({REGION}),
        scope_pairs=frozenset({(PROJECT, REGION)}),
        organization_scope_triples=frozenset({(ORG, PROJECT, REGION)}),
        organization_scoped_capabilities=frozenset(capabilities),
    )


def _app(service: RobotIngestService) -> TestClient:
    configure_robot_ingest(service)
    app = FastAPI()

    @app.exception_handler(ProblemException)
    async def handle_problem(_request: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(exc.problem.model_dump(mode="json"), status_code=exc.problem.status)

    @app.middleware("http")
    async def install_user_auth(request: Request, call_next: Any) -> Any:
        request.state.auth_context = _auth()
        request.state.request_id = "robot-ingest-api-test"
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _service() -> RobotIngestService:
    repository = InMemoryRobotIngestRepository(
        targets=(
            UploadTarget(
                collection_task_id=TASK,
                organization_id=ORG,
                project_id=PROJECT,
                dataset_id="dataset_api_target",
                region_code=REGION,
                task_status="ACTIVE",
            ),
        )
    )
    return RobotIngestService(
        repository,
        InMemoryObjectStorage(),
        credential_hmac_key="robot-ingest-api-hmac-key",
        clock=lambda: NOW,
    )


def _manifest() -> dict[str, object]:
    body = b"api-raw"
    return RobotIngestUploadManifest(
        client_upload_id=str(uuid4()),
        collection_task_id=TASK,
        robot_id=ROBOT,
        capture_mode="PRESEGMENTED",
        source_format="MCAP",
        source_format_version="1",
        capture_started_at=NOW,
        capture_ended_at=NOW + timedelta(seconds=10),
        declared_episode_count=1,
        assets=(
            {
                "asset_id": "raw",
                "path": "raw/capture.mcap",
                "media_type": "application/x-mcap",
                "size_bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "crc64": crc64_ecma(body),
            },
        ),
    ).model_dump(mode="json")


def test_admin_credential_is_returned_once_and_never_listed_as_plaintext() -> None:
    service = _service()
    client = _app(service)
    root = f"/api/v1/projects/{PROJECT}/robot-ingest/identities"
    headers = {"Authorization": "Bearer user-session", "X-Organization-Id": ORG}
    created = client.post(
        root,
        headers=headers,
        json={
            "robot_id": ROBOT,
            "display_name": "API robot",
            "allowed_formats": ["MCAP"],
        },
    )
    assert created.status_code == 201
    identity_id = created.json()["data"]["ingest_identity_id"]
    issued = client.post(
        f"{root}/{identity_id}/credentials",
        headers=headers,
        json={"expires_at": None, "revoke_previous": False},
    )
    assert issued.status_code == 201
    token = issued.json()["credential"]["token"]
    assert token.startswith("hcri_")
    listed = client.get(f"{root}/{identity_id}/credentials", headers=headers)
    assert listed.status_code == 200
    assert token not in listed.text
    assert 'token"' not in listed.text


def test_robot_route_rejects_user_bearer_and_accepts_only_robot_principal() -> None:
    service = _service()
    identity = service.create_identity(
        auth=_auth(),
        organization_id=ORG,
        project_id=PROJECT,
        command=CreateRobotIngestIdentity(robot_id=ROBOT, allowed_formats=("MCAP",)),
    ).data
    issued = service.issue_credential(
        auth=_auth(),
        organization_id=ORG,
        project_id=PROJECT,
        ingest_identity_id=identity.ingest_identity_id,
        command=IssueCredentialCommand(),
    ).credential
    assert issued is not None
    client = _app(service)
    rejected = client.post(
        "/api/v1/robot-ingest/uploads",
        headers={"Authorization": "Bearer user-session"},
        json=_manifest(),
    )
    assert rejected.status_code == 401
    assert rejected.json()["code"] == "ROBOT_CREDENTIAL_INVALID"
    accepted = client.post(
        "/api/v1/robot-ingest/uploads",
        headers={"Authorization": f"Bearer {issued.token}"},
        json=_manifest(),
    )
    assert accepted.status_code == 201
    assert accepted.json()["data"]["authenticated_robot_id"] == ROBOT
    assert accepted.json()["data"]["target"]["project_id"] == PROJECT


def test_production_composed_auth_middleware_leaves_robot_bearer_to_robot_auth() -> None:
    service = _service()
    identity = service.create_identity(
        auth=_auth(),
        organization_id=ORG,
        project_id=PROJECT,
        command=CreateRobotIngestIdentity(robot_id=ROBOT, allowed_formats=("MCAP",)),
    ).data
    issued = service.issue_credential(
        auth=_auth(),
        organization_id=ORG,
        project_id=PROJECT,
        ingest_identity_id=identity.ingest_identity_id,
        command=IssueCredentialCommand(),
    ).credential
    assert issued is not None
    configure_robot_ingest(service)
    app = create_app(settings=Settings(environment="test", runtime_backend="memory"))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/robot-ingest/uploads",
            headers={"Authorization": f"Bearer {issued.token}"},
            json=_manifest(),
        )
    assert response.status_code == 201
    assert response.json()["data"]["authenticated_robot_id"] == ROBOT
    assert response.json()["data"]["target"]["project_id"] == PROJECT


def test_robot_route_requires_bearer_and_does_not_accept_scope_payload_override() -> None:
    service = _service()
    client = _app(service)
    missing = client.post("/api/v1/robot-ingest/uploads", json=_manifest())
    assert missing.status_code == 401
    assert missing.json()["code"] == "ROBOT_CREDENTIAL_INVALID"

    identity = service.create_identity(
        auth=_auth(),
        organization_id=ORG,
        project_id=PROJECT,
        command=CreateRobotIngestIdentity(robot_id=ROBOT, allowed_formats=("MCAP",)),
    ).data
    issued = service.issue_credential(
        auth=_auth(),
        organization_id=ORG,
        project_id=PROJECT,
        ingest_identity_id=identity.ingest_identity_id,
        command=IssueCredentialCommand(),
    ).credential
    assert issued is not None
    forged_manifest = {**_manifest(), "organization_id": "forged-org"}
    forged = client.post(
        "/api/v1/robot-ingest/uploads",
        headers={"Authorization": f"Bearer {issued.token}"},
        json=forged_manifest,
    )
    assert forged.status_code == 403
    assert forged.json()["code"] == "UPLOAD_SCOPE_MISMATCH"


def test_project_history_requires_an_exact_region_scope_header() -> None:
    service = _service()
    client = _app(service)
    path = f"/api/v1/projects/{PROJECT}/robot-ingest/uploads"
    headers = {"Authorization": "Bearer user-session", "X-Organization-Id": ORG}
    missing_region = client.get(path, headers=headers)
    assert missing_region.status_code == 422
    visible = client.get(path, headers={**headers, "X-Region-Code": REGION})
    assert visible.status_code == 200
    assert visible.json() == {"items": []}
