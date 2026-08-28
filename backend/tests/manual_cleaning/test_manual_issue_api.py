from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.manual_cleaning.models import (
    ManualCleaningDraftRecord,
    ManualIssueDraftRef,
    ManualIssuePrincipal,
    ManualIssueRecord,
    ManualIssueResolutionCandidate,
    ManualIssueScope,
    ManualIssueSourceFacts,
)
from hc_data_platform.manual_cleaning.repository import InMemoryManualIssueRepository
from hc_data_platform.manual_cleaning.router import configure_manual_issues, router
from hc_data_platform.manual_cleaning.service import ManualIssueService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.versioning import ResourceVersion

PROJECT_ID = "p09-project"
REGION_CODE = "p09-region"
ORGANIZATION_ID = "p09-organization"
DATASET_ID = "dataset_p09fixture"
VERSION_ID = "version_p09source"
OUTPUT_VERSION_ID = "version_p09ready"
EPISODE_ID = "episode_p09fixture"
REVISION_ID = "revision_p09fixture"
STREAM_ID = "stream_p09fixture"
NOW = datetime(2026, 8, 19, 20, tzinfo=timezone.utc)


def _scope() -> ManualIssueScope:
    return ManualIssueScope(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
    )


def _auth(*, resolve: bool = True) -> AuthContext:
    capabilities = {
        "manual_issue.read",
        "manual_issue.create",
        "manual_issue.triage",
        "cleaning.create",
        "annotation.edit",
        "annotation.review",
    }
    if resolve:
        capabilities.add("manual_issue.resolve")
    return AuthContext(
        subject_id="p09-operator",
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        roles=frozenset(),
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset((PROJECT_ID, capability) for capability in capabilities),
    )


def _source() -> ManualIssueSourceFacts:
    return ManualIssueSourceFacts(
        scope=_scope(),
        dataset_id=DATASET_ID,
        version_id=VERSION_ID,
        episode_id=EPISODE_ID,
        revision_id=REVISION_ID,
        stream_id=STREAM_ID,
        stream_channel_path="joint.position",
        stream_start_ns="100",
        stream_end_ns="1000",
        schema_snapshot_id="schema_p09fixture",
    )


def _service(
    *,
    resolution_ready: bool = False,
    multiple_editing_drafts: bool = False,
) -> tuple[ManualIssueService, InMemoryManualIssueRepository]:
    issue = ManualIssueRecord(
        id="issue_p09existing",
        etag=ResourceVersion(3).etag,
        scope=_scope(),
        dataset_id=DATASET_ID,
        origin_dataset_version_id=VERSION_ID,
        episode_id=EPISODE_ID,
        episode_revision_id=REVISION_ID,
        episode_stream_id=STREAM_ID,
        schema_snapshot_id="schema_p09fixture",
        start_ns="200",
        end_ns="600",
        issue_type="POSE_JITTER",
        severity="HIGH",
        status="IN_PROGRESS",
        note="already assigned source-bound issue",
        assignee=ManualIssuePrincipal(id="p09-worker", display_name="P09 Worker"),
        related_drafts=(
            ManualIssueDraftRef(
                draft_id="draft_p09root",
                status="EDITING",
                updated_at=NOW,
            ),
        ),
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW,
    )
    draft = ManualCleaningDraftRecord(
        scope=_scope(),
        draft_id="draft_p09root",
        source_issue_id=issue.id,
        dataset_id=DATASET_ID,
        base_version_id=VERSION_ID,
        episode_id=EPISODE_ID,
        base_revision_id=REVISION_ID,
        selected_stream_id=STREAM_ID,
        selected_channel_path="joint.position",
        start_ns="200",
        end_ns="600",
        status="EDITING",
        created_at=NOW,
        updated_at=NOW,
    )
    second_draft = ManualCleaningDraftRecord(
        scope=_scope(),
        draft_id="draft_p09second",
        source_issue_id=issue.id,
        dataset_id=DATASET_ID,
        base_version_id=VERSION_ID,
        episode_id=EPISODE_ID,
        base_revision_id=REVISION_ID,
        selected_stream_id=STREAM_ID,
        selected_channel_path="joint.position",
        start_ns="250",
        end_ns="550",
        status="EDITING",
        created_at=NOW - timedelta(minutes=1),
        updated_at=NOW + timedelta(minutes=1),
    )
    candidates = (
        (
            ManualIssueResolutionCandidate(
                scope=_scope(),
                manual_issue_id=issue.id,
                version_id=OUTPUT_VERSION_ID,
                producer_draft_id=draft.draft_id,
                root_issue_draft_id=draft.draft_id,
                lineage_depth="0",
            ),
        )
        if resolution_ready
        else ()
    )
    repository = InMemoryManualIssueRepository(
        organization_projects=((ORGANIZATION_ID, PROJECT_ID),),
        issues=(issue,),
        source_facts=(_source(),),
        annotation_source_facts=(("annotation-task-p09", "/camera/front", _source()),),
        drafts=(draft, second_draft) if multiple_editing_drafts else (draft,),
        resolution_candidates=candidates,
    )
    return (
        ManualIssueService(
            repository,
            cursor_secret="p09-router-cursor-secret",
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
        request.state.request_id = "p09-router-test"
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


def _create_body() -> dict[str, str]:
    return {
        "origin_dataset_version_id": VERSION_ID,
        "episode_id": EPISODE_ID,
        "episode_revision_id": REVISION_ID,
        "episode_stream_id": STREAM_ID,
        "start_ns": "300",
        "end_ns": "500",
        "issue_type": "TIMESTAMP_DRIFT",
        "severity": "MEDIUM",
        "note": "fixed stream range needs a manual decision",
    }


def test_p09_source_bound_create_triage_draft_replay_and_page_contract() -> None:
    service, repository = _service()
    configure_manual_issues(service)
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/manual-issues"

    created = client.post(
        root,
        headers=_headers(**{"Idempotency-Key": "p09-create-key"}),
        json=_create_body(),
    )
    assert created.status_code == 201
    assert created.headers["etag"] == '"v1"'
    issue = created.json()["data"]
    issue_id = issue["id"]
    assert issue["dataset_id"] == DATASET_ID
    assert issue["status"] == "OPEN"
    assert issue["allowed_actions"] == ["VIEW_EPISODE", "TRIAGE", "CREATE_DRAFT"]
    assert "PREVIEW_RANGE" not in issue["allowed_actions"]

    replay = client.post(
        root,
        headers=_headers(**{"Idempotency-Key": "p09-create-key"}),
        json=_create_body(),
    )
    assert replay.status_code == 200
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.json()["data"]["id"] == issue_id

    page = client.get(f"{root}:page", headers=_headers())
    assert page.status_code == 200
    assert page.json()["data"]["counts"]["total"] == "2"
    assert page.json()["data"]["allowed_actions"] == ["TRIAGE", "CREATE_DRAFT", "RESOLVE"]

    triaged = client.post(
        f"{root}/{issue_id}:triage",
        headers=_headers(**{"If-Match": issue["etag"], "Idempotency-Key": "p09-triage-key"}),
        json={
            "target_status": "IN_PROGRESS",
            "severity": "HIGH",
            "assignee_id": "p09-assignee",
            "reason": "operator accepted the triage",
        },
    )
    assert triaged.status_code == 200
    assert triaged.json()["data"]["status"] == "IN_PROGRESS"
    assert triaged.json()["data"]["assignee"]["id"] == "p09-assignee"

    triage_replay = client.post(
        f"{root}/{issue_id}:triage",
        headers=_headers(**{"If-Match": issue["etag"], "Idempotency-Key": "p09-triage-key"}),
        json={
            "target_status": "IN_PROGRESS",
            "severity": "HIGH",
            "assignee_id": "p09-assignee",
            "reason": "operator accepted the triage",
        },
    )
    assert triage_replay.status_code == 200
    assert triage_replay.headers["idempotency-replayed"] == "true"
    assert triage_replay.json()["data"]["etag"] == '"v2"'

    draft = client.post(
        f"{root}/{issue_id}/cleaning-drafts",
        headers=_headers(**{"If-Match": '"v2"', "Idempotency-Key": "p09-draft-key"}),
        json={},
    )
    assert draft.status_code == 201
    assert draft.json()["data"]["disposition"] == "CREATED"
    draft_id = draft.json()["data"]["draft_id"]
    assert draft_id.startswith("draft_")

    draft_replay = client.post(
        f"{root}/{issue_id}/cleaning-drafts",
        headers=_headers(**{"If-Match": '"v2"', "Idempotency-Key": "p09-draft-key"}),
        json={},
    )
    assert draft_replay.status_code == 200
    assert draft_replay.headers["idempotency-replayed"] == "true"
    assert draft_replay.json()["data"]["draft_id"] == draft_id

    listed = client.get(root, headers=_headers(), params={"status": "IN_PROGRESS"})
    assert listed.status_code == 200
    created_row = next(item for item in listed.json()["items"] if item["id"] == issue_id)
    assert created_row["related_draft_count"] == "1"
    assert created_row["allowed_actions"] == ["VIEW_EPISODE", "TRIAGE", "CONTINUE_DRAFT", "RESOLVE"]
    assert any(event.action == "manual_issue.created" for event in repository.audit_events)
    assert any(event.action == "cleaning.draft.created" for event in repository.audit_events)
    assert all("note" not in (event.details or {}) for event in repository.audit_events)


def test_annotation_report_resolves_source_identity_and_enters_problem_data() -> None:
    service, _repository = _service()
    configure_manual_issues(service)
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/manual-issues"

    response = client.post(
        root,
        headers=_headers(**{"Idempotency-Key": "p09-annotation-report"}),
        json={
            "source_kind": "ANNOTATION_TASK",
            "discovery_source": "ANNOTATOR",
            "annotation_task_id": "annotation-task-p09",
            "stream_ref": "/camera/front",
            "relative_start_ns": "25",
            "relative_end_ns": "125",
            "issue_type": "MISSING_FRAME",
            "severity": "HIGH",
            "note": "annotator found frames missed by automatic QC",
        },
    )

    assert response.status_code == 201
    issue = response.json()["data"]
    assert issue["discovery_source"] == "ANNOTATOR"
    assert issue["annotation_task_id"] == "annotation-task-p09"
    assert issue["dataset_id"] == DATASET_ID
    assert issue["episode_stream_id"] == STREAM_ID
    assert issue["start_ns"] == "125"
    assert issue["end_ns"] == "225"

    listed = client.get(
        root,
        headers=_headers(),
        params={"discovery_source": "ANNOTATOR"},
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [issue["id"]]


def test_p09_rejects_stale_source_and_unqualified_resolution_without_faking_ready() -> None:
    service, _repository = _service()
    configure_manual_issues(service)
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/manual-issues"

    invalid = _create_body() | {"end_ns": "1100"}
    stale = client.post(
        root,
        headers=_headers(**{"Idempotency-Key": "p09-stale-source"}),
        json=invalid,
    )
    assert stale.status_code == 412
    assert stale.json()["code"] == "VERSION_CONFLICT"

    unresolved = client.post(
        f"{root}/issue_p09existing:resolve",
        headers=_headers(**{"If-Match": '"v3"', "Idempotency-Key": "p09-no-ready-output"}),
        json={
            "resolution_version_id": OUTPUT_VERSION_ID,
            "resolution_note": "must not manufacture an output version",
        },
    )
    assert unresolved.status_code == 422
    assert unresolved.json()["code"] == "RESOLUTION_VERSION_INELIGIBLE"

    missing_if_match = client.post(
        f"{root}/issue_p09existing:triage",
        headers=_headers(**{"Idempotency-Key": "p09-missing-etag"}),
        json={
            "target_status": "OPEN",
            "severity": "LOW",
            "assignee_id": None,
            "reason": "explicit precondition test",
        },
    )
    assert missing_if_match.status_code == 428
    assert missing_if_match.json()["code"] == "IF_MATCH_REQUIRED"


def test_p09_resolves_only_a_durable_ready_lineage_and_replays() -> None:
    service, repository = _service(resolution_ready=True)
    configure_manual_issues(service)
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/manual-issues"
    headers = _headers(**{"If-Match": '"v3"', "Idempotency-Key": "p09-resolve-ready"})
    body = {
        "resolution_version_id": OUTPUT_VERSION_ID,
        "resolution_note": "READY output and immutable draft ancestry were verified",
    }

    resolved = client.post(f"{root}/issue_p09existing:resolve", headers=headers, json=body)
    assert resolved.status_code == 200
    result = resolved.json()["data"]
    assert result["status"] == "RESOLVED"
    assert result["resolution_version"] == {
        "version_id": OUTPUT_VERSION_ID,
        "producer_draft_id": "draft_p09root",
        "root_issue_draft_id": "draft_p09root",
        "lineage_depth": "0",
        "resolved_at": NOW.isoformat().replace("+00:00", "Z"),
    }
    assert result["allowed_actions"] == ["VIEW_EPISODE"]

    replay = client.post(f"{root}/issue_p09existing:resolve", headers=headers, json=body)
    assert replay.status_code == 200
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.json()["data"]["etag"] == '"v4"'
    assert any(event.action == "manual_issue.resolved" for event in repository.audit_events)


def test_p09_requires_selection_for_multiple_existing_editing_drafts() -> None:
    service, repository = _service(multiple_editing_drafts=True)
    configure_manual_issues(service)
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/manual-issues"

    response = client.post(
        f"{root}/issue_p09existing/cleaning-drafts",
        headers=_headers(**{"If-Match": '"v3"', "Idempotency-Key": "p09-selection-required"}),
        json={},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["disposition"] == "SELECTION_REQUIRED"
    assert data["draft_id"] is None
    assert data["context"] is None
    assert {candidate["draft_id"] for candidate in data["candidates"]} == {
        "draft_p09root",
        "draft_p09second",
    }
    persisted = repository.get_issue(scope=_scope(), issue_id="issue_p09existing")
    assert persisted is not None
    assert persisted.etag == '"v3"'
    assert not any(event.action == "cleaning.draft.created" for event in repository.audit_events)
