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
    DatasetPageEpisodeDataBinding,
    DatasetPageEpisodePreviewBinding,
    DatasetPageEpisodeRecord,
    DatasetPageEpisodeRevision,
    DatasetPageEpisodeStream,
    DatasetPageManifestEntry,
    DatasetPageManifestSummary,
    DatasetPageMetadata,
    DatasetPageOperationalInventoryItem,
    DatasetPageReadyVersion,
    DatasetPageRecord,
    DatasetPageRequiredStorageItem,
    DatasetPageReviewingVersion,
    DatasetPageRevisionSnapshotReference,
    DatasetPageScope,
    DatasetPageVersionCapacityFacts,
    DatasetPageVersionContentProjection,
    DatasetPageVersionManifestEntryRecord,
    DatasetPageVersionSchemaChannel,
    DatasetPageVersionSchemaDetail,
)
from hc_data_platform.dataset_registry.repository import InMemoryDatasetPageRepository
from hc_data_platform.dataset_registry.router import configure_dataset_page, router
from hc_data_platform.dataset_registry.service import DatasetPageService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.versioning import ResourceVersion
from hc_data_platform.workflow.router import router as workflow_router

PROJECT_ID = "p07-project"
REGION_CODE = "p07-region"
ORGANIZATION_ID = "p07-organization"
DATASET_ID = "dataset_p07fixture"
REVIEWING_VERSION_ID = "version_p07review"
COMPARE_VERSION_ID = "version_p07compare"
EPISODE_ID = "episode_p07fixture"
REVISION_ID = "revision_p07fixture"
COMPARE_REVISION_ID = "revision_p07compare"
NOW = datetime(2026, 8, 19, 18, tzinfo=timezone.utc)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64


def _scope() -> DatasetPageScope:
    return DatasetPageScope(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
    )


def _auth(*, delete: bool = True) -> AuthContext:
    capabilities = {
        "dataset.read",
        "dataset_version.read",
        "data_schema.read",
        "storage.overview.read",
        "episode.read",
        "dataset_version.review",
        "datasets.publish",
    }
    if delete:
        capabilities.add("dataset.delete")
    return AuthContext(
        subject_id="p07-reviewer",
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        roles=frozenset(),
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset((PROJECT_ID, value) for value in capabilities),
    )


def _schema_reference() -> DatasetPageContentReference:
    return DatasetPageContentReference(
        reference_type="DATASET_SCHEMA",
        reference_id="schema_p07fixture",
        reference_version="schema-v1",
        sha256=SHA_B,
    )


def _revision_reference() -> DatasetPageRevisionSnapshotReference:
    return DatasetPageRevisionSnapshotReference(
        episode_id=EPISODE_ID,
        revision_id=REVISION_ID,
        ordinal=0,
        content_sha256=SHA_C,
    )


def _compare_revision_reference() -> DatasetPageRevisionSnapshotReference:
    return DatasetPageRevisionSnapshotReference(
        episode_id=EPISODE_ID,
        revision_id=COMPARE_REVISION_ID,
        ordinal=0,
        content_sha256=SHA_D,
    )


def _reviewing_version() -> DatasetPageReviewingVersion:
    return DatasetPageReviewingVersion(
        scope=_scope(),
        dataset_id=DATASET_ID,
        version_id=REVIEWING_VERSION_ID,
        display_version="v2",
        kind="CLEANED",
        created_at=NOW,
        etag='"p07-review-v1"',
        version_token="p07-review-version-token-00000001",
        source_draft_id="draft_p07source",
        delivery_status="NOT_STARTED",
        allowed_actions=(),
    )


def _compare_version() -> DatasetPageReadyVersion:
    return DatasetPageReadyVersion(
        scope=_scope(),
        dataset_id=DATASET_ID,
        version_id=COMPARE_VERSION_ID,
        display_version="v1",
        kind="RAW",
        created_at=NOW - timedelta(days=1),
        etag='"p07-compare-v1"',
        version_token="p07-compare-version-token-0000001",
        published_at=NOW - timedelta(hours=1),
        content_snapshot=DatasetPageContentSnapshot(
            content_snapshot_id="content_p07compare",
            content_snapshot_hash=SHA_D,
            revision_refs=(_revision_reference(),),
            schema_ref=_schema_reference(),
        ),
        manifest=DatasetPageManifestSummary(
            manifest_id="manifest_p07compare",
            format_version="v1",
            canonicalization="rfc8785",
            sha256=SHA_E,
            entry_count="1",
        ),
        allowed_actions=(),
    )


def _record() -> DatasetPageRecord:
    return DatasetPageRecord(
        scope=_scope(),
        dataset_id=DATASET_ID,
        name="P07 Fixed Version Dataset",
        description="P07 durable delivery and review fixture",
        labels=("p07",),
        availability="ACTIVE",
        owner=DatasetPageActor(id="p07-owner", display_name="P07 Owner"),
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


def _content_projection(
    *,
    version_id: str,
    snapshot_id: str,
    snapshot_hash: str,
    manifest_id: str,
    manifest_hash: str,
    entry_count: str,
    operational_revision: str,
    revision: DatasetPageRevisionSnapshotReference,
) -> DatasetPageVersionContentProjection:
    return DatasetPageVersionContentProjection(
        scope=_scope(),
        dataset_id=DATASET_ID,
        version_id=version_id,
        content_snapshot=DatasetPageContentSnapshot(
            content_snapshot_id=snapshot_id,
            content_snapshot_hash=snapshot_hash,
            revision_refs=(revision,),
            schema_ref=_schema_reference(),
        ),
        manifest=DatasetPageManifestSummary(
            manifest_id=manifest_id,
            format_version="v1",
            canonicalization="rfc8785",
            sha256=manifest_hash,
            entry_count=entry_count,
        ),
        operational_revision=operational_revision,
    )


def _service() -> DatasetPageService:
    revision = _revision_reference()
    compare_revision = _compare_revision_reference()
    review_projection = _content_projection(
        version_id=REVIEWING_VERSION_ID,
        snapshot_id="content_p07review",
        snapshot_hash=SHA_A,
        manifest_id="manifest_p07review",
        manifest_hash=SHA_D,
        entry_count="2",
        operational_revision="operational-p07-review-v1",
        revision=revision,
    )
    compare_projection = _content_projection(
        version_id=COMPARE_VERSION_ID,
        snapshot_id="content_p07compare",
        snapshot_hash=SHA_D,
        manifest_id="manifest_p07compare",
        manifest_hash=SHA_E,
        entry_count="1",
        operational_revision="operational-p07-compare-v1",
        revision=compare_revision,
    )
    repository = InMemoryDatasetPageRepository(
        organization_projects=((ORGANIZATION_ID, PROJECT_ID),),
        records=(_record(),),
        versions=(_reviewing_version(), _compare_version()),
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
                basis_revision="capacity-p07-v1",
            ),
        ),
        episodes=(
            DatasetPageEpisodeRecord(
                scope=_scope(),
                dataset_id=DATASET_ID,
                version_id=REVIEWING_VERSION_ID,
                episode_id=EPISODE_ID,
                selected_revision=revision,
                included=True,
                success_state="SUCCEEDED",
                task="pick",
                robot_id="robot_p07fixture",
                review_status="PENDING",
                review_finding_count="0",
                started_at=NOW - timedelta(hours=1),
                started_at_ns="100",
                has_finding=False,
                change_type="CLEANED",
            ),
            DatasetPageEpisodeRecord(
                scope=_scope(),
                dataset_id=DATASET_ID,
                version_id=COMPARE_VERSION_ID,
                episode_id=EPISODE_ID,
                selected_revision=compare_revision,
                included=True,
                success_state="SUCCEEDED",
                task="pick",
                robot_id="robot_p07fixture",
                review_status="ACCEPTED",
                review_finding_count="0",
                started_at=NOW - timedelta(days=1, hours=1),
                started_at_ns="100",
                has_finding=False,
                change_type="INGESTED",
            ),
        ),
        content_projections=(review_projection, compare_projection),
        episode_revisions=(
            DatasetPageEpisodeRevision(
                scope=_scope(),
                dataset_id=DATASET_ID,
                version_id=REVIEWING_VERSION_ID,
                episode_id=EPISODE_ID,
                revision_id=REVISION_ID,
                ordinal=0,
                content_sha256=SHA_C,
                started_at_ns="100",
                duration_ns="1000",
                streams=(
                    DatasetPageEpisodeStream(
                        episode_stream_id="stream_p07fixture",
                        channel_path="/camera/front/image_raw",
                        kind="RGB_VIDEO",
                        t_start_ns="100",
                        t_end_ns="1100",
                        preview_binding=DatasetPageEpisodePreviewBinding(
                            rollout_id="rollout_p07fixture",
                            lance_version=7,
                            annotation_revision=2,
                            camera_id="front-rgb",
                            frequency_hz=30,
                            start_step=0,
                            end_step=30,
                        ),
                    ),
                    DatasetPageEpisodeStream(
                        episode_stream_id="stream_p07joint",
                        channel_path="/joint_states/position",
                        kind="JOINT_STATE",
                        t_start_ns="100",
                        t_end_ns="1100",
                        data_binding=DatasetPageEpisodeDataBinding(
                            rollout_id="rollout_p07fixture",
                            lance_version=7,
                            modality_key="joint.position",
                            value_kind="VECTOR",
                            start_step=0,
                            end_step=30,
                        ),
                    ),
                ),
            ),
            DatasetPageEpisodeRevision(
                scope=_scope(),
                dataset_id=DATASET_ID,
                version_id=COMPARE_VERSION_ID,
                episode_id=EPISODE_ID,
                revision_id=COMPARE_REVISION_ID,
                ordinal=0,
                content_sha256=SHA_D,
                started_at_ns="100",
                duration_ns="1000",
                streams=(
                    DatasetPageEpisodeStream(
                        episode_stream_id="stream_p07fixture",
                        channel_path="/camera/front/image_raw",
                        kind="RGB_VIDEO",
                        t_start_ns="100",
                        t_end_ns="1100",
                        preview_binding=DatasetPageEpisodePreviewBinding(
                            rollout_id="rollout_p07fixture",
                            lance_version=6,
                            annotation_revision=1,
                            camera_id="front-rgb",
                            frequency_hz=30,
                            start_step=0,
                            end_step=30,
                        ),
                    ),
                ),
            ),
        ),
        schema_details=(
            DatasetPageVersionSchemaDetail(
                scope=_scope(),
                dataset_id=DATASET_ID,
                version_id=REVIEWING_VERSION_ID,
                schema_snapshot=_schema_reference(),
                channel_count="1",
                channels=(
                    DatasetPageVersionSchemaChannel(
                        channel_id="channel_p07joint",
                        name="joint.position",
                        data_type="float64",
                        unit="rad",
                    ),
                ),
            ),
        ),
        manifest_entries=(
            DatasetPageVersionManifestEntryRecord(
                scope=_scope(),
                dataset_id=DATASET_ID,
                version_id=REVIEWING_VERSION_ID,
                entry=DatasetPageManifestEntry(
                    entry_id="entry_p07metadata",
                    episode_id=EPISODE_ID,
                    revision_id=REVISION_ID,
                    role="METADATA",
                    size_bytes="24",
                    sha256=SHA_A,
                    safe_locator="manifest-metadata",
                ),
            ),
            DatasetPageVersionManifestEntryRecord(
                scope=_scope(),
                dataset_id=DATASET_ID,
                version_id=REVIEWING_VERSION_ID,
                entry=DatasetPageManifestEntry(
                    entry_id="entry_p07revision",
                    episode_id=EPISODE_ID,
                    revision_id=REVISION_ID,
                    role="REVISION",
                    size_bytes="1024",
                    sha256=SHA_B,
                    safe_locator="revision-summary",
                ),
            ),
            DatasetPageVersionManifestEntryRecord(
                scope=_scope(),
                dataset_id=DATASET_ID,
                version_id=COMPARE_VERSION_ID,
                entry=DatasetPageManifestEntry(
                    entry_id="entry_p07revision",
                    episode_id=EPISODE_ID,
                    revision_id=REVISION_ID,
                    role="REVISION",
                    size_bytes="1000",
                    sha256=SHA_C,
                    safe_locator="compare-revision-summary",
                ),
            ),
        ),
        required_storage=(
            DatasetPageRequiredStorageItem(
                scope=_scope(),
                dataset_id=DATASET_ID,
                version_id=REVIEWING_VERSION_ID,
                object_id="object_p07metadata",
                role="METADATA",
                size_bytes="24",
                reuse="REUSED",
                protection="RETENTION",
                safe_locator="metadata-summary",
            ),
            DatasetPageRequiredStorageItem(
                scope=_scope(),
                dataset_id=DATASET_ID,
                version_id=REVIEWING_VERSION_ID,
                object_id="object_p07revision",
                role="REVISION",
                size_bytes="1024",
                reuse="NEW",
                protection="NONE",
                safe_locator="revision-summary",
            ),
        ),
        operational_inventory=(
            DatasetPageOperationalInventoryItem(
                scope=_scope(),
                dataset_id=DATASET_ID,
                version_id=REVIEWING_VERSION_ID,
                inventory_id="inventory_p07older",
                kind="PREVIEW",
                operational_revision="operational-p07-review-v1",
                status="SUCCEEDED",
                size_bytes="64",
                job_id=None,
                created_at=NOW - timedelta(minutes=2),
                completed_at=NOW - timedelta(minutes=1),
            ),
            DatasetPageOperationalInventoryItem(
                scope=_scope(),
                dataset_id=DATASET_ID,
                version_id=REVIEWING_VERSION_ID,
                inventory_id="inventory_p07newer",
                kind="EXPORT",
                operational_revision="operational-p07-review-v1",
                status="RUNNING",
                size_bytes="128",
                job_id="job_p07export",
                created_at=NOW - timedelta(minutes=1),
                completed_at=None,
            ),
        ),
    )
    return DatasetPageService(
        repository,
        cursor_secret="p07-router-cursor-secret",
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
        request.state.request_id = "p07-router-test"
        return await call_next(request)

    app.include_router(router)
    app.include_router(workflow_router)
    return app


def _headers(**extra: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer test",
        "X-Organization-Id": ORGANIZATION_ID,
        "X-Project-Id": PROJECT_ID,
        "X-Region-Code": REGION_CODE,
        **extra,
    }


def _root() -> str:
    return f"/api/v1/projects/{PROJECT_ID}/datasets/{DATASET_ID}/versions/{REVIEWING_VERSION_ID}"


def _review_checks(client: TestClient) -> dict[str, Any]:
    response = client.post(
        f"{_root()}/review-checks",
        headers=_headers(**{"If-Match": _reviewing_version().etag}),
        json={"expected_status": "REVIEWING"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()["data"]
    assert payload["blockers"] == []
    assert payload["eligible_targets"][0]["output_revision_id"] == REVISION_ID
    return payload


def test_p07_fixed_version_reads_are_scoped_snapshot_bound_and_safe() -> None:
    configure_dataset_page(_service())
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))

    bootstrap = client.get(f"{_root()}/bootstrap", headers=_headers())
    assert bootstrap.status_code == 200
    assert bootstrap.headers["cache-control"] == "no-store"
    payload = bootstrap.json()["data"]
    snapshot_token = payload["snapshot_token"]
    assert payload["version"]["status"] == "REVIEWING"
    assert payload["operational_revision"] == "operational-p07-review-v1"

    episodes = client.get(
        f"{_root()}/episodes",
        headers=_headers(),
        params={"snapshot_token": snapshot_token, "limit": 20},
    )
    assert episodes.status_code == 200
    assert episodes.json()["items"][0]["selected_revision"]["revision_id"] == REVISION_ID

    revision = client.get(
        f"{_root()}/episode-revisions/{REVISION_ID}",
        headers=_headers(),
        params={"snapshot_token": snapshot_token},
    )
    assert revision.status_code == 200
    assert revision.json()["data"]["streams"][0]["episode_stream_id"] == "stream_p07fixture"
    assert revision.json()["data"]["streams"][0]["preview_binding"] == {
        "rollout_id": "rollout_p07fixture",
        "lance_version": 7,
        "annotation_revision": 2,
        "camera_id": "front-rgb",
        "frequency_hz": 30.0,
        "start_step": 0,
        "end_step": 30,
    }
    assert revision.json()["data"]["streams"][1]["data_binding"] == {
        "rollout_id": "rollout_p07fixture",
        "lance_version": 7,
        "modality_key": "joint.position",
        "value_kind": "VECTOR",
        "start_step": 0,
        "end_step": 30,
    }

    manifest = client.get(f"{_root()}/manifest", headers=_headers(), params={"limit": 20})
    assert manifest.status_code == 200
    assert [item["entry_id"] for item in manifest.json()["items"]] == [
        "entry_p07metadata",
        "entry_p07revision",
    ]
    assert "scope" not in manifest.json()["items"][0]

    schema = client.get(
        f"{_root()}/schema",
        headers=_headers(),
        params={"snapshot_token": snapshot_token},
    )
    assert schema.status_code == 200
    assert schema.json()["data"]["snapshot_token"] == snapshot_token
    assert schema.json()["data"]["channels"][0]["channel_id"] == "channel_p07joint"

    storage = client.get(
        f"{_root()}/required-storage",
        headers=_headers(),
        params={
            "snapshot_token": snapshot_token,
            "sort": "role:asc,object_id:asc",
            "limit": 20,
        },
    )
    assert storage.status_code == 200
    assert [item["object_id"] for item in storage.json()["items"]] == [
        "object_p07metadata",
        "object_p07revision",
    ]

    inventory = client.get(
        f"{_root()}/operational-inventory",
        headers=_headers(),
        params={
            "operational_revision": payload["operational_revision"],
            "sort": "created_at:desc,inventory_id:desc",
            "limit": 20,
        },
    )
    assert inventory.status_code == 200
    assert [item["inventory_id"] for item in inventory.json()["items"]] == [
        "inventory_p07newer",
        "inventory_p07older",
    ]

    compare_bootstrap = client.get(
        (
            f"/api/v1/projects/{PROJECT_ID}/datasets/{DATASET_ID}/versions/"
            f"{COMPARE_VERSION_ID}/bootstrap"
        ),
        headers=_headers(),
    )
    assert compare_bootstrap.status_code == 200
    stale = client.get(
        f"{_root()}/schema",
        headers=_headers(),
        params={"snapshot_token": compare_bootstrap.json()["data"]["snapshot_token"]},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "VERSION_SNAPSHOT_EXPIRED"


def test_p07_episode_revision_history_is_scoped_cursor_bound_and_immutable() -> None:
    configure_dataset_page(_service())
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    endpoint = (
        f"/api/v1/projects/{PROJECT_ID}/datasets/{DATASET_ID}/episodes/"
        f"{EPISODE_ID}/revision-history"
    )

    first = client.get(endpoint, headers=_headers(), params={"limit": 10})
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    payload = first.json()
    assert payload["scope"] == _scope().model_dump(mode="json")
    assert [item["version_id"] for item in payload["items"]] == [
        REVIEWING_VERSION_ID,
        COMPARE_VERSION_ID,
    ]
    assert [item["selected_revision"]["revision_id"] for item in payload["items"]] == [
        REVISION_ID,
        COMPARE_REVISION_ID,
    ]
    assert payload["page_info"]["has_next"] is False

    second = client.get(
        endpoint,
        headers=_headers(),
        params={"limit": 10, "after": payload["page_info"]["after"]},
    )
    assert second.status_code == 200
    assert second.json()["items"] == []
    assert second.json()["page_info"]["has_previous"] is True

    tampered = client.get(
        endpoint,
        headers=_headers(),
        params={"limit": 10, "after": payload["page_info"]["after"], "before": "bad"},
    )
    assert tampered.status_code == 422
    assert tampered.json()["code"] == "CURSOR_DIRECTION_CONFLICT"


def test_p07_approval_is_durable_idempotent_and_does_not_fake_ready() -> None:
    configure_dataset_page(_service())
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    initial_bootstrap = client.get(f"{_root()}/bootstrap", headers=_headers())
    assert initial_bootstrap.status_code == 200
    initial_snapshot_token = initial_bootstrap.json()["data"]["snapshot_token"]
    checks = _review_checks(client)
    request_headers = _headers(
        **{
            "If-Match": _reviewing_version().etag,
            "Idempotency-Key": "p07-approval-idempotency-key",
        }
    )
    command = {"expected_status": "REVIEWING", "review_token": checks["review_token"]}

    approved = client.post(f"{_root()}:approve", headers=request_headers, json=command)
    assert approved.status_code == 202
    assert approved.headers["idempotency-replayed"] == "false"
    result = approved.json()
    assert result["output_version"]["status"] == "REVIEWING"
    assert result["job"]["status"] == "SUCCEEDED"
    assert result["job"]["result_ref"]["state"] == "CANDIDATE_READY"

    replay = client.post(f"{_root()}:approve", headers=request_headers, json=command)
    assert replay.status_code == 202
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.json() == result

    job = client.get(f"/api/v1/jobs/{result['job']['job_id']}", headers=_headers())
    assert job.status_code == 200
    assert job.json()["job_type"] == "MANIFEST_MATERIALIZATION"
    assert job.json()["status"] == "SUCCEEDED"

    refreshed = client.get(f"{_root()}/bootstrap", headers=_headers())
    assert refreshed.status_code == 200
    version = refreshed.json()["data"]["version"]
    assert version["status"] == "REVIEWING"
    assert version["delivery_status"] == "CANDIDATE_READY"

    stale = client.get(
        f"{_root()}/schema",
        headers=_headers(),
        params={"snapshot_token": initial_snapshot_token},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "VERSION_SNAPSHOT_EXPIRED"


def test_p07_return_writes_immutable_findings_lineage_and_replays() -> None:
    configure_dataset_page(_service())
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    checks = _review_checks(client)
    request_headers = _headers(
        **{
            "If-Match": _reviewing_version().etag,
            "Idempotency-Key": "p07-return-idempotency-key",
        }
    )
    command = {
        "expected_status": "REVIEWING",
        "review_token": checks["review_token"],
        "finding_catalog_version": checks["finding_catalog"]["version"],
        "findings": [
            {
                "output_revision_id": REVISION_ID,
                "episode_stream_id": "stream_p07fixture",
                "start_ns": "100",
                "end_ns": "200",
                "finding_type": "RANGE_QUALITY",
                "severity": "HIGH",
                "note": "The fixed stream has a durable review finding.",
            }
        ],
    }

    returned = client.post(f"{_root()}:return", headers=request_headers, json=command)
    assert returned.status_code == 200
    assert returned.headers["idempotency-replayed"] == "false"
    data = returned.json()["data"]
    assert data["review_decision"]["decision"] == "RETURNED"
    assert data["findings"][0]["immutable"] is True
    assert data["output_version"]["status"] == "RETURNED"
    assert data["successor_draft_id"] != data["supersedes_draft_id"]

    replay = client.post(f"{_root()}:return", headers=request_headers, json=command)
    assert replay.status_code == 200
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.json() == returned.json()

    refreshed = client.get(f"{_root()}/bootstrap", headers=_headers())
    assert refreshed.status_code == 200
    token = refreshed.json()["data"]["snapshot_token"]
    assert refreshed.json()["data"]["version"]["status"] == "RETURNED"

    episodes = client.get(
        f"{_root()}/episodes",
        headers=_headers(),
        params={"snapshot_token": token, "limit": 20},
    )
    assert episodes.status_code == 200
    assert episodes.json()["items"][0]["review_finding_count"] == "1"

    dataset = client.get(
        f"/api/v1/projects/{PROJECT_ID}/datasets/{DATASET_ID}/bootstrap",
        headers=_headers(),
    )
    assert dataset.status_code == 200
    assert dataset.json()["data"]["summary"]["returned_version_count"] == "1"


def test_p07_diff_and_deletion_preflight_are_durable_and_non_executable() -> None:
    configure_dataset_page(_service())
    current: dict[str, AuthContext | None] = {"value": _auth(delete=False)}
    client = TestClient(_app(current))
    bootstrap = client.get(f"{_root()}/bootstrap", headers=_headers())
    assert bootstrap.status_code == 200
    token = bootstrap.json()["data"]["snapshot_token"]
    etag = _reviewing_version().etag
    diff_headers = _headers(**{"If-Match": etag, "Idempotency-Key": "p07-diff-idempotency-key"})
    diff_command = {"compare_to": COMPARE_VERSION_ID, "snapshot_token": token}

    accepted = client.post(f"{_root()}/diff-jobs", headers=diff_headers, json=diff_command)
    assert accepted.status_code == 202
    assert accepted.headers["idempotency-replayed"] == "false"
    job_id = accepted.json()["job"]["job_id"]

    replay = client.post(f"{_root()}/diff-jobs", headers=diff_headers, json=diff_command)
    assert replay.status_code == 202
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.json()["job"]["job_id"] == job_id

    job = client.get(f"/api/v1/jobs/{job_id}", headers=_headers())
    assert job.status_code == 200
    assert job.json()["result_ref"]["changed_entry_count"] == "1"
    assert job.json()["result_ref"]["added_entry_count"] == "1"

    preflight_headers = _headers(
        **{"If-Match": etag, "Idempotency-Key": "p07-delete-preflight-key"}
    )
    preflight = client.post(
        f"{_root()}/deletion-checks",
        headers=preflight_headers,
        json={"intent": "DELETE", "reason": "Acceptance preflight", "expected_etag": etag},
    )
    assert preflight.status_code == 200
    payload = preflight.json()
    assert payload["capability_status"] == "RESERVED_CONDITIONAL"
    assert payload["executable"] is False
    assert payload["domain_clear"] is False
    assert len(payload["checks"]) == 7
    assert any(
        reason["code"] == "DATASET_DELETE_CAPABILITY_REQUIRED"
        for reason in payload["blocked_reasons"]
    )
