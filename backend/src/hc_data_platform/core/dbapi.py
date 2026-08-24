"""Production psycopg connection factory with fail-closed PostgreSQL RLS scope."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .context import current_request_context


def normalize_postgres_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


def psycopg_connection_factory(dsn: str) -> Callable[[], Any]:
    normalized = normalize_postgres_dsn(dsn)

    def connect() -> Any:
        import psycopg

        context = current_request_context()
        if context.project_id is None:
            raise RuntimeError("a verified project scope is required before opening PostgreSQL")
        connection = psycopg.connect(normalized)
        try:
            connection.execute(
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
                    context.project_id,
                    context.region_code or "",
                    context.subject_id or "",
                    context.request_id,
                    "true" if context.service_identity else "false",
                    "true" if context.platform_admin else "false",
                    "true" if context.platform_admin else "false",
                ),
            )
        except BaseException:
            connection.close()
            raise
        return connection

    return connect
