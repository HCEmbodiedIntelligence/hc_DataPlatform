from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from httpx import Response as HttpxResponse

from hc_data_platform.annotation import (
    AnnotationActor,
    AnnotationOperation,
    InMemoryAnnotationService,
    LegacyAuditReference,
    OperationKind,
    RevisionOrigin,
    SelfReviewPolicy,
)
from hc_data_platform.annotation.repository import InMemoryAnnotationRepository
from hc_data_platform.annotation.router import (
    get_annotation_auth,
    get_annotation_service,
    router,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.models import ManifestDiscoveryV1
from hc_data_platform.ingest.router import get_service as get_ingest_service
from hc_data_platform.security import AuthContext

ANNOTATOR_CAPABILITIES = (
    "annotation_task.read",
    "annotation_task.claim",
    "annotation.edit",
    "annotation.save",
    "annotation.submit",
    "data_schema.read",
)
REVIEWER_CAPABILITIES = ("annotation_task.read", "annotation.review")
PUBLISHER_CAPABILITIES = (
    "annotation_task.read",
    "dataset_version.publish",
    "data_schema.read",
    "data_schema.publish",
)


def auth(
    subject: str,
    *capabilities: str,
    projects: tuple[str, ...] = ("project-a",),
    regions: tuple[str, ...] = ("cn-hz",),
):
    return AuthContext(
        subject_id=subject,
        project_ids=frozenset(projects),
        region_codes=frozenset(regions),
        scope_pairs=frozenset(
            {(project_id, None) for project_id in projects}
            | {(project_id, region) for project_id in projects for region in regions}
        ),
        scoped_capabilities=frozenset(
            (project_id, capability) for project_id in projects for capability in capabilities
        ),
    )


@pytest.fixture
def api() -> Iterator[tuple[TestClient, dict[str, AuthContext], InMemoryAnnotationService]]:
    service = InMemoryAnnotationService(self_review_policy=SelfReviewPolicy.DENY)
    service.create_task(
        task_id="task-api",
        project_id="project-a",
        dataset_id="dataset-a",
        dataset_version=8,
        rollout_id="rollout-api",
        region_code="cn-hz",
        base_step_count=2_000,
    )
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(ProblemException)
    async def problem_handler(_: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.problem.status,
            content=exc.problem.model_dump(mode="json", exclude_none=True),
            media_type="application/problem+json",
        )

    current = {"auth": auth("alice", *ANNOTATOR_CAPABILITIES)}
    app.dependency_overrides[get_annotation_service] = lambda: service
    app.dependency_overrides[get_annotation_auth] = lambda: current["auth"]
    with TestClient(app) as client:
        yield client, current, service


def test_api_requires_verified_be02_context() -> None:
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(ProblemException)
    async def problem_handler(_: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.problem.status,
            content=exc.problem.model_dump(mode="json", exclude_none=True),
        )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/annotation-tasks/unknown")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_revision_thread_index_is_scoped_paginated_and_audited(
    api: tuple[TestClient, dict[str, AuthContext], InMemoryAnnotationService],
) -> None:
    client, current, service = api
    service.create_task(
        task_id="task-api-2",
        project_id="project-a",
        region_code="cn-hz",
        dataset_id="dataset-b",
        dataset_version=9,
        rollout_id="rollout-api-2",
        base_step_count=2_000,
    )
    service.create_task(
        task_id="task-other-region",
        project_id="project-a",
        region_code="us-east",
        dataset_id="dataset-c",
        dataset_version=10,
        rollout_id="rollout-other-region",
        base_step_count=2_000,
    )
    headers = {
        "X-Project-ID": "project-a",
        "X-Region-Code": "cn-hz",
        "X-Request-ID": "revision-index-request",
    }
    first = client.get("/api/v1/annotations/revisions?limit=1", headers=headers)
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    first_body = first.json()
    assert len(first_body["items"]) == 1
    assert first_body["items"][0]["region_code"] == "cn-hz"
    assert first_body["items"][0]["legacy_draft_id"] is None
    assert first_body["items"][0]["latest_revision"]["origin"] == "ANNOTATION"
    assert first_body["page_info"]["has_next_page"] is True
    assert first_body["page_info"]["end_cursor"]

    second = client.get(
        "/api/v1/annotations/revisions",
        params={"limit": 1, "after": first_body["page_info"]["end_cursor"]},
        headers=headers,
    )
    assert second.status_code == 200
    assert len(second.json()["items"]) == 1
    assert second.json()["items"][0]["task_id"] != first_body["items"][0]["task_id"]
    assert second.json()["page_info"]["has_previous_page"] is True
    assert second.json()["page_info"]["has_next_page"] is False

    repository = service._repository
    assert isinstance(repository, InMemoryAnnotationRepository)
    assert repository.revision_thread_list_audits == [
        {
            "project_id": "project-a",
            "region_code": "cn-hz",
            "actor_id": "alice",
            "request_id": "revision-index-request",
            "action": "annotation.revision_thread.listed",
            "status": None,
            "origin": None,
            "legacy_draft_filter": False,
            "limit": 1,
        },
        {
            "project_id": "project-a",
            "region_code": "cn-hz",
            "actor_id": "alice",
            "request_id": "revision-index-request",
            "action": "annotation.revision_thread.listed",
            "status": None,
            "origin": None,
            "legacy_draft_filter": False,
            "limit": 1,
        },
    ]

    mismatched_legacy_filter = client.get(
        "/api/v1/annotations/revisions",
        params={
            "after": first_body["page_info"]["end_cursor"],
            "legacy_draft_id": "draft_cursor_mismatch",
        },
        headers=headers,
    )
    assert mismatched_legacy_filter.status_code == 400
    assert mismatched_legacy_filter.json()["code"] == "INVALID_CURSOR"

    current["auth"] = auth("bob", *ANNOTATOR_CAPABILITIES)
    replayed_by_another_subject = client.get(
        "/api/v1/annotations/revisions",
        params={"after": first_body["page_info"]["end_cursor"]},
        headers=headers,
    )
    assert replayed_by_another_subject.status_code == 400
    assert replayed_by_another_subject.json()["code"] == "INVALID_CURSOR"

    current["auth"] = auth("mallory", *ANNOTATOR_CAPABILITIES, projects=("project-b",))
    denied = client.get("/api/v1/annotations/revisions", headers=headers)
    assert denied.status_code == 403


def test_revision_thread_exposes_only_the_task_scoped_legacy_draft_mapping(
    api: tuple[TestClient, dict[str, AuthContext], InMemoryAnnotationService],
) -> None:
    client, _current, service = api
    service.create_task(
        task_id="task-api-legacy",
        project_id="project-a",
        region_code="cn-hz",
        dataset_id="dataset-legacy",
        dataset_version=9,
        rollout_id="rollout-legacy",
        base_step_count=2_000,
    )
    importer = AnnotationActor(
        actor_id="legacy-cleaning-migration",
        capabilities=frozenset(
            {
                "annotation_task.read",
                "annotation_task.claim",
                "annotation_task.assign",
                "annotation.edit",
                "annotation.save",
                "annotation.submit",
            }
        ),
        project_ids=frozenset({"project-a"}),
    )
    claimed = service.claim("task-api-legacy", importer)
    first_import = service.save_draft(
        "task-api-legacy",
        importer,
        (
            AnnotationOperation(
                operation_id="legacy-exclude",
                kind=OperationKind.EXCLUDE,
                start_step=10,
                end_step=20,
                reason="imported legacy operation",
            ),
        ),
        expected_revision=0,
        if_match=claimed.etag,
        client_mutation_id="legacy-cleaning:draft_api_legacy:1",
        origin=RevisionOrigin.LEGACY_CLEANING,
        legacy_audit=LegacyAuditReference(
            draft_id="draft_api_legacy",
            source_revision=1,
            source_actor_id="legacy-author",
            source_created_at=claimed.created_at,
            source_payload={"draft_id": "draft_api_legacy", "revision": 1},
        ),
        imported_author_id="legacy-author",
        imported_created_at=claimed.created_at,
    )
    service.save_draft(
        "task-api-legacy",
        importer,
        (),
        expected_revision=first_import.revision,
        if_match=service.get_task("task-api-legacy").etag,
        client_mutation_id="legacy-cleaning:draft_api_legacy_newer:0",
        origin=RevisionOrigin.LEGACY_CLEANING,
        legacy_audit=LegacyAuditReference(
            draft_id="draft_api_legacy_newer",
            source_revision=0,
            source_actor_id="legacy-author",
            source_created_at=claimed.created_at,
            source_payload={"draft_id": "draft_api_legacy_newer", "revision": 0},
        ),
        imported_author_id="legacy-author",
        imported_created_at=claimed.created_at,
    )

    response = client.get(
        "/api/v1/annotations/revisions",
        params={
            "origin": "LEGACY_CLEANING",
            "legacy_draft_id": "draft_api_legacy",
        },
        headers={"X-Project-ID": "project-a", "X-Region-Code": "cn-hz"},
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["task_id"] == "task-api-legacy"
    assert items[0]["legacy_draft_id"] == "draft_api_legacy"
    assert items[0]["latest_revision"]["origin"] == "LEGACY_CLEANING"

    newer = client.get(
        "/api/v1/annotations/revisions",
        params={"legacy_draft_id": "draft_api_legacy_newer"},
        headers={"X-Project-ID": "project-a", "X-Region-Code": "cn-hz"},
    )
    assert newer.status_code == 200
    assert newer.json()["items"][0]["legacy_draft_id"] == "draft_api_legacy_newer"

    unmapped = client.get(
        "/api/v1/annotations/revisions",
        params={"legacy_draft_id": "draft_not_in_scope"},
        headers={"X-Project-ID": "project-a", "X-Region-Code": "cn-hz"},
    )
    assert unmapped.status_code == 200
    assert unmapped.json()["items"] == []

    invalid = client.get(
        "/api/v1/annotations/revisions",
        params={"legacy_draft_id": "not-a-draft"},
        headers={"X-Project-ID": "project-a", "X-Region-Code": "cn-hz"},
    )
    assert invalid.status_code == 422


def test_claim_save_replay_submit_review_publish_and_invalidate_api(
    api: tuple[TestClient, dict[str, AuthContext], InMemoryAnnotationService],
) -> None:
    client, current, service = api
    claimed = client.post("/api/v1/annotation-tasks/task-api/claim")
    assert claimed.status_code == 200
    claimed_etag = claimed.headers["etag"]

    save_body = {
        "expected_revision": 0,
        "client_mutation_id": "browser-save-1",
        "operations": [
            {
                "operation_id": "exclude-1",
                "kind": "EXCLUDE",
                "start_step": 300,
                "end_step": 450,
            }
        ],
    }
    saved = client.post(
        "/api/v1/annotation-tasks/task-api/revisions",
        headers={"If-Match": claimed_etag},
        json=save_body,
    )
    assert saved.status_code == 201
    assert saved.json()["client_mutation_id"] == "browser-save-1"
    saved_etag = saved.headers["etag"]

    replay = client.post(
        "/api/v1/annotation-tasks/task-api/revisions",
        headers={"If-Match": claimed_etag},
        json=save_body,
    )
    assert replay.status_code == 201
    assert replay.json() == saved.json()
    assert len(service.list_revisions("task-api")) == 2

    stale = client.post(
        "/api/v1/annotation-tasks/task-api/revisions",
        headers={"If-Match": claimed_etag},
        json={
            "expected_revision": 0,
            "client_mutation_id": "browser-save-2",
            "operations": [],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "ANNOTATION_REVISION_CONFLICT"

    submitted = client.post(
        "/api/v1/annotation-tasks/task-api/submit",
        headers={"If-Match": saved_etag, "Idempotency-Key": "submit-api-1"},
        json={"expected_revision": 1},
    )
    assert submitted.status_code == 201
    assert submitted.json()["revision"] == 1
    assert submitted.json()["task_id"] == "task-api"

    current["auth"] = auth("alice", *ANNOTATOR_CAPABILITIES, *REVIEWER_CAPABILITIES)
    self_review = client.post(
        "/api/v1/annotation-tasks/task-api/reviews",
        headers={"If-Match": submitted.headers["etag"]},
        json={"revision": 1, "decision": "APPROVE"},
    )
    assert self_review.status_code == 403

    current["auth"] = auth("bob", *REVIEWER_CAPABILITIES)
    reviewed = client.post(
        "/api/v1/annotation-tasks/task-api/reviews",
        headers={"If-Match": submitted.headers["etag"]},
        json={"revision": 1, "decision": "APPROVE", "comment": "ready"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["approved_revision"] == 1
    approved_etag = reviewed.headers["etag"]

    reviewer_read = client.get(
        "/api/v1/projects/project-a/rollouts/rollout-api/approved-annotation"
    )
    assert reviewer_read.status_code == 403
    current["auth"] = auth("pat", *PUBLISHER_CAPABILITIES)
    snapshot = client.get("/api/v1/projects/project-a/rollouts/rollout-api/approved-annotation")
    assert snapshot.status_code == 200
    assert snapshot.json()["annotation_revision"] == 1
    assert snapshot.json()["excluded_ranges"] == [
        {"start_step": 300, "end_step": 450, "modality_scope": "ALL_MODALITIES"}
    ]

    current["auth"] = auth("alice", *ANNOTATOR_CAPABILITIES)
    edited = client.post(
        "/api/v1/annotation-tasks/task-api/revisions",
        headers={"If-Match": approved_etag},
        json={
            "expected_revision": 1,
            "client_mutation_id": "browser-save-3",
            "operations": [
                {
                    "operation_id": "restore-1",
                    "kind": "RESTORE",
                    "start_step": 350,
                    "end_step": 400,
                }
            ],
        },
    )
    assert edited.status_code == 201
    current["auth"] = auth("pat", *PUBLISHER_CAPABILITIES)
    no_longer_approved = client.get(
        "/api/v1/projects/project-a/rollouts/rollout-api/approved-annotation"
    )
    assert no_longer_approved.status_code == 409
    assert len(service.list_reviews("task-api")) == 1


def test_restore_api_appends_a_scoped_immutable_data_revision(
    api: tuple[TestClient, dict[str, AuthContext], InMemoryAnnotationService],
) -> None:
    client, current, service = api
    claimed = client.post("/api/v1/annotation-tasks/task-api/claim")
    assert claimed.status_code == 200
    saved = client.post(
        "/api/v1/annotation-tasks/task-api/revisions",
        headers={"If-Match": claimed.headers["etag"]},
        json={
            "expected_revision": 0,
            "client_mutation_id": "restore-source",
            "operations": [
                {
                    "operation_id": "exclude-before-restore",
                    "kind": "EXCLUDE",
                    "start_step": 300,
                    "end_step": 450,
                }
            ],
        },
    )
    assert saved.status_code == 201

    restored = client.post(
        "/api/v1/annotation-tasks/task-api/revisions:restore",
        headers={"If-Match": saved.headers["etag"]},
        json={
            "target_revision": 0,
            "expected_revision": 1,
            "client_mutation_id": "restore-r0",
        },
    )
    assert restored.status_code == 201
    assert restored.headers["cache-control"] == "no-store"
    assert restored.json()["revision"] == 2
    assert restored.json()["parent_revision"] == 1
    assert restored.json()["origin"] == "ANNOTATION_RESTORE"
    assert restored.json()["operations"] == [
        {
            "schema_version": "1",
            "operation_id": "restore:restore-r0:restore:0",
            "kind": "RESTORE",
            "start_step": 300,
            "end_step": 450,
            "reason": "Restore immutable annotation revision 0",
            "modality_scope": "ALL_MODALITIES",
        }
    ]
    assert client.get("/api/v1/annotation-tasks/task-api/exclusions").json() == []

    replay = client.post(
        "/api/v1/annotation-tasks/task-api/revisions:restore",
        headers={"If-Match": saved.headers["etag"]},
        json={
            "target_revision": 0,
            "expected_revision": 1,
            "client_mutation_id": "restore-r0",
        },
    )
    assert replay.status_code == 201
    assert replay.json() == restored.json()

    submitted = client.post(
        "/api/v1/annotation-tasks/task-api/submit",
        headers={
            "If-Match": restored.headers["etag"],
            "Idempotency-Key": "submit-restored-r2",
        },
        json={"expected_revision": 2},
    )
    assert submitted.status_code == 201
    assert submitted.json()["revision"] == 2

    current["auth"] = auth("bob", *ANNOTATOR_CAPABILITIES)
    denied = client.post(
        "/api/v1/annotation-tasks/task-api/revisions:restore",
        headers={"If-Match": restored.headers["etag"]},
        json={
            "target_revision": 0,
            "expected_revision": 2,
            "client_mutation_id": "restore-without-assignment",
        },
    )
    assert denied.status_code == 403
    assert service.get_task("task-api").current_revision == 2


def test_api_denies_cross_project_and_reports_unconfigured_provider(
    api: tuple[TestClient, dict[str, AuthContext], InMemoryAnnotationService],
) -> None:
    client, current, _ = api
    current["auth"] = auth("mallory", *REVIEWER_CAPABILITIES, projects=("project-b",))
    cross_project = client.get("/api/v1/annotation-tasks/task-api")
    assert cross_project.status_code == 403

    current["auth"] = auth("uploader", "upload.read", "upload.manage")
    wrong_role = client.get("/api/v1/annotation-tasks/task-api")
    assert wrong_role.status_code == 403

    capability = client.get("/api/v1/capabilities/auto-annotation")
    assert capability.status_code == 200
    assert capability.json() == {
        "enabled": False,
        "code": "PROVIDER_UNAVAILABLE",
        "providers": [],
        "max_concurrent_jobs_per_project": 0,
        "max_jobs_per_hour": 0,
        "daily_cost_limit_micros": 0,
    }

    current["auth"] = auth("alice", *ANNOTATOR_CAPABILITIES)
    disabled = client.post(
        "/api/v1/annotation-tasks/task-api/auto-annotation",
        json={"revision": 0},
        headers={"Idempotency-Key": "provider-unavailable"},
    )
    assert disabled.status_code == 503
    assert disabled.json()["code"] == "AUTO_ANNOTATION_PROVIDER_UNAVAILABLE"


def test_task_manifest_discovery_is_task_bound_and_sanitized(
    api: tuple[TestClient, dict[str, AuthContext], InMemoryAnnotationService],
) -> None:
    client, _, _ = api

    class TaskBoundManifestReader:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        def get_manifest_discovery_for_rollout(
            self,
            *,
            project_id: str,
            region_code: str,
            rollout_id: str,
        ) -> ManifestDiscoveryV1:
            self.calls.append((project_id, region_code, rollout_id))
            return ManifestDiscoveryV1(cameras=(), topics=())

    reader = TaskBoundManifestReader()
    client.app.dependency_overrides[get_ingest_service] = lambda: reader
    path = "/api/v1/projects/project-a/regions/cn-hz/annotation-tasks/task-api/manifest-discovery"

    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "source": "MANIFEST",
        "read_only": True,
        "robot_id": None,
        "cameras": [],
        "topics": [],
        "missing_expected_topics": [],
    }
    assert reader.calls == [("project-a", "cn-hz", "rollout-api")]

    mismatched_path = path.replace("projects/project-a", "projects/project-b")
    mismatched = client.get(mismatched_path)
    assert mismatched.status_code == 404
    assert mismatched.json()["code"] == "ANNOTATION_TASK_NOT_FOUND"
    assert reader.calls == [("project-a", "cn-hz", "rollout-api")]


def test_annotation_success_responses_are_not_cacheable(
    api: tuple[TestClient, dict[str, AuthContext], InMemoryAnnotationService],
) -> None:
    client, current, _ = api

    def assert_no_store(response: HttpxResponse) -> None:
        assert response.headers["cache-control"] == "no-store"

    claimed = client.post("/api/v1/annotation-tasks/task-api/claim")
    assert claimed.status_code == 200
    assert_no_store(claimed)

    for path in (
        "/api/v1/capabilities/auto-annotation",
        "/api/v1/projects/project-a/tag-schemas/legacy-flat/versions",
        "/api/v1/projects/project-a/tag-schemas/legacy-flat/versions/1",
        "/api/v1/projects/project-a/annotation-tasks",
        "/api/v1/annotation-tasks/task-api",
        "/api/v1/annotation-tasks/task-api/draft",
        "/api/v1/annotation-tasks/task-api/current",
        "/api/v1/annotation-tasks/task-api/revisions",
        "/api/v1/annotation-tasks/task-api/revisions/0",
        "/api/v1/annotation-tasks/task-api/submissions",
        "/api/v1/annotation-tasks/task-api/reviews",
        "/api/v1/annotation-tasks/task-api/history",
        "/api/v1/annotation-tasks/task-api/exclusions",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert_no_store(response)

    saved = client.post(
        "/api/v1/annotation-tasks/task-api/revisions",
        headers={"If-Match": claimed.headers["etag"]},
        json={
            "expected_revision": 0,
            "client_mutation_id": "cache-save-1",
            "operations": [],
        },
    )
    assert saved.status_code == 201
    assert_no_store(saved)

    submitted = client.post(
        "/api/v1/annotation-tasks/task-api/submit",
        headers={
            "If-Match": saved.headers["etag"],
            "Idempotency-Key": "cache-submit-1",
        },
        json={"expected_revision": 1},
    )
    assert submitted.status_code == 201
    assert_no_store(submitted)
    submission_id = submitted.json()["submission_id"]

    submission = client.get(f"/api/v1/annotation-tasks/task-api/submissions/{submission_id}")
    assert submission.status_code == 200
    assert_no_store(submission)

    current["auth"] = auth("bob", *REVIEWER_CAPABILITIES)
    reviewed = client.post(
        "/api/v1/annotation-tasks/task-api/reviews",
        headers={"If-Match": submitted.headers["etag"]},
        json={
            "revision": 1,
            "submission_id": submission_id,
            "decision": "APPROVE",
        },
    )
    assert reviewed.status_code == 200
    assert_no_store(reviewed)

    current["auth"] = auth("publisher", *PUBLISHER_CAPABILITIES)
    approved = client.get("/api/v1/projects/project-a/rollouts/rollout-api/approved-annotation")
    assert approved.status_code == 200
    assert_no_store(approved)

    created = client.post(
        "/api/v1/projects/project-a/tag-schemas",
        json={
            "schema_id": "cache-schema",
            "version": 1,
            "name": "Cache header schema",
            "document": {
                "nodes": [],
                "mutual_exclusions": [],
                "object_relations": [],
            },
        },
    )
    assert created.status_code == 201
    assert_no_store(created)
    published = client.post(
        "/api/v1/projects/project-a/tag-schemas/cache-schema/versions/1/publish"
    )
    assert published.status_code == 200
    assert_no_store(published)
