"""PostgreSQL-authoritative platform maintenance leases and writer fences."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Event, RLock, Thread
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from hc_data_platform.core.context import (
    bind_writer_permit,
    bind_writer_permit_retention,
    reset_writer_permit,
    reset_writer_permit_retention,
)
from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.platform_control.maintenance_contract import (
    EnvironmentFenceV1,
    MaintenanceCommandV1,
    MaintenanceContractError,
    MaintenanceLeaseV1,
    MaintenanceState,
    WriterPermitV1,
    apply_maintenance_transition,
    assert_writer_commit_allowed,
)

LEASE_SECONDS = 30
RENEWAL_INTERVAL_SECONDS = 10

EnvironmentId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9._:/-]{0,126}[A-Za-z0-9])?$",
    ),
]
OperationId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
SafeActorId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
PlanDigest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
OperationKind = Literal["BACKUP", "RESTORE", "MIGRATION", "RELEASE", "OTHER"]
WriterKind = Literal[
    "api_command",
    "presigned_upload_grant",
    "outbox_claim",
    "temporal_activity",
    "aligned_media_attempt",
    "maintenance_controller",
    "kubernetes_job",
]
PlatformAuditOutcome = Literal["SUCCEEDED", "DENIED", "FAILED"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EnvironmentFence(_StrictModel):
    environment_id: EnvironmentId
    mode: Literal["READ_WRITE", "READ_ONLY_MAINTENANCE"]
    fencing_token: int = Field(gt=0)
    mode_changed_at: datetime
    write_enabled_at: datetime


class MaintenanceOperation(_StrictModel):
    operation_id: OperationId
    environment_id: EnvironmentId
    operation_kind: OperationKind
    plan_digest: PlanDigest
    requested_by: SafeActorId
    state: MaintenanceState
    owner_instance_id: UUID | None
    fencing_token: int | None = Field(default=None, gt=0)
    lease_until: datetime | None
    state_version: int = Field(ge=0)
    reconciliation_passed: bool
    manual_approval_id: SafeActorId | None
    created_at: datetime
    updated_at: datetime

    def lease(self) -> MaintenanceLeaseV1:
        if self.owner_instance_id is None or self.fencing_token is None or self.lease_until is None:
            raise MaintenanceContractError(
                "PLATFORM_MAINTENANCE_NOT_LEASED",
                "the maintenance operation has no active ownership lease",
            )
        return MaintenanceLeaseV1(
            operation_id=self.operation_id,
            environment_id=self.environment_id,
            state=self.state,
            owner_instance_id=str(self.owner_instance_id),
            fencing_token=self.fencing_token,
            lease_until=self.lease_until,
            state_version=self.state_version,
        )


class WriterPermit(_StrictModel):
    permit_id: UUID
    environment_id: EnvironmentId
    operation_id: OperationId | None
    writer_id: SafeActorId
    writer_kind: WriterKind
    fencing_token: int = Field(gt=0)
    lease_until: datetime
    created_at: datetime
    released_at: datetime | None

    def contract(self) -> WriterPermitV1:
        return WriterPermitV1(
            environment_id=self.environment_id,
            writer_id=self.writer_id,
            writer_kind=self.writer_kind,
            fencing_token=self.fencing_token,
            lease_until=self.lease_until,
        )


class WriterInventoryItem(_StrictModel):
    writer_kind: WriterKind
    active_count: int = Field(ge=0)


class PlatformAuditContext(_StrictModel):
    actor_id: SafeActorId
    request_id: SafeActorId
    capability_key: SafeActorId


class PlatformAuditEvent(_StrictModel):
    actor_id: SafeActorId
    action: SafeActorId
    resource_type: SafeActorId
    resource_id: SafeActorId
    request_id: SafeActorId
    outcome: PlatformAuditOutcome
    safe_details: Mapping[str, object] = Field(default_factory=dict)


class MaintenanceWriteGate(Protocol):
    def issue_writer_permit(
        self,
        *,
        environment_id: EnvironmentId,
        writer_id: SafeActorId,
        writer_kind: WriterKind,
        operation_id: OperationId | None = None,
    ) -> WriterPermit: ...

    def assert_writer_permit(self, permit_id: UUID) -> None: ...

    def renew_writer_permit(self, permit_id: UUID) -> WriterPermit: ...

    def retain_writer_permit(self, permit_id: UUID, *, lease_seconds: int) -> WriterPermit: ...

    def release_writer_permit(self, permit_id: UUID) -> None: ...

    def append_platform_audit(self, event: PlatformAuditEvent) -> None: ...


class WriterPermitRenewer:
    """Keep a permit live for long operations and surface any lost fencing epoch."""

    def __init__(self, gate: MaintenanceWriteGate, permit_id: UUID) -> None:
        self._gate = gate
        self._permit_id = permit_id
        self._stop = Event()
        self._error: BaseException | None = None
        self._thread = Thread(
            target=self._run,
            name=f"writer-permit-{str(permit_id)[:8]}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1)

    def raise_if_failed(self) -> None:
        if self._error is not None:
            if isinstance(self._error, MaintenanceContractError):
                raise self._error
            raise MaintenanceContractError(
                "PLATFORM_WRITER_PERMIT_RENEWAL_FAILED",
                "writer permit renewal failed",
            ) from self._error

    def _run(self) -> None:
        while not self._stop.wait(RENEWAL_INTERVAL_SECONDS):
            try:
                self._gate.renew_writer_permit(self._permit_id)
            except BaseException as exc:
                self._error = exc
                self._stop.set()
                return


@contextmanager
def writer_permit_scope(
    gate: MaintenanceWriteGate | None,
    *,
    environment_id: EnvironmentId,
    writer_id: SafeActorId,
    writer_kind: WriterKind,
    operation_id: OperationId | None = None,
) -> Iterator[WriterPermit | None]:
    """Bind one permit across all scoped adapters and revalidate after object-side effects."""

    if gate is None:
        yield None
        return
    permit = gate.issue_writer_permit(
        environment_id=environment_id,
        writer_id=writer_id,
        writer_kind=writer_kind,
        operation_id=operation_id,
    )
    token = bind_writer_permit(permit.permit_id)
    retention_token = bind_writer_permit_retention()
    renewer = WriterPermitRenewer(gate, permit.permit_id)
    renewer.start()
    try:
        yield permit
        renewer.stop()
        renewer.raise_if_failed()
        gate.assert_writer_permit(permit.permit_id)
    finally:
        renewer.stop()
        reset_writer_permit(token)
        reset_writer_permit_retention(retention_token)
        gate.release_writer_permit(permit.permit_id)


class InMemoryMaintenanceWriteGate:
    """Deterministic middleware adapter; production always uses PostgreSQL."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._clock = clock
        self._fences: dict[str, EnvironmentFence] = {}
        self._permits: dict[UUID, WriterPermit] = {}
        self.platform_audit_events: list[PlatformAuditEvent] = []
        self._lock = RLock()

    def ensure_environment(self, environment_id: EnvironmentId) -> EnvironmentFence:
        with self._lock:
            current = self._fences.get(environment_id)
            if current is not None:
                return current
            now = self._clock()
            created = EnvironmentFence(
                environment_id=environment_id,
                mode="READ_WRITE",
                fencing_token=1,
                mode_changed_at=now,
                write_enabled_at=now,
            )
            self._fences[environment_id] = created
            return created

    def set_mode(
        self,
        environment_id: EnvironmentId,
        mode: Literal["READ_WRITE", "READ_ONLY_MAINTENANCE"],
    ) -> EnvironmentFence:
        with self._lock:
            current = self.ensure_environment(environment_id)
            now = self._clock()
            updated = current.model_copy(
                update={
                    "mode": mode,
                    "fencing_token": current.fencing_token + 1,
                    "mode_changed_at": now,
                    "write_enabled_at": (now if mode == "READ_WRITE" else current.write_enabled_at),
                }
            )
            self._fences[environment_id] = updated
            return updated

    def issue_writer_permit(
        self,
        *,
        environment_id: EnvironmentId,
        writer_id: SafeActorId,
        writer_kind: WriterKind,
        operation_id: OperationId | None = None,
    ) -> WriterPermit:
        with self._lock:
            fence = self.ensure_environment(environment_id)
            if fence.mode != "READ_WRITE":
                raise MaintenanceContractError(
                    "PLATFORM_MAINTENANCE",
                    "the environment is read-only for maintenance",
                )
            now = self._clock()
            permit = WriterPermit(
                permit_id=uuid4(),
                environment_id=environment_id,
                operation_id=operation_id,
                writer_id=writer_id,
                writer_kind=writer_kind,
                fencing_token=fence.fencing_token,
                lease_until=now + timedelta(seconds=LEASE_SECONDS),
                created_at=now,
                released_at=None,
            )
            self._permits[permit.permit_id] = permit
            return permit

    def assert_writer_permit(self, permit_id: UUID) -> None:
        with self._lock:
            permit = self._permits.get(permit_id)
            if permit is None or permit.released_at is not None:
                raise MaintenanceContractError(
                    "PLATFORM_WRITER_PERMIT_NOT_FOUND",
                    "writer permit validation failed",
                )
            fence = self._fences[permit.environment_id]
            assert_writer_commit_allowed(
                permit.contract(),
                EnvironmentFenceV1(
                    environment_id=fence.environment_id,
                    mode=fence.mode,
                    fencing_token=fence.fencing_token,
                ),
                database_now=self._clock(),
            )

    def renew_writer_permit(self, permit_id: UUID) -> WriterPermit:
        with self._lock:
            self.assert_writer_permit(permit_id)
            permit = self._permits[permit_id]
            renewed = permit.model_copy(
                update={"lease_until": self._clock() + timedelta(seconds=LEASE_SECONDS)}
            )
            self._permits[permit_id] = renewed
            return renewed

    def retain_writer_permit(self, permit_id: UUID, *, lease_seconds: int) -> WriterPermit:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        with self._lock:
            self.assert_writer_permit(permit_id)
            permit = self._permits[permit_id]
            retained = permit.model_copy(
                update={
                    "writer_kind": "presigned_upload_grant",
                    "lease_until": max(
                        permit.lease_until,
                        self._clock() + timedelta(seconds=lease_seconds),
                    ),
                }
            )
            self._permits[permit_id] = retained
            return retained

    def release_writer_permit(self, permit_id: UUID) -> None:
        with self._lock:
            permit = self._permits.get(permit_id)
            if permit is not None and permit.released_at is None:
                self._permits[permit_id] = permit.model_copy(update={"released_at": self._clock()})

    def writer_inventory(self, environment_id: EnvironmentId) -> tuple[WriterInventoryItem, ...]:
        with self._lock:
            counts: dict[WriterKind, int] = {}
            now = self._clock()
            for permit in self._permits.values():
                if (
                    permit.environment_id == environment_id
                    and permit.released_at is None
                    and permit.lease_until > now
                ):
                    counts[permit.writer_kind] = counts.get(permit.writer_kind, 0) + 1
            return tuple(
                WriterInventoryItem(writer_kind=kind, active_count=count)
                for kind, count in sorted(counts.items())
            )

    def append_platform_audit(self, event: PlatformAuditEvent) -> None:
        with self._lock:
            self.platform_audit_events.append(event)


class DbApiCursor(Protocol):
    description: Sequence[Sequence[object] | object] | None
    rowcount: int

    def execute(self, query: str, params: Sequence[object] | None = None) -> object: ...

    def fetchone(self) -> Mapping[str, Any] | None: ...

    def fetchall(self) -> Sequence[Mapping[str, Any]]: ...


class DbApiConnection(Protocol):
    def __enter__(self) -> DbApiConnection: ...

    def __exit__(self, *args: object) -> None: ...

    def cursor(self) -> DbApiCursor: ...


class PostgresMaintenanceRepository:
    """Global, unscoped maintenance repository using only database-clock decisions."""

    def __init__(self, connection_factory: Callable[[], DbApiConnection]) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(cls, dsn: str) -> PostgresMaintenanceRepository:
        normalized = normalize_postgres_dsn(dsn)

        def connect() -> DbApiConnection:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(normalized, row_factory=dict_row)  # type: ignore[return-value]

        return cls(connect)

    def ensure_environment(self, environment_id: EnvironmentId) -> EnvironmentFence:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO platform.environment_fences (environment_id)
                VALUES (%s)
                ON CONFLICT (environment_id) DO NOTHING
                """,
                (environment_id,),
            )
            cursor.execute(
                "SELECT * FROM platform.environment_fences WHERE environment_id = %s",
                (environment_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("environment fence initialization returned no row")
        return _fence_from_row(row)

    def request_operation(
        self,
        *,
        operation_id: OperationId,
        environment_id: EnvironmentId,
        operation_kind: OperationKind,
        plan_digest: PlanDigest,
        requested_by: SafeActorId,
        audit: PlatformAuditContext | None = None,
    ) -> MaintenanceOperation:
        self.ensure_environment(environment_id)
        try:
            with self._connection_factory() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    INSERT INTO platform.maintenance_operations (
                        operation_id, environment_id, operation_kind, plan_digest, requested_by
                    ) VALUES (%s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        operation_id,
                        environment_id,
                        operation_kind,
                        plan_digest,
                        requested_by,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("maintenance request returned no row")
                self._append_event(
                    cursor,
                    row,
                    "PLATFORM_MAINTENANCE_REQUESTED",
                    audit=audit,
                )
        except Exception as exc:
            if _constraint_name(exc) == "maintenance_operations_one_active_environment_idx":
                raise MaintenanceContractError(
                    "PLATFORM_MAINTENANCE_ALREADY_ACTIVE",
                    "the environment already has a nonterminal maintenance operation",
                ) from exc
            raise
        return _operation_from_row(row)

    def get_operation(self, operation_id: OperationId) -> MaintenanceOperation:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT * FROM platform.maintenance_operations WHERE operation_id = %s",
                (operation_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise MaintenanceContractError(
                "PLATFORM_MAINTENANCE_NOT_FOUND", "the maintenance operation does not exist"
            )
        return _operation_from_row(row)

    def current_fence(self, environment_id: EnvironmentId) -> EnvironmentFence:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT * FROM platform.environment_fences WHERE environment_id = %s",
                (environment_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise MaintenanceContractError(
                "PLATFORM_MAINTENANCE_ENVIRONMENT_NOT_FOUND",
                "the environment fence does not exist",
            )
        return _fence_from_row(row)

    def acquire(
        self,
        operation_id: OperationId,
        *,
        owner_instance_id: UUID,
        audit: PlatformAuditContext | None = None,
    ) -> MaintenanceOperation:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            row = self._locked_operation(cursor, operation_id)
            operation = _operation_from_row(row)
            if operation.state is not MaintenanceState.REQUESTED:
                raise MaintenanceContractError(
                    "PLATFORM_MAINTENANCE_STATE_CONFLICT",
                    "only a requested operation can acquire its first lease",
                )
            cursor.execute(
                "SELECT nextval('platform.maintenance_fencing_token_seq') AS fencing_token"
            )
            token_row = cursor.fetchone()
            if token_row is None:
                raise RuntimeError("maintenance fencing sequence returned no token")
            token = int(token_row["fencing_token"])
            cursor.execute(
                """
                UPDATE platform.maintenance_operations
                SET state = 'LEASED', owner_instance_id = %s, fencing_token = %s,
                    lease_until = statement_timestamp() + (%s * interval '1 second'),
                    state_version = state_version + 1,
                    updated_at = statement_timestamp()
                WHERE operation_id = %s AND state = 'REQUESTED' AND state_version = %s
                RETURNING *
                """,
                (owner_instance_id, token, LEASE_SECONDS, operation_id, operation.state_version),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise MaintenanceContractError(
                    "PLATFORM_MAINTENANCE_STATE_CONFLICT",
                    "the maintenance operation changed before lease acquisition",
                )
            self._append_event(
                cursor,
                updated,
                "PLATFORM_MAINTENANCE_ACQUIRED",
                audit=audit,
            )
        return _operation_from_row(updated)

    def renew(
        self,
        operation_id: OperationId,
        *,
        owner_instance_id: UUID,
        fencing_token: int,
        audit: PlatformAuditContext | None = None,
    ) -> MaintenanceOperation:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            row, database_now = self._locked_operation_with_clock(cursor, operation_id)
            operation = _operation_from_row(row)
            self._assert_current_owner(
                operation,
                owner_instance_id=owner_instance_id,
                fencing_token=fencing_token,
                database_now=database_now,
            )
            cursor.execute(
                """
                UPDATE platform.maintenance_operations
                SET lease_until = statement_timestamp() + (%s * interval '1 second'),
                    updated_at = statement_timestamp()
                WHERE operation_id = %s AND environment_id = %s
                  AND owner_instance_id = %s AND fencing_token = %s
                  AND lease_until > statement_timestamp()
                  AND state = %s AND state_version = %s
                RETURNING *
                """,
                (
                    LEASE_SECONDS,
                    operation.operation_id,
                    operation.environment_id,
                    owner_instance_id,
                    fencing_token,
                    operation.state.value,
                    operation.state_version,
                ),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise MaintenanceContractError(
                    "PLATFORM_MAINTENANCE_STATE_CONFLICT",
                    "the maintenance lease changed before renewal",
                )
            self._append_event(
                cursor,
                updated,
                "PLATFORM_MAINTENANCE_LEASE_RENEWED",
                audit=audit,
            )
        return _operation_from_row(updated)

    def takeover(
        self,
        operation_id: OperationId,
        *,
        new_owner_instance_id: UUID,
        audit: PlatformAuditContext | None = None,
    ) -> MaintenanceOperation:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            row, database_now = self._locked_operation_with_clock(cursor, operation_id)
            operation = _operation_from_row(row)
            if operation.state in {
                MaintenanceState.REQUESTED,
                MaintenanceState.CANCELLED,
                MaintenanceState.SUCCEEDED,
                MaintenanceState.FAILED_RELEASED,
            }:
                raise MaintenanceContractError(
                    "PLATFORM_MAINTENANCE_TAKEOVER_INVALID",
                    "the operation state cannot be taken over",
                )
            if operation.lease_until is None or database_now < operation.lease_until:
                raise MaintenanceContractError(
                    "PLATFORM_MAINTENANCE_LEASE_ACTIVE",
                    "the current maintenance lease has not expired",
                )
            cursor.execute(
                "SELECT nextval('platform.maintenance_fencing_token_seq') AS fencing_token"
            )
            token_row = cursor.fetchone()
            if token_row is None:
                raise RuntimeError("maintenance fencing sequence returned no token")
            new_token = int(token_row["fencing_token"])
            if operation.fencing_token is not None and new_token <= operation.fencing_token:
                raise RuntimeError("maintenance fencing sequence did not advance")
            cursor.execute(
                """
                UPDATE platform.maintenance_operations
                SET owner_instance_id = %s, fencing_token = %s,
                    lease_until = statement_timestamp() + (%s * interval '1 second'),
                    updated_at = statement_timestamp()
                WHERE operation_id = %s AND environment_id = %s
                  AND lease_until <= statement_timestamp()
                  AND state = %s AND state_version = %s
                RETURNING *
                """,
                (
                    new_owner_instance_id,
                    new_token,
                    LEASE_SECONDS,
                    operation.operation_id,
                    operation.environment_id,
                    operation.state.value,
                    operation.state_version,
                ),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise MaintenanceContractError(
                    "PLATFORM_MAINTENANCE_STATE_CONFLICT",
                    "the maintenance lease changed before takeover",
                )
            cursor.execute(
                """
                UPDATE platform.environment_fences
                SET fencing_token = %s,
                    mode_changed_at = greatest(statement_timestamp(), mode_changed_at)
                WHERE environment_id = %s
                  AND mode = 'READ_ONLY_MAINTENANCE'
                  AND fencing_token = %s
                """,
                (new_token, operation.environment_id, operation.fencing_token),
            )
            self._append_event(
                cursor,
                updated,
                "PLATFORM_MAINTENANCE_TAKEN_OVER",
                audit=audit,
            )
        return _operation_from_row(updated)

    def transition(
        self,
        command: MaintenanceCommandV1,
        *,
        audit: PlatformAuditContext | None = None,
    ) -> MaintenanceOperation:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            row, database_now = self._locked_operation_with_clock(cursor, command.operation_id)
            operation = _operation_from_row(row)
            next_lease = apply_maintenance_transition(
                operation.lease(), command, database_now=database_now
            )
            approval_id = command.manual_approval_id or operation.manual_approval_id

            if command.next_state is MaintenanceState.FENCED:
                self._assert_inventory_zero(cursor, operation.environment_id)
            if command.next_state is MaintenanceState.SUCCEEDED:
                self._assert_inventory_zero(cursor, operation.environment_id)
                if not operation.reconciliation_passed:
                    raise MaintenanceContractError(
                        "PLATFORM_MAINTENANCE_RECONCILIATION_REQUIRED",
                        "write enable requires a passed reconciliation",
                    )
                if approval_id is None:
                    raise MaintenanceContractError(
                        "PLATFORM_MAINTENANCE_MANUAL_APPROVAL_REQUIRED",
                        "write enable requires an explicit approval",
                    )

            cursor.execute(
                """
                UPDATE platform.maintenance_operations
                SET state = %s, state_version = %s, manual_approval_id = %s,
                    updated_at = statement_timestamp()
                WHERE operation_id = %s AND environment_id = %s
                  AND owner_instance_id = %s AND fencing_token = %s
                  AND lease_until > statement_timestamp()
                  AND state = %s AND state_version = %s
                RETURNING *
                """,
                (
                    next_lease.state.value,
                    next_lease.state_version,
                    approval_id,
                    command.operation_id,
                    command.environment_id,
                    UUID(command.owner_instance_id),
                    command.fencing_token,
                    command.expected_state.value,
                    command.expected_state_version,
                ),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise MaintenanceContractError(
                    "PLATFORM_MAINTENANCE_STATE_CONFLICT",
                    "the maintenance operation changed before transition",
                )

            if command.next_state is MaintenanceState.READ_ONLY:
                cursor.execute(
                    """
                    UPDATE platform.environment_fences
                    SET mode = 'READ_ONLY_MAINTENANCE', fencing_token = %s,
                        mode_changed_at = greatest(
                            statement_timestamp(), mode_changed_at, write_enabled_at
                        )
                    WHERE environment_id = %s AND fencing_token < %s
                    """,
                    (command.fencing_token, command.environment_id, command.fencing_token),
                )
                if cursor.rowcount != 1:
                    raise MaintenanceContractError(
                        "PLATFORM_MAINTENANCE_FENCING_TOKEN_STALE",
                        "the environment fence already advanced",
                    )
            elif command.next_state is MaintenanceState.SUCCEEDED:
                cursor.execute(
                    "SELECT nextval('platform.maintenance_fencing_token_seq') AS fencing_token"
                )
                token_row = cursor.fetchone()
                if token_row is None:
                    raise RuntimeError("maintenance fencing sequence returned no token")
                write_token = int(token_row["fencing_token"])
                cursor.execute(
                    """
                    UPDATE platform.environment_fences
                    SET mode = 'READ_WRITE', fencing_token = %s,
                        mode_changed_at = greatest(
                            statement_timestamp(), mode_changed_at, write_enabled_at
                        ),
                        write_enabled_at = greatest(
                            statement_timestamp(), mode_changed_at, write_enabled_at
                        )
                    WHERE environment_id = %s
                      AND mode = 'READ_ONLY_MAINTENANCE'
                      AND fencing_token = %s
                    """,
                    (write_token, command.environment_id, command.fencing_token),
                )
                if cursor.rowcount != 1:
                    raise MaintenanceContractError(
                        "PLATFORM_MAINTENANCE_FENCING_TOKEN_STALE",
                        "the environment fence changed before write enable",
                    )
            self._append_event(
                cursor,
                updated,
                f"PLATFORM_MAINTENANCE_{command.next_state.value}",
                approval_id=approval_id,
                audit=audit,
            )
        return _operation_from_row(updated)

    def mark_reconciliation_passed(
        self,
        operation_id: OperationId,
        *,
        owner_instance_id: UUID,
        fencing_token: int,
        expected_state_version: int,
        audit: PlatformAuditContext | None = None,
    ) -> MaintenanceOperation:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE platform.maintenance_operations
                SET reconciliation_passed = true, updated_at = statement_timestamp()
                WHERE operation_id = %s AND owner_instance_id = %s
                  AND fencing_token = %s AND lease_until > statement_timestamp()
                  AND state = 'VERIFYING' AND state_version = %s
                RETURNING *
                """,
                (operation_id, owner_instance_id, fencing_token, expected_state_version),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise MaintenanceContractError(
                    "PLATFORM_MAINTENANCE_STATE_CONFLICT",
                    "reconciliation receipt does not match the current owner and state",
                )
            self._append_event(
                cursor,
                updated,
                "PLATFORM_MAINTENANCE_RECONCILED",
                audit=audit,
            )
        return _operation_from_row(updated)

    def issue_writer_permit(
        self,
        *,
        environment_id: EnvironmentId,
        writer_id: SafeActorId,
        writer_kind: WriterKind,
        operation_id: OperationId | None = None,
    ) -> WriterPermit:
        permit_id = uuid4()
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT * FROM platform.environment_fences
                WHERE environment_id = %s
                FOR UPDATE
                """,
                (environment_id,),
            )
            fence_row = cursor.fetchone()
            if fence_row is None or fence_row["mode"] != "READ_WRITE":
                raise MaintenanceContractError(
                    "PLATFORM_MAINTENANCE",
                    "the environment is read-only for maintenance",
                )
            cursor.execute(
                """
                INSERT INTO platform.writer_permits (
                    permit_id, environment_id, operation_id, writer_id,
                    writer_kind, fencing_token, lease_until
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    statement_timestamp() + (%s * interval '1 second')
                )
                RETURNING *
                """,
                (
                    permit_id,
                    environment_id,
                    operation_id,
                    writer_id,
                    writer_kind,
                    int(fence_row["fencing_token"]),
                    LEASE_SECONDS,
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("writer permit creation returned no row")
        return _permit_from_row(row)

    def assert_writer_permit(self, permit_id: UUID) -> None:
        try:
            with self._connection_factory() as connection:
                connection.cursor().execute(
                    "SELECT platform.assert_writer_permit(%s)", (permit_id,)
                )
        except Exception as exc:
            code = _database_contract_code(exc)
            if code is not None:
                raise MaintenanceContractError(code, "writer permit validation failed") from exc
            raise

    def renew_writer_permit(self, permit_id: UUID) -> WriterPermit:
        try:
            with self._connection_factory() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    UPDATE platform.writer_permits permit
                    SET lease_until = statement_timestamp() + (%s * interval '1 second')
                    FROM platform.environment_fences fence
                    WHERE permit.permit_id = %s
                      AND permit.environment_id = fence.environment_id
                      AND permit.released_at IS NULL
                      AND permit.lease_until > statement_timestamp()
                      AND fence.mode = 'READ_WRITE'
                      AND permit.fencing_token = fence.fencing_token
                    RETURNING permit.*
                    """,
                    (LEASE_SECONDS, permit_id),
                )
                row = cursor.fetchone()
            if row is None:
                raise MaintenanceContractError(
                    "PLATFORM_WRITER_PERMIT_RENEWAL_FAILED",
                    "writer permit renewal failed",
                )
            return _permit_from_row(row)
        except MaintenanceContractError:
            raise
        except Exception as exc:
            code = _database_contract_code(exc)
            if code is not None:
                raise MaintenanceContractError(code, "writer permit renewal failed") from exc
            raise

    def retain_writer_permit(self, permit_id: UUID, *, lease_seconds: int) -> WriterPermit:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE platform.writer_permits permit
                SET writer_kind = 'presigned_upload_grant',
                    lease_until = greatest(
                        permit.lease_until,
                        statement_timestamp() + (%s * interval '1 second')
                    )
                FROM platform.environment_fences fence
                WHERE permit.permit_id = %s
                  AND permit.environment_id = fence.environment_id
                  AND permit.released_at IS NULL
                  AND permit.lease_until > statement_timestamp()
                  AND fence.mode = 'READ_WRITE'
                  AND permit.fencing_token = fence.fencing_token
                RETURNING permit.*
                """,
                (lease_seconds, permit_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise MaintenanceContractError(
                "PLATFORM_WRITER_PERMIT_RETENTION_FAILED",
                "writer permit retention failed",
            )
        return _permit_from_row(row)

    def assert_environment_writable(self, environment_id: EnvironmentId) -> None:
        try:
            with self._connection_factory() as connection:
                connection.cursor().execute(
                    "SELECT platform.assert_environment_writable(%s)", (environment_id,)
                )
        except Exception as exc:
            code = _database_contract_code(exc)
            if code is not None:
                raise MaintenanceContractError(
                    code, "the environment is under maintenance"
                ) from exc
            raise

    def release_writer_permit(self, permit_id: UUID) -> None:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE platform.writer_permits
                SET released_at = statement_timestamp()
                WHERE permit_id = %s AND released_at IS NULL
                """,
                (permit_id,),
            )

    def writer_inventory(self, environment_id: EnvironmentId) -> tuple[WriterInventoryItem, ...]:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT writer_kind, count(*) AS active_count
                FROM platform.writer_permits
                WHERE environment_id = %s AND released_at IS NULL
                  AND lease_until > statement_timestamp()
                GROUP BY writer_kind
                ORDER BY writer_kind
                """,
                (environment_id,),
            )
            rows = cursor.fetchall()
        return tuple(WriterInventoryItem.model_validate(row) for row in rows)

    def append_platform_audit(self, event: PlatformAuditEvent) -> None:
        with self._connection_factory() as connection:
            self._insert_platform_audit(connection.cursor(), event)

    @staticmethod
    def _locked_operation(cursor: DbApiCursor, operation_id: str) -> Mapping[str, Any]:
        cursor.execute(
            "SELECT * FROM platform.maintenance_operations WHERE operation_id = %s FOR UPDATE",
            (operation_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise MaintenanceContractError(
                "PLATFORM_MAINTENANCE_NOT_FOUND", "the maintenance operation does not exist"
            )
        return row

    @classmethod
    def _locked_operation_with_clock(
        cls, cursor: DbApiCursor, operation_id: str
    ) -> tuple[Mapping[str, Any], datetime]:
        row = cls._locked_operation(cursor, operation_id)
        cursor.execute("SELECT statement_timestamp() AS database_now")
        clock_row = cursor.fetchone()
        if clock_row is None:
            raise RuntimeError("PostgreSQL database clock returned no row")
        return row, clock_row["database_now"]

    @staticmethod
    def _assert_current_owner(
        operation: MaintenanceOperation,
        *,
        owner_instance_id: UUID,
        fencing_token: int,
        database_now: datetime,
    ) -> None:
        lease = operation.lease()
        if database_now >= lease.lease_until:
            raise MaintenanceContractError(
                "PLATFORM_MAINTENANCE_LEASE_EXPIRED",
                "the maintenance operation lease expired",
            )
        if lease.owner_instance_id != str(owner_instance_id):
            raise MaintenanceContractError(
                "PLATFORM_MAINTENANCE_OWNER_MISMATCH",
                "the maintenance operation owner changed",
            )
        if lease.fencing_token != fencing_token:
            raise MaintenanceContractError(
                "PLATFORM_MAINTENANCE_FENCING_TOKEN_STALE",
                "the maintenance fencing token is stale",
            )

    @staticmethod
    def _assert_inventory_zero(cursor: DbApiCursor, environment_id: str) -> None:
        cursor.execute(
            """
            SELECT count(*) AS active_count
            FROM platform.writer_permits
            WHERE environment_id = %s AND released_at IS NULL
              AND lease_until > statement_timestamp()
            """,
            (environment_id,),
        )
        row = cursor.fetchone()
        if row is None or int(row["active_count"]) != 0:
            raise MaintenanceContractError(
                "PLATFORM_MAINTENANCE_WRITERS_ACTIVE",
                "writer inventory is not drained",
            )

    @staticmethod
    def _append_event(
        cursor: DbApiCursor,
        operation: Mapping[str, Any],
        event_code: str,
        *,
        approval_id: str | None = None,
        audit: PlatformAuditContext | None = None,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO platform.maintenance_events (
                operation_id, environment_id, event_code, owner_instance_id,
                fencing_token, state, state_version, approval_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                operation["operation_id"],
                operation["environment_id"],
                event_code,
                operation["owner_instance_id"],
                operation["fencing_token"],
                operation["state"],
                operation["state_version"],
                approval_id,
            ),
        )
        if audit is not None:
            PostgresMaintenanceRepository._insert_platform_audit(
                cursor,
                PlatformAuditEvent(
                    actor_id=audit.actor_id,
                    action=event_code.lower()
                    .replace("platform_", "platform.", 1)
                    .replace("_", "."),
                    resource_type="maintenance_operation",
                    resource_id=str(operation["operation_id"]),
                    request_id=audit.request_id,
                    outcome="SUCCEEDED",
                    safe_details={
                        "capability_key": audit.capability_key,
                        "environment_id": str(operation["environment_id"]),
                        "operation_kind": str(operation["operation_kind"]),
                        "state": str(operation["state"]),
                        "state_version": int(operation["state_version"]),
                    },
                ),
            )

    @staticmethod
    def _insert_platform_audit(cursor: DbApiCursor, event: PlatformAuditEvent) -> None:
        cursor.execute(
            """
            INSERT INTO access_control.audit_events (
                event_id, scope_kind, organization_id, project_id, actor_id, action,
                resource_type, resource_id, request_id, outcome, safe_details
            ) VALUES (
                %s, 'PLATFORM', NULL, NULL, %s, %s, %s, %s, %s, %s, %s::jsonb
            )
            """,
            (
                uuid4(),
                event.actor_id,
                event.action,
                event.resource_type,
                event.resource_id,
                event.request_id,
                event.outcome,
                json.dumps(dict(event.safe_details), ensure_ascii=False, sort_keys=True),
            ),
        )


def _operation_from_row(row: Mapping[str, Any]) -> MaintenanceOperation:
    return MaintenanceOperation.model_validate(row)


def _fence_from_row(row: Mapping[str, Any]) -> EnvironmentFence:
    return EnvironmentFence.model_validate(row)


def _permit_from_row(row: Mapping[str, Any]) -> WriterPermit:
    return WriterPermit.model_validate(row)


def _constraint_name(error: BaseException) -> str | None:
    diagnostic = getattr(error, "diag", None)
    value = getattr(diagnostic, "constraint_name", None)
    return value if isinstance(value, str) else None


def _database_contract_code(error: BaseException) -> str | None:
    value = str(error).partition("\n")[0].strip()
    if value.startswith("PLATFORM_") and value.replace("_", "").isalnum():
        return value
    return None
