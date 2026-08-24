from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.cleaning_workbench.calculation import (
    EMPTY_EDL_HASH,
    calculate_edl,
    draft_etag,
)
from hc_data_platform.cleaning_workbench.models import (
    CleaningDraft,
    CleaningDraftBase,
    CleaningEdl,
    CleaningOutputVersion,
    CleaningStream,
    CleaningWorkbenchScope,
    CleaningWorkbenchState,
    ImmutableReviewDecision,
    IssueDerivedContext,
    IssueDerivedOrigin,
    ReviewFindingProjection,
    ReviewReturnFeedback,
    ReviewReturnLineage,
    ReviewReturnOrigin,
    ReviewSuccessorComposition,
    ReviewSuccessorCompositionMember,
)
from hc_data_platform.cleaning_workbench.repository import InMemoryCleaningWorkbenchRepository
from hc_data_platform.cleaning_workbench.router import configure_cleaning_workbench, router
from hc_data_platform.cleaning_workbench.service import CleaningWorkbenchService
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.auth import AuthContext

PROJECT_ID = "p11-project"
REGION_CODE = "p11-region"
ORGANIZATION_ID = "p11-organization"
DATASET_ID = "dataset_p11fixture"
BASE_VERSION_ID = "version_p11base"
OUTPUT_VERSION_ID = "version_p11output"
EPISODE_ID = "episode_p11fixture"
BASE_REVISION_ID = "revision_p11base"
OUTPUT_REVISION_ID = "revision_p11output"
STREAM_ID = "stream_p11fixture"
DRAFT_ID = "draft_p11editing"
SUCCESSOR_DRAFT_ID = "draft_p11successor"
ISSUE_ID = "issue_p11fixture"
DECISION_ID = "review_decision_p11returned"
FINDING_ID = "review_finding_p11returned"
NOW = datetime(2026, 8, 19, 23, tzinfo=timezone.utc)


def _scope() -> CleaningWorkbenchScope:
    return CleaningWorkbenchScope(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
    )


def _auth(*capabilities: str) -> AuthContext:
    return AuthContext(
        subject_id="p11-operator",
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        roles=frozenset(),
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        scoped_capabilities=frozenset((PROJECT_ID, capability) for capability in capabilities),
    )


def _editing_state() -> CleaningWorkbenchState:
    scope = _scope()
    stream = CleaningStream(
        stream_id=STREAM_ID,
        channel_path="/camera/front",
        kind="RGB_VIDEO",
        duration_ns="1000",
    )
    validation, summary, _mapping = calculate_edl(
        operations=(),
        streams=(stream,),
        edl_revision=0,
        operation_signature=EMPTY_EDL_HASH,
        calculated_at=NOW,
    )
    origin = IssueDerivedOrigin(
        manual_issue_context=IssueDerivedContext(
            dataset_id=DATASET_ID,
            base_version_id=BASE_VERSION_ID,
            episode_id=EPISODE_ID,
            base_revision_id=BASE_REVISION_ID,
            selected_stream_id=STREAM_ID,
            selected_channel_path="/camera/front",
            start_ns="100",
            end_ns="200",
            manual_issue_ids=(ISSUE_ID,),
        )
    )
    draft = CleaningDraft(
        draft_id=DRAFT_ID,
        etag=draft_etag(draft_id=DRAFT_ID, workbench_version=1),
        status="EDITING",
        origin=origin,
        base_version_id=BASE_VERSION_ID,
        base_revision_id=BASE_REVISION_ID,
        episode_id=EPISODE_ID,
        manual_issue_count="1",
        preview_status="NONE",
        commit_status="NONE",
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW,
    )
    return CleaningWorkbenchState(
        scope=scope,
        workbench_version=1,
        draft=draft,
        base=CleaningDraftBase(
            dataset_id=DATASET_ID,
            version_id=BASE_VERSION_ID,
            episode_id=EPISODE_ID,
            revision_id=BASE_REVISION_ID,
            schema_snapshot_id="schema_p11fixture",
        ),
        streams=(stream,),
        edl=CleaningEdl(
            edl_revision="0",
            etag=draft.etag,
            operation_hash=EMPTY_EDL_HASH,
            operations=(),
            validation=validation,
            summary=summary,
            updated_at=NOW,
        ),
    )


def _successor_state() -> CleaningWorkbenchState:
    stream = CleaningStream(
        stream_id=STREAM_ID,
        channel_path="/camera/front",
        kind="RGB_VIDEO",
        duration_ns="1000",
    )
    validation, summary, _mapping = calculate_edl(
        operations=(),
        streams=(stream,),
        edl_revision=0,
        operation_signature=EMPTY_EDL_HASH,
        calculated_at=NOW,
    )
    lineage = ReviewReturnLineage(
        supersedes_draft_id=DRAFT_ID,
        returned_from_version_id=OUTPUT_VERSION_ID,
        returned_from_review_decision_id=DECISION_ID,
    )
    origin = ReviewReturnOrigin(review_return_lineage=lineage)
    draft = CleaningDraft(
        draft_id=SUCCESSOR_DRAFT_ID,
        etag=draft_etag(draft_id=SUCCESSOR_DRAFT_ID, workbench_version=1),
        status="EDITING",
        origin=origin,
        base_version_id=OUTPUT_VERSION_ID,
        base_revision_id=OUTPUT_REVISION_ID,
        episode_id=EPISODE_ID,
        manual_issue_count="0",
        preview_status="NONE",
        commit_status="NONE",
        created_at=NOW,
        updated_at=NOW,
    )
    feedback = ReviewReturnFeedback(
        review_decision=ImmutableReviewDecision(
            id=DECISION_ID,
            output_version_id=OUTPUT_VERSION_ID,
            created_at=NOW,
        ),
        output_version=CleaningOutputVersion(
            version_id=OUTPUT_VERSION_ID,
            status="RETURNED",
            draft_id=DRAFT_ID,
            commit_id="commit_p11returned",
        ),
        output_version_id=OUTPUT_VERSION_ID,
        findings=(
            ReviewFindingProjection(
                id=FINDING_ID,
                output_revision_id=OUTPUT_REVISION_ID,
                episode_stream_id=STREAM_ID,
                start_ns="1",
                end_ns="2",
                finding_type="POSE_DISCONTINUITY",
                severity="HIGH",
                note="Immutable P07 return finding.",
                created_at=NOW,
            ),
        ),
        review_finding_ids=(FINDING_ID,),
        successor_draft_id=SUCCESSOR_DRAFT_ID,
        supersedes_draft_id=DRAFT_ID,
        returned_from_version_id=OUTPUT_VERSION_ID,
        returned_from_review_decision_id=DECISION_ID,
    )
    composition = ReviewSuccessorComposition(
        source_version_id=OUTPUT_VERSION_ID,
        editable_base_revision_id=OUTPUT_REVISION_ID,
        members=(
            ReviewSuccessorCompositionMember(
                source_revision_id=OUTPUT_REVISION_ID,
                source_ordinal=0,
                handling="EDITABLE_BASE",
            ),
        ),
        composition_hash=f"sha256:{'a' * 64}",
    )
    return CleaningWorkbenchState(
        scope=_scope(),
        workbench_version=1,
        draft=draft,
        base=CleaningDraftBase(
            dataset_id=DATASET_ID,
            version_id=OUTPUT_VERSION_ID,
            episode_id=EPISODE_ID,
            revision_id=OUTPUT_REVISION_ID,
            schema_snapshot_id="schema_p11fixture",
        ),
        streams=(stream,),
        edl=CleaningEdl(
            edl_revision="0",
            etag=draft.etag,
            operation_hash=EMPTY_EDL_HASH,
            operations=(),
            validation=validation,
            summary=summary,
            updated_at=NOW,
        ),
        successor_composition=composition,
        review_feedback=feedback,
    )


def _service() -> tuple[CleaningWorkbenchService, InMemoryCleaningWorkbenchRepository]:
    repository = InMemoryCleaningWorkbenchRepository(
        organization_projects=((ORGANIZATION_ID, PROJECT_ID),),
        states=(_editing_state(), _successor_state()),
    )
    return CleaningWorkbenchService(repository, clock=lambda: NOW), repository


def _app(current: dict[str, AuthContext | None]) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(ProblemException)
    async def handle_problem(_request: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(exc.problem.model_dump(mode="json"), status_code=exc.problem.status)

    @app.middleware("http")
    async def install_auth(request: Request, call_next: Any) -> Any:
        request.state.auth_context = current["value"]
        request.state.request_id = "p11-router-test"
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


def test_p11_real_workbench_save_preview_commit_is_non_destructive_and_idempotent() -> None:
    service, repository = _service()
    configure_cleaning_workbench(service)
    current: dict[str, AuthContext | None] = {
        "value": _auth("cleaning.read", "cleaning.edit", "cleaning.preview", "cleaning.submit")
    }
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/cleaning-drafts/{DRAFT_ID}"

    bootstrap = client.get(f"{root}/bootstrap", headers=_headers())
    assert bootstrap.status_code == 200
    assert bootstrap.headers["cache-control"] == "no-store"
    assert bootstrap.json()["data"]["allowed_actions"] == ["VIEW", "SAVE_EDL", "CREATE_PREVIEW"]
    assert "object_locator" not in bootstrap.text
    initial_etag = bootstrap.headers["etag"]
    initial_hash = bootstrap.json()["data"]["edl"]["operation_hash"]

    command = {
        "expected_edl_revision": "0",
        "expected_operation_hash": initial_hash,
        "client_mutation_id": "mutation_p11save",
        "operations": [
            {
                "id": "operation_p11exclude",
                "sequence_no": 0,
                "enabled": True,
                "schema_version": "1",
                "type": "EXCLUDE_RANGE",
                "start_ns": "100",
                "end_ns": "200",
                "reason": "Remove the interval without changing the raw source.",
            }
        ],
    }
    saved = client.put(f"{root}/edl", headers=_headers(**{"If-Match": initial_etag}), json=command)
    assert saved.status_code == 200
    assert saved.json()["data"]["edl"]["edl_revision"] == "1"
    assert saved.json()["data"]["edl"]["summary"]["output_duration_ns"] == "900"
    saved_etag = saved.headers["etag"]

    replay = client.put(f"{root}/edl", headers=_headers(**{"If-Match": initial_etag}), json=command)
    assert replay.status_code == 200
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.json()["data"]["edl"]["edl_revision"] == "1"

    stale = client.put(
        f"{root}/edl",
        headers=_headers(**{"If-Match": initial_etag}),
        json={**command, "client_mutation_id": "mutation_p11stale"},
    )
    assert stale.status_code == 412
    assert stale.json()["code"] == "CLEANING_DRAFT_PRECONDITION_FAILED"

    preview = client.post(
        f"{root}/previews",
        headers=_headers(**{"If-Match": saved_etag, "Idempotency-Key": "preview-p11-key"}),
        json={
            "base_revision_id": BASE_REVISION_ID,
            "edl_revision": "1",
            "operation_hash": saved.json()["data"]["edl"]["operation_hash"],
        },
    )
    assert preview.status_code == 202
    assert preview.json()["preview"]["status"] == "READY"
    assert (
        preview.json()["preview"]["source_to_output_map"]["segments"][0]["source_start_ns"] == "0"
    )

    preview_replay = client.post(
        f"{root}/previews",
        headers=_headers(**{"If-Match": saved_etag, "Idempotency-Key": "preview-p11-key"}),
        json={
            "base_revision_id": BASE_REVISION_ID,
            "edl_revision": "1",
            "operation_hash": saved.json()["data"]["edl"]["operation_hash"],
        },
    )
    assert preview_replay.status_code == 202
    assert preview_replay.headers["idempotency-replayed"] == "true"

    committed = client.post(
        f"{root}/commits",
        headers=_headers(**{"If-Match": saved_etag, "Idempotency-Key": "commit-p11-key"}),
        json={
            "preview_id": preview.json()["preview"]["preview_id"],
            "base_revision_id": BASE_REVISION_ID,
            "edl_revision": "1",
            "operation_hash": saved.json()["data"]["edl"]["operation_hash"],
            "successor_composition_hash": None,
            "acknowledgement": {"reviewed_summary": True, "compared_preview": True},
        },
    )
    assert committed.status_code == 202
    assert committed.json()["commit"]["status"] == "SUCCEEDED"
    assert committed.json()["commit"]["output_version"]["status"] == "REVIEWING"
    assert committed.json()["commit"]["materialization_status"] == "NOT_STARTED"

    after_commit = client.get(f"{root}/bootstrap", headers=_headers())
    assert after_commit.status_code == 200
    assert after_commit.json()["data"]["draft"]["status"] == "COMMITTED"
    assert after_commit.json()["data"]["edl"]["edl_revision"] == "1"
    assert all(
        event.before_hash != event.after_hash
        for event in repository.audit_events
        if event.before_hash
    )


def test_p11_enforces_half_open_rules_capability_and_read_only_p07_feedback() -> None:
    service, _repository = _service()
    configure_cleaning_workbench(service)
    current: dict[str, AuthContext | None] = {"value": _auth("cleaning.read", "cleaning.edit")}
    client = TestClient(_app(current))
    root = f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/cleaning-drafts/{DRAFT_ID}"
    bootstrap = client.get(f"{root}/bootstrap", headers=_headers())
    assert bootstrap.status_code == 200
    invalid = client.put(
        f"{root}/edl",
        headers=_headers(**{"If-Match": bootstrap.headers["etag"]}),
        json={
            "expected_edl_revision": "0",
            "expected_operation_hash": EMPTY_EDL_HASH,
            "client_mutation_id": "mutation_p11invalid",
            "operations": [
                {
                    "id": "operation_p11invalid",
                    "sequence_no": 0,
                    "enabled": True,
                    "schema_version": "1",
                    "type": "EXCLUDE_RANGE",
                    "start_ns": "200",
                    "end_ns": "200",
                    "reason": None,
                }
            ],
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "CLEANING_EDL_RANGE_INVALID"

    no_preview = client.post(
        f"{root}/previews",
        headers=_headers(
            **{"If-Match": bootstrap.headers["etag"], "Idempotency-Key": "no-preview"}
        ),
        json={
            "base_revision_id": BASE_REVISION_ID,
            "edl_revision": "0",
            "operation_hash": EMPTY_EDL_HASH,
        },
    )
    assert no_preview.status_code == 403
    assert no_preview.json()["code"] == "CAPABILITY_REQUIRED"

    successor_root = (
        f"/api/v1/projects/{PROJECT_ID}/regions/{REGION_CODE}/cleaning-drafts/{SUCCESSOR_DRAFT_ID}"
    )
    feedback = client.get(f"{successor_root}/review-findings", headers=_headers())
    assert feedback.status_code == 200
    payload = feedback.json()["data"]["feedback"]
    assert payload["review_decision"]["immutable"] is True
    assert payload["findings"][0]["immutable"] is True
    assert payload["successor_draft_id"] == SUCCESSOR_DRAFT_ID
    assert client.post(f"{successor_root}/review-findings", headers=_headers()).status_code == 405
