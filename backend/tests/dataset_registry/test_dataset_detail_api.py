from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.dataset_registry.models import (
    DatasetPageActor,
    DatasetPageContentReference,
    DatasetPageContentSnapshot,
    DatasetPageDetailFacts,
    DatasetPageDetailSummary,
    DatasetPageEpisodeRecord,
    DatasetPageManifestSummary,
    DatasetPageMetadata,
    DatasetPageReadyVersion,
    DatasetPageRecord,
    DatasetPageReviewingVersion,
    DatasetPageRevisionSnapshotReference,
    DatasetPageScope,
    DatasetPageSourceProvenance,
    DatasetPageVersionCapacityFacts,
    DatasetPageVersionSchemaSummary,
)
from hc_data_platform.dataset_registry.repository import InMemoryDatasetPageRepository
from hc_data_platform.dataset_registry.router import configure_dataset_page, router
from hc_data_platform.dataset_registry.service import DatasetPageService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.versioning import ResourceVersion

PROJECT_ID = "p06-project"
REGION_CODE = "p06-region"
ORGANIZATION_ID = "p06-organization"
DATASET_ID = "dataset_p06fixture"
REVIEWING_VERSION_ID = "version_p06review"
READY_VERSION_ID = "version_p06ready"
NOW = datetime(2026, 8, 19, 16, tzinfo=timezone.utc)
SHA = "a" * 64


def _scope() -> DatasetPageScope:
    return DatasetPageScope(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
    )


def _auth(*, all_capabilities: bool = True) -> AuthContext:
    names = {"dataset.read", "dataset_version.read", "data_schema.read", "episode.read"}
    if all_capabilities:
        names.add("storage.overview.read")
    return AuthContext(
        subject_id="p06-reader",
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        roles=frozenset(),
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset((PROJECT_ID, name) for name in names),
    )


def _record() -> DatasetPageRecord:
    return DatasetPageRecord(
        scope=_scope(),
        dataset_id=DATASET_ID,
        name="P06 Durable Dataset",
        description="P06 detail projection fixture",
        labels=("p06",),
        availability="ACTIVE",
        owner=DatasetPageActor(id="p06-owner", display_name="P06 Owner"),
        created_at=NOW - timedelta(days=2),
        updated_at=NOW,
        activity_at=NOW,
        etag=ResourceVersion(1).etag,
        metadata=DatasetPageMetadata(asset_state="READY", storage_class="STANDARD"),
        episode_count="1",
        pending_review_version_count="1",
        returned_version_count="0",
        actionable_draft_count="1",
    )


def _schema_reference() -> DatasetPageContentReference:
    return DatasetPageContentReference(
        reference_type="DATASET_SCHEMA",
        reference_id="schema_p06snapshot",
        reference_version="schema-v1",
        sha256="b" * 64,
    )


def _reviewing_version() -> DatasetPageReviewingVersion:
    return DatasetPageReviewingVersion(
        scope=_scope(),
        dataset_id=DATASET_ID,
        version_id=REVIEWING_VERSION_ID,
        display_version="v2",
        kind="CLEANED",
        created_at=NOW,
        etag='"p06-review-v1"',
        version_token="p06-review-version-token-0001",
        source_draft_id="draft_p06source",
        delivery_status="NOT_STARTED",
        allowed_actions=(),
    )


def _ready_version() -> DatasetPageReadyVersion:
    revision = DatasetPageRevisionSnapshotReference(
        episode_id="episode_p06fixture",
        revision_id="revision_p06fixture",
        ordinal=0,
        content_sha256=SHA,
    )
    return DatasetPageReadyVersion(
        scope=_scope(),
        dataset_id=DATASET_ID,
        version_id=READY_VERSION_ID,
        display_version="v1",
        kind="RAW",
        created_at=NOW - timedelta(days=1),
        etag='"p06-ready-v1"',
        version_token="p06-ready-version-token-00001",
        published_at=NOW - timedelta(hours=1),
        content_snapshot=DatasetPageContentSnapshot(
            content_snapshot_id="content_p06snapshot",
            content_snapshot_hash="c" * 64,
            revision_refs=(revision,),
            schema_ref=_schema_reference(),
        ),
        manifest=DatasetPageManifestSummary(
            manifest_id="manifest_p06fixture",
            format_version="v1",
            canonicalization="rfc8785",
            sha256="d" * 64,
            entry_count="1",
        ),
        allowed_actions=(),
    )


def _service() -> DatasetPageService:
    revision = DatasetPageRevisionSnapshotReference(
        episode_id="episode_p06fixture",
        revision_id="revision_p06fixture",
        ordinal=0,
        content_sha256=SHA,
    )
    repository = InMemoryDatasetPageRepository(
        organization_projects=((ORGANIZATION_ID, PROJECT_ID),),
        records=(_record(),),
        versions=(_reviewing_version(), _ready_version()),
        detail_facts=(
            DatasetPageDetailFacts(
                scope=_scope(),
                dataset_id=DATASET_ID,
                summary=DatasetPageDetailSummary(
                    episode_count="1",
                    effective_duration_ns="1000000000",
                    source_bytes="1024",
                    required_physical_bytes="2048",
                    actual_oss_bytes="2048",
                    pending_review_version_count="1",
                    returned_version_count="0",
                    actionable_draft_count="1",
                    calculated_at=NOW,
                    calculation_state="SETTLED",
                ),
            ),
        ),
        schema_summaries=(
            DatasetPageVersionSchemaSummary(
                scope=_scope(),
                dataset_id=DATASET_ID,
                version_id=REVIEWING_VERSION_ID,
                schema_snapshot=_schema_reference(),
                channel_count="4",
            ),
        ),
        source_provenance=(
            DatasetPageSourceProvenance(
                scope=_scope(),
                dataset_id=DATASET_ID,
                version_id=REVIEWING_VERSION_ID,
                provenance_id="provenance_p06fixture",
                upload_id="upload_p06fixture",
                source_id="source_p06fixture",
                source_display_name="P06 source",
                source_manifest_id="manifest_p06source",
                source_manifest_sha256="e" * 64,
                verified_object_set_hash="f" * 64,
                registered_at=NOW - timedelta(hours=2),
            ),
        ),
        capacity_facts=(
            DatasetPageVersionCapacityFacts(
                scope=_scope(),
                dataset_id=DATASET_ID,
                version_id=REVIEWING_VERSION_ID,
                state="SETTLED",
                source_bytes="1024",
                required_physical_bytes="2048",
                actual_oss_bytes="2048",
                calculated_at=NOW,
                basis_revision="capacity-p06-v1",
            ),
        ),
        episodes=(
            DatasetPageEpisodeRecord(
                scope=_scope(),
                dataset_id=DATASET_ID,
                version_id=REVIEWING_VERSION_ID,
                episode_id="episode_p06fixture",
                selected_revision=revision,
                included=True,
                success_state="SUCCEEDED",
                task="pick",
                robot_id="robot_p06fixture",
                review_status="HAS_FINDING",
                review_finding_count="1",
                started_at=NOW - timedelta(hours=3),
                started_at_ns="100",
                has_finding=True,
                change_type="CLEANED",
            ),
        ),
    )
    return DatasetPageService(
        repository,
        cursor_secret="p06-router-cursor-secret",
        clock=lambda: NOW,
    )


def _app(current: dict[str, AuthContext | None]) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(ProblemException)
    async def handle_problem(_request: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(exc.problem.model_dump(mode="json"), status_code=exc.problem.status)

    @app.middleware("http")
    async def install_auth(request: Request, call_next: Any) -> Any:
        request.state.auth_context = current["value"]
        request.state.request_id = "p06-router-test"
        return await call_next(request)

    app.include_router(router)
    return app


def _headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer test",
        "X-Organization-Id": ORGANIZATION_ID,
        "X-Project-Id": PROJECT_ID,
        "X-Region-Code": REGION_CODE,
    }


def test_p06_detail_reads_are_real_scoped_and_strict() -> None:
    configure_dataset_page(_service())
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/datasets/{DATASET_ID}"

    bootstrap = client.get(f"{root}/bootstrap", headers=_headers())
    assert bootstrap.status_code == 200
    assert bootstrap.headers["cache-control"] == "no-store"
    assert bootstrap.json()["data"]["current_ready_version"]["version_id"] == READY_VERSION_ID
    assert bootstrap.json()["data"]["suggested_version_id"] == REVIEWING_VERSION_ID
    assert bootstrap.json()["data"]["summary"]["actual_oss_bytes"] == "2048"

    versions = client.get(
        f"{root}/versions",
        headers=_headers(),
        params={"version_kind": "CLEANED", "limit": 10},
    )
    assert versions.status_code == 200
    assert [item["version_id"] for item in versions.json()["items"]] == [REVIEWING_VERSION_ID]
    assert versions.json()["items"][0]["status"] == "REVIEWING"

    schema = client.get(
        f"{root}/versions/{REVIEWING_VERSION_ID}/schema-summary", headers=_headers()
    )
    assert schema.status_code == 200
    assert schema.json()["data"]["channel_count"] == "4"

    sources = client.get(
        f"{root}/versions/{REVIEWING_VERSION_ID}/source-provenance",
        headers=_headers(),
        params={"q": "source"},
    )
    assert sources.status_code == 200
    assert sources.json()["items"][0]["source_id"] == "source_p06fixture"

    capacity = client.get(
        f"{root}/versions/{REVIEWING_VERSION_ID}/capacity-facts", headers=_headers()
    )
    assert capacity.status_code == 200
    assert capacity.json()["data"]["state"] == "SETTLED"

    episodes = client.get(
        f"{root}/versions/{REVIEWING_VERSION_ID}/episodes",
        headers=_headers(),
        params={"task": "pick", "has_finding": "true", "limit": 10},
    )
    assert episodes.status_code == 200
    item = episodes.json()["items"][0]
    assert item["episode_id"] == "episode_p06fixture"
    assert "started_at" not in item
    assert "change_type" not in item


def test_p06_detail_fails_closed_for_capability_unknown_version_and_cursor() -> None:
    configure_dataset_page(_service())
    current: dict[str, AuthContext | None] = {"value": _auth(all_capabilities=False)}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/datasets/{DATASET_ID}"

    denied_capacity = client.get(
        f"{root}/versions/{REVIEWING_VERSION_ID}/capacity-facts", headers=_headers()
    )
    assert denied_capacity.status_code == 403
    assert denied_capacity.json()["code"] == "CAPABILITY_REQUIRED"

    missing = client.get(f"{root}/versions/version_p06missing/schema-summary", headers=_headers())
    assert missing.status_code == 404
    assert missing.json()["code"] == "DATASET_VERSION_NOT_FOUND"

    invalid_cursor = client.get(
        f"{root}/versions", headers=_headers(), params={"after": "not-a-cursor"}
    )
    assert invalid_cursor.status_code == 400
    assert invalid_cursor.json()["code"] == "INVALID_CURSOR"
