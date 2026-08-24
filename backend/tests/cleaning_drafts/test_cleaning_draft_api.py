from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.cleaning_drafts.models import (
    CleaningDraftEvent,
    CleaningDraftIssueContext,
    CleaningDraftIssueDerivedOrigin,
    CleaningDraftProjection,
    CleaningDraftRecord,
    CleaningDraftRelationships,
    CleaningDraftReviewSummary,
    CleaningDraftScope,
)
from hc_data_platform.cleaning_drafts.repository import InMemoryCleaningDraftRepository
from hc_data_platform.cleaning_drafts.router import configure_cleaning_drafts, router
from hc_data_platform.cleaning_drafts.service import CleaningDraftService
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.versioning import ResourceVersion

PROJECT_ID = "p10-project"
REGION_CODE = "p10-region"
ORGANIZATION_ID = "p10-organization"
DATASET_ID = "dataset_p10fixture"
BASE_VERSION_ID = "version_p10base"
OUTPUT_VERSION_ID = "version_p10output"
EPISODE_ID = "episode_p10fixture"
BASE_REVISION_ID = "revision_p10base"
OUTPUT_REVISION_ID = "revision_p10output"
STREAM_ID = "stream_p10fixture"
ISSUE_ID = "issue_p10fixture"
EDITING_DRAFT_ID = "draft_p10editing"
RETURNED_DRAFT_ID = "draft_p10returned"
SUCCESSOR_DRAFT_ID = "draft_p10successor"
REVIEW_DECISION_ID = "review_decision_p10returned"
REVIEW_FINDING_ID = "review_finding_p10returned"
NOW = datetime(2026, 8, 19, 22, tzinfo=timezone.utc)


def _scope() -> CleaningDraftScope:
    return CleaningDraftScope(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
    )


def _auth(*, read: bool = True, edit: bool = False) -> AuthContext:
    capabilities = {"cleaning.read"} if read else set()
    if edit:
        capabilities.add("cleaning.edit")
    return AuthContext(
        subject_id="p10-operator",
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        roles=frozenset(),
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset((PROJECT_ID, capability) for capability in capabilities),
    )


def _origin() -> CleaningDraftIssueDerivedOrigin:
    return CleaningDraftIssueDerivedOrigin(
        manual_issue_context=CleaningDraftIssueContext(
            dataset_id=DATASET_ID,
            base_version_id=BASE_VERSION_ID,
            episode_id=EPISODE_ID,
            base_revision_id=BASE_REVISION_ID,
            selected_stream_id=STREAM_ID,
            selected_channel_path="joint.position",
            start_ns="100",
            end_ns="1000",
            manual_issue_ids=(ISSUE_ID,),
        )
    )


def _editing_projection() -> CleaningDraftProjection:
    draft = CleaningDraftRecord(
        draft_id=EDITING_DRAFT_ID,
        etag=ResourceVersion(1).etag,
        status="EDITING",
        origin=_origin(),
        base_version_id=BASE_VERSION_ID,
        base_revision_id=BASE_REVISION_ID,
        episode_id=EPISODE_ID,
        manual_issue_count="1",
        preview_status="NONE",
        commit_status="NONE",
        review_finding_count="0",
        created_at=NOW - timedelta(hours=2),
        updated_at=NOW - timedelta(hours=1),
    )
    return CleaningDraftProjection(
        scope=_scope(),
        draft=draft,
        relationships=CleaningDraftRelationships(),
        creator_id="p10-operator",
        robot_id="robot_p10fixture",
    )


def _returned_projection() -> CleaningDraftProjection:
    review = CleaningDraftReviewSummary(
        review_decision_id=REVIEW_DECISION_ID,
        review_finding_ids=(REVIEW_FINDING_ID,),
        finding_count="1",
        successor_draft_id=SUCCESSOR_DRAFT_ID,
        supersedes_draft_id=RETURNED_DRAFT_ID,
        returned_from_version_id=OUTPUT_VERSION_ID,
        returned_from_review_decision_id=REVIEW_DECISION_ID,
    )
    draft = CleaningDraftRecord(
        draft_id=RETURNED_DRAFT_ID,
        etag=ResourceVersion(2).etag,
        status="COMMITTED",
        origin=_origin(),
        base_version_id=BASE_VERSION_ID,
        base_revision_id=BASE_REVISION_ID,
        episode_id=EPISODE_ID,
        manual_issue_count="1",
        preview_status="NONE",
        commit_status="SUCCEEDED",
        output_version_status="RETURNED",
        review_decision_id=REVIEW_DECISION_ID,
        successor_draft_id=SUCCESSOR_DRAFT_ID,
        review_finding_count="1",
        review_summary=review,
        created_at=NOW - timedelta(hours=4),
        updated_at=NOW,
    )
    return CleaningDraftProjection(
        scope=_scope(),
        draft=draft,
        relationships=CleaningDraftRelationships(
            commit_id="commit_p10returned",
            output_version_id=OUTPUT_VERSION_ID,
            output_revision_ids=(OUTPUT_REVISION_ID,),
            review_decision_id=REVIEW_DECISION_ID,
            review_finding_ids=(REVIEW_FINDING_ID,),
            successor_draft_id=SUCCESSOR_DRAFT_ID,
            supersedes_draft_id=RETURNED_DRAFT_ID,
            returned_from_version_id=OUTPUT_VERSION_ID,
            returned_from_review_decision_id=REVIEW_DECISION_ID,
        ),
        creator_id="p10-operator",
        robot_id="robot_p10fixture",
        review_finding_types=("POSE_DISCONTINUITY",),
        review_finding_severities=("HIGH",),
    )


def _service() -> tuple[CleaningDraftService, InMemoryCleaningDraftRepository]:
    returned_event = CleaningDraftEvent(
        event_id="event_p10returned",
        event_type="dataset_version.review.returned",
        occurred_at=NOW,
        result="SUCCESS",
        safe_summary="Dataset version review returned the output to a successor Draft.",
        request_id="request_p10returned",
    )
    repository = InMemoryCleaningDraftRepository(
        organization_projects=((ORGANIZATION_ID, PROJECT_ID),),
        drafts=(_editing_projection(), _returned_projection()),
        events=((_scope(), RETURNED_DRAFT_ID, returned_event),),
    )
    return (
        CleaningDraftService(
            repository,
            cursor_secret="p10-router-cursor-secret",
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
        request.state.request_id = "p10-router-test"
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


def test_p10_reads_p11_authoritative_drafts_with_capability_safe_navigation_actions() -> None:
    service, repository = _service()
    configure_cleaning_drafts(service)
    current: dict[str, AuthContext | None] = {"value": _auth()}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/cleaning-drafts"

    listed = client.get(root, headers=_headers(), params={"scope": "mine", "limit": 20})
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    list_payload = listed.json()
    assert list_payload["contract_version"] == "manual-cleaning.v1"
    assert {item["draft_id"] for item in list_payload["items"]} == {
        EDITING_DRAFT_ID,
        RETURNED_DRAFT_ID,
    }
    actions_by_draft = {item["draft_id"]: item["allowed_actions"] for item in list_payload["items"]}
    assert actions_by_draft[EDITING_DRAFT_ID] == ["VIEW", "VIEW_EVENTS"]
    assert actions_by_draft[RETURNED_DRAFT_ID] == ["VIEW", "VIEW_EVENTS", "OPEN_SUCCESSOR"]

    returned = client.get(root, headers=_headers(), params={"scope": "returned", "limit": 20})
    assert returned.status_code == 200
    returned_row = returned.json()["items"]
    assert len(returned_row) == 1
    assert returned_row[0]["draft_id"] == RETURNED_DRAFT_ID
    assert returned_row[0]["origin"]["origin_type"] == "ISSUE_DERIVED"
    assert returned_row[0]["successor_draft_id"] == SUCCESSOR_DRAFT_ID
    assert returned_row[0]["review_summary"]["review_finding_ids"] == [REVIEW_FINDING_ID]
    assert returned_row[0]["allowed_actions"] == ["VIEW", "VIEW_EVENTS", "OPEN_SUCCESSOR"]

    summary = client.get(f"{root}:summary", headers=_headers(), params={"scope": "returned"})
    assert summary.status_code == 200
    summary_data = summary.json()["data"]
    assert summary_data["scope_counts"] == {
        "EDITING": "0",
        "COMMITTED": "1",
        "RETURNED": "1",
        "REVIEWING": "0",
    }
    assert summary_data["metrics"]["review_return_count"] == "1"

    detail = client.get(f"{root}/{RETURNED_DRAFT_ID}/summary", headers=_headers())
    assert detail.status_code == 200
    detail_data = detail.json()["data"]
    assert detail_data["ordered_operations"] == []
    assert detail_data["preview_summary"] is None
    assert detail_data["draft"]["allowed_actions"] == [
        "VIEW",
        "VIEW_EVENTS",
        "OPEN_SUCCESSOR",
    ]
    assert detail_data["relationships"]["output_revision_ids"] == [OUTPUT_REVISION_ID]
    assert detail_data["relationships"]["successor_draft_id"] == SUCCESSOR_DRAFT_ID

    events = client.get(f"{root}/{RETURNED_DRAFT_ID}/events", headers=_headers())
    assert events.status_code == 200
    assert events.json()["items"] == [
        {
            "event_id": "event_p10returned",
            "event_type": "dataset_version.review.returned",
            "occurred_at": "2026-08-19T22:00:00Z",
            "result": "SUCCESS",
            "safe_summary": "Dataset version review returned the output to a successor Draft.",
            "request_id": "request_p10returned",
        }
    ]
    assert {event.action for event in repository.audit_events} >= {
        "cleaning.draft.listed",
        "cleaning.draft.summary_viewed",
        "cleaning.draft.viewed",
        "cleaning.draft.events_viewed",
    }


def test_p10_advertises_edit_navigation_only_with_the_p11_edit_capability() -> None:
    service, _repository = _service()
    configure_cleaning_drafts(service)
    current: dict[str, AuthContext | None] = {"value": _auth(edit=True)}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/cleaning-drafts"

    response = client.get(root, headers=_headers(), params={"scope": "all", "limit": 20})

    assert response.status_code == 200
    actions_by_draft = {
        item["draft_id"]: item["allowed_actions"] for item in response.json()["items"]
    }
    assert actions_by_draft[EDITING_DRAFT_ID] == ["VIEW", "VIEW_EVENTS", "EDIT"]
    assert actions_by_draft[RETURNED_DRAFT_ID] == ["VIEW", "VIEW_EVENTS", "OPEN_SUCCESSOR"]


def test_p10_enforces_read_scope_and_does_not_fabricate_missing_records() -> None:
    service, _repository = _service()
    configure_cleaning_drafts(service)
    current: dict[str, AuthContext | None] = {"value": None}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/cleaning-drafts"

    unauthenticated = client.get(root, headers=_headers(), params={"limit": 20})
    assert unauthenticated.status_code == 401

    current["value"] = _auth(read=False)
    forbidden = client.get(root, headers=_headers(), params={"limit": 20})
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "CAPABILITY_REQUIRED"

    current["value"] = _auth()
    wrong_organization = client.get(
        root,
        headers=_headers(**{"X-Organization-Id": "p10-other-organization"}),
        params={"limit": 20},
    )
    assert wrong_organization.status_code == 403
    assert wrong_organization.json()["code"] == "ORGANIZATION_SCOPE_DENIED"

    missing = client.get(f"{root}/draft_p10missing/summary", headers=_headers())
    assert missing.status_code == 404
    assert missing.json()["code"] == "CLEANING_DRAFT_NOT_FOUND"

    unsupported_page_size = client.get(root, headers=_headers(), params={"limit": 10})
    assert unsupported_page_size.status_code == 422
    assert unsupported_page_size.json()["code"] == "PAGE_LIMIT_INVALID"
