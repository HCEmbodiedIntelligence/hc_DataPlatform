from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.data_schemas.models import (
    DataSchemaVersionRecord,
    SchemaBlockedReason,
    SchemaHash,
)
from hc_data_platform.data_schemas.repository import (
    InMemoryDataSchemaRepository,
    SchemaRouteFact,
)
from hc_data_platform.data_schemas.router import configure_data_schemas, router
from hc_data_platform.data_schemas.service import DataSchemaService
from hc_data_platform.security.auth import AuthContext

PROJECT_ID = "project-a"
REGION_CODE = "cn-shanghai-01"
ORGANIZATION_ID = "organization-a"


def _auth(*, capabilities: frozenset[str] | None = None) -> AuthContext:
    resolved_capabilities = (
        capabilities
        if capabilities is not None
        else frozenset(
            {
                "data_schema.read",
                "data_schema.create",
                "data_schema.import",
                "data_schema.validate",
                "data_schema.publish",
                "dataset_version.read",
            }
        )
    )
    return AuthContext(
        subject_id="schema-reader",
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        scope_pairs=frozenset({(PROJECT_ID, None), (PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset(
            (PROJECT_ID, capability) for capability in resolved_capabilities
        ),
    )


def _service() -> tuple[DataSchemaService, InMemoryDataSchemaRepository]:
    version = DataSchemaVersionRecord(
        schema_id="image-schema",
        family_id="image-family",
        schema_version="1",
        display_name="前视图像",
        logical_type="IMAGE",
        status="PUBLISHED",
        compatibility_mode="BACKWARD",
        compatibility_result="BACKWARD",
        schema_hash=SchemaHash(
            algorithm="SHA-256",
            canonicalization_version="schema-c14n-v1",
            value="a" * 64,
        ),
        schema_definition={"fields": [{"name": "image", "type": "bytes"}]},
        etag='"image-schema:1"',
        allowed_actions=("VIEW",),
        blocked_reasons=(
            SchemaBlockedReason(code="SCHEMA_PUBLISH_UNAPPROVED", message="当前范围未开放发布。"),
        ),
    )
    repository = InMemoryDataSchemaRepository(
        organization_projects=((ORGANIZATION_ID, PROJECT_ID),),
        versions=((ORGANIZATION_ID, version),),
        route_facts=(
            SchemaRouteFact(
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                component_id="camera-a",
                schema_id="image-schema",
                schema_version="1",
                relation_revision="camera-a:channel-a:image-schema:1",
            ),
        ),
        dataset_versions=(
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                REGION_CODE,
                "dataset_p17reference",
                "version_p17reference",
                "READY",
            ),
        ),
    )
    return (
        DataSchemaService(
            repository,
            clock=lambda: datetime(2026, 8, 19, 12, tzinfo=timezone.utc),
        ),
        repository,
    )


def _app(current: dict[str, AuthContext | None]) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(ProblemException)
    async def handle_problem(_request: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(exc.problem.model_dump(mode="json"), status_code=exc.problem.status)

    @app.middleware("http")
    async def install_auth(request: Request, call_next: Any) -> Any:
        request.state.auth_context = current["value"]
        request.state.request_id = "data-schema-router-test"
        return await call_next(request)

    app.include_router(router)
    return app


def test_data_schema_read_route_resolution_and_real_authoring_publication() -> None:
    service, repository = _service()
    configure_data_schemas(service)
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    root = f"/api/v1/organizations/{ORGANIZATION_ID}/stream-schemas"
    headers = {"Authorization": "Bearer test", "X-Project-ID": PROJECT_ID}

    listing = client.get(root, params={"q": "图像"}, headers=headers)
    assert listing.status_code == 200
    assert listing.headers["cache-control"] == "private, no-store"
    assert listing.json()["items"][0]["schema_id"] == "image-schema"

    detail = client.get(f"{root}/image-schema/versions/1", headers=headers)
    assert detail.status_code == 200
    assert detail.headers["etag"] == '"image-schema:1"'
    assert detail.json()["data"]["schema_hash"]["algorithm"] == "SHA-256"

    route = client.get(
        f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/route-resolutions/p15-to-p17",
        params={
            "schema_id": "image-schema",
            "schema_version": "1",
            "component_id": "camera-a",
            "detail_tab": "references",
        },
        headers={"Authorization": "Bearer test"},
    )
    assert route.status_code == 200
    assert route.json()["data"]["scope"] == {
        "organization_id": ORGANIZATION_ID,
        "project_id": PROJECT_ID,
        "region_code": REGION_CODE,
    }

    create = client.post(
        root,
        headers={**headers, "Idempotency-Key": "schema-create-a"},
        json={
            "schema_id": "camera-schema",
            "family_id": "camera-family",
            "display_name": "相机图像",
            "logical_type": "IMAGE",
            "compatibility_mode": "BACKWARD",
            "schema_definition": {
                "fields": [{"name": "image", "type": "bytes", "description": "camera payload"}]
            },
            "change_summary": "create real camera schema",
        },
    )
    assert create.status_code == 201
    assert create.headers["location"].endswith("/camera-schema/versions/1")
    created = create.json()["data"]
    assert created["status"] == "DRAFT"
    dataset_references = (
        f"/api/v1/organizations/{ORGANIZATION_ID}/projects/{PROJECT_ID}"
        f"/regions/{REGION_CODE}/stream-schemas/camera-schema/versions/1/dataset-references"
    )
    draft_reference = client.post(
        dataset_references,
        headers={
            "Authorization": "Bearer test",
            "If-Match": created["etag"],
            "Idempotency-Key": "schema-reference-draft",
        },
        json={
            "dataset_id": "dataset_p17reference",
            "dataset_version_id": "version_p17reference",
        },
    )
    assert draft_reference.status_code == 409
    assert (
        draft_reference.json()["code"] == "DATA_SCHEMA_DATASET_REFERENCE_REQUIRES_PUBLISHED_VERSION"
    )

    updated = client.patch(
        f"{root}/camera-schema/versions/1",
        headers={
            **headers,
            "If-Match": created["etag"],
            "Idempotency-Key": "schema-update-a",
        },
        json={
            "display_name": "相机图像 v1",
            "change_summary": "clarify display name",
        },
    )
    assert updated.status_code == 200
    updated_data = updated.json()["data"]
    assert updated_data["etag"] != created["etag"]

    validation = client.post(
        f"{root}/camera-schema/versions/1:validate",
        headers={
            **headers,
            "If-Match": updated_data["etag"],
            "Idempotency-Key": "schema-validate-a",
        },
    )
    assert validation.status_code == 200
    report = validation.json()["data"]
    assert report["status"] == "PASSED"

    preflight = client.post(
        f"{root}/camera-schema/versions/1:preflight-publish",
        headers={
            **headers,
            "If-Match": updated_data["etag"],
            "Idempotency-Key": "schema-preflight-a",
        },
        json={
            "expected_hash": updated_data["schema_hash"]["value"],
            "expected_etag": updated_data["etag"],
            "validation_report_id": report["id"],
            "compatibility_check_id": report["compatibility_check_id"],
            "change_summary": "publish validated schema",
            "acknowledge_warning_codes": [],
        },
    )
    assert preflight.status_code == 200
    token = preflight.json()["data"]["preflight_token"]
    assert token

    published = client.post(
        f"{root}/camera-schema/versions/1:publish",
        headers={
            **headers,
            "If-Match": updated_data["etag"],
            "Idempotency-Key": "schema-publish-a",
        },
        json={"preflight_token": token},
    )
    assert published.status_code == 200
    assert published.json()["data"]["status"] == "PUBLISHED"
    assert repository.audit_events[-1].action == "data_schema.version.published"

    reference = client.post(
        dataset_references,
        headers={
            "Authorization": "Bearer test",
            "If-Match": published.json()["data"]["etag"],
            "Idempotency-Key": "schema-reference-published",
        },
        json={
            "dataset_id": "dataset_p17reference",
            "dataset_version_id": "version_p17reference",
        },
    )
    assert reference.status_code == 201
    assert reference.headers["cache-control"] == "private, no-store"
    assert reference.json()["data"]["associated_by"] == "schema-reader"

    replay = client.post(
        dataset_references,
        headers={
            "Authorization": "Bearer test",
            "If-Match": published.json()["data"]["etag"],
            "Idempotency-Key": "schema-reference-published",
        },
        json={
            "dataset_id": "dataset_p17reference",
            "dataset_version_id": "version_p17reference",
        },
    )
    assert replay.status_code == 201
    assert replay.json()["data"] == reference.json()["data"]
    listed_references = client.get(
        dataset_references,
        headers={"Authorization": "Bearer test"},
    )
    assert listed_references.status_code == 200
    assert [item["dataset_id"] for item in listed_references.json()["items"]] == [
        "dataset_p17reference"
    ]

    first_published_page = client.get(
        root,
        params={"status": "PUBLISHED", "logical_type": "IMAGE", "limit": "1"},
        headers=headers,
    )
    assert first_published_page.status_code == 200
    first_page_payload = first_published_page.json()
    assert len(first_page_payload["items"]) == 1
    assert first_page_payload["page_info"]["has_next_page"] is True
    assert first_page_payload["page_info"]["end_cursor"]

    second_published_page = client.get(
        root,
        params={
            "status": "PUBLISHED",
            "logical_type": "IMAGE",
            "limit": "1",
            "after": first_page_payload["page_info"]["end_cursor"],
        },
        headers=headers,
    )
    assert second_published_page.status_code == 200
    assert len(second_published_page.json()["items"]) == 1
    assert second_published_page.json()["page_info"]["has_previous_page"] is True

    foreign = client.get("/api/v1/organizations/organization-b/stream-schemas", headers=headers)
    assert foreign.status_code == 403
    assert foreign.json()["code"] == "ORGANIZATION_SCOPE_DENIED"


def test_data_schema_router_rejects_missing_auth_and_capability() -> None:
    service, _repository = _service()
    configure_data_schemas(service)
    current: dict[str, AuthContext | None] = {"value": None}
    client = TestClient(_app(current))
    path = f"/api/v1/organizations/{ORGANIZATION_ID}/stream-schemas"
    assert client.get(path, headers={"X-Project-ID": PROJECT_ID}).status_code == 401

    current["value"] = _auth(capabilities=frozenset())
    denied = client.get(path, headers={"Authorization": "Bearer test", "X-Project-ID": PROJECT_ID})
    assert denied.status_code == 403
    assert denied.json()["code"] == "CAPABILITY_REQUIRED"


def test_data_schema_list_uses_bound_keyset_cursors_and_filters() -> None:
    def record(name: str, schema_id: str, schema_status: str) -> DataSchemaVersionRecord:
        return DataSchemaVersionRecord(
            schema_id=schema_id,
            family_id="camera-family",
            schema_version="1",
            display_name=name,
            logical_type="IMAGE",
            status=schema_status,
            compatibility_mode="BACKWARD",
            compatibility_result=None,
            schema_hash=SchemaHash(
                algorithm="SHA-256",
                canonicalization_version="schema-c14n-v1",
                value=(schema_id[0] * 64),
            ),
            schema_definition={"fields": [{"name": "image", "type": "bytes"}]},
            etag=f'"{schema_id}:1"',
        )

    history = tuple(
        (
            ORGANIZATION_ID,
            record(f"History {number}", "history", "DRAFT").model_copy(
                update={
                    "schema_version": str(number),
                    "logical_type": "HISTORY",
                    "etag": f'"history:{number}"',
                }
            ),
        )
        for number in range(1, 129)
    )
    repository = InMemoryDataSchemaRepository(
        organization_projects=((ORGANIZATION_ID, PROJECT_ID),),
        versions=(
            tuple(
                (ORGANIZATION_ID, item)
                for item in (
                    record("Alpha", "alpha", "PUBLISHED"),
                    record("Bravo", "bravo", "DRAFT"),
                    record("Charlie", "charlie", "PUBLISHED"),
                    record("Delta", "delta", "DRAFT"),
                )
            )
            + history
        ),
    )
    service = DataSchemaService(
        repository,
        clock=lambda: datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
        cursor_secret="p17-page-test-secret",
    )
    first = service.list_versions(
        auth=_auth(),
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        query=None,
        status=None,
        logical_type="IMAGE",
        after=None,
        before=None,
        limit=2,
        request_id="p17-page-first",
    )
    assert [item.schema_id for item in first.items] == ["alpha", "bravo"]
    assert first.page_info.has_next_page is True
    assert first.page_info.end_cursor

    second = service.list_versions(
        auth=_auth(),
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        query=None,
        status=None,
        logical_type="IMAGE",
        after=first.page_info.end_cursor,
        before=None,
        limit=2,
        request_id="p17-page-second",
    )
    assert [item.schema_id for item in second.items] == ["charlie", "delta"]
    assert second.page_info.has_previous_page is True
    assert second.page_info.start_cursor

    previous = service.list_versions(
        auth=_auth(),
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        query=None,
        status=None,
        logical_type="IMAGE",
        after=None,
        before=second.page_info.start_cursor,
        limit=2,
        request_id="p17-page-previous",
    )
    assert [item.schema_id for item in previous.items] == ["alpha", "bravo"]

    filtered = service.list_versions(
        auth=_auth(),
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        query=None,
        status="PUBLISHED",
        logical_type="IMAGE",
        after=None,
        before=None,
        limit=20,
        request_id="p17-page-filtered",
    )
    assert [item.schema_id for item in filtered.items] == ["alpha", "charlie"]

    # Compatibility lookup must remain bounded even when one schema has more
    # versions than a page can contain.
    prior = repository.get_previous_version(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        schema_id="history",
        schema_version="129",
    )
    assert prior is not None
    assert prior.schema_version == "128"

    with pytest.raises(ProblemException) as mismatch:
        service.list_versions(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            query=None,
            status="DRAFT",
            logical_type="IMAGE",
            after=first.page_info.end_cursor,
            before=None,
            limit=2,
            request_id="p17-page-mismatch",
        )
    assert mismatch.value.problem.code == "INVALID_CURSOR"
