from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.dataset_registry.models import (
    DatasetPageActor,
    DatasetPageEpisodeRecord,
    DatasetPageMetadata,
    DatasetPageRecord,
    DatasetPageRevisionSnapshotReference,
    DatasetPageScope,
)
from hc_data_platform.dataset_registry.repository import (
    DatasetPageFilters,
    InMemoryDatasetPageRepository,
)
from hc_data_platform.dataset_registry.router import configure_dataset_page, router
from hc_data_platform.dataset_registry.service import DatasetPageService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.versioning import ResourceVersion

PROJECT_ID = "p05-project"
REGION_CODE = "p05-region"
ORGANIZATION_ID = "p05-organization"
NOW = datetime(2026, 8, 19, 14, tzinfo=timezone.utc)


def _auth(*, read: bool = True, create: bool = True) -> AuthContext:
    capabilities: set[tuple[str, str]] = set()
    if read:
        capabilities.add((PROJECT_ID, "dataset.read"))
    if create:
        capabilities.add((PROJECT_ID, "dataset.create"))
    return AuthContext(
        subject_id="p05-operator",
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset(capabilities),
        capability_revision=7,
    )


def _record(
    *,
    dataset_id: str,
    name: str,
    created_at: datetime,
    robot_id: str | None = None,
    collection_task_id: str | None = None,
    task: str | None = "pick",
    labels: tuple[str, ...] = ("fixture",),
    asset_state: str = "READY",
    channels: tuple[str, ...] = (),
    pending_review_version_count: str = "1",
    returned_version_count: str = "0",
    actionable_draft_count: str = "1",
) -> DatasetPageRecord:
    return DatasetPageRecord(
        scope=DatasetPageScope(
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
        ),
        dataset_id=dataset_id,
        name=name,
        description="dataset page integration fixture",
        labels=labels,
        availability="ACTIVE",
        owner=DatasetPageActor(id="p05-owner", display_name="P05 Owner"),
        created_at=created_at,
        updated_at=created_at,
        activity_at=created_at,
        etag=ResourceVersion(1).etag,
        metadata=DatasetPageMetadata(
            robot_model_id="model-p05",
            robot_id=robot_id,
            collection_task_id=collection_task_id,
            task=task,
            scene="lab",
            asset_state=asset_state,
            storage_class="STANDARD",
            channels=channels,
        ),
        episode_count="3",
        pending_review_version_count=pending_review_version_count,
        returned_version_count=returned_version_count,
        actionable_draft_count=actionable_draft_count,
    )


def _service() -> tuple[DatasetPageService, InMemoryDatasetPageRepository]:
    repository = InMemoryDatasetPageRepository(
        organization_projects=((ORGANIZATION_ID, PROJECT_ID),),
        records=(
            _record(
                dataset_id="dataset_p05fixturea",
                name="P05 采集数据 A",
                created_at=NOW - timedelta(hours=2),
                robot_id="robot-p05-a",
                channels=("/camera/front", "/joint"),
            ),
            _record(
                dataset_id="dataset_p05fixtureb",
                name="P05 采集数据 B",
                created_at=NOW - timedelta(hours=1),
                robot_id="robot-p05-b",
                channels=("/camera/rear",),
            ),
        ),
    )
    return (
        DatasetPageService(
            repository,
            cursor_secret="p05-router-cursor-secret",
            clock=lambda: NOW,
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
        request.state.request_id = "p05-router-test"
        return await call_next(request)

    app.include_router(router)
    return app


def _headers(**extra: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer test",
        "X-Organization-Id": ORGANIZATION_ID,
        "X-Project-Id": PROJECT_ID,
        "X-Region-Code": REGION_CODE,
        **extra,
    }


def _create_body(name: str = "P05 新建 Dataset") -> dict[str, object]:
    return {
        "name": name,
        "folder_path": ["机器人", "G1"],
        "description": "创建空数据集，不隐式创建版本。",
        "labels": ["robot", "p05", "robot"],
    }


def test_p05_page_aggregate_create_replay_and_scoped_read_contract() -> None:
    service, repository = _service()
    configure_dataset_page(service)
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/datasets"

    listed = client.get(root, headers=_headers(), params={"channels": "/camera/front"})
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    page = listed.json()
    assert [item["dataset_id"] for item in page["items"]] == ["dataset_p05fixturea"]
    assert page["items"][0]["folder_path"] == []
    assert page["items"][0]["availability"] == "ACTIVE"
    assert page["page_info"]["total_count"] == "1"
    assert page["items"][0]["allowed_actions"][0] == {
        "action": "OPEN_DATASET",
        "allowed": True,
        "blocked_reasons": [],
    }

    summary = client.get(f"{root}:summary", headers=_headers())
    assert summary.status_code == 200
    assert summary.json()["data"]["dataset_count"] == "2"
    assert summary.json()["data"]["episode_count"] == "6"
    assert set(summary.json()["meta"]) == {
        "request_id",
        "trace_id",
        "correlation_id",
        "generated_at",
        "as_of",
        "projection_version",
        "event_cursor",
    }

    facets = client.get(f"{root}:facets", headers=_headers())
    assert facets.status_code == 200
    assert facets.json()["data"]["robots"] == [
        {"value": "robot-p05-a", "count": "1"},
        {"value": "robot-p05-b", "count": "1"},
    ]
    assert facets.json()["data"]["tags"] == [{"value": "fixture", "count": "2"}]

    capabilities = client.get(f"{root}:page-capabilities", headers=_headers())
    assert capabilities.status_code == 200
    assert capabilities.json()["data"]["allowed_actions"] == ["CREATE_DATASET"]
    assert capabilities.json()["data"]["authorization_revision"] == "7"

    create_headers = _headers(**{"Idempotency-Key": "p05-create-a"})
    created = client.post(root, headers=create_headers, json=_create_body())
    assert created.status_code == 201
    assert created.headers["etag"] == '"v1"'
    assert created.headers["idempotency-replayed"] == "false"
    data = created.json()["data"]
    assert data["dataset_id"].startswith("dataset_")
    assert data["folder_path"] == ["机器人", "G1"]
    assert data["labels"] == ["robot", "p05"]
    assert data["availability"] == "ACTIVE"
    assert data["owner"]["id"] == "p05-operator"

    replay = client.post(root, headers=create_headers, json=_create_body())
    assert replay.status_code == 201
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.json()["data"]["dataset_id"] == data["dataset_id"]

    conflict = client.post(
        root,
        headers=_headers(**{"Idempotency-Key": "p05-create-b"}),
        json=_create_body(),
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "DATASET_CONFLICT"

    assert any(event.action == "dataset.created" for event in repository.audit_events)
    assert all("description" not in (event.details or {}) for event in repository.audit_events)


def test_p05_page_fails_closed_for_capability_organization_and_invalid_cursor() -> None:
    service, _repository = _service()
    configure_dataset_page(service)
    current: dict[str, AuthContext | None] = {"value": _auth(create=False)}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/datasets"

    denied_create = client.post(
        root,
        headers=_headers(**{"Idempotency-Key": "p05-denied"}),
        json=_create_body("不得创建"),
    )
    assert denied_create.status_code == 403
    assert denied_create.json()["code"] == "CAPABILITY_REQUIRED"

    wrong_organization = client.get(
        root,
        headers=_headers(**{"X-Organization-Id": "p05-other-organization"}),
    )
    assert wrong_organization.status_code == 403
    assert wrong_organization.json()["code"] == "ORGANIZATION_SCOPE_DENIED"

    invalid_cursor = client.get(root, headers=_headers(), params={"after": "not-a-cursor"})
    assert invalid_cursor.status_code == 400
    assert invalid_cursor.json()["code"] == "INVALID_CURSOR"

    current["value"] = None
    unauthenticated = client.get(root, headers=_headers())
    assert unauthenticated.status_code == 401


def test_p05_search_filters_before_keyset_paging_and_binds_cursor_to_query() -> None:
    repository = InMemoryDatasetPageRepository(
        organization_projects=((ORGANIZATION_ID, PROJECT_ID),),
        records=tuple(
            _record(
                dataset_id=f"dataset_search{index:02d}",
                name=f"Needle dataset {index:02d}",
                created_at=NOW + timedelta(minutes=index),
            )
            for index in range(21)
        ),
    )
    configure_dataset_page(
        DatasetPageService(
            repository,
            cursor_secret="p05-search-cursor-secret",
            clock=lambda: NOW,
        )
    )
    client = TestClient(_app({"value": _auth(create=False)}))
    root = f"/api/v1/projects/{PROJECT_ID}/datasets"
    query = {"q": "Needle", "sort": "name:asc,dataset_id:asc", "limit": 20}

    first = client.get(root, headers=_headers(), params=query)
    assert first.status_code == 200
    first_page = first.json()
    assert len(first_page["items"]) == 20
    assert first_page["page_info"]["has_next"] is True
    assert all("Needle" in item["name"] for item in first_page["items"])
    cursor = first_page["page_info"]["after"]
    assert cursor

    second = client.get(root, headers=_headers(), params={**query, "after": cursor})
    assert second.status_code == 200
    assert [item["dataset_id"] for item in second.json()["items"]] == ["dataset_search20"]

    mismatched = client.get(
        root,
        headers=_headers(),
        params={**query, "q": "different", "after": cursor},
    )
    assert mismatched.status_code == 400
    assert mismatched.json()["code"] == "INVALID_CURSOR"
    list_events = [event for event in repository.audit_events if event.action == "dataset.listed"]
    assert [event.details for event in list_events] == [
        {"result_count": 20},
        {"result_count": 1},
    ]


def test_p05_task_filter_follows_episode_collection_task_identity() -> None:
    scope = DatasetPageScope(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
    )
    first = _record(
        dataset_id="dataset_tasklineagea",
        name="Task lineage A",
        created_at=NOW,
    )
    second = _record(
        dataset_id="dataset_tasklineageb",
        name="Task lineage B",
        created_at=NOW,
    )
    collection_task_id = "collection-task-unique-02"
    repository = InMemoryDatasetPageRepository(
        records=(first, second),
        episodes=(
            DatasetPageEpisodeRecord(
                scope=scope,
                dataset_id=first.dataset_id,
                version_id="version_tasklineage",
                episode_id="episode_tasklineage",
                selected_revision=DatasetPageRevisionSnapshotReference(
                    episode_id="episode_tasklineage",
                    revision_id="revision_tasklineage",
                    ordinal=0,
                    content_sha256="a" * 64,
                ),
                included=True,
                success_state="SUCCEEDED",
                task=collection_task_id,
            ),
            DatasetPageEpisodeRecord(
                scope=scope,
                dataset_id=second.dataset_id,
                version_id="version_tasklineageb",
                episode_id="episode_tasklineageb",
                selected_revision=DatasetPageRevisionSnapshotReference(
                    episode_id="episode_tasklineageb",
                    revision_id="revision_tasklineageb",
                    ordinal=0,
                    content_sha256="b" * 64,
                ),
                included=True,
                success_state="SUCCEEDED",
                task=collection_task_id,
            ),
        ),
    )

    matched = repository.list_records(
        scope=scope,
        filters=DatasetPageFilters(task=collection_task_id),
    )

    assert {record.dataset_id for record in matched} == {first.dataset_id, second.dataset_id}


def test_p05_collection_task_filter_uses_exact_dataset_ownership() -> None:
    collection_task_id = "collection-task-owned-01"
    owned = _record(
        dataset_id="dataset_collectionowned",
        name="Owned collection dataset",
        created_at=NOW,
        collection_task_id=collection_task_id,
        task=collection_task_id,
    )
    historical_episode_match = _record(
        dataset_id="dataset_historicalepisode",
        name="Historical episode match",
        created_at=NOW,
        task=None,
    )
    scope = owned.scope
    repository = InMemoryDatasetPageRepository(
        organization_projects=((ORGANIZATION_ID, PROJECT_ID),),
        records=(owned, historical_episode_match),
        episodes=(
            DatasetPageEpisodeRecord(
                scope=scope,
                dataset_id=historical_episode_match.dataset_id,
                version_id="version_historicalepisode",
                episode_id="episode_historicalepisode",
                selected_revision=DatasetPageRevisionSnapshotReference(
                    episode_id="episode_historicalepisode",
                    revision_id="revision_historicalepisode",
                    ordinal=0,
                    content_sha256="c" * 64,
                ),
                included=True,
                success_state="SUCCEEDED",
                task=collection_task_id,
            ),
        ),
    )

    matched = repository.list_records(
        scope=scope,
        filters=DatasetPageFilters(collection_task_id=collection_task_id),
    )

    assert [record.dataset_id for record in matched] == [owned.dataset_id]

    configure_dataset_page(
        DatasetPageService(repository, cursor_secret="p05-collection-task-exact", clock=lambda: NOW)
    )
    client = TestClient(_app({"value": _auth(create=False)}))
    response = client.get(
        f"/api/v1/projects/{PROJECT_ID}/datasets",
        headers=_headers(),
        params={"collection_task_id": collection_task_id},
    )

    assert response.status_code == 200
    assert [item["dataset_id"] for item in response.json()["items"]] == [owned.dataset_id]


def test_p05_task_contains_match_is_case_insensitive_and_shared_by_aggregates() -> None:
    scope = DatasetPageScope(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
    )
    dataset_match = _record(
        dataset_id="dataset_taskcontainsa",
        name="Dataset task match",
        created_at=NOW,
        robot_id="robot-task-a",
        task="PickBox",
    )
    episode_match = _record(
        dataset_id="dataset_taskcontainsb",
        name="Episode task match",
        created_at=NOW,
        robot_id="robot-task-b",
        task="unrelated",
    )
    no_match = _record(
        dataset_id="dataset_taskcontainsc",
        name="No task match",
        created_at=NOW,
        robot_id="robot-task-c",
        task="place",
    )
    episodes = tuple(
        DatasetPageEpisodeRecord(
            scope=scope,
            dataset_id=episode_match.dataset_id,
            version_id="version_taskcontains",
            episode_id=f"episode_taskcontains{index}",
            selected_revision=DatasetPageRevisionSnapshotReference(
                episode_id=f"episode_taskcontains{index}",
                revision_id=f"revision_taskcontains{index}",
                ordinal=index,
                content_sha256=str(index + 1) * 64,
            ),
            included=True,
            success_state="SUCCEEDED",
            task="robot-picking-task",
        )
        for index in range(2)
    )
    repository = InMemoryDatasetPageRepository(
        records=(dataset_match, episode_match, no_match),
        episodes=episodes,
    )
    configure_dataset_page(
        DatasetPageService(repository, cursor_secret="p05-task-contains", clock=lambda: NOW)
    )
    client = TestClient(_app({"value": _auth(create=False)}))
    root = f"/api/v1/projects/{PROJECT_ID}/datasets"

    listed = client.get(root, headers=_headers(), params={"task": "  PICK  "})
    summary = client.get(f"{root}:summary", headers=_headers(), params={"task": "pick"})
    facets = client.get(f"{root}:facets", headers=_headers(), params={"task": "pick"})

    listed_ids = [item["dataset_id"] for item in listed.json()["items"]]
    assert set(listed_ids) == {dataset_match.dataset_id, episode_match.dataset_id}
    assert len(listed_ids) == 2
    assert summary.json()["data"]["dataset_count"] == "2"
    assert facets.json()["data"]["robots"] == [
        {"value": "robot-task-a", "count": "1"},
        {"value": "robot-task-b", "count": "1"},
    ]


def test_p05_task_treats_sql_wildcard_characters_as_plain_text() -> None:
    scope = DatasetPageScope(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
    )
    literal = _record(
        dataset_id="dataset_taskliteral",
        name="Literal task",
        created_at=NOW,
        task="literal-%_task",
    )
    ordinary = _record(
        dataset_id="dataset_taskordinary",
        name="Ordinary task",
        created_at=NOW,
        task="literal-any-task",
    )
    repository = InMemoryDatasetPageRepository(records=(literal, ordinary))

    matched = repository.list_records(scope=scope, filters=DatasetPageFilters(task="%_"))

    assert [record.dataset_id for record in matched] == [literal.dataset_id]


def test_p05_tag_filter_matches_dataset_labels_across_page_aggregates() -> None:
    matched = _record(
        dataset_id="dataset_tagmatch",
        name="Tagged dataset",
        created_at=NOW,
        robot_id="robot-tagged",
        labels=("Grip-Ready", "training"),
    )
    unmatched = _record(
        dataset_id="dataset_tagother",
        name="Other dataset",
        created_at=NOW,
        robot_id="robot-other",
        labels=("evaluation",),
    )
    repository = InMemoryDatasetPageRepository(records=(matched, unmatched))
    configure_dataset_page(
        DatasetPageService(repository, cursor_secret="p05-tag-filter", clock=lambda: NOW)
    )
    client = TestClient(_app({"value": _auth(create=False)}))
    root = f"/api/v1/projects/{PROJECT_ID}/datasets"

    listed = client.get(root, headers=_headers(), params={"tag": "  grip  "})
    summary = client.get(f"{root}:summary", headers=_headers(), params={"tag": "GRIP"})
    facets = client.get(f"{root}:facets", headers=_headers(), params={"tag": "grip"})

    assert [item["dataset_id"] for item in listed.json()["items"]] == [matched.dataset_id]
    assert summary.json()["data"]["dataset_count"] == "1"
    assert summary.json()["data"]["normalized_filters"]["tag"] == "GRIP"
    assert facets.json()["data"]["robots"] == [{"value": "robot-tagged", "count": "1"}]


def test_p05_workflow_state_filters_list_summary_and_facets_consistently() -> None:
    records = (
        _record(
            dataset_id="dataset_workflowpending",
            name="Pending workflow",
            created_at=NOW,
            robot_id="robot-workflow-pending",
            task="PickBox",
            pending_review_version_count="2",
            returned_version_count="0",
            actionable_draft_count="0",
        ),
        _record(
            dataset_id="dataset_workflowreturned",
            name="Returned workflow",
            created_at=NOW - timedelta(days=2),
            robot_id="robot-workflow-returned",
            task="place",
            pending_review_version_count="0",
            returned_version_count="1",
            actionable_draft_count="0",
        ),
        _record(
            dataset_id="dataset_workflowdraft",
            name="Draft workflow",
            created_at=NOW,
            robot_id="robot-workflow-draft",
            task="place",
            pending_review_version_count="0",
            returned_version_count="0",
            actionable_draft_count="3",
        ),
    )
    repository = InMemoryDatasetPageRepository(records=records)
    configure_dataset_page(
        DatasetPageService(repository, cursor_secret="p05-workflow-state", clock=lambda: NOW)
    )
    client = TestClient(_app({"value": _auth(create=False)}))
    root = f"/api/v1/projects/{PROJECT_ID}/datasets"

    expected = {
        "pendingReview": "dataset_workflowpending",
        "returned": "dataset_workflowreturned",
        "actionableDraft": "dataset_workflowdraft",
    }
    for workflow_state, dataset_id in expected.items():
        listed = client.get(root, headers=_headers(), params={"workflow_state": workflow_state})
        assert [item["dataset_id"] for item in listed.json()["items"]] == [dataset_id]

    combined = {
        "workflow_state": "pendingReview",
        "task": "pick",
        "asset_state": "READY",
        "created_from": NOW.date().isoformat(),
    }
    listed = client.get(root, headers=_headers(), params=combined)
    summary = client.get(f"{root}:summary", headers=_headers(), params=combined)
    facets = client.get(f"{root}:facets", headers=_headers(), params=combined)

    assert [item["dataset_id"] for item in listed.json()["items"]] == ["dataset_workflowpending"]
    assert summary.json()["data"]["dataset_count"] == "1"
    assert summary.json()["data"]["normalized_filters"]["workflow_state"] == "pendingReview"
    assert facets.json()["data"]["robots"] == [{"value": "robot-workflow-pending", "count": "1"}]


def test_p05_rejects_an_unknown_workflow_state() -> None:
    service, _repository = _service()
    configure_dataset_page(service)
    client = TestClient(_app({"value": _auth(create=False)}))

    response = client.get(
        f"/api/v1/projects/{PROJECT_ID}/datasets",
        headers=_headers(),
        params={"workflow_state": "unknown"},
    )

    assert response.status_code == 422
