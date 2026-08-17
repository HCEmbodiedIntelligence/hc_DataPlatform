from __future__ import annotations

import importlib
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

import pytest

from hc_data_platform.annotation import (
    AnnotationActor,
    AnnotationConflictError,
    AnnotationOperation,
    AnnotationService,
    OperationKind,
    PostgresAnnotationRepository,
    ReviewDecision,
)
from hc_data_platform.annotation.postgres import DbApiConnection


@pytest.mark.integration
def test_postgres_repository_full_revision_and_approval_round_trip() -> None:
    dsn = os.environ.get("HC_ANNOTATION_TEST_POSTGRES_DSN")
    if dsn is None:
        pytest.skip("set HC_ANNOTATION_TEST_POSTGRES_DSN to run PostgreSQL integration")
    psycopg = importlib.import_module("psycopg")

    def connect() -> DbApiConnection:
        return cast(DbApiConnection, cast(Any, psycopg).connect(dsn))

    service = AnnotationService(
        PostgresAnnotationRepository(connect), id_factory=lambda: "review-postgres-1"
    )
    annotator = AnnotationActor(
        actor_id="alice",
        roles=frozenset({"annotator"}),
        project_ids=frozenset({"project-postgres"}),
    )
    reviewer = AnnotationActor(
        actor_id="bob",
        roles=frozenset({"reviewer"}),
        project_ids=frozenset({"project-postgres"}),
    )
    service.create_task(
        task_id="task-postgres",
        project_id="project-postgres",
        dataset_id="dataset-postgres",
        dataset_version=4,
        rollout_id="rollout-postgres",
    )
    claimed = service.claim("task-postgres", annotator)
    operation = AnnotationOperation(
        operation_id="exclude-postgres",
        kind=OperationKind.EXCLUDE,
        start_step=300,
        end_step=450,
    )
    revision = service.save_draft(
        "task-postgres",
        annotator,
        (operation,),
        expected_revision=0,
        if_match=claimed.etag,
        client_mutation_id="mutation-postgres",
    )
    assert (
        service.save_draft(
            "task-postgres",
            annotator,
            (operation,),
            expected_revision=0,
            if_match=claimed.etag,
            client_mutation_id="mutation-postgres",
        )
        == revision
    )
    submitted = service.submit(
        "task-postgres",
        annotator,
        expected_revision=1,
        if_match=service.get_task("task-postgres").etag,
    )
    approval = service.review(
        "task-postgres",
        reviewer,
        ReviewDecision.APPROVE,
        revision=1,
        if_match=submitted.etag,
    )
    assert approval is not None
    assert approval.annotation_revision == 1
    assert [(item.start_step, item.end_step) for item in approval.excluded_ranges] == [(300, 450)]
    assert len(service.list_revisions("task-postgres")) == 2
    assert len(service.list_reviews("task-postgres")) == 1

    service.create_task(
        task_id="task-postgres-concurrent",
        project_id="project-postgres",
        dataset_id="dataset-postgres",
        dataset_version=4,
        rollout_id="rollout-postgres-concurrent",
    )
    stale = service.claim("task-postgres-concurrent", annotator)
    administrator = AnnotationActor(
        actor_id="database-admin",
        roles=frozenset({"admin"}),
        project_ids=frozenset(),
    )

    def concurrent_save(identity: AnnotationActor, suffix: str) -> int:
        try:
            service.save_draft(
                "task-postgres-concurrent",
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
        assert sorted(future.result() for future in futures) == [201, 412]
    assert service.get_task("task-postgres-concurrent").current_revision == 1
    assert len(service.list_revisions("task-postgres-concurrent")) == 2
