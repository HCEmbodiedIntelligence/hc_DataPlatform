"""Synchronous PostgreSQL adapter for scoped storage governance facts."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol, TypeVar, cast
from uuid import uuid4

from pydantic import BaseModel

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.security.idempotency import (
    IdempotencyResult,
    idempotency_conflict,
    request_fingerprint,
)

from .models import (
    BusinessCapacityCategory,
    CapacityHistoryPoint,
    CapacityInventoryFact,
    CapacitySnapshot,
    InventoryDisposition,
    LifecycleAuditEvent,
    LifecycleExecution,
    LifecycleExecutionItem,
    LifecycleExecutionLog,
    LifecycleExecutionStatus,
    LifecyclePolicy,
    LifecyclePolicyAction,
    LifecyclePolicyState,
    LifecycleSchedule,
    ManagedMultipartUploadRecord,
    ManagedStorageObjectRecord,
    ObjectRole,
    StorageObjectOperation,
    StorageObjectStatus,
    StorageTier,
)


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
_T = TypeVar("_T")


class _TransactionConnectionProxy:
    """Let repositories share an idempotency transaction without committing it early."""

    def __init__(self, connection: DbApiConnection) -> None:
        self._connection = connection

    def cursor(self) -> DbApiCursor:
        return self._connection.cursor()

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class StorageTransactionConnectionFactory:
    """Context-aware DB-API factory shared by the repository and idempotency store."""

    def __init__(self, base_factory: ConnectionFactory) -> None:
        self._base_factory = base_factory
        self._bound: ContextVar[DbApiConnection | None] = ContextVar(
            "storage_transaction_connection",
            default=None,
        )

    def __call__(self) -> DbApiConnection:
        bound = self._bound.get()
        if bound is None:
            return self._base_factory()
        return _TransactionConnectionProxy(bound)

    @contextmanager
    def bind(self, connection: DbApiConnection) -> Iterator[None]:
        if self._bound.get() is not None:
            raise RuntimeError("a storage transaction is already bound")
        token = self._bound.set(connection)
        try:
            yield
        finally:
            self._bound.reset(token)


_POLICY_COLUMNS = """
project_id, policy_id, name, business_category, object_role, action,
minimum_age_days, priority, state, version, etag, created_at, updated_at
""".strip()

_FACT_COLUMNS = """
snapshot_id, project_id, physical_instance_id, logical_object_id, physical_bytes,
disposition, business_category, object_role, observed_at
""".strip()

_AUDIT_COLUMNS = """
audit_id, project_id, policy_id, actor_id, action, before_digest, after_digest,
request_id, details, occurred_at
""".strip()

_MANAGED_OBJECT_COLUMNS = """
project_id, object_id, display_key, object_key, original_object_key, physical_bytes,
checksum_sha256, business_category, object_role, storage_tier, status,
active_reference_count, retention_until, legal_hold, governance_hold, rebuild_source_id,
recoverable_until, version, etag, created_at, updated_at
""".strip()

_MULTIPART_COLUMNS = """
project_id, multipart_id, display_key, object_key, upload_id, received_bytes, part_count,
status, version, etag, started_at, updated_at
""".strip()

_OPERATION_COLUMNS = """
project_id, operation_id, object_id, multipart_id, action, status, idempotency_key,
request_fingerprint, actor_id, request_id, attempt, error_code, created_at, completed_at
""".strip()

_EXECUTION_COLUMNS = """
project_id, execution_id, policy_id, policy_version, action, status, dry_run, plan_hash,
approval_id, requested_by, approved_by, next_batch, created_at, updated_at
""".strip()

_SCHEDULE_COLUMNS = """
project_id, schedule_id, policy_id, interval_seconds, enabled, next_run_at,
last_execution_id, version, etag, created_at, updated_at
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


def _byte_string(value: object) -> str:
    if isinstance(value, Decimal):
        return str(int(value))
    return str(value)


def _details(value: object) -> dict[str, str | int | bool | None]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, Mapping):
        raise RuntimeError("lifecycle audit details must be a JSON object")
    return {str(key): cast(str | int | bool | None, item) for key, item in decoded.items()}


def _string_tuple(value: object) -> tuple[str, ...]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, Sequence) or isinstance(decoded, (str, bytes)):
        raise RuntimeError("stored lifecycle blocked reasons must be an array")
    return tuple(str(item) for item in decoded)


def _version_conflict() -> Exception:
    return problem(
        status=412,
        code="LIFECYCLE_POLICY_VERSION_CONFLICT",
        title="Lifecycle policy changed",
        detail="The lifecycle policy changed after it was read.",
    )


def _unique_conflict() -> Exception:
    return problem(
        status=409,
        code="LIFECYCLE_POLICY_CONFLICT",
        title="Lifecycle policy conflict",
        detail="The policy name or enabled target conflicts with an existing policy.",
    )


class PostgresStorageRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def replace_inventory_snapshot(
        self,
        *,
        snapshot: CapacitySnapshot,
        facts: Sequence[CapacityInventoryFact],
    ) -> None:
        unique = {fact.physical_instance_id: fact for fact in facts}
        content_digest = canonical_hash(
            {
                "snapshot": snapshot.model_dump(mode="json"),
                "facts": [unique[key].model_dump(mode="json") for key in sorted(unique)],
            }
        )
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO storage.inventory_snapshots (
                    project_id, snapshot_id, observed_at, physical_total_bytes,
                    physical_instance_count, candidate_business_total_bytes,
                    candidate_logical_object_count, replica_overhead_bytes,
                    temporary_bytes, duplicate_inventory_rows_ignored, content_digest
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (project_id, snapshot_id) DO NOTHING
                RETURNING snapshot_id
                """,
                (
                    snapshot.project_id,
                    snapshot.snapshot_id,
                    snapshot.observed_at,
                    snapshot.physical_total_bytes,
                    snapshot.physical_instance_count,
                    snapshot.candidate_business_total_bytes,
                    snapshot.candidate_logical_object_count,
                    snapshot.reconciliation.replica_overhead_bytes,
                    snapshot.reconciliation.temporary_bytes,
                    snapshot.reconciliation.duplicate_inventory_rows_ignored,
                    content_digest,
                ),
            )
            inserted = cursor.fetchone() is not None
            if not inserted:
                cursor.execute(
                    """
                    SELECT content_digest
                    FROM storage.inventory_snapshots
                    WHERE project_id = %s AND snapshot_id = %s
                    """,
                    (snapshot.project_id, snapshot.snapshot_id),
                )
                existing = cursor.fetchone()
                existing_digest = (
                    None if existing is None else next(iter(_row(cursor, existing).values()))
                )
                if existing_digest != content_digest:
                    raise problem(
                        status=409,
                        code="CAPACITY_SNAPSHOT_IMMUTABLE",
                        title="Capacity snapshot is immutable",
                        detail="The snapshot ID was already published with different facts.",
                    )
                connection.commit()
                return

            for physical_id in sorted(unique):
                fact = unique[physical_id]
                cursor.execute(
                    """
                    INSERT INTO storage.inventory_facts (
                        project_id, snapshot_id, physical_instance_id, logical_object_id,
                        physical_bytes, disposition, business_category, object_role, observed_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        fact.project_id,
                        fact.snapshot_id,
                        fact.physical_instance_id,
                        fact.logical_object_id,
                        fact.physical_bytes,
                        fact.disposition.value,
                        None if fact.business_category is None else fact.business_category.value,
                        fact.object_role.value,
                        fact.observed_at,
                    ),
                )
            cursor.execute(
                """
                UPDATE storage.inventory_snapshots
                SET sealed = true
                WHERE project_id = %s AND snapshot_id = %s AND sealed = false
                """,
                (snapshot.project_id, snapshot.snapshot_id),
            )
            if getattr(cursor, "rowcount", 1) != 1:
                raise RuntimeError("capacity snapshot could not be sealed")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def latest_snapshot_id(self, *, project_id: str) -> str | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT snapshot_id
                FROM storage.inventory_snapshots
                WHERE project_id = %s
                ORDER BY observed_at DESC, snapshot_id DESC
                LIMIT 1
                """,
                (project_id,),
            )
            raw = cursor.fetchone()
            return None if raw is None else str(next(iter(_row(cursor, raw).values())))
        finally:
            cursor.close()
            connection.close()

    def list_capacity_history(
        self,
        *,
        project_id: str,
        window_start: datetime,
        window_end: datetime,
        limit: int,
    ) -> tuple[CapacityHistoryPoint, ...]:
        """Read one latest immutable snapshot per UTC day without fetching facts."""

        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                WITH latest_per_day AS (
                    SELECT DISTINCT ON ((observed_at AT TIME ZONE 'UTC')::date)
                        snapshot_id,
                        observed_at,
                        physical_total_bytes,
                        candidate_business_total_bytes
                    FROM storage.inventory_snapshots
                    WHERE project_id = %s
                      AND observed_at >= %s
                      AND observed_at <= %s
                    ORDER BY
                        (observed_at AT TIME ZONE 'UTC')::date,
                        observed_at DESC,
                        snapshot_id DESC
                )
                SELECT snapshot_id, observed_at, physical_total_bytes,
                       candidate_business_total_bytes
                FROM latest_per_day
                ORDER BY observed_at ASC, snapshot_id ASC
                LIMIT %s
                """,
                (project_id, window_start, window_end, limit),
            )
            return tuple(
                CapacityHistoryPoint(
                    snapshot_id=str(row["snapshot_id"]),
                    observed_at=cast(datetime, row["observed_at"]),
                    physical_total_bytes=_byte_string(row["physical_total_bytes"]),
                    candidate_business_total_bytes=_byte_string(
                        row["candidate_business_total_bytes"]
                    ),
                )
                for row in _rows(cursor, cursor.fetchall())
            )
        finally:
            cursor.close()
            connection.close()

    def inventory_facts(
        self,
        *,
        project_id: str,
        snapshot_id: str,
    ) -> tuple[CapacityInventoryFact, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT {_FACT_COLUMNS}
                FROM storage.inventory_facts
                WHERE project_id = %s AND snapshot_id = %s
                ORDER BY physical_instance_id
                """,
                (project_id, snapshot_id),
            )
            return tuple(self._fact(row) for row in _rows(cursor, cursor.fetchall()))
        finally:
            cursor.close()
            connection.close()

    def inventory_duplicate_rows_ignored(
        self,
        *,
        project_id: str,
        snapshot_id: str,
    ) -> int:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT duplicate_inventory_rows_ignored
                FROM storage.inventory_snapshots
                WHERE project_id = %s AND snapshot_id = %s
                """,
                (project_id, snapshot_id),
            )
            raw = cursor.fetchone()
            if raw is None:
                return 0
            return int(cast(Any, next(iter(_row(cursor, raw).values()))))
        finally:
            cursor.close()
            connection.close()

    def list_inventory(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        anchor_physical_instance_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[CapacityInventoryFact, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT {_FACT_COLUMNS}
                FROM storage.inventory_facts
                WHERE project_id = %s
                  AND snapshot_id = %s
                  AND (
                    %s::text IS NULL
                    OR (%s AND physical_instance_id < %s)
                    OR (NOT %s AND physical_instance_id > %s)
                  )
                ORDER BY
                  CASE WHEN %s THEN physical_instance_id END DESC,
                  CASE WHEN NOT %s THEN physical_instance_id END ASC
                LIMIT %s
                """,
                (
                    project_id,
                    snapshot_id,
                    anchor_physical_instance_id,
                    before,
                    anchor_physical_instance_id,
                    before,
                    anchor_physical_instance_id,
                    before,
                    before,
                    limit,
                ),
            )
            return tuple(self._fact(row) for row in _rows(cursor, cursor.fetchall()))
        finally:
            cursor.close()
            connection.close()

    def get_policy(self, *, project_id: str, policy_id: str) -> LifecyclePolicy | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT {_POLICY_COLUMNS}
                FROM storage.lifecycle_policies
                WHERE project_id = %s AND policy_id = %s
                """,
                (project_id, policy_id),
            )
            raw = cursor.fetchone()
            return None if raw is None else self._policy(_row(cursor, raw))
        finally:
            cursor.close()
            connection.close()

    def list_policies(
        self,
        *,
        project_id: str,
        anchor_policy_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[LifecyclePolicy, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT {_POLICY_COLUMNS}
                FROM storage.lifecycle_policies
                WHERE project_id = %s
                  AND (
                    %s::text IS NULL
                    OR (%s AND policy_id < %s)
                    OR (NOT %s AND policy_id > %s)
                  )
                ORDER BY
                  CASE WHEN %s THEN policy_id END DESC,
                  CASE WHEN NOT %s THEN policy_id END ASC
                LIMIT %s
                """,
                (
                    project_id,
                    anchor_policy_id,
                    before,
                    anchor_policy_id,
                    before,
                    anchor_policy_id,
                    before,
                    before,
                    limit,
                ),
            )
            return tuple(self._policy(row) for row in _rows(cursor, cursor.fetchall()))
        finally:
            cursor.close()
            connection.close()

    def all_policies(self, *, project_id: str) -> tuple[LifecyclePolicy, ...]:
        return self.list_policies(
            project_id=project_id,
            anchor_policy_id=None,
            before=False,
            limit=10001,
        )

    def save_policy(
        self,
        policy: LifecyclePolicy,
        *,
        expected_version: int | None,
        audit_event: LifecycleAuditEvent,
    ) -> LifecyclePolicy:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            if expected_version is None:
                cursor.execute(
                    """
                    INSERT INTO storage.lifecycle_policies (
                        project_id, policy_id, name, business_category, object_role,
                        action, minimum_age_days, priority, state, version, etag,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING policy_id
                    """,
                    self._policy_values(policy),
                )
            else:
                cursor.execute(
                    """
                    UPDATE storage.lifecycle_policies
                    SET name = %s,
                        business_category = %s,
                        object_role = %s,
                        action = %s,
                        minimum_age_days = %s,
                        priority = %s,
                        state = %s,
                        version = %s,
                        etag = %s,
                        updated_at = %s
                    WHERE project_id = %s AND policy_id = %s AND version = %s
                    RETURNING policy_id
                    """,
                    (
                        policy.name,
                        policy.business_category.value,
                        policy.object_role.value,
                        policy.action.value,
                        policy.minimum_age_days,
                        policy.priority,
                        policy.state.value,
                        policy.version,
                        policy.etag,
                        policy.updated_at,
                        policy.project_id,
                        policy.policy_id,
                        expected_version,
                    ),
                )
            if cursor.fetchone() is None:
                raise _version_conflict()
            self._insert_audit(cursor, audit_event)
            connection.commit()
            return policy
        except ProblemException:
            connection.rollback()
            raise
        except Exception as exc:
            connection.rollback()
            if getattr(exc, "sqlstate", None) == "23505":
                raise _unique_conflict() from exc
            raise
        finally:
            cursor.close()
            connection.close()

    def delete_policy(
        self,
        *,
        project_id: str,
        policy_id: str,
        expected_version: int,
        audit_event: LifecycleAuditEvent,
    ) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                DELETE FROM storage.lifecycle_policies
                WHERE project_id = %s AND policy_id = %s AND version = %s
                RETURNING policy_id
                """,
                (project_id, policy_id, expected_version),
            )
            if cursor.fetchone() is None:
                raise _version_conflict()
            self._insert_audit(cursor, audit_event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def append_audit(self, event: LifecycleAuditEvent) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._insert_audit(cursor, event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def list_audit(
        self,
        *,
        project_id: str,
        anchor_audit_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[LifecycleAuditEvent, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT {_AUDIT_COLUMNS}
                FROM storage.lifecycle_audit_events
                WHERE project_id = %s
                  AND (
                    %s::text IS NULL
                    OR (%s AND audit_id < %s)
                    OR (NOT %s AND audit_id > %s)
                  )
                ORDER BY
                  CASE WHEN %s THEN audit_id END DESC,
                  CASE WHEN NOT %s THEN audit_id END ASC
                LIMIT %s
                """,
                (
                    project_id,
                    anchor_audit_id,
                    before,
                    anchor_audit_id,
                    before,
                    anchor_audit_id,
                    before,
                    before,
                    limit,
                ),
            )
            return tuple(self._audit(row) for row in _rows(cursor, cursor.fetchall()))
        finally:
            cursor.close()
            connection.close()

    def get_managed_object(
        self, *, project_id: str, object_id: str
    ) -> ManagedStorageObjectRecord | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT {_MANAGED_OBJECT_COLUMNS}
                FROM storage.managed_objects
                WHERE project_id = %s AND object_id = %s
                """,
                (project_id, object_id),
            )
            raw = cursor.fetchone()
            return None if raw is None else self._managed_object(_row(cursor, raw))
        finally:
            cursor.close()
            connection.close()

    def list_managed_objects(
        self,
        *,
        project_id: str,
        anchor_object_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[ManagedStorageObjectRecord, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT {_MANAGED_OBJECT_COLUMNS}
                FROM storage.managed_objects
                WHERE project_id = %s
                  AND (
                    %s::text IS NULL
                    OR (%s AND object_id < %s)
                    OR (NOT %s AND object_id > %s)
                  )
                ORDER BY
                  CASE WHEN %s THEN object_id END DESC,
                  CASE WHEN NOT %s THEN object_id END ASC
                LIMIT %s
                """,
                (
                    project_id,
                    anchor_object_id,
                    before,
                    anchor_object_id,
                    before,
                    anchor_object_id,
                    before,
                    before,
                    limit,
                ),
            )
            return tuple(self._managed_object(row) for row in _rows(cursor, cursor.fetchall()))
        finally:
            cursor.close()
            connection.close()

    def save_managed_object(
        self,
        record: ManagedStorageObjectRecord,
        *,
        expected_version: int | None,
        audit_action: str,
        actor_id: str,
        request_id: str,
        before: ManagedStorageObjectRecord | None,
    ) -> ManagedStorageObjectRecord:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            if expected_version is None:
                cursor.execute(
                    """
                    INSERT INTO storage.managed_objects (
                        project_id, object_id, display_key, object_key, original_object_key,
                        physical_bytes, checksum_sha256, business_category, object_role,
                        storage_tier, status, active_reference_count, retention_until,
                        legal_hold, governance_hold, rebuild_source_id, recoverable_until,
                        version, etag, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s
                    ) RETURNING object_id
                    """,
                    self._managed_object_values(record),
                )
            else:
                cursor.execute(
                    """
                    UPDATE storage.managed_objects
                    SET display_key = %s,
                        object_key = %s,
                        original_object_key = %s,
                        physical_bytes = %s,
                        checksum_sha256 = %s,
                        business_category = %s,
                        object_role = %s,
                        storage_tier = %s,
                        status = %s,
                        active_reference_count = %s,
                        retention_until = %s,
                        legal_hold = %s,
                        governance_hold = %s,
                        rebuild_source_id = %s,
                        recoverable_until = %s,
                        version = %s,
                        etag = %s,
                        updated_at = %s
                    WHERE project_id = %s AND object_id = %s AND version = %s
                    RETURNING object_id
                    """,
                    (
                        record.display_key,
                        record.object_key,
                        record.original_object_key,
                        record.physical_bytes,
                        record.checksum_sha256,
                        record.business_category.value,
                        record.object_role.value,
                        record.storage_tier.value,
                        record.status.value,
                        record.active_reference_count,
                        record.retention_until,
                        record.legal_hold,
                        record.governance_hold,
                        record.rebuild_source_id,
                        record.recoverable_until,
                        record.version,
                        record.etag,
                        record.updated_at,
                        record.project_id,
                        record.object_id,
                        expected_version,
                    ),
                )
            if cursor.fetchone() is None:
                raise _version_conflict()
            self._insert_core_audit(
                cursor,
                project_id=record.project_id,
                actor_id=actor_id,
                action=audit_action,
                resource_type="storage_object",
                resource_id=record.object_id,
                request_id=request_id,
                before=None if before is None else before.public(now=record.updated_at),
                after=record.public(now=record.updated_at),
                details={
                    "status": record.status.value,
                    "storage_tier": record.storage_tier.value,
                    "version": record.version,
                },
            )
            connection.commit()
            return record
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def get_managed_multipart(
        self, *, project_id: str, multipart_id: str
    ) -> ManagedMultipartUploadRecord | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT {_MULTIPART_COLUMNS}
                FROM storage.managed_multipart_uploads
                WHERE project_id = %s AND multipart_id = %s
                """,
                (project_id, multipart_id),
            )
            raw = cursor.fetchone()
            return None if raw is None else self._multipart(_row(cursor, raw))
        finally:
            cursor.close()
            connection.close()

    def save_managed_multipart(
        self,
        record: ManagedMultipartUploadRecord,
        *,
        expected_version: int,
        audit_action: str,
        actor_id: str,
        request_id: str,
    ) -> ManagedMultipartUploadRecord:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE storage.managed_multipart_uploads
                SET status = %s, version = %s, etag = %s, updated_at = %s
                WHERE project_id = %s AND multipart_id = %s AND version = %s
                RETURNING multipart_id
                """,
                (
                    record.status,
                    record.version,
                    record.etag,
                    record.updated_at,
                    record.project_id,
                    record.multipart_id,
                    expected_version,
                ),
            )
            if cursor.fetchone() is None:
                raise _version_conflict()
            self._insert_core_audit(
                cursor,
                project_id=record.project_id,
                actor_id=actor_id,
                action=audit_action,
                resource_type="storage_multipart",
                resource_id=record.multipart_id,
                request_id=request_id,
                before=None,
                after=record.public(),
                details={"status": record.status, "version": record.version},
            )
            connection.commit()
            return record
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def begin_object_operation(
        self, operation: StorageObjectOperation
    ) -> tuple[StorageObjectOperation, bool]:
        resource_id = operation.object_id or operation.multipart_id
        assert resource_id is not None
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO storage.object_operations (
                    project_id, operation_id, object_id, multipart_id, action, status,
                    idempotency_key, request_fingerprint, actor_id, request_id, attempt,
                    error_code, created_at, completed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING operation_id
                """,
                self._operation_values(operation),
            )
            inserted = cursor.fetchone() is not None
            if not inserted:
                cursor.execute(
                    f"""
                    SELECT {_OPERATION_COLUMNS}
                    FROM storage.object_operations
                    WHERE project_id = %s AND action = %s
                      AND COALESCE(object_id, multipart_id) = %s
                      AND idempotency_key = %s
                    FOR UPDATE
                    """,
                    (
                        operation.project_id,
                        operation.action,
                        resource_id,
                        operation.idempotency_key,
                    ),
                )
                raw = cursor.fetchone()
                if raw is None:
                    raise RuntimeError("storage operation replay row is unavailable")
                existing = self._operation(_row(cursor, raw))
                if existing.request_fingerprint != operation.request_fingerprint:
                    raise idempotency_conflict()
                connection.commit()
                return existing, True
            connection.commit()
            return operation, False
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def finish_object_operation(
        self,
        *,
        project_id: str,
        operation_id: str,
        status: str,
        error_code: str | None,
        completed_at: datetime,
    ) -> StorageObjectOperation:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                UPDATE storage.object_operations
                SET status = %s, error_code = %s, attempt = attempt + 1,
                    completed_at = %s
                WHERE project_id = %s AND operation_id = %s
                RETURNING {_OPERATION_COLUMNS}
                """,
                (status, error_code, completed_at, project_id, operation_id),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise RuntimeError("storage operation disappeared")
            value = self._operation(_row(cursor, raw))
            connection.commit()
            return value
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def policy_candidates(
        self,
        *,
        project_id: str,
        policy: LifecyclePolicy,
        older_than: datetime,
        limit: int,
    ) -> tuple[ManagedStorageObjectRecord, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT {_MANAGED_OBJECT_COLUMNS}
                FROM storage.managed_objects
                WHERE project_id = %s AND status = 'ACTIVE'
                  AND business_category = %s AND object_role = %s
                  AND updated_at <= %s
                ORDER BY object_id
                LIMIT %s
                """,
                (
                    project_id,
                    policy.business_category.value,
                    policy.object_role.value,
                    older_than,
                    limit,
                ),
            )
            return tuple(self._managed_object(row) for row in _rows(cursor, cursor.fetchall()))
        finally:
            cursor.close()
            connection.close()

    def create_execution(
        self,
        execution: LifecycleExecution,
        *,
        candidates: Sequence[ManagedStorageObjectRecord],
        actor_id: str,
        request_id: str,
    ) -> LifecycleExecution:
        candidate_by_id = {candidate.object_id: candidate for candidate in candidates}
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO storage.lifecycle_executions (
                    project_id, execution_id, policy_id, policy_version, action,
                    production, production_execution_approved, request_fingerprint,
                    status, next_batch, created_at, updated_at, dry_run, plan_hash,
                    requested_by
                ) VALUES (
                    %s, %s, %s, %s, %s, false, false, %s, %s, 0, %s, %s,
                    true, %s, %s
                )
                """,
                (
                    execution.project_id,
                    execution.execution_id,
                    execution.policy_id,
                    execution.policy_version,
                    execution.action.value,
                    execution.plan_hash,
                    execution.status.value,
                    execution.created_at,
                    execution.updated_at,
                    execution.plan_hash,
                    execution.requested_by,
                ),
            )
            for item in execution.items:
                candidate = candidate_by_id[item.object_id]
                cursor.execute(
                    """
                    INSERT INTO storage.lifecycle_execution_items (
                        project_id, execution_id, physical_instance_id, logical_object_id,
                        object_role, rebuild_source_id, active_reference_count,
                        protection_verified, status, attempt, last_error, updated_at,
                        object_id, retention_active, legal_hold, governance_hold,
                        blocked_reasons, last_error_code
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, true, %s, 0, NULL, %s,
                        %s, %s, %s, %s, %s::jsonb, NULL
                    )
                    """,
                    (
                        execution.project_id,
                        execution.execution_id,
                        item.physical_instance_id,
                        candidate.object_id,
                        candidate.object_role.value,
                        candidate.rebuild_source_id,
                        candidate.active_reference_count,
                        item.status,
                        execution.created_at,
                        candidate.object_id,
                        candidate.retention_until is not None
                        and candidate.retention_until > execution.created_at,
                        candidate.legal_hold,
                        candidate.governance_hold,
                        json.dumps(item.blocked_reasons),
                    ),
                )
            self._insert_execution_log(
                cursor,
                project_id=execution.project_id,
                execution_id=execution.execution_id,
                level="INFO",
                event="storage.lifecycle_execution.dry_run_created",
                details={
                    "total_items": execution.total_items,
                    "blocked_items": execution.blocked_items,
                },
                occurred_at=execution.created_at,
            )
            self._insert_core_audit(
                cursor,
                project_id=execution.project_id,
                actor_id=actor_id,
                action="storage.lifecycle_execution.dry_run_created",
                resource_type="storage_lifecycle_execution",
                resource_id=execution.execution_id,
                request_id=request_id,
                before=None,
                after=execution,
                details={
                    "policy_id": execution.policy_id,
                    "policy_version": execution.policy_version,
                    "total_items": execution.total_items,
                    "blocked_items": execution.blocked_items,
                },
            )
            connection.commit()
            return execution
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def get_execution(self, *, project_id: str, execution_id: str) -> LifecycleExecution | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            return self._read_execution(cursor, project_id=project_id, execution_id=execution_id)
        finally:
            cursor.close()
            connection.close()

    def approve_execution(
        self,
        *,
        project_id: str,
        execution_id: str,
        approval_id: str,
        plan_hash: str,
        approver_id: str,
        justification: str,
        approved_at: datetime,
        expires_at: datetime,
        request_id: str,
    ) -> LifecycleExecution:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT requested_by, plan_hash, status
                FROM storage.lifecycle_executions
                WHERE project_id = %s AND execution_id = %s
                FOR UPDATE
                """,
                (project_id, execution_id),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise _version_conflict()
            current = _row(cursor, raw)
            if str(current["plan_hash"]) != plan_hash:
                raise _version_conflict()
            if str(current["requested_by"]) == approver_id:
                raise problem(
                    status=409,
                    code="LIFECYCLE_SELF_APPROVAL_DENIED",
                    title="Independent approval required",
                    detail="The requester cannot approve the same physical execution.",
                )
            cursor.execute(
                """
                INSERT INTO storage.lifecycle_execution_approvals (
                    project_id, approval_id, execution_id, plan_hash, requested_by,
                    approved_by, justification, approved_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    project_id,
                    approval_id,
                    execution_id,
                    plan_hash,
                    str(current["requested_by"]),
                    approver_id,
                    justification,
                    approved_at,
                    expires_at,
                ),
            )
            cursor.execute(
                """
                UPDATE storage.lifecycle_executions
                SET status = 'APPROVED', approval_id = %s, approved_by = %s,
                    approved_at = %s, updated_at = %s
                WHERE project_id = %s AND execution_id = %s
                """,
                (approval_id, approver_id, approved_at, approved_at, project_id, execution_id),
            )
            self._insert_execution_log(
                cursor,
                project_id=project_id,
                execution_id=execution_id,
                level="INFO",
                event="storage.lifecycle_execution.approved",
                details={"approval_id": approval_id},
                occurred_at=approved_at,
            )
            self._insert_core_audit(
                cursor,
                project_id=project_id,
                actor_id=approver_id,
                action="storage.lifecycle_execution.approved",
                resource_type="storage_lifecycle_execution",
                resource_id=execution_id,
                request_id=request_id,
                before=None,
                after=None,
                details={"approval_id": approval_id},
            )
            value = self._read_execution(cursor, project_id=project_id, execution_id=execution_id)
            assert value is not None
            connection.commit()
            return value
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def queue_execution(
        self,
        *,
        project_id: str,
        execution_id: str,
        approval_id: str,
        plan_hash: str,
        queued_at: datetime,
        request_id: str,
    ) -> LifecycleExecution:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT approval_id, execution_id, plan_hash, approved_by, approved_at,
                       expires_at, consumed_at
                FROM storage.lifecycle_execution_approvals
                WHERE project_id = %s AND approval_id = %s
                FOR UPDATE
                """,
                (project_id, approval_id),
            )
            raw = cursor.fetchone()
            approval = None if raw is None else _row(cursor, raw)
            if (
                approval is None
                or str(approval["execution_id"]) != execution_id
                or str(approval["plan_hash"]) != plan_hash
                or cast(datetime, approval["expires_at"]) <= queued_at
                or approval.get("consumed_at") is not None
            ):
                raise problem(
                    status=409,
                    code="LIFECYCLE_APPROVAL_INVALID",
                    title="Lifecycle approval is invalid",
                    detail="The approval is expired, consumed, or bound to another plan.",
                )
            cursor.execute(
                """
                UPDATE storage.lifecycle_execution_approvals
                SET consumed_at = %s
                WHERE project_id = %s AND approval_id = %s
                """,
                (queued_at, project_id, approval_id),
            )
            cursor.execute(
                """
                UPDATE storage.lifecycle_executions
                SET production = true, production_execution_approved = true,
                    status = 'QUEUED', dry_run = false, updated_at = %s,
                    temporal_workflow_id = %s
                WHERE project_id = %s AND execution_id = %s
                  AND approval_id = %s AND plan_hash = %s AND status = 'APPROVED'
                RETURNING execution_id
                """,
                (
                    queued_at,
                    f"storage-lifecycle/{project_id}/{execution_id}",
                    project_id,
                    execution_id,
                    approval_id,
                    plan_hash,
                ),
            )
            if cursor.fetchone() is None:
                raise _version_conflict()
            self._insert_execution_log(
                cursor,
                project_id=project_id,
                execution_id=execution_id,
                level="INFO",
                event="storage.lifecycle_execution.queued",
                details={"approval_id": approval_id},
                occurred_at=queued_at,
            )
            context = current_request_context()
            event_id = str(uuid4())
            workflow_id = f"storage-lifecycle/{project_id}/{execution_id}"
            envelope = {
                "event_id": event_id,
                "event_type": "storage.lifecycle.execution.requested.v1",
                "schema_version": 1,
                "aggregate_type": "storage_lifecycle_execution",
                "aggregate_id": execution_id,
                "organization_id": context.organization_id,
                "project_id": project_id,
                "region_code": context.region_code,
                "occurred_at": queued_at.isoformat(),
                "trace_id": request_id,
                "payload": {
                    "workflow_id": workflow_id,
                    "execution_id": execution_id,
                },
            }
            cursor.execute(
                """
                INSERT INTO core.outbox_events (
                    event_id, organization_id, project_id, region_code, event_type, envelope,
                    occurred_at, available_at
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                """,
                (
                    event_id,
                    context.organization_id,
                    project_id,
                    context.region_code,
                    "storage.lifecycle.execution.requested.v1",
                    json.dumps(envelope, sort_keys=True, separators=(",", ":")),
                    queued_at,
                    queued_at,
                ),
            )
            self._insert_core_audit(
                cursor,
                project_id=project_id,
                actor_id=context.subject_id or "unknown",
                action="storage.lifecycle_execution.queued",
                resource_type="storage_lifecycle_execution",
                resource_id=execution_id,
                request_id=request_id,
                before=None,
                after=None,
                details={"approval_id": approval_id},
            )
            value = self._read_execution(cursor, project_id=project_id, execution_id=execution_id)
            assert value is not None
            connection.commit()
            return value
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def list_executions(
        self,
        *,
        project_id: str,
        anchor_execution_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[LifecycleExecution, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT execution_id
                FROM storage.lifecycle_executions
                WHERE project_id = %s
                  AND (
                    %s::text IS NULL
                    OR (%s AND execution_id < %s)
                    OR (NOT %s AND execution_id > %s)
                  )
                ORDER BY
                  CASE WHEN %s THEN execution_id END DESC,
                  CASE WHEN NOT %s THEN execution_id END ASC
                LIMIT %s
                """,
                (
                    project_id,
                    anchor_execution_id,
                    before,
                    anchor_execution_id,
                    before,
                    anchor_execution_id,
                    before,
                    before,
                    limit,
                ),
            )
            ids = [str(next(iter(_row(cursor, raw).values()))) for raw in cursor.fetchall()]
            values: list[LifecycleExecution] = []
            for execution_id in ids:
                value = self._read_execution(
                    cursor, project_id=project_id, execution_id=execution_id
                )
                if value is not None:
                    values.append(value)
            return tuple(values)
        finally:
            cursor.close()
            connection.close()

    def list_execution_logs(
        self,
        *,
        project_id: str,
        execution_id: str,
        anchor_sequence: int | None,
        before: bool,
        limit: int,
    ) -> tuple[LifecycleExecutionLog, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT sequence, project_id, execution_id, level, event, details, occurred_at
                FROM storage.lifecycle_execution_logs
                WHERE project_id = %s AND execution_id = %s
                  AND (
                    %s::bigint IS NULL
                    OR (%s AND sequence < %s)
                    OR (NOT %s AND sequence > %s)
                  )
                ORDER BY
                  CASE WHEN %s THEN sequence END DESC,
                  CASE WHEN NOT %s THEN sequence END ASC
                LIMIT %s
                """,
                (
                    project_id,
                    execution_id,
                    anchor_sequence,
                    before,
                    anchor_sequence,
                    before,
                    anchor_sequence,
                    before,
                    before,
                    limit,
                ),
            )
            return tuple(
                LifecycleExecutionLog(
                    sequence=int(cast(Any, row["sequence"])),
                    project_id=str(row["project_id"]),
                    execution_id=str(row["execution_id"]),
                    level=str(row["level"]),
                    event=str(row["event"]),
                    details=_details(row["details"]),
                    occurred_at=cast(datetime, row["occurred_at"]),
                )
                for row in _rows(cursor, cursor.fetchall())
            )
        finally:
            cursor.close()
            connection.close()

    def cancel_execution(
        self,
        *,
        project_id: str,
        execution_id: str,
        actor_id: str,
        reason: str,
        cancelled_at: datetime,
        request_id: str,
    ) -> LifecycleExecution:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE storage.lifecycle_executions
                SET status = 'CANCELLED', updated_at = %s, completed_at = %s,
                    last_error = 'LIFECYCLE_EXECUTION_CANCELLED'
                WHERE project_id = %s AND execution_id = %s
                  AND status IN (
                    'AWAITING_APPROVAL', 'APPROVED', 'QUEUED', 'RUNNING',
                    'BLOCKED', 'FAILED'
                  )
                RETURNING execution_id
                """,
                (cancelled_at, cancelled_at, project_id, execution_id),
            )
            if cursor.fetchone() is None:
                raise problem(
                    status=409,
                    code="LIFECYCLE_EXECUTION_NOT_CANCELLABLE",
                    title="Lifecycle execution cannot be cancelled",
                    detail="Only a non-terminal lifecycle execution can be cancelled.",
                )
            cursor.execute(
                """
                UPDATE core.outbox_events
                SET published_at = %s, last_error = 'LIFECYCLE_EXECUTION_CANCELLED',
                    claimed_by = NULL, claim_token = NULL, claimed_until = NULL
                WHERE project_id = %s
                  AND event_type = 'storage.lifecycle.execution.requested.v1'
                  AND envelope ->> 'aggregate_id' = %s
                  AND published_at IS NULL
                """,
                (cancelled_at, project_id, execution_id),
            )
            self._insert_execution_log(
                cursor,
                project_id=project_id,
                execution_id=execution_id,
                level="WARNING",
                event="storage.lifecycle_execution.cancelled",
                details={"actor_id": actor_id, "reason": reason},
                occurred_at=cancelled_at,
            )
            self._insert_core_audit(
                cursor,
                project_id=project_id,
                actor_id=actor_id,
                action="storage.lifecycle_execution.cancelled",
                resource_type="storage_lifecycle_execution",
                resource_id=execution_id,
                request_id=request_id,
                before=None,
                after=None,
                details={"reason": reason},
            )
            value = self._read_execution(
                cursor,
                project_id=project_id,
                execution_id=execution_id,
            )
            assert value is not None
            connection.commit()
            return value
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def retry_execution(
        self,
        *,
        project_id: str,
        execution_id: str,
        plan_hash: str,
        actor_id: str,
        reason: str,
        retried_at: datetime,
        request_id: str,
    ) -> LifecycleExecution:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE storage.lifecycle_executions
                SET status = 'QUEUED', updated_at = %s, completed_at = NULL,
                    last_error = NULL
                WHERE project_id = %s AND execution_id = %s
                  AND status IN ('BLOCKED', 'FAILED')
                  AND plan_hash = %s AND approval_id IS NOT NULL
                  AND production_execution_approved
                RETURNING approval_id
                """,
                (retried_at, project_id, execution_id, plan_hash),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise problem(
                    status=409,
                    code="LIFECYCLE_EXECUTION_NOT_RETRYABLE",
                    title="Lifecycle execution cannot be retried",
                    detail="Only an approved failed or blocked production execution is retryable.",
                )
            approval_id = str(next(iter(_row(cursor, raw).values())))
            cursor.execute(
                """
                UPDATE storage.lifecycle_execution_items
                SET status = 'PENDING', blocked_reasons = '[]'::jsonb,
                    last_error = NULL, last_error_code = NULL, updated_at = %s
                WHERE project_id = %s AND execution_id = %s
                  AND status IN ('BLOCKED', 'FAILED') AND attempt > 0
                """,
                (retried_at, project_id, execution_id),
            )
            self._insert_execution_requested_outbox(
                cursor,
                project_id=project_id,
                execution_id=execution_id,
                occurred_at=retried_at,
                request_id=request_id,
            )
            self._insert_execution_log(
                cursor,
                project_id=project_id,
                execution_id=execution_id,
                level="WARNING",
                event="storage.lifecycle_execution.retry_queued",
                details={
                    "actor_id": actor_id,
                    "approval_id": approval_id,
                    "reason": reason,
                },
                occurred_at=retried_at,
            )
            self._insert_core_audit(
                cursor,
                project_id=project_id,
                actor_id=actor_id,
                action="storage.lifecycle_execution.retry_queued",
                resource_type="storage_lifecycle_execution",
                resource_id=execution_id,
                request_id=request_id,
                before=None,
                after=None,
                details={"approval_id": approval_id, "reason": reason},
            )
            value = self._read_execution(
                cursor,
                project_id=project_id,
                execution_id=execution_id,
            )
            assert value is not None
            connection.commit()
            return value
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def get_schedule(self, *, project_id: str, schedule_id: str) -> LifecycleSchedule | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT {_SCHEDULE_COLUMNS}
                FROM storage.lifecycle_schedules
                WHERE project_id = %s AND schedule_id = %s
                """,
                (project_id, schedule_id),
            )
            raw = cursor.fetchone()
            return None if raw is None else self._schedule(_row(cursor, raw))
        finally:
            cursor.close()
            connection.close()

    def list_schedules(
        self,
        *,
        project_id: str,
        anchor_schedule_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[LifecycleSchedule, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT {_SCHEDULE_COLUMNS}
                FROM storage.lifecycle_schedules
                WHERE project_id = %s
                  AND (
                    %s::text IS NULL
                    OR (%s AND schedule_id < %s)
                    OR (NOT %s AND schedule_id > %s)
                  )
                ORDER BY
                  CASE WHEN %s THEN schedule_id END DESC,
                  CASE WHEN NOT %s THEN schedule_id END ASC
                LIMIT %s
                """,
                (
                    project_id,
                    anchor_schedule_id,
                    before,
                    anchor_schedule_id,
                    before,
                    anchor_schedule_id,
                    before,
                    before,
                    limit,
                ),
            )
            return tuple(self._schedule(row) for row in _rows(cursor, cursor.fetchall()))
        finally:
            cursor.close()
            connection.close()

    def save_schedule(
        self,
        schedule: LifecycleSchedule,
        *,
        expected_version: int | None,
        actor_id: str,
        action: str,
        request_id: str,
    ) -> LifecycleSchedule:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            if expected_version is None:
                cursor.execute(
                    """
                    INSERT INTO storage.lifecycle_schedules (
                        project_id, schedule_id, policy_id, interval_seconds, enabled,
                        next_run_at, last_execution_id, version, etag, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING schedule_id
                    """,
                    self._schedule_values(schedule),
                )
            else:
                cursor.execute(
                    """
                    UPDATE storage.lifecycle_schedules
                    SET policy_id = %s, interval_seconds = %s, enabled = %s,
                        next_run_at = %s, last_execution_id = %s, version = %s,
                        etag = %s, updated_at = %s
                    WHERE project_id = %s AND schedule_id = %s AND version = %s
                    RETURNING schedule_id
                    """,
                    (
                        schedule.policy_id,
                        schedule.interval_seconds,
                        schedule.enabled,
                        schedule.next_run_at,
                        schedule.last_execution_id,
                        schedule.version,
                        schedule.etag,
                        schedule.updated_at,
                        schedule.project_id,
                        schedule.schedule_id,
                        expected_version,
                    ),
                )
            if cursor.fetchone() is None:
                raise _version_conflict()
            self._insert_core_audit(
                cursor,
                project_id=schedule.project_id,
                actor_id=actor_id,
                action=action,
                resource_type="storage_lifecycle_schedule",
                resource_id=schedule.schedule_id,
                request_id=request_id,
                before=None,
                after=schedule,
                details={"policy_id": schedule.policy_id, "enabled": schedule.enabled},
            )
            connection.commit()
            return schedule
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def has_schedule_for_policy(self, *, project_id: str, policy_id: str) -> bool:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM storage.lifecycle_schedules
                    WHERE project_id = %s AND policy_id = %s
                )
                """,
                (project_id, policy_id),
            )
            raw = cursor.fetchone()
            assert raw is not None
            return bool(next(iter(_row(cursor, raw).values())))
        finally:
            cursor.close()
            connection.close()

    def delete_schedule(
        self,
        *,
        project_id: str,
        schedule_id: str,
        expected_version: int,
        actor_id: str,
        request_id: str,
    ) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                DELETE FROM storage.lifecycle_schedules
                WHERE project_id = %s AND schedule_id = %s AND version = %s
                RETURNING policy_id
                """,
                (project_id, schedule_id, expected_version),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise _version_conflict()
            policy_id = str(next(iter(_row(cursor, raw).values())))
            self._insert_core_audit(
                cursor,
                project_id=project_id,
                actor_id=actor_id,
                action="storage.lifecycle_schedule.deleted",
                resource_type="storage_lifecycle_schedule",
                resource_id=schedule_id,
                request_id=request_id,
                before=None,
                after=None,
                details={"policy_id": policy_id},
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def link_schedule_execution(
        self,
        *,
        project_id: str,
        schedule_id: str,
        execution_id: str,
        linked_at: datetime,
    ) -> LifecycleSchedule:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT version, last_execution_id
                FROM storage.lifecycle_schedules
                WHERE project_id = %s AND schedule_id = %s AND enabled
                FOR UPDATE
                """,
                (project_id, schedule_id),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise problem(
                    status=409,
                    code="LIFECYCLE_SCHEDULE_NOT_ENABLED",
                    title="Lifecycle schedule is not enabled",
                    detail="A paused or deleted schedule cannot record a new execution.",
                )
            current = _row(cursor, raw)
            if (
                current.get("last_execution_id") is not None
                and str(current["last_execution_id"]) == execution_id
            ):
                cursor.execute(
                    f"""
                    SELECT {_SCHEDULE_COLUMNS}
                    FROM storage.lifecycle_schedules
                    WHERE project_id = %s AND schedule_id = %s
                    """,
                    (project_id, schedule_id),
                )
                existing = cursor.fetchone()
                assert existing is not None
                connection.commit()
                return self._schedule(_row(cursor, existing))
            version = int(cast(Any, current["version"])) + 1
            etag = f'"v{version}"'
            cursor.execute(
                """
                UPDATE storage.lifecycle_schedules
                SET last_execution_id = %s, version = %s, etag = %s, updated_at = %s
                WHERE project_id = %s AND schedule_id = %s
                """,
                (execution_id, version, etag, linked_at, project_id, schedule_id),
            )
            cursor.execute(
                f"""
                SELECT {_SCHEDULE_COLUMNS}
                FROM storage.lifecycle_schedules
                WHERE project_id = %s AND schedule_id = %s
                """,
                (project_id, schedule_id),
            )
            result = cursor.fetchone()
            assert result is not None
            value = self._schedule(_row(cursor, result))
            connection.commit()
            return value
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def enqueue_due_schedules(
        self,
        *,
        project_id: str,
        region_code: str,
        now: datetime,
        limit: int,
    ) -> int:
        context = current_request_context()
        if context.organization_id is None:
            raise RuntimeError("storage schedule enqueue requires an organization scope")
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT schedule_id, next_run_at, interval_seconds
                FROM storage.lifecycle_schedules
                WHERE project_id = %s AND enabled AND next_run_at <= %s
                ORDER BY next_run_at, schedule_id
                FOR UPDATE SKIP LOCKED
                LIMIT %s
                """,
                (project_id, now, limit),
            )
            due = _rows(cursor, cursor.fetchall())
            for row in due:
                schedule_id = str(row["schedule_id"])
                scheduled_for = cast(datetime, row["next_run_at"])
                interval_seconds = int(cast(Any, row["interval_seconds"]))
                elapsed = max(0, int((now - scheduled_for).total_seconds()))
                periods = elapsed // interval_seconds + 1
                next_run_at = scheduled_for + timedelta(seconds=periods * interval_seconds)
                cursor.execute(
                    """
                    UPDATE storage.lifecycle_schedules
                    SET next_run_at = %s, version = version + 1,
                        etag = '"v' || (version + 1)::text || '"', updated_at = %s
                    WHERE project_id = %s AND schedule_id = %s
                    """,
                    (next_run_at, now, project_id, schedule_id),
                )
                event_id = str(uuid4())
                envelope = {
                    "event_id": event_id,
                    "event_type": "storage.lifecycle.schedule.due.v1",
                    "schema_version": 1,
                    "aggregate_type": "storage_lifecycle_schedule",
                    "aggregate_id": schedule_id,
                    "organization_id": context.organization_id,
                    "project_id": project_id,
                    "region_code": region_code,
                    "occurred_at": now.isoformat(),
                    "trace_id": f"storage-schedule/{schedule_id}/{scheduled_for.isoformat()}",
                    "payload": {
                        "schedule_id": schedule_id,
                        "scheduled_for": scheduled_for.isoformat(),
                    },
                }
                cursor.execute(
                    """
                    INSERT INTO core.outbox_events (
                        event_id, organization_id, project_id, region_code, event_type, envelope,
                        occurred_at, available_at
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    """,
                    (
                        event_id,
                        context.organization_id,
                        project_id,
                        region_code,
                        "storage.lifecycle.schedule.due.v1",
                        json.dumps(envelope, sort_keys=True, separators=(",", ":")),
                        now,
                        now,
                    ),
                )
            connection.commit()
            return len(due)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def _read_execution(
        self,
        cursor: DbApiCursor,
        *,
        project_id: str,
        execution_id: str,
    ) -> LifecycleExecution | None:
        cursor.execute(
            f"""
            SELECT {_EXECUTION_COLUMNS}
            FROM storage.lifecycle_executions
            WHERE project_id = %s AND execution_id = %s
            """,
            (project_id, execution_id),
        )
        raw = cursor.fetchone()
        if raw is None:
            return None
        execution_row = _row(cursor, raw)
        cursor.execute(
            """
            SELECT object_id, physical_instance_id, status, attempt, blocked_reasons,
                   last_error_code
            FROM storage.lifecycle_execution_items
            WHERE project_id = %s AND execution_id = %s
            ORDER BY physical_instance_id
            """,
            (project_id, execution_id),
        )
        items = tuple(
            LifecycleExecutionItem(
                object_id=str(row["object_id"]),
                physical_instance_id=str(row["physical_instance_id"]),
                status=str(row["status"]),
                attempt=int(cast(Any, row["attempt"])),
                blocked_reasons=_string_tuple(row["blocked_reasons"]),
                last_error_code=None
                if row.get("last_error_code") is None
                else str(row["last_error_code"]),
            )
            for row in _rows(cursor, cursor.fetchall())
        )
        return LifecycleExecution(
            execution_id=str(execution_row["execution_id"]),
            project_id=str(execution_row["project_id"]),
            policy_id=str(execution_row["policy_id"]),
            policy_version=int(cast(Any, execution_row["policy_version"])),
            action=LifecyclePolicyAction(str(execution_row["action"])),
            status=LifecycleExecutionStatus(str(execution_row["status"])),
            dry_run=bool(execution_row["dry_run"]),
            plan_hash=str(execution_row["plan_hash"]),
            approval_id=None
            if execution_row.get("approval_id") is None
            else str(execution_row["approval_id"]),
            requested_by=str(execution_row["requested_by"]),
            approved_by=None
            if execution_row.get("approved_by") is None
            else str(execution_row["approved_by"]),
            total_items=len(items),
            processed_items=sum(item.status == "PROCESSED" for item in items),
            blocked_items=sum(item.status == "BLOCKED" for item in items),
            failed_items=sum(item.status == "FAILED" for item in items),
            next_batch=int(cast(Any, execution_row["next_batch"])),
            items=items,
            created_at=cast(datetime, execution_row["created_at"]),
            updated_at=cast(datetime, execution_row["updated_at"]),
        )

    @staticmethod
    def _managed_object_values(record: ManagedStorageObjectRecord) -> tuple[object, ...]:
        return (
            record.project_id,
            record.object_id,
            record.display_key,
            record.object_key,
            record.original_object_key,
            record.physical_bytes,
            record.checksum_sha256,
            record.business_category.value,
            record.object_role.value,
            record.storage_tier.value,
            record.status.value,
            record.active_reference_count,
            record.retention_until,
            record.legal_hold,
            record.governance_hold,
            record.rebuild_source_id,
            record.recoverable_until,
            record.version,
            record.etag,
            record.created_at,
            record.updated_at,
        )

    @staticmethod
    def _managed_object(row: Mapping[str, object]) -> ManagedStorageObjectRecord:
        return ManagedStorageObjectRecord(
            object_id=str(row["object_id"]),
            project_id=str(row["project_id"]),
            display_key=str(row["display_key"]),
            object_key=str(row["object_key"]),
            original_object_key=str(row["original_object_key"]),
            physical_bytes=_byte_string(row["physical_bytes"]),
            checksum_sha256=str(row["checksum_sha256"]),
            business_category=BusinessCapacityCategory(str(row["business_category"])),
            object_role=ObjectRole(str(row["object_role"])),
            storage_tier=StorageTier(str(row["storage_tier"])),
            status=StorageObjectStatus(str(row["status"])),
            active_reference_count=int(cast(Any, row["active_reference_count"])),
            retention_until=cast(datetime | None, row.get("retention_until")),
            legal_hold=bool(row["legal_hold"]),
            governance_hold=bool(row["governance_hold"]),
            rebuild_source_id=None
            if row.get("rebuild_source_id") is None
            else str(row["rebuild_source_id"]),
            recoverable_until=cast(datetime | None, row.get("recoverable_until")),
            version=int(cast(Any, row["version"])),
            etag=str(row["etag"]),
            created_at=cast(datetime, row["created_at"]),
            updated_at=cast(datetime, row["updated_at"]),
        )

    @staticmethod
    def _multipart(row: Mapping[str, object]) -> ManagedMultipartUploadRecord:
        return ManagedMultipartUploadRecord(
            multipart_id=str(row["multipart_id"]),
            project_id=str(row["project_id"]),
            display_key=str(row["display_key"]),
            object_key=str(row["object_key"]),
            upload_id=str(row["upload_id"]),
            received_bytes=_byte_string(row["received_bytes"]),
            part_count=int(cast(Any, row["part_count"])),
            status=str(row["status"]),
            started_at=cast(datetime, row["started_at"]),
            updated_at=cast(datetime, row["updated_at"]),
            version=int(cast(Any, row["version"])),
            etag=str(row["etag"]),
        )

    @staticmethod
    def _operation_values(operation: StorageObjectOperation) -> tuple[object, ...]:
        return (
            operation.project_id,
            operation.operation_id,
            operation.object_id,
            operation.multipart_id,
            operation.action,
            operation.status,
            operation.idempotency_key,
            operation.request_fingerprint,
            operation.actor_id,
            operation.request_id,
            operation.attempt,
            operation.error_code,
            operation.created_at,
            operation.completed_at,
        )

    @staticmethod
    def _operation(row: Mapping[str, object]) -> StorageObjectOperation:
        return StorageObjectOperation(
            project_id=str(row["project_id"]),
            operation_id=str(row["operation_id"]),
            object_id=None if row.get("object_id") is None else str(row["object_id"]),
            multipart_id=None if row.get("multipart_id") is None else str(row["multipart_id"]),
            action=str(row["action"]),
            status=str(row["status"]),
            idempotency_key=str(row["idempotency_key"]),
            request_fingerprint=str(row["request_fingerprint"]),
            actor_id=str(row["actor_id"]),
            request_id=str(row["request_id"]),
            attempt=int(cast(Any, row["attempt"])),
            error_code=None if row.get("error_code") is None else str(row["error_code"]),
            created_at=cast(datetime, row["created_at"]),
            completed_at=cast(datetime | None, row.get("completed_at")),
        )

    @staticmethod
    def _schedule_values(schedule: LifecycleSchedule) -> tuple[object, ...]:
        return (
            schedule.project_id,
            schedule.schedule_id,
            schedule.policy_id,
            schedule.interval_seconds,
            schedule.enabled,
            schedule.next_run_at,
            schedule.last_execution_id,
            schedule.version,
            schedule.etag,
            schedule.created_at,
            schedule.updated_at,
        )

    @staticmethod
    def _schedule(row: Mapping[str, object]) -> LifecycleSchedule:
        return LifecycleSchedule(
            schedule_id=str(row["schedule_id"]),
            project_id=str(row["project_id"]),
            policy_id=str(row["policy_id"]),
            interval_seconds=int(cast(Any, row["interval_seconds"])),
            enabled=bool(row["enabled"]),
            next_run_at=cast(datetime, row["next_run_at"]),
            last_execution_id=None
            if row.get("last_execution_id") is None
            else str(row["last_execution_id"]),
            version=int(cast(Any, row["version"])),
            etag=str(row["etag"]),
            created_at=cast(datetime, row["created_at"]),
            updated_at=cast(datetime, row["updated_at"]),
        )

    @staticmethod
    def _insert_core_audit(
        cursor: DbApiCursor,
        *,
        project_id: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
        before: BaseModel | None,
        after: BaseModel | None,
        details: Mapping[str, object],
    ) -> None:
        cursor.execute(
            """
            INSERT INTO core.audit_events (
                audit_id, project_id, region_code, actor_id, action, resource_type,
                resource_id, request_id, before_hash, after_hash, details, occurred_at
            ) VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
            """,
            (
                str(uuid4()),
                project_id,
                actor_id,
                action,
                resource_type,
                resource_id,
                request_id,
                None if before is None else canonical_hash(before.model_dump(mode="json")),
                None if after is None else canonical_hash(after.model_dump(mode="json")),
                json.dumps(details, sort_keys=True, separators=(",", ":"), default=str),
            ),
        )

    @staticmethod
    def _insert_execution_requested_outbox(
        cursor: DbApiCursor,
        *,
        project_id: str,
        execution_id: str,
        occurred_at: datetime,
        request_id: str,
    ) -> None:
        context = current_request_context()
        event_id = str(uuid4())
        workflow_id = f"storage-lifecycle/{project_id}/{execution_id}"
        envelope = {
            "event_id": event_id,
            "event_type": "storage.lifecycle.execution.requested.v1",
            "schema_version": 1,
            "aggregate_type": "storage_lifecycle_execution",
            "aggregate_id": execution_id,
            "organization_id": context.organization_id,
            "project_id": project_id,
            "region_code": context.region_code,
            "occurred_at": occurred_at.isoformat(),
            "trace_id": request_id,
            "payload": {
                "workflow_id": workflow_id,
                "execution_id": execution_id,
            },
        }
        cursor.execute(
            """
            INSERT INTO core.outbox_events (
                event_id, organization_id, project_id, region_code, event_type, envelope,
                occurred_at, available_at
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            """,
            (
                event_id,
                context.organization_id,
                project_id,
                context.region_code,
                "storage.lifecycle.execution.requested.v1",
                json.dumps(envelope, sort_keys=True, separators=(",", ":")),
                occurred_at,
                occurred_at,
            ),
        )

    @staticmethod
    def _insert_execution_log(
        cursor: DbApiCursor,
        *,
        project_id: str,
        execution_id: str,
        level: str,
        event: str,
        details: Mapping[str, object],
        occurred_at: datetime,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO storage.lifecycle_execution_logs (
                project_id, execution_id, level, event, details, occurred_at
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                project_id,
                execution_id,
                level,
                event,
                json.dumps(details, sort_keys=True, separators=(",", ":"), default=str),
                occurred_at,
            ),
        )

    @staticmethod
    def _policy_values(policy: LifecyclePolicy) -> tuple[object, ...]:
        return (
            policy.project_id,
            policy.policy_id,
            policy.name,
            policy.business_category.value,
            policy.object_role.value,
            policy.action.value,
            policy.minimum_age_days,
            policy.priority,
            policy.state.value,
            policy.version,
            policy.etag,
            policy.created_at,
            policy.updated_at,
        )

    @staticmethod
    def _insert_audit(cursor: DbApiCursor, event: LifecycleAuditEvent) -> None:
        cursor.execute(
            """
            INSERT INTO storage.lifecycle_audit_events (
                project_id, audit_id, policy_id, actor_id, action, before_digest,
                after_digest, request_id, details, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                event.project_id,
                event.audit_id,
                event.policy_id,
                event.actor_id,
                event.action,
                event.before_digest,
                event.after_digest,
                event.request_id,
                json.dumps(event.details, sort_keys=True, separators=(",", ":")),
                event.occurred_at,
            ),
        )

    @staticmethod
    def _fact(row: Mapping[str, object]) -> CapacityInventoryFact:
        category = row.get("business_category")
        return CapacityInventoryFact(
            snapshot_id=str(row["snapshot_id"]),
            project_id=str(row["project_id"]),
            physical_instance_id=str(row["physical_instance_id"]),
            logical_object_id=None
            if row.get("logical_object_id") is None
            else str(row["logical_object_id"]),
            physical_bytes=_byte_string(row["physical_bytes"]),
            disposition=InventoryDisposition(str(row["disposition"])),
            business_category=None if category is None else BusinessCapacityCategory(str(category)),
            object_role=ObjectRole(str(row["object_role"])),
            observed_at=cast(datetime, row["observed_at"]),
        )

    @staticmethod
    def _policy(row: Mapping[str, object]) -> LifecyclePolicy:
        return LifecyclePolicy(
            policy_id=str(row["policy_id"]),
            project_id=str(row["project_id"]),
            name=str(row["name"]),
            business_category=BusinessCapacityCategory(str(row["business_category"])),
            object_role=ObjectRole(str(row["object_role"])),
            action=LifecyclePolicyAction(str(row["action"])),
            minimum_age_days=int(cast(Any, row["minimum_age_days"])),
            priority=int(cast(Any, row["priority"])),
            state=LifecyclePolicyState(str(row["state"])),
            version=int(cast(Any, row["version"])),
            etag=str(row["etag"]),
            created_at=cast(datetime, row["created_at"]),
            updated_at=cast(datetime, row["updated_at"]),
        )

    @staticmethod
    def _audit(row: Mapping[str, object]) -> LifecycleAuditEvent:
        return LifecycleAuditEvent(
            audit_id=str(row["audit_id"]),
            project_id=str(row["project_id"]),
            policy_id=str(row["policy_id"]),
            actor_id=str(row["actor_id"]),
            action=str(row["action"]),
            before_digest=None if row.get("before_digest") is None else str(row["before_digest"]),
            after_digest=None if row.get("after_digest") is None else str(row["after_digest"]),
            request_id=str(row["request_id"]),
            details=_details(row.get("details", {})),
            occurred_at=cast(datetime, row["occurred_at"]),
        )


class PostgresStorageIdempotencyStore:
    """Durable command replay sharing one transaction with policy and audit writes."""

    def __init__(
        self,
        base_connection_factory: ConnectionFactory,
        transaction_factory: StorageTransactionConnectionFactory,
        *,
        ttl: timedelta = timedelta(hours=24),
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("idempotency TTL must be positive")
        self._base_connection_factory = base_connection_factory
        self._transaction_factory = transaction_factory
        self._ttl = ttl

    def execute(
        self,
        *,
        scope: str,
        key: str,
        payload: Any,
        action: Callable[[], _T],
    ) -> IdempotencyResult[_T]:
        if not scope or not key:
            raise ValueError("idempotency scope and key must not be empty")
        context = current_request_context()
        if context.project_id is None:
            raise problem(
                status=403,
                code="IDEMPOTENCY_SCOPE_DENIED",
                title="Idempotency scope denied",
                detail="A verified project scope is required for lifecycle commands.",
            )
        if context.organization_id is None:
            raise problem(
                status=403,
                code="IDEMPOTENCY_SCOPE_DENIED",
                title="Idempotency scope denied",
                detail="A verified organization scope is required for lifecycle commands.",
            )
        fingerprint = request_fingerprint(payload)
        now = datetime.now(timezone.utc)
        expires_at = now + self._ttl
        identity = (
            context.organization_id,
            context.project_id,
            context.region_code or "",
            scope,
            key,
        )
        connection = self._base_connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO core.idempotency_records (
                    organization_id, project_id, region_code, scope_key, idempotency_key,
                    request_fingerprint, response_json, created_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s)
                ON CONFLICT (
                    organization_id, project_id, region_code, scope_key, idempotency_key
                )
                DO NOTHING
                """,
                (*identity, fingerprint, now, expires_at),
            )
            cursor.execute(
                """
                SELECT request_fingerprint, response_json, expires_at
                FROM core.idempotency_records
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND scope_key = %s AND idempotency_key = %s
                FOR UPDATE
                """,
                identity,
            )
            raw = cursor.fetchone()
            if raw is None:
                raise problem(
                    status=403,
                    code="IDEMPOTENCY_SCOPE_DENIED",
                    title="Idempotency scope denied",
                    detail="The command record is outside the selected database scope.",
                )
            row = _row(cursor, raw)
            stored_expiry = cast(datetime, row["expires_at"])
            if stored_expiry > now:
                if str(row["request_fingerprint"]) != fingerprint:
                    raise idempotency_conflict()
                if row.get("response_json") is not None:
                    value = self._decode_response(row["response_json"])
                    connection.commit()
                    return IdempotencyResult(value=cast(_T, value), replayed=True)
            else:
                cursor.execute(
                    """
                    UPDATE core.idempotency_records
                    SET request_fingerprint = %s,
                        response_json = NULL,
                        created_at = %s,
                        expires_at = %s
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                      AND scope_key = %s AND idempotency_key = %s
                    """,
                    (fingerprint, now, expires_at, *identity),
                )

            with self._transaction_factory.bind(connection):
                value = action()
            cursor.execute(
                """
                UPDATE core.idempotency_records
                SET response_json = %s::jsonb, expires_at = %s
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND scope_key = %s AND idempotency_key = %s
                """,
                (self._encode_response(value), expires_at, *identity),
            )
            connection.commit()
            return IdempotencyResult(value=value, replayed=False)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _encode_response(value: object) -> str:
        payload: dict[str, Any]
        if isinstance(value, LifecyclePolicy):
            payload = {
                "kind": "LifecyclePolicy",
                "value": value.model_dump(mode="json"),
            }
        elif isinstance(value, BaseModel):
            payload = {
                "kind": value.__class__.__name__,
                "value": value.model_dump(mode="json"),
            }
        elif value is None:
            payload = {"kind": "None", "value": None}
        else:
            payload = {"kind": "JSON", "value": value}
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _decode_response(value: object) -> object:
        payload = json.loads(value) if isinstance(value, str) else value
        if not isinstance(payload, Mapping):
            raise RuntimeError("stored lifecycle command response is malformed")
        if payload.get("kind") == "LifecyclePolicy":
            return LifecyclePolicy.model_validate(payload.get("value"))
        if payload.get("kind") == "LifecycleExecution":
            return LifecycleExecution.model_validate(payload.get("value"))
        if payload.get("kind") == "None":
            return None
        return payload.get("value")
