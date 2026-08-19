from __future__ import annotations

import importlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from hc_data_platform.annotation import (
    AnnotationActor,
    AnnotationConflictError,
    AnnotationOperation,
    AnnotationService,
    OperationKind,
    PostgresAnnotationRepository,
    ReviewDecision,
    TagNodeDefinition,
    TagSchemaDocument,
    TagSchemaTarget,
)
from hc_data_platform.annotation.automation import (
    AutomaticAnnotationRequest,
    AutomaticAnnotationTaskService,
    PostgresAutomaticAnnotationRepository,
)
from hc_data_platform.annotation.postgres import DbApiConnection


@pytest.mark.integration
def test_postgres_repository_full_revision_and_approval_round_trip() -> None:
    dsn = os.environ.get("HC_ANNOTATION_TEST_POSTGRES_DSN")
    if dsn is None:
        pytest.skip("set HC_ANNOTATION_TEST_POSTGRES_DSN to run PostgreSQL integration")
    psycopg = importlib.import_module("psycopg")
    suffix = uuid4().hex[:12]
    project_id = f"annotation-it-{suffix}"
    region_code = "cn-test"
    first_task_id = f"task-{suffix}"
    concurrent_task_id = f"task-concurrent-{suffix}"

    migration = Path(__file__).parents[2] / "migrations" / "annotation" / "0003_automatic_tasks.sql"
    with cast(Any, psycopg).connect(dsn, autocommit=True) as migration_connection:
        migration_connection.execute(migration.read_text(encoding="utf-8"))

    def connect_for(selected_region: str) -> DbApiConnection:
        connection = cast(Any, psycopg).connect(dsn)
        connection.execute(
            """
            SELECT set_config('app.project_id', %s, false),
                   set_config('app.region_code', %s, false),
                   set_config('app.subject_id', 'annotation-postgres-integration', false),
                   set_config('app.request_id', %s, false)
            """,
            (project_id, selected_region, f"annotation-it-{suffix}"),
        )
        return cast(DbApiConnection, connection)

    def connect() -> DbApiConnection:
        return connect_for(region_code)

    service = AnnotationService(
        PostgresAnnotationRepository(connect), id_factory=lambda: f"review-{suffix}"
    )
    annotator = AnnotationActor(
        actor_id="alice",
        roles=frozenset({"annotator"}),
        project_ids=frozenset({project_id}),
    )
    reviewer = AnnotationActor(
        actor_id="bob",
        roles=frozenset({"reviewer"}),
        project_ids=frozenset({project_id}),
    )
    service.create_task(
        task_id=first_task_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id="dataset-postgres",
        dataset_version=4,
        rollout_id="rollout-postgres",
        base_step_count=2_000,
    )
    claimed = service.claim(first_task_id, annotator)
    operation = AnnotationOperation(
        operation_id="exclude-postgres",
        kind=OperationKind.EXCLUDE,
        start_step=300,
        end_step=450,
    )
    revision = service.save_draft(
        first_task_id,
        annotator,
        (operation,),
        expected_revision=0,
        if_match=claimed.etag,
        client_mutation_id="mutation-postgres",
    )
    assert (
        service.save_draft(
            first_task_id,
            annotator,
            (operation,),
            expected_revision=0,
            if_match=claimed.etag,
            client_mutation_id="mutation-postgres",
        )
        == revision
    )
    submitted = service.submit(
        first_task_id,
        annotator,
        expected_revision=1,
        if_match=service.get_task(first_task_id).etag,
    )
    approval = service.review(
        first_task_id,
        reviewer,
        ReviewDecision.APPROVE,
        revision=1,
        if_match=submitted.etag,
    )
    assert approval is not None
    assert approval.annotation_revision == 1
    assert [(item.start_step, item.end_step) for item in approval.excluded_ranges] == [(300, 450)]
    assert len(service.list_revisions(first_task_id)) == 2
    assert len(service.list_reviews(first_task_id)) == 1

    service.create_task(
        task_id=concurrent_task_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id="dataset-postgres",
        dataset_version=4,
        rollout_id="rollout-postgres-concurrent",
        base_step_count=2_000,
    )
    stale = service.claim(concurrent_task_id, annotator)
    administrator = AnnotationActor(
        actor_id="database-admin",
        roles=frozenset({"admin"}),
        project_ids=frozenset(),
    )

    def concurrent_save(identity: AnnotationActor, suffix: str) -> int:
        try:
            service.save_draft(
                concurrent_task_id,
                identity,
                (
                    AnnotationOperation(
                        operation_id=f"operation-{suffix}",
                        kind=OperationKind.EXCLUDE,
                        start_step=0,
                        end_step=10,
                    ),
                ),
                expected_revision=0,
                if_match=stale.etag,
                client_mutation_id=f"mutation-{suffix}",
            )
            return 201
        except AnnotationConflictError as exc:
            return exc.problem.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (
            pool.submit(concurrent_save, annotator, "annotator"),
            pool.submit(concurrent_save, administrator, "admin"),
        )
        assert sorted(future.result() for future in futures) == [201, 409]
    assert service.get_task(concurrent_task_id).current_revision == 1
    assert len(service.list_revisions(concurrent_task_id)) == 2

    schema = service.create_tag_schema_version(
        project_id=project_id,
        schema_id=f"tag-schema-{suffix}",
        version=1,
        name="automatic integration",
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
                region_code=region_code,
                dataset_id="dataset-auto",
                dataset_schema_snapshot_id="dataset-schema-auto",
            ),
        ),
        actor=AnnotationActor(
            actor_id="publisher",
            roles=frozenset({"publisher"}),
            project_ids=frozenset({project_id}),
        ),
    )
    service.publish_tag_schema_version(
        project_id=project_id,
        schema_id=schema.schema_id,
        version=schema.version,
        actor=AnnotationActor(
            actor_id="publisher",
            roles=frozenset({"publisher"}),
            project_ids=frozenset({project_id}),
        ),
    )
    automatic_repository = PostgresAutomaticAnnotationRepository(connect)
    automatic_service = AutomaticAnnotationTaskService(
        automatic_repository,
        clock=lambda: datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    automatic_request = AutomaticAnnotationRequest(
        project_id=project_id,
        region_code=region_code,
        rollout_id="rollout-auto",
        dataset_id="dataset-auto",
        dataset_version=11,
        lance_version=23,
        dataset_schema_snapshot_id="dataset-schema-auto",
        base_step_count=500,
        source_workflow_id=f"ingest-rollout:v1:{project_id}:cn-test%2Frollout-auto",
    )

    with ThreadPoolExecutor(max_workers=8) as pool:
        automatic_tasks = list(
            pool.map(lambda _: automatic_service.ensure_task(automatic_request), range(16))
        )
    assert {task.task_id for task in automatic_tasks} == {automatic_request.task_id}
    assert automatic_tasks[0].base_lance_version == 23
    assert automatic_tasks[0].base_step_count == 500
    assert automatic_tasks[0].tag_schema_id == schema.schema_id
    wrong_region_repository = PostgresAnnotationRepository(lambda: connect_for("other-region"))
    assert wrong_region_repository.get(automatic_request.task_id) is None
    with connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM annotation.annotation_task_triggers "
            "WHERE project_id = %s AND task_id = %s",
            (project_id, automatic_request.task_id),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM core.audit_events "
            "WHERE project_id = %s AND action = 'annotation.task.created'",
            (project_id,),
        ).fetchone() == (16,)
