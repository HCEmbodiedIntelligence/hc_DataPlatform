from __future__ import annotations

import asyncio
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
    RevisionOrigin,
    TagNodeDefinition,
    TagSchemaDocument,
    TagSchemaTarget,
)
from hc_data_platform.annotation.auto_jobs import (
    AutoAnnotationInputSelection,
    AutoAnnotationJobService,
    AutoAnnotationOutboxHandler,
    AutoAnnotationProviderResult,
    AutoAnnotationUsage,
    DeterministicAutoAnnotationProvider,
    PostgresAutoAnnotationJobRepository,
)
from hc_data_platform.annotation.automation import (
    AutomaticAnnotationRequest,
    AutomaticAnnotationTaskService,
    PostgresAutomaticAnnotationRepository,
)
from hc_data_platform.annotation.postgres import DbApiConnection
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.outbox import OutboxDispatcher, PostgresOutboxDeliveryRepository


@pytest.mark.integration
def test_postgres_repository_full_revision_and_approval_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn = os.environ.get("HC_ANNOTATION_TEST_POSTGRES_DSN")
    if dsn is None:
        pytest.skip("set HC_ANNOTATION_TEST_POSTGRES_DSN to run PostgreSQL integration")
    psycopg = importlib.import_module("psycopg")
    suffix = uuid4().hex[:12]
    project_id = f"annotation-it-{suffix}"
    organization_id = f"annotation-it-org-{suffix}"
    region_code = "cn-test"
    first_task_id = f"task-{suffix}"
    concurrent_task_id = f"task-concurrent-{suffix}"
    restore_task_id = f"task-restore-{suffix}"

    migrations = (
        Path(__file__).parents[2] / "migrations" / "annotation" / "0003_automatic_tasks.sql",
        Path(__file__).parents[2]
        / "migrations"
        / "annotation"
        / "0007_annotation_restore_origin.sql",
    )
    with cast(Any, psycopg).connect(dsn, autocommit=True) as migration_connection:
        for migration in migrations:
            migration_connection.execute(migration.read_text(encoding="utf-8"))
        migration_connection.execute(
            "INSERT INTO registry.organization_projects (organization_id, project_id) "
            "VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (organization_id, project_id),
        )

    def connect_for(selected_region: str) -> DbApiConnection:
        connection = cast(Any, psycopg).connect(dsn)
        connection.execute(
            """
            SELECT set_config('app.project_id', %s, false),
                   set_config('app.organization_id', %s, false),
                   set_config('app.region_code', %s, false),
                   set_config('app.subject_id', 'annotation-postgres-integration', false),
                   set_config('app.request_id', %s, false)
            """,
            (project_id, organization_id, selected_region, f"annotation-it-{suffix}"),
        )
        return cast(DbApiConnection, connection)

    def connect() -> DbApiConnection:
        return connect_for(region_code)

    generated_ids = iter(f"annotation-{suffix}-{index}" for index in range(20))
    service = AnnotationService(
        PostgresAnnotationRepository(connect), id_factory=lambda: next(generated_ids)
    )
    annotator = AnnotationActor(
        actor_id="alice",
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
        project_ids=frozenset({project_id}),
    )
    reviewer = AnnotationActor(
        actor_id="bob",
        capabilities=frozenset({"annotation_task.read", "annotation.review"}),
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
        task_id=restore_task_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id="dataset-postgres",
        dataset_version=4,
        rollout_id="rollout-postgres-restore",
        base_step_count=2_000,
    )
    restore_claim = service.claim(restore_task_id, annotator)
    service.save_draft(
        restore_task_id,
        annotator,
        (
            AnnotationOperation(
                operation_id="exclude-before-version-restore",
                kind=OperationKind.EXCLUDE,
                start_step=300,
                end_step=450,
            ),
        ),
        expected_revision=0,
        if_match=restore_claim.etag,
        client_mutation_id="restore-source-postgres",
    )
    restored = service.restore_revision(
        restore_task_id,
        annotator,
        target_revision=0,
        expected_revision=1,
        if_match=service.get_task(restore_task_id).etag,
        client_mutation_id="restore-r0-postgres",
    )
    assert restored.origin is RevisionOrigin.ANNOTATION_RESTORE
    assert service.get_revision(restore_task_id, 1).origin is RevisionOrigin.ANNOTATION
    assert service.effective_exclusions(restore_task_id, revision=2) == ()
    restored_submission = service.submit_for_review(
        restore_task_id,
        annotator,
        expected_revision=2,
        if_match=service.get_task(restore_task_id).etag,
        idempotency_key="submit-restored-r2-postgres",
    )
    assert restored_submission.revision == 2
    assert restored_submission.episode_version == 1
    assert (
        service.review(
            restore_task_id,
            reviewer,
            ReviewDecision.REJECT,
            revision=2,
            submission_id=restored_submission.submission_id,
            if_match=service.get_task(restore_task_id).etag,
            comment="fixed revision still violates the review policy",
        )
        is None
    )
    assert service.get_task(restore_task_id).status.value == "REJECTED"
    revised_after_reject = service.save_draft(
        restore_task_id,
        annotator,
        (
            AnnotationOperation(
                operation_id="exclude-after-reject",
                kind=OperationKind.EXCLUDE,
                start_step=50,
                end_step=60,
            ),
        ),
        expected_revision=2,
        if_match=service.get_task(restore_task_id).etag,
        client_mutation_id="revise-after-reject-postgres",
    )
    resubmitted_after_reject = service.submit_for_review(
        restore_task_id,
        annotator,
        expected_revision=revised_after_reject.revision,
        if_match=service.get_task(restore_task_id).etag,
        idempotency_key="resubmit-after-reject-postgres",
    )
    assert resubmitted_after_reject.episode_version == 2
    assert (
        service.review(
            restore_task_id,
            reviewer,
            ReviewDecision.NEEDS_REVISION,
            revision=revised_after_reject.revision,
            submission_id=resubmitted_after_reject.submission_id,
            if_match=service.get_task(restore_task_id).etag,
            comment="adjust the remaining exclusion boundary",
        )
        is None
    )
    assert service.get_task(restore_task_id).status.value == "NEEDS_REVISION"
    with connect() as connection:
        assert connection.execute(
            "SELECT origin FROM annotation.annotation_revisions "
            "WHERE task_id = %s AND revision = 2",
            (restore_task_id,),
        ).fetchone() == ("ANNOTATION_RESTORE",)

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
        capabilities=frozenset({"annotation.save", "annotation_task.assign"}),
        project_ids=frozenset({project_id}),
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
    with connect() as connection:
        transition_audits = connection.execute(
            """
            SELECT action, count(*)
            FROM core.audit_events
            WHERE organization_id = %s
              AND project_id = %s
              AND action IN (
                  'annotation.task.claimed',
                  'annotation.revision.created',
                  'annotation.revision.restored',
                  'annotation.submission.created',
                  'annotation.review.approved',
                  'annotation.review.rejected',
                  'annotation.review.needs_revision'
              )
            GROUP BY action
            ORDER BY action
            """,
            (organization_id, project_id),
        ).fetchall()
        assert transition_audits == [
            ("annotation.review.approved", 1),
            ("annotation.review.needs_revision", 1),
            ("annotation.review.rejected", 1),
            ("annotation.revision.created", 4),
            ("annotation.revision.restored", 1),
            ("annotation.submission.created", 3),
            ("annotation.task.claimed", 3),
        ]
        rejected_details = connection.execute(
            """
            SELECT details
            FROM core.audit_events
            WHERE organization_id = %s
              AND project_id = %s
              AND action = 'annotation.review.rejected'
            """,
            (organization_id, project_id),
        ).fetchone()[0]
        assert rejected_details == {
            "decision": "REJECT",
            "revision": 2,
            "submission_id": restored_submission.submission_id,
        }
        assert "comment" not in rejected_details

    audit_failure_task_id = f"task-audit-failure-{suffix}"
    service.create_task(
        task_id=audit_failure_task_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id="dataset-postgres",
        dataset_version=4,
        rollout_id="rollout-postgres-audit-failure",
        base_step_count=2_000,
    )

    def reject_audit(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("injected annotation audit failure")

    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(
            PostgresAnnotationRepository,
            "_insert_transition_audit",
            staticmethod(reject_audit),
        )
        with pytest.raises(RuntimeError, match="injected annotation audit failure"):
            service.claim(audit_failure_task_id, annotator)
    unchanged_after_audit_failure = service.get_task(audit_failure_task_id)
    assert unchanged_after_audit_failure.assignee_id is None
    assert unchanged_after_audit_failure.state_version == 0

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
            capabilities=frozenset({"dataset_version.publish", "data_schema.publish"}),
            project_ids=frozenset({project_id}),
        ),
    )
    service.publish_tag_schema_version(
        project_id=project_id,
        schema_id=schema.schema_id,
        version=schema.version,
        actor=AnnotationActor(
            actor_id="publisher",
            capabilities=frozenset({"dataset_version.publish", "data_schema.publish"}),
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
            "SELECT organization_id, count(*) FROM core.audit_events "
            "WHERE project_id = %s AND action = 'annotation.task.created' "
            "GROUP BY organization_id",
            (project_id,),
        ).fetchone() == (organization_id, 16)


@pytest.mark.integration
def test_postgres_revision_thread_index_is_bounded_scoped_and_audited() -> None:
    dsn = os.environ.get("HC_ANNOTATION_TEST_POSTGRES_DSN")
    if dsn is None:
        pytest.skip("set HC_ANNOTATION_TEST_POSTGRES_DSN to run PostgreSQL integration")
    psycopg = importlib.import_module("psycopg")
    suffix = uuid4().hex[:12]
    project_id = f"annotation-thread-{suffix}"
    organization_id = f"annotation-thread-org-{suffix}"
    region_code = "cn-test"

    def connect_for(selected_region: str) -> DbApiConnection:
        connection = cast(Any, psycopg).connect(dsn)
        connection.execute(
            """
            SELECT set_config('app.project_id', %s, false),
                   set_config('app.organization_id', %s, false),
                   set_config('app.region_code', %s, false),
                   set_config('app.subject_id', 'annotation-thread-reader', false),
                   set_config('app.request_id', %s, false)
            """,
            (project_id, organization_id, selected_region, f"annotation-thread-{suffix}"),
        )
        return cast(DbApiConnection, connection)

    with cast(Any, psycopg).connect(dsn, autocommit=True) as registry_connection:
        registry_connection.execute(
            "INSERT INTO registry.organization_projects (organization_id, project_id) "
            "VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (organization_id, project_id),
        )

    service = AnnotationService(
        PostgresAnnotationRepository(lambda: connect_for(region_code)),
        cursor_secret="annotation-postgres-thread-cursor-secret",
    )
    reader = AnnotationActor(
        actor_id="reader",
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
        project_ids=frozenset({project_id}),
    )
    for task_id in (f"thread-a-{suffix}", f"thread-b-{suffix}"):
        service.create_task(
            task_id=task_id,
            project_id=project_id,
            region_code=region_code,
            dataset_id=f"dataset-{task_id}",
            dataset_version=1,
            rollout_id=f"rollout-{task_id}",
            base_step_count=100,
        )
    other_region_service = AnnotationService(
        PostgresAnnotationRepository(lambda: connect_for("us-test")),
        cursor_secret="annotation-postgres-thread-cursor-secret",
    )
    other_region_service.create_task(
        task_id=f"thread-outside-{suffix}",
        project_id=project_id,
        region_code="us-test",
        dataset_id=f"dataset-thread-outside-{suffix}",
        dataset_version=1,
        rollout_id=f"rollout-thread-outside-{suffix}",
        base_step_count=100,
    )

    first = service.list_revision_threads(
        project_id=project_id,
        region_code=region_code,
        actor=reader,
        request_id=f"thread-list-{suffix}",
        limit=1,
    )
    assert len(first.items) == 1
    assert first.items[0].region_code == region_code
    assert first.page_info.has_next_page is True
    assert first.page_info.end_cursor is not None
    second = service.list_revision_threads(
        project_id=project_id,
        region_code=region_code,
        actor=reader,
        request_id=f"thread-list-{suffix}",
        after=first.page_info.end_cursor,
        limit=1,
    )
    assert len(second.items) == 1
    assert second.items[0].task_id != first.items[0].task_id
    assert second.page_info.has_next_page is False

    with connect_for(region_code) as connection:
        events = connection.execute(
            """
            SELECT actor_id, action, region_code, resource_type, details
            FROM core.audit_events
            WHERE project_id = %s AND request_id = %s
            ORDER BY occurred_at, audit_id
            """,
            (project_id, f"thread-list-{suffix}"),
        ).fetchall()
    assert len(events) == 2
    assert all(
        event[:4]
        == (
            "reader",
            "annotation.revision_thread.listed",
            region_code,
            "annotation_revision_thread",
        )
        for event in events
    )
    assert all(
        event[4]
        == {
            "limit": 1,
            "origin": None,
            "status": None,
        }
        for event in events
    )


@pytest.mark.integration
def test_postgres_auto_annotation_quota_reservation_is_atomic() -> None:
    dsn = os.environ.get("HC_ANNOTATION_TEST_POSTGRES_DSN")
    if dsn is None:
        pytest.skip("set HC_ANNOTATION_TEST_POSTGRES_DSN to run PostgreSQL integration")
    psycopg = importlib.import_module("psycopg")
    suffix = uuid4().hex[:12]
    project_id = f"annotation-auto-quota-{suffix}"
    organization_id = f"annotation-auto-org-{suffix}"
    region_code = "cn-test"
    task_id = f"auto-quota-task-{suffix}"
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)

    migration = (
        Path(__file__).parents[2] / "migrations" / "annotation" / "0006_auto_annotation_jobs.sql"
    )
    with cast(Any, psycopg).connect(dsn, autocommit=True) as migration_connection:
        migration_connection.execute(migration.read_text(encoding="utf-8"))
        migration_connection.execute(
            "INSERT INTO registry.organization_projects (organization_id, project_id) "
            "VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (organization_id, project_id),
        )

    def connect() -> DbApiConnection:
        connection = cast(Any, psycopg).connect(dsn)
        connection.execute(
            """
            SELECT set_config('app.project_id', %s, false),
                   set_config('app.organization_id', %s, false),
                   set_config('app.region_code', %s, false),
                   set_config('app.subject_id', 'annotation-auto-quota', false),
                   set_config('app.request_id', %s, false)
            """,
            (project_id, organization_id, region_code, f"auto-quota-{suffix}"),
        )
        return cast(DbApiConnection, connection)

    auth = AuthContext(
        subject_id="annotation-auto-quota",
        project_ids=frozenset({project_id}),
        region_codes=frozenset({region_code}),
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
        scope_pairs=frozenset({(project_id, region_code)}),
    )
    annotation = AnnotationService(PostgresAnnotationRepository(connect), clock=lambda: now)
    annotation.create_task(
        task_id=task_id,
        project_id=project_id,
        region_code=region_code,
        dataset_id=f"dataset-{suffix}",
        dataset_version=1,
        rollout_id=f"rollout-{suffix}",
        base_step_count=100,
    )
    annotation.claim(task_id, auth)
    provider = DeterministicAutoAnnotationProvider(
        AutoAnnotationProviderResult(
            usage=AutoAnnotationUsage(input_units=100, output_units=1, cost_micros=250)
        )
    )
    jobs = AutoAnnotationJobService(
        annotation,
        PostgresAutoAnnotationJobRepository(connect),
        (provider,),
        max_concurrent_jobs_per_project=1,
        clock=lambda: now,
    )

    def create(index: int) -> tuple[int, str]:
        try:
            job = jobs.create(
                auth=auth,
                task_id=task_id,
                source_revision=0,
                provider_name="deterministic-fake",
                model="fake-v1",
                input_selection=AutoAnnotationInputSelection(),
                idempotency_key=f"quota-{index}",
                request_id=f"quota-{index}",
            )
            return 202, job.job_id
        except ProblemException as exc:
            return exc.problem.status, exc.problem.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(create, range(2)))

    assert sorted(status for status, _ in outcomes) == [202, 429]
    assert any(value == "AUTO_ANNOTATION_CONCURRENCY_LIMIT" for _, value in outcomes)
    job_id = next(value for status, value in outcomes if status == 202)
    dispatcher = OutboxDispatcher(
        PostgresOutboxDeliveryRepository(connect),
        {AutoAnnotationOutboxHandler.EVENT_TYPE: AutoAnnotationOutboxHandler(jobs)},
        worker_id=f"annotation-auto-quota-{suffix}",
    )
    assert asyncio.run(
        dispatcher.dispatch_one(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
    )
    assert jobs.get(auth=auth, task_id=task_id, job_id=job_id).status == "SUCCEEDED"
    connection = connect()
    try:
        assert connection.execute(
            """SELECT count(*) FROM annotation.auto_annotation_jobs
                 WHERE project_id=%s AND region_code=%s""",
            (project_id, region_code),
        ).fetchone() == (1,)
        assert connection.execute(
            """SELECT count(*) FROM core.outbox_events
                 WHERE organization_id=%s AND project_id=%s AND region_code=%s
                   AND event_type='annotation.auto_annotation.requested.v1'
                   AND published_at IS NOT NULL""",
            (organization_id, project_id, region_code),
        ).fetchone() == (1,)
        assert set(
            connection.execute(
                """SELECT organization_id, action FROM core.audit_events
                     WHERE project_id=%s AND region_code=%s
                       AND resource_type='AUTO_ANNOTATION_JOB' AND resource_id=%s
                       AND action LIKE 'annotation.auto_job.%%'""",
                (project_id, region_code, job_id),
            ).fetchall()
        ) == {
            (organization_id, "annotation.auto_job.queued"),
            (organization_id, "annotation.auto_job.completed"),
        }
    finally:
        connection.close()
