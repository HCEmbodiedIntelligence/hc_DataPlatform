from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.annotation.automation import (
    AutomaticAnnotationBlocked,
    AutomaticAnnotationRequest,
    AutomaticAnnotationTaskService,
    InMemoryAutomaticAnnotationRepository,
)
from hc_data_platform.annotation.models import (
    AnnotationTaskCreationSource,
    TagNodeDefinition,
    TagSchemaDocument,
    TagSchemaStatus,
    TagSchemaTarget,
    TagSchemaVersion,
)
from hc_data_platform.annotation.router import get_annotation_service, router
from hc_data_platform.annotation.service import (
    AnnotationPermissionError,
    AnnotationService,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security import AuthContext


def _request(**changes: object) -> AutomaticAnnotationRequest:
    values: dict[str, object] = {
        "project_id": "project-a",
        "region_code": "cn-hz",
        "rollout_id": "rollout-1",
        "dataset_id": "dataset-1",
        "dataset_version": 7,
        "lance_version": 19,
        "dataset_schema_snapshot_id": "dataset-schema-3",
        "base_step_count": 4_096,
        "source_workflow_id": "ingest-rollout:v1:project-a:cn-hz%2Frollout-1",
    }
    values.update(changes)
    return AutomaticAnnotationRequest(**values)  # type: ignore[arg-type]


def _publish_compatible_schema(repository: InMemoryAutomaticAnnotationRepository) -> None:
    from hc_data_platform.annotation.validation import schema_content_hash

    draft = TagSchemaVersion(
        schema_id="schema-1",
        project_id="project-a",
        name="events",
        version=1,
        document=TagSchemaDocument(
            nodes=(
                TagNodeDefinition(
                    tag_id="event",
                    code="event",
                    display_name="Event",
                ),
            )
        ),
        compatible_targets=(
            TagSchemaTarget(
                region_code="cn-hz",
                dataset_id="dataset-1",
                dataset_schema_snapshot_id="dataset-schema-3",
            ),
        ),
        content_hash="a" * 64,
        created_by="schema-author",
    )
    draft = draft.model_copy(update={"content_hash": schema_content_hash(draft.document)})
    repository.create_tag_schema_version(draft)
    assert repository.publish_tag_schema_version(
        draft,
        draft.model_copy(
            update={
                "status": TagSchemaStatus.PUBLISHED,
                "published_by": "schema-publisher",
                "published_at": datetime(2026, 8, 18, tzinfo=timezone.utc),
            }
        ),
    )


def test_missing_schema_is_audited_as_retryable_and_never_selects_arbitrary_latest() -> None:
    repository = InMemoryAutomaticAnnotationRepository()
    service = AutomaticAnnotationTaskService(repository)

    unrelated = TagSchemaVersion(
        schema_id="unrelated",
        project_id="project-a",
        name="unrelated latest",
        version=1,
        document=TagSchemaDocument(nodes=()),
        content_hash="b" * 64,
        created_by="schema-author",
    )
    repository.create_tag_schema_version(unrelated)
    repository.publish_tag_schema_version(
        unrelated,
        unrelated.model_copy(
            update={
                "status": TagSchemaStatus.PUBLISHED,
                "published_by": "schema-publisher",
                "published_at": datetime(2026, 8, 18, tzinfo=timezone.utc),
            }
        ),
    )

    with pytest.raises(AutomaticAnnotationBlocked) as blocked:
        service.ensure_task(_request())
    assert blocked.value.code == "ANNOTATION_SCHEMA_BINDING_MISSING"
    trigger = repository.automatic_triggers[_request().trigger_id]
    assert trigger["status"] == "BLOCKED_RETRYABLE"
    assert trigger["error_code"] == "ANNOTATION_SCHEMA_BINDING_MISSING"
    assert repository.list_for_project("project-a") == ()
    assert repository.automatic_audit[-1]["action"] == "annotation.task.blocked"


def test_concurrent_replay_has_one_task_with_immutable_lance_and_schema_lineage() -> None:
    repository = InMemoryAutomaticAnnotationRepository()
    _publish_compatible_schema(repository)
    service = AutomaticAnnotationTaskService(repository)
    request = _request()

    with ThreadPoolExecutor(max_workers=16) as pool:
        tasks = list(pool.map(lambda _: service.ensure_task(request), range(64)))

    assert {task.task_id for task in tasks} == {request.task_id}
    assert len(repository.list_for_project(request.project_id)) == 1
    task = tasks[0]
    assert task.region_code == request.region_code
    assert task.dataset_version == request.dataset_version
    assert task.base_lance_version == request.lance_version
    assert task.base_step_count == request.base_step_count
    assert task.tag_schema_id == "schema-1"
    assert task.tag_schema_version == 1
    assert task.creation_source is AnnotationTaskCreationSource.SYSTEM_LANCE
    assert task.source_workflow_id == request.source_workflow_id
    assert (
        sum(event["action"] == "annotation.task.created" for event in repository.automatic_audit)
        == 1
    )


def test_slice_descriptions_are_saved_as_full_episode_tags_and_remain_editable() -> None:
    from hc_data_platform.annotation.models import AnnotationActor
    from hc_data_platform.annotation.validation import revision_content_hash, validate_tag_revision

    repository = InMemoryAutomaticAnnotationRepository()
    _publish_compatible_schema(repository)
    automatic = AutomaticAnnotationTaskService(repository)
    description = "将物体放入盒子后回到起始位置。" * 40
    request = _request(task_labels=(description, "保持物体直立", description))
    task = automatic.ensure_task(request)
    aggregate = repository.get(task.task_id)
    initial = aggregate.revisions[0]
    assert [tag.label for tag in initial.tags] == [description, "保持物体直立"]
    assert all((tag.start_step, tag.end_step) == (0, request.base_step_count) for tag in initial.tags)
    assert len({tag.annotation_id for tag in initial.tags}) == 2
    schema = repository.get_tag_schema_version(schema_id=task.tag_schema_id, version=task.tag_schema_version)
    validate_tag_revision(tags=initial.tags, operations=(), schema=schema, base_step_count=task.base_step_count)
    assert initial.content_hash == revision_content_hash(
        base_lance_version=request.lance_version, data_ref=None,
        tag_schema_id=task.tag_schema_id, tag_schema_version=task.tag_schema_version,
        tags=initial.tags, operations=(),
    )
    actor = AnnotationActor(actor_id="editor", project_ids=frozenset({request.project_id}),
                            capabilities=frozenset({"annotation_task.claim", "annotation_task.read", "annotation.save"}))
    service = AnnotationService(repository)
    claimed = service.claim(task.task_id, actor)
    saved = service.save_draft(task.task_id, actor, (), tags=(), expected_revision=0,
                              if_match=claimed.etag, client_mutation_id="clear-defaults")
    assert saved.tags == ()
    automatic.ensure_task(request)
    assert service.get_draft(task.task_id, actor).tags == ()
    assert repository.get(task.task_id).revisions[0].tags == initial.tags


def _scoped_auth(
    *,
    project_id: str = "project-a",
    region_code: str = "cn-hz",
    capabilities: frozenset[str] = frozenset({"annotation_task.read"}),
) -> AuthContext:
    return AuthContext(
        subject_id="annotator",
        project_ids=frozenset({project_id}),
        region_codes=frozenset({region_code}),
        scope_pairs=frozenset({(project_id, region_code)}),
        scoped_capabilities=frozenset((project_id, capability) for capability in capabilities),
    )


def test_annotation_service_denies_known_task_without_exact_capability_or_scope() -> None:
    repository = InMemoryAutomaticAnnotationRepository()
    service = AnnotationService(repository)
    service.create_task(
        task_id="known-task-id",
        project_id="project-a",
        region_code="cn-hz",
        dataset_id="dataset-1",
        dataset_version=1,
        rollout_id="rollout-1",
        base_step_count=10,
    )

    assert service.get_task("known-task-id", _scoped_auth()).task_id == "known-task-id"
    with pytest.raises(AnnotationPermissionError):
        service.get_task(
            "known-task-id",
            _scoped_auth(capabilities=frozenset()),
        )
    with pytest.raises(AnnotationPermissionError):
        service.get_task(
            "known-task-id",
            _scoped_auth(region_code="us-west"),
        )
    with pytest.raises(AnnotationPermissionError):
        service.get_task(
            "known-task-id",
            _scoped_auth(project_id="project-b"),
        )


def test_annotation_router_requires_exact_project_region_and_capability() -> None:
    service = AnnotationService(InMemoryAutomaticAnnotationRepository())
    service.create_task(
        task_id="router-task",
        project_id="project-a",
        region_code="cn-hz",
        dataset_id="dataset-1",
        dataset_version=1,
        rollout_id="rollout-1",
        base_step_count=10,
    )
    app = FastAPI()
    app.include_router(router)
    current = {"auth": _scoped_auth()}

    @app.middleware("http")
    async def install_auth(request: Request, call_next):
        request.state.auth_context = current["auth"]
        return await call_next(request)

    @app.exception_handler(ProblemException)
    async def problem_handler(_: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.problem.status,
            content=exc.problem.model_dump(mode="json", exclude_none=True),
        )

    app.dependency_overrides[get_annotation_service] = lambda: service
    with TestClient(app) as client:
        missing_region = client.get(
            "/api/v1/annotation-tasks/router-task",
            headers={"X-Project-ID": "project-a"},
        )
        assert missing_region.status_code == 400
        assert missing_region.json()["code"] == "REGION_SCOPE_REQUIRED"

        wrong_region = client.get(
            "/api/v1/annotation-tasks/router-task",
            headers={"X-Project-ID": "project-a", "X-Region-Code": "us-west"},
        )
        assert wrong_region.status_code == 403
        assert wrong_region.json()["code"] == "REGION_SCOPE_DENIED"

        current["auth"] = _scoped_auth(capabilities=frozenset())
        missing_capability = client.get(
            "/api/v1/annotation-tasks/router-task",
            headers={"X-Project-ID": "project-a", "X-Region-Code": "cn-hz"},
        )
        assert missing_capability.status_code == 403
        assert missing_capability.json()["code"] == "ANNOTATION_FORBIDDEN"

        current["auth"] = _scoped_auth()
        allowed = client.get(
            "/api/v1/annotation-tasks/router-task",
            headers={"X-Project-ID": "project-a", "X-Region-Code": "cn-hz"},
        )
        assert allowed.status_code == 200
        assert allowed.json()["task_id"] == "router-task"
