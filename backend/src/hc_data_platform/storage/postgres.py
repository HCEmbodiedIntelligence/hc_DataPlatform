"""Synchronous PostgreSQL adapter for scoped storage governance facts."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol, TypeVar, cast

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
    CapacityInventoryFact,
    CapacitySnapshot,
    InventoryDisposition,
    LifecycleAuditEvent,
    LifecyclePolicy,
    LifecyclePolicyAction,
    LifecyclePolicyState,
    ObjectRole,
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
        fingerprint = request_fingerprint(payload)
        now = datetime.now(timezone.utc)
        expires_at = now + self._ttl
        identity = (
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
                    project_id, region_code, scope_key, idempotency_key,
                    request_fingerprint, response_json, created_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s, NULL, %s, %s)
                ON CONFLICT (project_id, region_code, scope_key, idempotency_key)
                DO NOTHING
                """,
                (*identity, fingerprint, now, expires_at),
            )
            cursor.execute(
                """
                SELECT request_fingerprint, response_json, expires_at
                FROM core.idempotency_records
                WHERE project_id = %s AND region_code = %s
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
                    WHERE project_id = %s AND region_code = %s
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
                WHERE project_id = %s AND region_code = %s
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
        if payload.get("kind") == "None":
            return None
        return payload.get("value")
