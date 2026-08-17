"""Synchronous PostgreSQL adapter for the annotation repository port.

The adapter accepts a DB-API 2 connection factory (for example a psycopg pool's
``connection`` wrapper). The driver remains an application-composition choice, so
importing this module does not add a mandatory database dependency to unit tests.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, cast

from .models import (
    AnnotationMutationRecord,
    AnnotationOperation,
    AnnotationReview,
    AnnotationRevision,
    AnnotationStatus,
    AnnotationTask,
    OperationKind,
    ReviewDecision,
)
from .ports import AnnotationAggregate
from .repository import validate_aggregate


class DbApiCursor(Protocol):
    description: Sequence[Sequence[Any]] | None

    def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    def fetchone(self) -> object | None: ...

    def fetchall(self) -> Sequence[object]: ...

    def close(self) -> None: ...


class DbApiConnection(Protocol):
    def cursor(self) -> DbApiCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[], DbApiConnection]


_TASK_COLUMNS = """
task_id, project_id, dataset_id, dataset_version, rollout_id, assignee_id,
current_revision, state_version, status, submitted_revision, submitted_by,
approved_revision, approved_review_id, etag, created_at, updated_at
""".strip()


def _row(cursor: DbApiCursor, raw: object) -> dict[str, object]:
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    if cursor.description is None:
        raise RuntimeError("database cursor did not describe its result columns")
    values = cast(Sequence[object], raw)
    names = [str(column[0]) for column in cursor.description]
    return dict(zip(names, values, strict=True))


def _rows(cursor: DbApiCursor, values: Sequence[object]) -> tuple[dict[str, object], ...]:
    return tuple(_row(cursor, value) for value in values)


def _as_int(value: object) -> int:
    return int(cast(Any, value))


class PostgresAnnotationRepository:
    """PostgreSQL implementation with row locking and state-version CAS."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def create(self, aggregate: AnnotationAggregate) -> AnnotationAggregate:
        validate_aggregate(aggregate)
        if len(aggregate.revisions) != 1 or aggregate.task.current_revision != 0:
            raise ValueError("task creation requires only immutable revision zero")
        task = aggregate.task
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO annotation.annotation_tasks (
                    task_id, project_id, dataset_id, dataset_version, rollout_id,
                    assignee_id, current_revision, state_version, status,
                    submitted_revision, submitted_by, approved_revision,
                    approved_review_id, etag, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING task_id
                """,
                (
                    task.task_id,
                    task.project_id,
                    task.dataset_id,
                    task.dataset_version,
                    task.rollout_id,
                    task.assignee_id,
                    task.current_revision,
                    task.state_version,
                    task.status.value,
                    task.submitted_revision,
                    task.submitted_by,
                    task.approved_revision,
                    task.approved_review_id,
                    task.etag,
                    task.created_at,
                    task.updated_at,
                ),
            )
            inserted = cursor.fetchone() is not None
            if inserted:
                self._insert_revision(cursor, aggregate.revisions[0])
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

        existing = self.get(task.task_id)
        if existing is None:
            existing = self.find_by_rollout(project_id=task.project_id, rollout_id=task.rollout_id)
        if existing is None:
            raise RuntimeError("PostgreSQL did not return the created annotation task")
        return existing

    def get(self, task_id: str) -> AnnotationAggregate | None:
        return self._load_one(
            f"SELECT {_TASK_COLUMNS} FROM annotation.annotation_tasks WHERE task_id = %s",
            (task_id,),
        )

    def find_by_rollout(self, *, project_id: str, rollout_id: str) -> AnnotationAggregate | None:
        return self._load_one(
            f"""
            SELECT {_TASK_COLUMNS}
            FROM annotation.annotation_tasks
            WHERE project_id = %s AND rollout_id = %s
            """,
            (project_id, rollout_id),
        )

    def list_for_project(self, project_id: str) -> tuple[AnnotationAggregate, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT {_TASK_COLUMNS}
                FROM annotation.annotation_tasks
                WHERE project_id = %s
                ORDER BY updated_at DESC, task_id DESC
                """,
                (project_id,),
            )
            task_rows = _rows(cursor, cursor.fetchall())
        finally:
            cursor.close()
            connection.close()
        return tuple(self._load_related(self._task_from_row(row)) for row in task_rows)

    def compare_and_swap(
        self,
        aggregate: AnnotationAggregate,
        *,
        expected_state_version: int,
    ) -> bool:
        validate_aggregate(aggregate)
        task = aggregate.task
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT current_revision, state_version
                FROM annotation.annotation_tasks
                WHERE task_id = %s
                FOR UPDATE
                """,
                (task.task_id,),
            )
            locked_raw = cursor.fetchone()
            if locked_raw is None:
                connection.rollback()
                return False
            locked = _row(cursor, locked_raw)
            if _as_int(locked["state_version"]) != expected_state_version:
                connection.rollback()
                return False
            previous_revision = _as_int(locked["current_revision"])
            if task.state_version != expected_state_version + 1:
                raise ValueError("replacement must increment state_version exactly once")
            if task.current_revision not in {previous_revision, previous_revision + 1}:
                raise ValueError("a transaction may append at most one revision")

            cursor.execute(
                """
                UPDATE annotation.annotation_tasks
                SET assignee_id = %s,
                    current_revision = %s,
                    state_version = %s,
                    status = %s,
                    submitted_revision = %s,
                    submitted_by = %s,
                    approved_revision = %s,
                    approved_review_id = %s,
                    etag = %s,
                    updated_at = %s
                WHERE task_id = %s AND state_version = %s
                RETURNING task_id
                """,
                (
                    task.assignee_id,
                    task.current_revision,
                    task.state_version,
                    task.status.value,
                    task.submitted_revision,
                    task.submitted_by,
                    task.approved_revision,
                    task.approved_review_id,
                    task.etag,
                    task.updated_at,
                    task.task_id,
                    expected_state_version,
                ),
            )
            if cursor.fetchone() is None:
                connection.rollback()
                return False
            if task.current_revision == previous_revision + 1:
                self._insert_revision(cursor, aggregate.revisions[-1])
            self._insert_missing_reviews(cursor, aggregate.reviews)
            self._insert_missing_mutations(cursor, aggregate.mutations)
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def _load_one(self, query: str, params: Sequence[object]) -> AnnotationAggregate | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(query, params)
            raw = cursor.fetchone()
            task = None if raw is None else self._task_from_row(_row(cursor, raw))
        finally:
            cursor.close()
            connection.close()
        return None if task is None else self._load_related(task)

    def _load_related(self, task: AnnotationTask) -> AnnotationAggregate:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT task_id, revision, parent_revision, author_id,
                       client_mutation_id, created_at
                FROM annotation.annotation_revisions
                WHERE task_id = %s
                ORDER BY revision
                """,
                (task.task_id,),
            )
            revision_rows = _rows(cursor, cursor.fetchall())
            cursor.execute(
                """
                SELECT task_id, revision, operation_id, kind, start_step, end_step,
                       reason, modality_scope
                FROM annotation.annotation_operations
                WHERE task_id = %s
                ORDER BY revision, operation_sequence
                """,
                (task.task_id,),
            )
            operation_rows = _rows(cursor, cursor.fetchall())
            cursor.execute(
                """
                SELECT review_id, task_id, revision, reviewer_id, decision, comment, created_at
                FROM annotation.annotation_reviews
                WHERE task_id = %s
                ORDER BY created_at, review_id
                """,
                (task.task_id,),
            )
            review_rows = _rows(cursor, cursor.fetchall())
            cursor.execute(
                """
                SELECT task_id, client_mutation_id, actor_id, request_fingerprint,
                       expected_revision, request_etag, result_revision, result_etag, created_at
                FROM annotation.annotation_mutations
                WHERE task_id = %s
                ORDER BY created_at, client_mutation_id
                """,
                (task.task_id,),
            )
            mutation_rows = _rows(cursor, cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

        operations: dict[int, list[AnnotationOperation]] = {}
        for row in operation_rows:
            number = _as_int(row["revision"])
            operations.setdefault(number, []).append(
                AnnotationOperation(
                    operation_id=str(row["operation_id"]),
                    kind=OperationKind(str(row["kind"])),
                    start_step=_as_int(row["start_step"]),
                    end_step=_as_int(row["end_step"]),
                    reason=str(row["reason"]),
                    modality_scope="ALL_MODALITIES",
                )
            )
        revisions = tuple(
            AnnotationRevision(
                task_id=str(row["task_id"]),
                revision=_as_int(row["revision"]),
                parent_revision=(
                    None if row["parent_revision"] is None else _as_int(row["parent_revision"])
                ),
                author_id=str(row["author_id"]),
                client_mutation_id=str(row["client_mutation_id"]),
                operations=tuple(operations.get(_as_int(row["revision"]), ())),
                created_at=cast(datetime, row["created_at"]),
            )
            for row in revision_rows
        )
        reviews = tuple(
            AnnotationReview(
                review_id=str(row["review_id"]),
                task_id=str(row["task_id"]),
                revision=_as_int(row["revision"]),
                reviewer_id=str(row["reviewer_id"]),
                decision=ReviewDecision(str(row["decision"])),
                comment=str(row["comment"]),
                created_at=cast(datetime, row["created_at"]),
            )
            for row in review_rows
        )
        mutations = tuple(
            AnnotationMutationRecord(
                task_id=str(row["task_id"]),
                client_mutation_id=str(row["client_mutation_id"]),
                actor_id=str(row["actor_id"]),
                request_fingerprint=str(row["request_fingerprint"]),
                expected_revision=_as_int(row["expected_revision"]),
                request_etag=str(row["request_etag"]),
                result_revision=_as_int(row["result_revision"]),
                result_etag=str(row["result_etag"]),
                created_at=cast(datetime, row["created_at"]),
            )
            for row in mutation_rows
        )
        aggregate = AnnotationAggregate(
            task=task,
            revisions=revisions,
            reviews=reviews,
            mutations=mutations,
        )
        validate_aggregate(aggregate)
        return aggregate

    @staticmethod
    def _task_from_row(row: Mapping[str, object]) -> AnnotationTask:
        return AnnotationTask(
            task_id=str(row["task_id"]),
            project_id=str(row["project_id"]),
            dataset_id=str(row["dataset_id"]),
            dataset_version=_as_int(row["dataset_version"]),
            rollout_id=str(row["rollout_id"]),
            assignee_id=None if row["assignee_id"] is None else str(row["assignee_id"]),
            current_revision=_as_int(row["current_revision"]),
            state_version=_as_int(row["state_version"]),
            status=AnnotationStatus(str(row["status"])),
            submitted_revision=(
                None if row["submitted_revision"] is None else _as_int(row["submitted_revision"])
            ),
            submitted_by=(None if row["submitted_by"] is None else str(row["submitted_by"])),
            approved_revision=(
                None if row["approved_revision"] is None else _as_int(row["approved_revision"])
            ),
            approved_review_id=(
                None if row["approved_review_id"] is None else str(row["approved_review_id"])
            ),
            etag=str(row["etag"]),
            created_at=cast(datetime, row["created_at"]),
            updated_at=cast(datetime, row["updated_at"]),
        )

    @staticmethod
    def _insert_revision(cursor: DbApiCursor, revision: AnnotationRevision) -> None:
        cursor.execute(
            """
            INSERT INTO annotation.annotation_revisions (
                task_id, revision, parent_revision, author_id,
                client_mutation_id, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                revision.task_id,
                revision.revision,
                revision.parent_revision,
                revision.author_id,
                revision.client_mutation_id,
                revision.created_at,
            ),
        )
        for sequence, operation in enumerate(revision.operations):
            cursor.execute(
                """
                INSERT INTO annotation.annotation_operations (
                    task_id, revision, operation_id, operation_sequence, kind,
                    start_step, end_step, reason, modality_scope
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'ALL_MODALITIES')
                """,
                (
                    revision.task_id,
                    revision.revision,
                    operation.operation_id,
                    sequence,
                    operation.kind.value,
                    operation.start_step,
                    operation.end_step,
                    operation.reason,
                ),
            )

    @staticmethod
    def _insert_missing_reviews(cursor: DbApiCursor, reviews: Sequence[AnnotationReview]) -> None:
        for review in reviews:
            cursor.execute(
                """
                INSERT INTO annotation.annotation_reviews (
                    review_id, task_id, revision, reviewer_id, decision, comment, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (review_id) DO NOTHING
                """,
                (
                    review.review_id,
                    review.task_id,
                    review.revision,
                    review.reviewer_id,
                    review.decision.value,
                    review.comment,
                    review.created_at,
                ),
            )

    @staticmethod
    def _insert_missing_mutations(
        cursor: DbApiCursor, mutations: Sequence[AnnotationMutationRecord]
    ) -> None:
        for mutation in mutations:
            cursor.execute(
                """
                INSERT INTO annotation.annotation_mutations (
                    task_id, client_mutation_id, actor_id, request_fingerprint,
                    expected_revision, request_etag, result_revision, result_etag, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (task_id, client_mutation_id) DO NOTHING
                """,
                (
                    mutation.task_id,
                    mutation.client_mutation_id,
                    mutation.actor_id,
                    mutation.request_fingerprint,
                    mutation.expected_revision,
                    mutation.request_etag,
                    mutation.result_revision,
                    mutation.result_etag,
                    mutation.created_at,
                ),
            )
