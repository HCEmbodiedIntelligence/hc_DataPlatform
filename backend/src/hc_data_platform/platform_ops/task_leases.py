"""PostgreSQL-authoritative singleton leases for platform maintenance tasks."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Annotated, Any, NoReturn, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.platform_control.maintenance_contract import MaintenanceContractError

from .maintenance import EnvironmentId

TASK_LEASE_SECONDS = 30
TASK_LEASE_RENEWAL_INTERVAL_SECONDS = 10

TaskId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9._:/-]{0,253}[A-Za-z0-9])?$",
    ),
]


class PlatformTaskLease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment_id: EnvironmentId
    task_id: TaskId
    lease_id: UUID
    owner_instance_id: UUID
    fencing_token: int = Field(gt=0)
    lease_until: datetime
    lease_version: int = Field(ge=1)
    acquired_at: datetime
    updated_at: datetime


class PlatformTaskLeaseRepository(Protocol):
    def try_acquire(
        self,
        *,
        environment_id: EnvironmentId,
        task_id: TaskId,
        owner_instance_id: UUID,
    ) -> PlatformTaskLease | None: ...

    def renew(self, lease_id: UUID) -> PlatformTaskLease: ...

    def release(self, lease_id: UUID) -> None: ...


class DbApiCursor(Protocol):
    description: Sequence[Sequence[object] | object] | None
    rowcount: int

    def execute(self, query: str, params: Sequence[object] | None = None) -> object: ...

    def fetchone(self) -> Mapping[str, Any] | None: ...


class DbApiConnection(Protocol):
    def __enter__(self) -> DbApiConnection: ...

    def __exit__(self, *args: object) -> None: ...

    def cursor(self) -> DbApiCursor: ...


class PostgresPlatformTaskLeaseRepository:
    """One current lease row per environment/task, fenced by a global DB sequence."""

    def __init__(self, connection_factory: Callable[[], DbApiConnection]) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(cls, dsn: str) -> PostgresPlatformTaskLeaseRepository:
        normalized = normalize_postgres_dsn(dsn)

        def connect() -> DbApiConnection:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(normalized, row_factory=dict_row)  # type: ignore[return-value]

        return cls(connect)

    def try_acquire(
        self,
        *,
        environment_id: EnvironmentId,
        task_id: TaskId,
        owner_instance_id: UUID,
    ) -> PlatformTaskLease | None:
        candidate_lease_id = uuid4()
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
                """
                SELECT mode
                FROM platform.environment_fences
                WHERE environment_id = %s
                FOR SHARE
                """,
                (environment_id,),
            )
            fence = cursor.fetchone()
            if fence is None or fence["mode"] != "READ_WRITE":
                raise MaintenanceContractError(
                    "PLATFORM_MAINTENANCE",
                    "platform task lease acquisition is disabled during maintenance",
                )
            cursor.execute(
                """
                INSERT INTO platform.platform_task_leases (
                    environment_id, task_id, lease_id, owner_instance_id,
                    fencing_token, lease_until, lease_version
                ) VALUES (
                    %s, %s, %s, %s,
                    nextval('platform.maintenance_fencing_token_seq'),
                    statement_timestamp() + (%s * interval '1 second'), 1
                )
                ON CONFLICT (environment_id, task_id) DO NOTHING
                RETURNING *
                """,
                (
                    environment_id,
                    task_id,
                    candidate_lease_id,
                    owner_instance_id,
                    TASK_LEASE_SECONDS,
                ),
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                lease = _lease_from_row(inserted)
                self._append_event(cursor, lease, "PLATFORM_TASK_LEASE_ACQUIRED")
                return lease

            cursor.execute(
                """
                SELECT *, statement_timestamp() AS database_now
                FROM platform.platform_task_leases
                WHERE environment_id = %s AND task_id = %s
                FOR UPDATE
                """,
                (environment_id, task_id),
            )
            current = cursor.fetchone()
            if current is None:
                raise RuntimeError("platform task lease disappeared during acquisition")
            database_now = current["database_now"]
            if current["lease_until"] > database_now:
                if current["owner_instance_id"] != owner_instance_id:
                    return None
                cursor.execute(
                    """
                    UPDATE platform.platform_task_leases
                    SET lease_until = statement_timestamp() + (%s * interval '1 second'),
                        lease_version = lease_version + 1,
                        updated_at = statement_timestamp()
                    WHERE lease_id = %s AND lease_until > statement_timestamp()
                    RETURNING *
                    """,
                    (TASK_LEASE_SECONDS, current["lease_id"]),
                )
                renewed = cursor.fetchone()
                if renewed is None:
                    raise MaintenanceContractError(
                        "PLATFORM_TASK_LEASE_EXPIRED",
                        "platform task lease expired during acquisition",
                    )
                return _lease_from_row(renewed)

            previous_owner = current["owner_instance_id"]
            cursor.execute(
                """
                UPDATE platform.platform_task_leases
                SET lease_id = %s,
                    owner_instance_id = %s,
                    fencing_token = nextval('platform.maintenance_fencing_token_seq'),
                    lease_until = statement_timestamp() + (%s * interval '1 second'),
                    lease_version = lease_version + 1,
                    acquired_at = statement_timestamp(),
                    updated_at = statement_timestamp()
                WHERE environment_id = %s AND task_id = %s
                  AND lease_until <= statement_timestamp()
                RETURNING *
                """,
                (
                    candidate_lease_id,
                    owner_instance_id,
                    TASK_LEASE_SECONDS,
                    environment_id,
                    task_id,
                ),
            )
            taken_over = cursor.fetchone()
            if taken_over is None:
                return None
            lease = _lease_from_row(taken_over)
            event_code = (
                "PLATFORM_TASK_LEASE_REACQUIRED"
                if previous_owner == owner_instance_id
                else "PLATFORM_TASK_LEASE_TAKEN_OVER"
            )
            self._append_event(cursor, lease, event_code)
            return lease

    def renew(self, lease_id: UUID) -> PlatformTaskLease:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE platform.platform_task_leases AS task
                SET lease_until = statement_timestamp() + (%s * interval '1 second'),
                    lease_version = lease_version + 1,
                    updated_at = statement_timestamp()
                FROM platform.environment_fences AS fence
                WHERE task.lease_id = %s
                  AND task.environment_id = fence.environment_id
                  AND fence.mode = 'READ_WRITE'
                  AND task.lease_until > statement_timestamp()
                RETURNING task.*
                """,
                (TASK_LEASE_SECONDS, lease_id),
            )
            renewed = cursor.fetchone()
            if renewed is None:
                self._raise_lease_failure(cursor, lease_id)
            return _lease_from_row(renewed)

    def release(self, lease_id: UUID) -> None:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE platform.platform_task_leases
                SET lease_until = statement_timestamp(),
                    lease_version = lease_version + 1,
                    updated_at = statement_timestamp()
                WHERE lease_id = %s
                RETURNING *
                """,
                (lease_id,),
            )
            released = cursor.fetchone()
            if released is None:
                raise MaintenanceContractError(
                    "PLATFORM_TASK_LEASE_STALE",
                    "platform task lease is no longer current",
                )
            self._append_event(
                cursor,
                _lease_from_row(released),
                "PLATFORM_TASK_LEASE_RELEASED",
            )

    @staticmethod
    def _append_event(cursor: DbApiCursor, lease: PlatformTaskLease, event_code: str) -> None:
        cursor.execute(
            """
            INSERT INTO platform.platform_task_lease_events (
                environment_id, task_id, lease_id, owner_instance_id,
                fencing_token, lease_version, event_code
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                lease.environment_id,
                lease.task_id,
                lease.lease_id,
                lease.owner_instance_id,
                lease.fencing_token,
                lease.lease_version,
                event_code,
            ),
        )

    @staticmethod
    def _raise_lease_failure(cursor: DbApiCursor, lease_id: UUID) -> NoReturn:
        cursor.execute(
            """
            SELECT task.lease_until, fence.mode, statement_timestamp() AS database_now
            FROM platform.platform_task_leases AS task
            JOIN platform.environment_fences AS fence
              ON fence.environment_id = task.environment_id
            WHERE task.lease_id = %s
            """,
            (lease_id,),
        )
        current = cursor.fetchone()
        if current is None:
            raise MaintenanceContractError(
                "PLATFORM_TASK_LEASE_STALE",
                "platform task lease is no longer current",
            )
        if current["mode"] != "READ_WRITE":
            raise MaintenanceContractError(
                "PLATFORM_MAINTENANCE",
                "platform task lease is disabled during maintenance",
            )
        raise MaintenanceContractError(
            "PLATFORM_TASK_LEASE_EXPIRED",
            "platform task lease expired according to the database clock",
        )


def _lease_from_row(row: Mapping[str, Any]) -> PlatformTaskLease:
    return PlatformTaskLease(
        environment_id=row["environment_id"],
        task_id=row["task_id"],
        lease_id=row["lease_id"],
        owner_instance_id=row["owner_instance_id"],
        fencing_token=int(row["fencing_token"]),
        lease_until=row["lease_until"],
        lease_version=int(row["lease_version"]),
        acquired_at=row["acquired_at"],
        updated_at=row["updated_at"],
    )


__all__ = [
    "PlatformTaskLease",
    "PlatformTaskLeaseRepository",
    "PostgresPlatformTaskLeaseRepository",
    "TASK_LEASE_RENEWAL_INTERVAL_SECONDS",
    "TASK_LEASE_SECONDS",
    "TaskId",
]
