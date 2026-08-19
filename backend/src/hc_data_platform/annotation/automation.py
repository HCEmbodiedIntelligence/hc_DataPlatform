"""System-only annotation task creation after an immutable Lance commit."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.security.audit import canonical_hash

from .models import (
    AnnotationRevision,
    AnnotationStatus,
    AnnotationTask,
    AnnotationTaskCreationSource,
    AnnotationTaskKind,
    TagSchemaVersion,
)
from .ports import AnnotationAggregate
from .repository import InMemoryAnnotationRepository
from .service import annotation_etag
from .validation import revision_content_hash


class AutomaticAnnotationBlocked(RuntimeError):
    code = "ANNOTATION_SCHEMA_BINDING_MISSING"


@dataclass(frozen=True, slots=True)
class AutomaticAnnotationRequest:
    project_id: str
    region_code: str
    rollout_id: str
    dataset_id: str
    dataset_version: int
    lance_version: int
    dataset_schema_snapshot_id: str
    base_step_count: int
    source_workflow_id: str
    task_kind: AnnotationTaskKind = AnnotationTaskKind.TAGGING

    def __post_init__(self) -> None:
        string_values = (
            self.project_id,
            self.region_code,
            self.rollout_id,
            self.dataset_id,
            self.dataset_schema_snapshot_id,
            self.source_workflow_id,
        )
        if any(not value for value in string_values):
            raise ValueError("automatic annotation lineage values must not be empty")
        if min(self.dataset_version, self.lance_version, self.base_step_count) < 1:
            raise ValueError("automatic annotation versions and step count must be positive")

    @property
    def identity(self) -> str:
        return ":".join(
            (
                self.project_id,
                self.region_code,
                self.rollout_id,
                self.dataset_id,
                str(self.dataset_version),
                str(self.lance_version),
                self.task_kind.value,
            )
        )

    @property
    def trigger_id(self) -> str:
        return str(uuid5(NAMESPACE_URL, f"annotation-trigger:{self.identity}"))

    @property
    def task_id(self) -> str:
        return str(uuid5(NAMESPACE_URL, f"annotation-task:{self.identity}"))


class AutomaticAnnotationRepository(Protocol):
    def resolve_published_schema(
        self,
        *,
        project_id: str,
        region_code: str,
        dataset_id: str,
        dataset_schema_snapshot_id: str,
        task_kind: AnnotationTaskKind,
    ) -> TagSchemaVersion | None: ...

    def record_automatic_blocked(self, request: AutomaticAnnotationRequest) -> None: ...

    def create_automatic(
        self,
        request: AutomaticAnnotationRequest,
        aggregate: AnnotationAggregate,
    ) -> AnnotationTask: ...


class AutomaticAnnotationTaskService:
    def __init__(
        self,
        repository: AutomaticAnnotationRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._clock = clock

    def ensure_task(self, request: AutomaticAnnotationRequest) -> AnnotationTask:
        schema = self._repository.resolve_published_schema(
            project_id=request.project_id,
            region_code=request.region_code,
            dataset_id=request.dataset_id,
            dataset_schema_snapshot_id=request.dataset_schema_snapshot_id,
            task_kind=request.task_kind,
        )
        if schema is None:
            self._repository.record_automatic_blocked(request)
            raise AutomaticAnnotationBlocked(
                "no explicitly compatible published Tag Schema is bound to this Lance target"
            )
        now = self._clock()
        content_hash = revision_content_hash(
            base_lance_version=request.lance_version,
            tag_schema_id=schema.schema_id,
            tag_schema_version=schema.version,
            tags=(),
            operations=(),
        )
        revision = AnnotationRevision(
            task_id=request.task_id,
            revision=0,
            parent_revision=None,
            author_id="system",
            client_mutation_id="initial",
            base_lance_version=request.lance_version,
            tag_schema_id=schema.schema_id,
            tag_schema_version=schema.version,
            tags=(),
            operations=(),
            content_hash=content_hash,
            created_at=now,
        )
        task = AnnotationTask(
            task_id=request.task_id,
            project_id=request.project_id,
            region_code=request.region_code,
            dataset_id=request.dataset_id,
            dataset_version=request.dataset_version,
            base_lance_version=request.lance_version,
            base_step_count=request.base_step_count,
            tag_schema_id=schema.schema_id,
            tag_schema_version=schema.version,
            rollout_id=request.rollout_id,
            task_kind=request.task_kind,
            creation_source=AnnotationTaskCreationSource.SYSTEM_LANCE,
            source_workflow_id=request.source_workflow_id,
            current_revision=0,
            state_version=0,
            status=AnnotationStatus.DRAFT,
            etag=annotation_etag(request.task_id, 0, 0),
            created_at=now,
            updated_at=now,
        )
        persisted = self._repository.create_automatic(
            request,
            AnnotationAggregate(task=task, revisions=(revision,)),
        )
        expected = (
            task.project_id,
            task.region_code,
            task.rollout_id,
            task.dataset_id,
            task.dataset_version,
            task.base_lance_version,
            task.base_step_count,
            task.tag_schema_id,
            task.tag_schema_version,
            task.task_kind,
            task.creation_source,
            task.source_workflow_id,
        )
        actual = (
            persisted.project_id,
            persisted.region_code,
            persisted.rollout_id,
            persisted.dataset_id,
            persisted.dataset_version,
            persisted.base_lance_version,
            persisted.base_step_count,
            persisted.tag_schema_id,
            persisted.tag_schema_version,
            persisted.task_kind,
            persisted.creation_source,
            persisted.source_workflow_id,
        )
        if actual != expected:
            raise RuntimeError("automatic annotation identity conflict")
        return persisted


class InMemoryAutomaticAnnotationRepository(InMemoryAnnotationRepository):
    def __init__(self) -> None:
        super().__init__()
        self.automatic_triggers: dict[str, dict[str, object]] = {}
        self.automatic_audit: list[dict[str, object]] = []
        self._automatic_lock = RLock()

    def record_automatic_blocked(self, request: AutomaticAnnotationRequest) -> None:
        with self._automatic_lock:
            current = self.automatic_triggers.get(request.trigger_id, {})
            previous_attempts = current.get("attempts", 0)
            attempts = (previous_attempts if isinstance(previous_attempts, int) else 0) + 1
            self.automatic_triggers[request.trigger_id] = {
                "status": "BLOCKED_RETRYABLE",
                "error_code": AutomaticAnnotationBlocked.code,
                "attempts": attempts,
                "workflow_id": request.source_workflow_id,
            }
            self.automatic_audit.append(
                {
                    "action": "annotation.task.blocked",
                    "project_id": request.project_id,
                    "region_code": request.region_code,
                    "resource_id": request.rollout_id,
                    "workflow_id": request.source_workflow_id,
                    "error_code": AutomaticAnnotationBlocked.code,
                }
            )

    def create_automatic(
        self,
        request: AutomaticAnnotationRequest,
        aggregate: AnnotationAggregate,
    ) -> AnnotationTask:
        with self._automatic_lock:
            persisted = self.create(aggregate).task
            current = self.automatic_triggers.get(request.trigger_id, {})
            previous_attempts = current.get("attempts", 0)
            self.automatic_triggers[request.trigger_id] = {
                "status": "CREATED",
                "error_code": None,
                "attempts": (previous_attempts if isinstance(previous_attempts, int) else 0) + 1,
                "workflow_id": request.source_workflow_id,
                "task_id": persisted.task_id,
            }
            if not any(
                event.get("task_id") == persisted.task_id
                for event in self.automatic_audit
                if event.get("action") == "annotation.task.created"
            ):
                self.automatic_audit.append(
                    {
                        "action": "annotation.task.created",
                        "project_id": request.project_id,
                        "region_code": request.region_code,
                        "resource_id": persisted.task_id,
                        "task_id": persisted.task_id,
                        "workflow_id": request.source_workflow_id,
                    }
                )
            return persisted


class PostgresAutomaticAnnotationRepository:
    """Atomic task/revision/trigger/audit persistence under exact worker RLS scope."""

    def __init__(self, connection_factory: Any) -> None:
        from .postgres import PostgresAnnotationRepository

        self._connection_factory = connection_factory
        self._schemas = PostgresAnnotationRepository(connection_factory)

    def resolve_published_schema(self, **kwargs: Any) -> TagSchemaVersion | None:
        return self._schemas.resolve_published_schema(**kwargs)

    def record_automatic_blocked(self, request: AutomaticAnnotationRequest) -> None:
        from .postgres import require_repository_scope

        now = datetime.now(timezone.utc)
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                require_repository_scope(
                    cursor,
                    project_id=request.project_id,
                    region_code=request.region_code,
                )
                cursor.execute(
                    """
                    INSERT INTO annotation.annotation_task_triggers (
                        trigger_id, project_id, region_code, rollout_id, dataset_id,
                        dataset_version, lance_version, dataset_schema_snapshot_id,
                        task_kind, source_workflow_id, status, error_code,
                        attempts, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        'BLOCKED_RETRYABLE', %s, 1, %s, %s
                    )
                    ON CONFLICT (
                        project_id, region_code, rollout_id, dataset_id,
                        dataset_version, lance_version, task_kind
                    ) DO UPDATE SET
                        status = 'BLOCKED_RETRYABLE', task_id = NULL,
                        error_code = EXCLUDED.error_code,
                        attempts = annotation.annotation_task_triggers.attempts + 1,
                        updated_at = EXCLUDED.updated_at
                    RETURNING attempts
                    """,
                    (
                        request.trigger_id,
                        request.project_id,
                        request.region_code,
                        request.rollout_id,
                        request.dataset_id,
                        request.dataset_version,
                        request.lance_version,
                        request.dataset_schema_snapshot_id,
                        request.task_kind.value,
                        request.source_workflow_id,
                        AutomaticAnnotationBlocked.code,
                        now,
                        now,
                    ),
                )
                attempt = int(cursor.fetchone()[0])
                self._insert_audit(
                    cursor,
                    request=request,
                    action="annotation.task.blocked",
                    resource_id=request.rollout_id,
                    attempt=attempt,
                    error_code=AutomaticAnnotationBlocked.code,
                    occurred_at=now,
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_automatic(
        self,
        request: AutomaticAnnotationRequest,
        aggregate: AnnotationAggregate,
    ) -> AnnotationTask:
        from .postgres import PostgresAnnotationRepository, require_repository_scope

        task = aggregate.task
        revision = aggregate.revisions[0]
        now = task.created_at
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                require_repository_scope(
                    cursor,
                    project_id=request.project_id,
                    region_code=request.region_code,
                )
                cursor.execute(
                    """
                    INSERT INTO annotation.annotation_tasks (
                        task_id, project_id, region_code, dataset_id, dataset_version,
                        rollout_id, assignee_id, base_lance_version, base_step_count,
                        tag_schema_id, tag_schema_version, task_kind, creation_source,
                        source_workflow_id, current_revision, state_version, status,
                        submitted_revision, submitted_by, current_submission_id,
                        approved_revision, approved_review_id, etag, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s,
                        %s, %s, %s, %s, 0, 0, 'DRAFT', NULL, NULL, NULL,
                        NULL, NULL, %s, %s, %s
                    ) ON CONFLICT DO NOTHING
                    RETURNING task_id
                    """,
                    (
                        task.task_id,
                        task.project_id,
                        task.region_code,
                        task.dataset_id,
                        task.dataset_version,
                        task.rollout_id,
                        task.base_lance_version,
                        task.base_step_count,
                        task.tag_schema_id,
                        task.tag_schema_version,
                        task.task_kind.value,
                        task.creation_source.value,
                        task.source_workflow_id,
                        task.etag,
                        task.created_at,
                        task.updated_at,
                    ),
                )
                if cursor.fetchone() is not None:
                    PostgresAnnotationRepository._insert_revision(cursor, revision)
                cursor.execute(
                    """
                    SELECT task_id, project_id, region_code, rollout_id, dataset_id,
                           dataset_version, base_lance_version, base_step_count,
                           tag_schema_id, tag_schema_version, task_kind,
                           creation_source, source_workflow_id
                    FROM annotation.annotation_tasks
                    WHERE project_id = %s AND region_code = %s AND rollout_id = %s
                      AND dataset_id = %s AND dataset_version = %s
                      AND base_lance_version = %s AND task_kind = %s
                    """,
                    (
                        request.project_id,
                        request.region_code,
                        request.rollout_id,
                        request.dataset_id,
                        request.dataset_version,
                        request.lance_version,
                        request.task_kind.value,
                    ),
                )
                raw = cursor.fetchone()
                if raw is None:
                    raise RuntimeError("automatic annotation task was not persisted")
                values = tuple(raw)
                if str(values[0]) != task.task_id:
                    raise RuntimeError("automatic annotation target has another winner")
                cursor.execute(
                    """
                    INSERT INTO annotation.annotation_task_triggers (
                        trigger_id, project_id, region_code, rollout_id, dataset_id,
                        dataset_version, lance_version, dataset_schema_snapshot_id,
                        task_kind, source_workflow_id, task_id, status, error_code,
                        attempts, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, 'CREATED', NULL, 1, %s, %s
                    )
                    ON CONFLICT (
                        project_id, region_code, rollout_id, dataset_id,
                        dataset_version, lance_version, task_kind
                    ) DO UPDATE SET
                        task_id = EXCLUDED.task_id, status = 'CREATED', error_code = NULL,
                        attempts = annotation.annotation_task_triggers.attempts + 1,
                        updated_at = EXCLUDED.updated_at
                    WHERE annotation.annotation_task_triggers.task_id IS NULL
                       OR annotation.annotation_task_triggers.task_id = EXCLUDED.task_id
                    RETURNING attempts
                    """,
                    (
                        request.trigger_id,
                        request.project_id,
                        request.region_code,
                        request.rollout_id,
                        request.dataset_id,
                        request.dataset_version,
                        request.lance_version,
                        request.dataset_schema_snapshot_id,
                        request.task_kind.value,
                        request.source_workflow_id,
                        task.task_id,
                        now,
                        now,
                    ),
                )
                attempt_row = cursor.fetchone()
                if attempt_row is None:
                    raise RuntimeError("automatic annotation trigger has another winner")
                attempt = int(attempt_row[0])
                self._insert_audit(
                    cursor,
                    request=request,
                    action="annotation.task.created",
                    resource_id=task.task_id,
                    attempt=attempt,
                    error_code=None,
                    occurred_at=now,
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        persisted = self._schemas.get(task.task_id)
        if persisted is None:
            raise RuntimeError("automatic annotation task cannot be reloaded")
        return persisted.task

    @staticmethod
    def _insert_audit(
        cursor: Any,
        *,
        request: AutomaticAnnotationRequest,
        action: str,
        resource_id: str,
        attempt: int,
        error_code: str | None,
        occurred_at: datetime,
    ) -> None:
        details: dict[str, object] = {
            "workflow_id": request.source_workflow_id,
            "dataset_id": request.dataset_id,
            "dataset_version": request.dataset_version,
            "lance_version": request.lance_version,
            "task_kind": request.task_kind.value,
            "attempt": attempt,
        }
        if error_code is not None:
            details["error_code"] = error_code
        audit_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{request.trigger_id}:{action}:{attempt}",
            )
        )
        cursor.execute(
            """
            INSERT INTO core.audit_events (
                audit_id, project_id, region_code, actor_id, action,
                resource_type, resource_id, request_id, before_hash,
                after_hash, details, occurred_at
            ) VALUES (
                %s, %s, %s, 'hc-data-worker', %s, 'annotation_task',
                %s, %s, NULL, %s, %s::jsonb, %s
            ) ON CONFLICT (audit_id) DO NOTHING
            """,
            (
                audit_id,
                request.project_id,
                request.region_code,
                action,
                resource_id,
                request.source_workflow_id,
                canonical_hash({"action": action, **details}),
                json.dumps(details, sort_keys=True),
                occurred_at,
            ),
        )
