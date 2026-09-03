"""Synchronous durable idempotency for FastAPI services backed by psycopg."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

from pydantic import BaseModel

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem

from .idempotency import IdempotencyResult, idempotency_conflict, request_fingerprint

T = TypeVar("T")


class PsycopgIdempotencyStore:
    """Serialize one scoped command and its typed response in the caller's RLS context."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        response_decoder: Callable[[object], Any] | None = None,
        ttl: timedelta = timedelta(hours=24),
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("idempotency TTL must be positive")
        self._connection_factory = connection_factory
        self._response_decoder = response_decoder or (lambda value: value)
        self._ttl = ttl

    def execute(
        self,
        *,
        scope: str,
        key: str,
        payload: Any,
        action: Callable[[], T],
    ) -> IdempotencyResult[T]:
        if not scope or not key:
            raise ValueError("idempotency scope and key must not be empty")
        context = current_request_context()
        if context.organization_id is None:
            raise problem(
                status=403,
                code="IDEMPOTENCY_SCOPE_DENIED",
                title="Idempotency scope denied",
                detail="A verified organization scope is required for idempotent commands.",
            )
        if context.project_id != scope:
            raise problem(
                status=403,
                code="IDEMPOTENCY_SCOPE_DENIED",
                title="Idempotency scope denied",
                detail="The idempotency command does not match the selected project scope.",
            )

        fingerprint = request_fingerprint(payload)
        now = datetime.now(timezone.utc)
        expires_at = now + self._ttl
        identity = (context.organization_id, scope, context.region_code or "", scope, key)
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
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
                row = cursor.fetchone()
                if row is None:
                    raise problem(
                        status=403,
                        code="IDEMPOTENCY_SCOPE_DENIED",
                        title="Idempotency scope denied",
                        detail="The idempotency record is outside the selected database scope.",
                    )

                stored_fingerprint, stored_response, stored_expiry = row
                expired = stored_expiry <= now
                if not expired:
                    if str(stored_fingerprint) != fingerprint:
                        raise idempotency_conflict()
                    if stored_response is not None:
                        decoded = self._decode(stored_response)
                        connection.commit()
                        return IdempotencyResult(value=decoded, replayed=True)
                else:
                    cursor.execute(
                        """
                        UPDATE core.idempotency_records
                        SET request_fingerprint = %s, response_json = NULL,
                            created_at = %s, expires_at = %s
                        WHERE organization_id = %s AND project_id = %s AND region_code = %s
                          AND scope_key = %s AND idempotency_key = %s
                        """,
                        (fingerprint, now, expires_at, *identity),
                    )

                value = action()
                encoded = self._encode(value)
                cursor.execute(
                    """
                    UPDATE core.idempotency_records
                    SET response_json = %s::jsonb, expires_at = %s
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                      AND scope_key = %s AND idempotency_key = %s
                    """,
                    (encoded, expires_at, *identity),
                )
            connection.commit()
            return IdempotencyResult(value=value, replayed=False)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _encode(value: object) -> str:
        payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)

    def _decode(self, value: object) -> Any:
        payload = json.loads(value) if isinstance(value, str) else value
        return self._response_decoder(payload)
