"""Synchronous PostgreSQL adapter for the annotation repository port.

The adapter accepts a DB-API 2 connection factory (for example a psycopg pool's
``connection`` wrapper). The driver remains an application-composition choice, so
importing this module does not add a mandatory database dependency to unit tests.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import uuid4

from .models import (
    AnnotationMutationRecord,
    AnnotationOperation,
    AnnotationReview,
    AnnotationReviewCheck,
    AnnotationRevision,
    AnnotationRevisionThread,
    AnnotationRevisionThreadRevision,
    AnnotationStatus,
    AnnotationSubmission,
    AnnotationSubmissionMutationRecord,
    AnnotationTag,
    AnnotationTask,
    AnnotationTaskCreationSource,
    AnnotationTaskKind,
    OperationKind,
    ReviewCheckKind,
    ReviewDecision,
    RevisionOrigin,
    TagSchemaDocument,
    TagSchemaStatus,
    TagSchemaTarget,
    TagSchemaVersion,
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
task_id, project_id, region_code, dataset_id, dataset_version, rollout_id, assignee_id,
base_lance_version, base_step_count, tag_schema_id, tag_schema_version,
task_kind, creation_source, source_workflow_id,
current_revision, state_version, status, submitted_revision, submitted_by,
current_submission_id,
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


def _as_json(value: object) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def require_repository_scope(
    cursor: DbApiCursor,
    *,
    project_id: str,
    region_code: str | None,
) -> None:
    """Reject writes when the selected DB scope is not the target scope.

    RLS remains the database backstop, but an owner/superuser connection can bypass
    RLS.  Repository writes therefore verify the selected scope explicitly too.
    Project-wide Schema rows intentionally omit ``region_code``; task rows do not.
    """

    cursor.execute(
        """
        SELECT NULLIF(current_setting('app.project_id', true), '') = %s::text
           AND (
               %s::text IS NULL
               OR NULLIF(current_setting('app.region_code', true), '') = %s::text
           )
        """,
        (project_id, region_code, region_code),
    )
    raw = cursor.fetchone()
    if isinstance(raw, Mapping):
        allowed = bool(next(iter(raw.values()), False))
    else:
        allowed = raw is not None and bool(tuple(cast(Sequence[object], raw))[0])
    if not allowed:
        raise PermissionError("annotation repository scope mismatch")


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
            require_repository_scope(
                cursor,
                project_id=task.project_id,
                region_code=task.region_code,
            )
            cursor.execute(
                """
                INSERT INTO annotation.annotation_tasks (
                    task_id, project_id, region_code, dataset_id, dataset_version, rollout_id,
                    assignee_id, base_lance_version, base_step_count,
                    tag_schema_id, tag_schema_version,
                    task_kind, creation_source, source_workflow_id,
                    current_revision, state_version, status,
                    submitted_revision, submitted_by, current_submission_id, approved_revision,
                    approved_review_id, etag, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                ON CONFLICT DO NOTHING
                RETURNING task_id
                """,
                (
                    task.task_id,
                    task.project_id,
                    task.region_code,
                    task.dataset_id,
                    task.dataset_version,
                    task.rollout_id,
                    task.assignee_id,
                    task.base_lance_version,
                    task.base_step_count,
                    task.tag_schema_id,
                    task.tag_schema_version,
                    task.task_kind.value,
                    task.creation_source.value,
                    task.source_workflow_id,
                    task.current_revision,
                    task.state_version,
                    task.status.value,
                    task.submitted_revision,
                    task.submitted_by,
                    task.current_submission_id,
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
            f"""
            SELECT {_TASK_COLUMNS}
            FROM annotation.annotation_tasks
            WHERE task_id = %s
              AND project_id = NULLIF(current_setting('app.project_id', true), '')
              AND region_code = NULLIF(current_setting('app.region_code', true), '')
            """,
            (task_id,),
        )

    def find_by_rollout(self, *, project_id: str, rollout_id: str) -> AnnotationAggregate | None:
        return self._load_one(
            f"""
            SELECT {_TASK_COLUMNS}
            FROM annotation.annotation_tasks
            WHERE project_id = %s AND rollout_id = %s
              AND project_id = NULLIF(current_setting('app.project_id', true), '')
              AND region_code = NULLIF(current_setting('app.region_code', true), '')
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
                  AND project_id = NULLIF(current_setting('app.project_id', true), '')
                  AND region_code = NULLIF(current_setting('app.region_code', true), '')
                ORDER BY updated_at DESC, task_id DESC
                """,
                (project_id,),
            )
            task_rows = _rows(cursor, cursor.fetchall())
        finally:
            cursor.close()
            connection.close()
        return tuple(self._load_related(self._task_from_row(row)) for row in task_rows)

    def list_revision_threads(
        self,
        *,
        project_id: str,
        region_code: str,
        status: AnnotationStatus | None,
        origin: RevisionOrigin | None,
        snapshot_at: datetime,
        after_updated_at: datetime | None,
        after_task_id: str | None,
        limit: int,
    ) -> tuple[AnnotationRevisionThread, ...]:
        """Read one bounded page without loading every revision aggregate.

        Both the explicit predicates and the current-setting predicates matter: the
        first makes accidental cross-scope calls empty, while the latter still protects
        an owner connection that can otherwise bypass RLS.
        """

        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT
                    task.task_id,
                    task.project_id,
                    task.region_code,
                    task.dataset_id,
                    task.dataset_version,
                    task.rollout_id,
                    task.status,
                    task.submitted_revision,
                    task.current_submission_id,
                    submission.episode_version AS current_episode_version,
                    task.approved_revision,
                    task.approved_review_id,
                    task.updated_at,
                    revision.revision AS latest_revision,
                    revision.origin AS latest_origin,
                    revision.author_id AS latest_author_id,
                    revision.content_hash AS latest_content_hash,
                    revision.created_at AS latest_created_at
                FROM annotation.annotation_tasks AS task
                INNER JOIN annotation.annotation_revisions AS revision
                  ON revision.task_id = task.task_id
                 AND revision.revision = task.current_revision
                LEFT JOIN annotation.annotation_submissions AS submission
                  ON submission.task_id = task.task_id
                 AND submission.submission_id = task.current_submission_id
                WHERE task.project_id = %s
                  AND task.region_code = %s
                  AND task.project_id = NULLIF(current_setting('app.project_id', true), '')
                  AND task.region_code = NULLIF(current_setting('app.region_code', true), '')
                  AND task.updated_at <= %s
                  AND (%s::text IS NULL OR task.status = %s)
                  AND (%s::text IS NULL OR revision.origin = %s)
                  AND (
                      %s::timestamptz IS NULL
                      OR (task.updated_at, task.task_id) < (%s::timestamptz, %s::text)
                  )
                ORDER BY task.updated_at DESC, task.task_id DESC
                LIMIT %s
                """,
                (
                    project_id,
                    region_code,
                    snapshot_at,
                    None if status is None else status.value,
                    None if status is None else status.value,
                    None if origin is None else origin.value,
                    None if origin is None else origin.value,
                    after_updated_at,
                    after_updated_at,
                    after_task_id,
                    limit,
                ),
            )
            return tuple(
                self._revision_thread_from_row(row) for row in _rows(cursor, cursor.fetchall())
            )
        finally:
            cursor.close()
            connection.close()

    def append_revision_thread_list_audit(
        self,
        *,
        project_id: str,
        region_code: str,
        actor_id: str,
        request_id: str,
        status: AnnotationStatus | None,
        origin: RevisionOrigin | None,
        limit: int,
    ) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            require_repository_scope(cursor, project_id=project_id, region_code=region_code)
            cursor.execute(
                """
                INSERT INTO core.audit_events (
                    audit_id, project_id, region_code, actor_id, action,
                    resource_type, resource_id, request_id, before_hash,
                    after_hash, details, occurred_at
                ) VALUES (
                    %s, %s, %s, %s, 'annotation.revision_thread.listed',
                    'annotation_revision_thread', %s, %s, NULL,
                    NULL, %s::jsonb, now()
                )
                """,
                (
                    str(uuid4()),
                    project_id,
                    region_code,
                    actor_id,
                    f"{project_id}:{region_code}",
                    request_id,
                    json.dumps(
                        {
                            "status": None if status is None else status.value,
                            "origin": None if origin is None else origin.value,
                            "limit": limit,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def create_tag_schema_version(self, schema: TagSchemaVersion) -> TagSchemaVersion:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            require_repository_scope(cursor, project_id=schema.project_id, region_code=None)
            cursor.execute(
                """
                INSERT INTO annotation.tag_schema_versions (
                    schema_id, version, project_id, name, status, document,
                    compatible_targets, content_hash, created_by, created_at,
                    published_by, published_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                    %s, %s, %s, %s, %s
                )
                ON CONFLICT DO NOTHING
                """,
                (
                    schema.schema_id,
                    schema.version,
                    schema.project_id,
                    schema.name,
                    schema.status.value,
                    json.dumps(schema.document.model_dump(mode="json")),
                    json.dumps(
                        [target.model_dump(mode="json") for target in schema.compatible_targets]
                    ),
                    schema.content_hash,
                    schema.created_by,
                    schema.created_at,
                    schema.published_by,
                    schema.published_at,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
        existing = self.get_tag_schema_version(schema_id=schema.schema_id, version=schema.version)
        if existing is None:
            raise RuntimeError("PostgreSQL did not return the Tag Schema version")
        return existing

    def get_tag_schema_version(self, *, schema_id: str, version: int) -> TagSchemaVersion | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT schema_id, version, project_id, name, status, document,
                       compatible_targets, content_hash, created_by, created_at,
                       published_by, published_at
                FROM annotation.tag_schema_versions
                WHERE schema_id = %s AND version = %s
                  AND project_id = NULLIF(current_setting('app.project_id', true), '')
                """,
                (schema_id, version),
            )
            raw = cursor.fetchone()
            return None if raw is None else self._schema_from_row(_row(cursor, raw))
        finally:
            cursor.close()
            connection.close()

    def list_tag_schema_versions(
        self, *, project_id: str, schema_id: str
    ) -> tuple[TagSchemaVersion, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT schema_id, version, project_id, name, status, document,
                       compatible_targets, content_hash, created_by, created_at,
                       published_by, published_at
                FROM annotation.tag_schema_versions
                WHERE project_id = %s AND schema_id = %s
                  AND project_id = NULLIF(current_setting('app.project_id', true), '')
                ORDER BY version
                """,
                (project_id, schema_id),
            )
            return tuple(self._schema_from_row(row) for row in _rows(cursor, cursor.fetchall()))
        finally:
            cursor.close()
            connection.close()

    def publish_tag_schema_version(
        self, draft: TagSchemaVersion, published: TagSchemaVersion
    ) -> bool:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            require_repository_scope(cursor, project_id=draft.project_id, region_code=None)
            cursor.execute(
                """
                UPDATE annotation.tag_schema_versions
                SET status = 'PUBLISHED', published_by = %s, published_at = %s
                WHERE schema_id = %s AND version = %s AND status = 'DRAFT'
                  AND content_hash = %s
                  AND project_id = NULLIF(current_setting('app.project_id', true), '')
                RETURNING schema_id
                """,
                (
                    published.published_by,
                    published.published_at,
                    draft.schema_id,
                    draft.version,
                    draft.content_hash,
                ),
            )
            changed = cursor.fetchone() is not None
            if changed:
                for target in published.compatible_targets:
                    cursor.execute(
                        """
                        INSERT INTO annotation.tag_schema_bindings (
                            project_id, region_code, dataset_id,
                            dataset_schema_snapshot_id, task_kind,
                            schema_id, schema_version, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            published.project_id,
                            target.region_code,
                            target.dataset_id,
                            target.dataset_schema_snapshot_id,
                            target.task_kind.value,
                            published.schema_id,
                            published.version,
                            published.published_at,
                        ),
                    )
                    cursor.execute(
                        """
                        SELECT schema_id, schema_version
                        FROM annotation.tag_schema_bindings
                        WHERE project_id = %s AND region_code = %s
                          AND dataset_id = %s AND dataset_schema_snapshot_id = %s
                          AND task_kind = %s
                        """,
                        (
                            published.project_id,
                            target.region_code,
                            target.dataset_id,
                            target.dataset_schema_snapshot_id,
                            target.task_kind.value,
                        ),
                    )
                    owner = cursor.fetchone()
                    owner_identity = None
                    if owner is not None:
                        owner_row = _row(cursor, owner)
                        owner_identity = (
                            str(owner_row["schema_id"]),
                            _as_int(owner_row["schema_version"]),
                        )
                    if owner_identity != (published.schema_id, published.version):
                        raise ValueError(
                            "a Tag Schema target is already bound to another published version"
                        )
            connection.commit()
            return changed
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def resolve_published_schema(
        self,
        *,
        project_id: str,
        region_code: str,
        dataset_id: str,
        dataset_schema_snapshot_id: str,
        task_kind: AnnotationTaskKind,
    ) -> TagSchemaVersion | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT schema.schema_id, schema.version, schema.project_id, schema.name,
                       schema.status, schema.document, schema.compatible_targets,
                       schema.content_hash, schema.created_by, schema.created_at,
                       schema.published_by, schema.published_at
                FROM annotation.tag_schema_bindings binding
                JOIN annotation.tag_schema_versions schema
                  ON schema.schema_id = binding.schema_id
                 AND schema.version = binding.schema_version
                 AND schema.project_id = binding.project_id
                WHERE binding.project_id = %s AND binding.region_code = %s
                  AND binding.dataset_id = %s
                  AND binding.dataset_schema_snapshot_id = %s
                  AND binding.task_kind = %s AND schema.status = 'PUBLISHED'
                  AND binding.project_id = NULLIF(
                      current_setting('app.project_id', true), ''
                  )
                  AND binding.region_code = NULLIF(
                      current_setting('app.region_code', true), ''
                  )
                """,
                (
                    project_id,
                    region_code,
                    dataset_id,
                    dataset_schema_snapshot_id,
                    task_kind.value,
                ),
            )
            raw = cursor.fetchone()
            return None if raw is None else self._schema_from_row(_row(cursor, raw))
        finally:
            cursor.close()
            connection.close()

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
            require_repository_scope(
                cursor,
                project_id=task.project_id,
                region_code=task.region_code,
            )
            cursor.execute(
                """
                SELECT organization_id, current_revision, state_version, status,
                       assignee_id, current_submission_id
                FROM annotation.annotation_tasks
                WHERE task_id = %s
                  AND project_id = NULLIF(current_setting('app.project_id', true), '')
                  AND region_code = NULLIF(current_setting('app.region_code', true), '')
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
                    current_submission_id = %s,
                    approved_revision = %s,
                    approved_review_id = %s,
                    etag = %s,
                    updated_at = %s
                WHERE task_id = %s AND state_version = %s
                  AND project_id = NULLIF(current_setting('app.project_id', true), '')
                  AND region_code = NULLIF(current_setting('app.region_code', true), '')
                RETURNING task_id
                """,
                (
                    task.assignee_id,
                    task.current_revision,
                    task.state_version,
                    task.status.value,
                    task.submitted_revision,
                    task.submitted_by,
                    task.current_submission_id,
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
            self._insert_missing_submissions(cursor, aggregate.submissions)
            self._insert_missing_submission_mutations(cursor, aggregate.submission_mutations)
            self._insert_transition_audit(cursor, aggregate=aggregate, previous=locked)
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
            require_repository_scope(
                cursor,
                project_id=task.project_id,
                region_code=task.region_code,
            )
            cursor.execute(
                """
                SELECT task_id, revision, parent_revision, author_id,
                       client_mutation_id, base_lance_version, tag_schema_id,
                       tag_schema_version, tags, origin,
                       content_hash, created_at
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
                SELECT review_id, submission_id, task_id, revision, reviewer_id,
                       decision, checked_kinds, comment, created_at
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
            cursor.execute(
                """
                SELECT submission_id, task_id, episode_version, revision, submitted_by,
                       base_lance_version, tag_schema_id, tag_schema_version,
                       tag_schema_hash, revision_content_hash, checks, created_at
                FROM annotation.annotation_submissions
                WHERE task_id = %s
                ORDER BY created_at, submission_id
                """,
                (task.task_id,),
            )
            submission_rows = _rows(cursor, cursor.fetchall())
            cursor.execute(
                """
                SELECT task_id, idempotency_key, actor_id, request_fingerprint,
                       submission_id, result_etag, created_at
                FROM annotation.annotation_submission_mutations
                WHERE task_id = %s
                ORDER BY created_at, idempotency_key
                """,
                (task.task_id,),
            )
            submission_mutation_rows = _rows(cursor, cursor.fetchall())
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
                base_lance_version=_as_int(row["base_lance_version"]),
                tag_schema_id=str(row["tag_schema_id"]),
                tag_schema_version=_as_int(row["tag_schema_version"]),
                tags=tuple(AnnotationTag.model_validate(item) for item in _as_json(row["tags"])),
                operations=tuple(operations.get(_as_int(row["revision"]), ())),
                origin=RevisionOrigin(str(row["origin"])),
                content_hash=str(row["content_hash"]),
                created_at=cast(datetime, row["created_at"]),
            )
            for row in revision_rows
        )
        reviews = tuple(
            AnnotationReview(
                review_id=str(row["review_id"]),
                submission_id=row["submission_id"],
                task_id=str(row["task_id"]),
                revision=_as_int(row["revision"]),
                reviewer_id=str(row["reviewer_id"]),
                decision=ReviewDecision(str(row["decision"])),
                checked_kinds=tuple(
                    ReviewCheckKind(item) for item in _as_json(row["checked_kinds"])
                ),
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
        submissions = tuple(
            AnnotationSubmission(
                submission_id=str(row["submission_id"]),
                task_id=str(row["task_id"]),
                episode_version=_as_int(row["episode_version"]),
                revision=_as_int(row["revision"]),
                submitted_by=str(row["submitted_by"]),
                base_lance_version=_as_int(row["base_lance_version"]),
                tag_schema_id=str(row["tag_schema_id"]),
                tag_schema_version=_as_int(row["tag_schema_version"]),
                tag_schema_hash=str(row["tag_schema_hash"]),
                revision_content_hash=str(row["revision_content_hash"]),
                checks=tuple(
                    AnnotationReviewCheck.model_validate(item) for item in _as_json(row["checks"])
                ),
                created_at=cast(datetime, row["created_at"]),
            )
            for row in submission_rows
        )
        submission_mutations = tuple(
            AnnotationSubmissionMutationRecord(
                task_id=str(row["task_id"]),
                idempotency_key=str(row["idempotency_key"]),
                actor_id=str(row["actor_id"]),
                request_fingerprint=str(row["request_fingerprint"]),
                submission_id=str(row["submission_id"]),
                result_etag=str(row["result_etag"]),
                created_at=cast(datetime, row["created_at"]),
            )
            for row in submission_mutation_rows
        )
        aggregate = AnnotationAggregate(
            task=task,
            revisions=revisions,
            reviews=reviews,
            mutations=mutations,
            submissions=submissions,
            submission_mutations=submission_mutations,
        )
        validate_aggregate(aggregate)
        return aggregate

    @staticmethod
    def _task_from_row(row: Mapping[str, object]) -> AnnotationTask:
        return AnnotationTask(
            task_id=str(row["task_id"]),
            project_id=str(row["project_id"]),
            region_code=(None if row["region_code"] is None else str(row["region_code"])),
            dataset_id=str(row["dataset_id"]),
            dataset_version=_as_int(row["dataset_version"]),
            base_lance_version=_as_int(row["base_lance_version"]),
            base_step_count=(
                None if row["base_step_count"] is None else _as_int(row["base_step_count"])
            ),
            tag_schema_id=str(row["tag_schema_id"]),
            tag_schema_version=_as_int(row["tag_schema_version"]),
            rollout_id=str(row["rollout_id"]),
            task_kind=AnnotationTaskKind(str(row["task_kind"])),
            creation_source=AnnotationTaskCreationSource(str(row["creation_source"])),
            source_workflow_id=(
                None if row["source_workflow_id"] is None else str(row["source_workflow_id"])
            ),
            assignee_id=None if row["assignee_id"] is None else str(row["assignee_id"]),
            current_revision=_as_int(row["current_revision"]),
            state_version=_as_int(row["state_version"]),
            status=AnnotationStatus(str(row["status"])),
            submitted_revision=(
                None if row["submitted_revision"] is None else _as_int(row["submitted_revision"])
            ),
            submitted_by=(None if row["submitted_by"] is None else str(row["submitted_by"])),
            current_submission_id=(
                None if row["current_submission_id"] is None else str(row["current_submission_id"])
            ),
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
    def _revision_thread_from_row(row: Mapping[str, object]) -> AnnotationRevisionThread:
        return AnnotationRevisionThread(
            task_id=str(row["task_id"]),
            project_id=str(row["project_id"]),
            region_code=str(row["region_code"]),
            dataset_id=str(row["dataset_id"]),
            dataset_version=_as_int(row["dataset_version"]),
            rollout_id=str(row["rollout_id"]),
            status=AnnotationStatus(str(row["status"])),
            latest_revision=AnnotationRevisionThreadRevision(
                revision=_as_int(row["latest_revision"]),
                origin=RevisionOrigin(str(row["latest_origin"])),
                author_id=str(row["latest_author_id"]),
                content_hash=str(row["latest_content_hash"]),
                created_at=cast(datetime, row["latest_created_at"]),
            ),
            submitted_revision=(
                None if row["submitted_revision"] is None else _as_int(row["submitted_revision"])
            ),
            current_submission_id=(
                None if row["current_submission_id"] is None else str(row["current_submission_id"])
            ),
            current_episode_version=(
                None
                if row["current_episode_version"] is None
                else _as_int(row["current_episode_version"])
            ),
            approved_revision=(
                None if row["approved_revision"] is None else _as_int(row["approved_revision"])
            ),
            approved_review_id=(
                None if row["approved_review_id"] is None else str(row["approved_review_id"])
            ),
            updated_at=cast(datetime, row["updated_at"]),
        )

    @staticmethod
    def _insert_revision(cursor: DbApiCursor, revision: AnnotationRevision) -> None:
        cursor.execute(
            """
            INSERT INTO annotation.annotation_revisions (
                task_id, revision, parent_revision, author_id,
                client_mutation_id, base_lance_version, tag_schema_id,
                tag_schema_version, tags, origin, content_hash, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                %s, %s, %s
            )
            """,
            (
                revision.task_id,
                revision.revision,
                revision.parent_revision,
                revision.author_id,
                revision.client_mutation_id,
                revision.base_lance_version,
                revision.tag_schema_id,
                revision.tag_schema_version,
                json.dumps([tag.model_dump(mode="json") for tag in revision.tags]),
                revision.origin.value,
                revision.content_hash,
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
    def _insert_transition_audit(
        cursor: DbApiCursor,
        *,
        aggregate: AnnotationAggregate,
        previous: Mapping[str, object],
    ) -> None:
        task = aggregate.task
        previous_revision = _as_int(previous["current_revision"])
        previous_status = str(previous["status"])
        previous_assignee = (
            None if previous["assignee_id"] is None else str(previous["assignee_id"])
        )
        previous_submission = (
            None
            if previous["current_submission_id"] is None
            else str(previous["current_submission_id"])
        )

        action: str
        actor_id: str
        resource_type: str
        resource_id: str
        occurred_at: datetime
        details: dict[str, object]
        if task.current_revision == previous_revision + 1:
            revision = aggregate.revisions[-1]
            mutation = aggregate.mutations[-1]
            action = {
                RevisionOrigin.ANNOTATION: "annotation.revision.created",
                RevisionOrigin.ANNOTATION_RESTORE: "annotation.revision.restored",
            }[revision.origin]
            actor_id = mutation.actor_id
            resource_type = "annotation_revision"
            resource_id = f"{task.task_id}:{revision.revision}"
            occurred_at = revision.created_at
            details = {
                "origin": revision.origin.value,
                "revision": revision.revision,
                "operation_count": len(revision.operations),
                "tag_count": len(revision.tags),
            }
        elif task.current_submission_id != previous_submission:
            submission = next(
                item
                for item in reversed(aggregate.submissions)
                if item.submission_id == task.current_submission_id
            )
            action = "annotation.submission.created"
            actor_id = submission.submitted_by
            resource_type = "annotation_submission"
            resource_id = submission.submission_id
            occurred_at = submission.created_at
            details = {"revision": submission.revision}
        elif aggregate.reviews and task.status.value != previous_status:
            review = aggregate.reviews[-1]
            action = {
                ReviewDecision.APPROVE: "annotation.review.approved",
                ReviewDecision.NEEDS_REVISION: "annotation.review.needs_revision",
                ReviewDecision.REJECT: "annotation.review.rejected",
            }[review.decision]
            actor_id = review.reviewer_id
            resource_type = "annotation_review"
            resource_id = review.review_id
            occurred_at = review.created_at
            details = {
                "decision": review.decision.value,
                "revision": review.revision,
                "submission_id": review.submission_id,
            }
        elif task.assignee_id != previous_assignee and task.assignee_id is not None:
            action = "annotation.task.claimed"
            actor_id = task.assignee_id
            resource_type = "annotation_task"
            resource_id = task.task_id
            occurred_at = task.updated_at
            details = {}
        else:
            raise ValueError("annotation transition has no auditable kind")

        cursor.execute(
            """
            INSERT INTO core.audit_events (
                audit_id, organization_id, project_id, region_code, actor_id,
                action, resource_type, resource_id, request_id, details, occurred_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                NULLIF(current_setting('app.request_id', true), ''), %s::jsonb, %s
            )
            """,
            (
                str(uuid4()),
                str(previous["organization_id"]),
                task.project_id,
                task.region_code,
                actor_id,
                action,
                resource_type,
                resource_id,
                json.dumps(details, sort_keys=True, separators=(",", ":")),
                occurred_at,
            ),
        )

    @staticmethod
    def _insert_missing_reviews(cursor: DbApiCursor, reviews: Sequence[AnnotationReview]) -> None:
        for review in reviews:
            cursor.execute(
                """
                INSERT INTO annotation.annotation_reviews (
                    review_id, submission_id, task_id, revision, reviewer_id,
                    decision, checked_kinds, comment, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (review_id) DO NOTHING
                """,
                (
                    review.review_id,
                    review.submission_id,
                    review.task_id,
                    review.revision,
                    review.reviewer_id,
                    review.decision.value,
                    json.dumps([kind.value for kind in review.checked_kinds]),
                    review.comment,
                    review.created_at,
                ),
            )

    @staticmethod
    def _insert_missing_submissions(
        cursor: DbApiCursor, submissions: Sequence[AnnotationSubmission]
    ) -> None:
        for submission in submissions:
            cursor.execute(
                """
                INSERT INTO annotation.annotation_submissions (
                    submission_id, task_id, episode_version, revision, submitted_by,
                    base_lance_version, tag_schema_id, tag_schema_version,
                    tag_schema_hash, revision_content_hash, checks, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s
                )
                ON CONFLICT (submission_id) DO NOTHING
                """,
                (
                    submission.submission_id,
                    submission.task_id,
                    submission.episode_version,
                    submission.revision,
                    submission.submitted_by,
                    submission.base_lance_version,
                    submission.tag_schema_id,
                    submission.tag_schema_version,
                    submission.tag_schema_hash,
                    submission.revision_content_hash,
                    json.dumps([check.model_dump(mode="json") for check in submission.checks]),
                    submission.created_at,
                ),
            )

    @staticmethod
    def _insert_missing_submission_mutations(
        cursor: DbApiCursor,
        mutations: Sequence[AnnotationSubmissionMutationRecord],
    ) -> None:
        for mutation in mutations:
            cursor.execute(
                """
                INSERT INTO annotation.annotation_submission_mutations (
                    task_id, idempotency_key, actor_id, request_fingerprint,
                    submission_id, result_etag, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (task_id, idempotency_key) DO NOTHING
                """,
                (
                    mutation.task_id,
                    mutation.idempotency_key,
                    mutation.actor_id,
                    mutation.request_fingerprint,
                    mutation.submission_id,
                    mutation.result_etag,
                    mutation.created_at,
                ),
            )

    @staticmethod
    def _schema_from_row(row: Mapping[str, object]) -> TagSchemaVersion:
        return TagSchemaVersion(
            schema_id=str(row["schema_id"]),
            project_id=str(row["project_id"]),
            name=str(row["name"]),
            version=_as_int(row["version"]),
            status=TagSchemaStatus(str(row["status"])),
            document=TagSchemaDocument.model_validate(_as_json(row["document"])),
            compatible_targets=tuple(
                TagSchemaTarget.model_validate(item) for item in _as_json(row["compatible_targets"])
            ),
            content_hash=str(row["content_hash"]),
            created_by=str(row["created_by"]),
            created_at=cast(datetime, row["created_at"]),
            published_by=(None if row["published_by"] is None else str(row["published_by"])),
            published_at=cast(datetime | None, row["published_at"]),
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
