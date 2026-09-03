"""Production psycopg connection factory with fail-closed PostgreSQL RLS scope."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from hc_data_platform.platform_control.maintenance_contract import MaintenanceContractError

from .context import (
    current_platform_task_lease,
    current_request_context,
    current_writer_permit,
)


def normalize_postgres_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


def psycopg_connection_factory(dsn: str) -> Callable[[], Any]:
    normalized = normalize_postgres_dsn(dsn)

    def connect() -> Any:
        import psycopg

        context = current_request_context()
        if context.project_id is None and context.organization_id is None:
            raise RuntimeError(
                "a verified organization or project scope is required before opening PostgreSQL"
            )
        raw_connection = psycopg.connect(normalized)
        try:
            raw_connection.execute(
                """
                SELECT
                    set_config('app.organization_id', %s, false),
                    set_config('app.project_id', %s, false),
                    set_config('app.region_code', %s, false),
                    set_config('app.subject_id', %s, false),
                    set_config('app.request_id', %s, false),
                    set_config('app.service_identity', %s, false),
                    set_config('app.platform_admin', %s, false),
                    set_config('app.is_admin', %s, false)
                """,
                (
                    context.organization_id or "",
                    context.project_id or "",
                    context.region_code or "",
                    context.subject_id or "",
                    context.request_id,
                    "true" if context.service_identity else "false",
                    "true" if context.platform_admin else "false",
                    "true" if context.platform_admin else "false",
                ),
            )
        except BaseException:
            raw_connection.close()
            raise
        return fence_connection_if_bound(raw_connection)

    return connect


def fence_connection_if_bound(connection: Any) -> Any:
    """Validate every ambient database fence in the transaction that commits the write."""

    permit_id = current_writer_permit()
    task_lease_id = current_platform_task_lease()
    if permit_id is None and task_lease_id is None:
        return connection
    return _FencedConnection(connection, permit_id=permit_id, task_lease_id=task_lease_id)


class _FencedConnection:
    """Validate the request permit in the same transaction immediately before commit."""

    def __init__(
        self,
        connection: Any,
        *,
        permit_id: UUID | None,
        task_lease_id: UUID | None,
    ) -> None:
        self._connection = connection
        self._permit_id = permit_id
        self._task_lease_id = task_lease_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def __enter__(self) -> _FencedConnection:
        self._connection.__enter__()
        return self

    def __exit__(self, *args: object) -> object:
        exception_type = args[0] if args else None
        if exception_type is None:
            try:
                self._assert_permit()
            except BaseException as exc:
                self._connection.__exit__(type(exc), exc, exc.__traceback__)
                raise
        return self._connection.__exit__(*args)

    def commit(self) -> None:
        self._assert_permit()
        self._connection.commit()

    def _assert_permit(self) -> None:
        try:
            if self._task_lease_id is not None:
                self._connection.execute(
                    "SELECT platform.assert_task_lease(%s)",
                    (self._task_lease_id,),
                )
            if self._permit_id is not None:
                self._connection.execute(
                    "SELECT platform.assert_writer_permit(%s)",
                    (self._permit_id,),
                )
        except Exception as exc:
            code = str(exc).partition("\n")[0].strip()
            if code.startswith("PLATFORM_") and code.replace("_", "").isalnum():
                raise MaintenanceContractError(
                    code, "database write fence validation failed"
                ) from exc
            raise
