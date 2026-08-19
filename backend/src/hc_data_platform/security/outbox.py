"""Scope-bound outbox claiming and dispatch with safe, durable retry facts."""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

from hc_data_platform.core.events import DomainEventEnvelope

from .audit import canonical_hash

_ERROR_CODE = re.compile(r"^[A-Z0-9_]{1,128}$")


@dataclass(frozen=True, slots=True)
class OutboxClaim:
    event: DomainEventEnvelope
    claim_token: str
    attempt: int


class OutboxDeliveryRepository(Protocol):
    def claim_next(
        self,
        *,
        project_id: str,
        region_code: str,
        worker_id: str,
        now: datetime,
        claimed_until: datetime,
    ) -> OutboxClaim | None: ...

    def mark_dispatched(self, claim: OutboxClaim, *, occurred_at: datetime) -> None: ...

    def mark_retry(
        self,
        claim: OutboxClaim,
        *,
        error_code: str,
        retry_at: datetime,
        occurred_at: datetime,
    ) -> None: ...


OutboxHandler = Callable[[DomainEventEnvelope], Awaitable[object] | object]


class OutboxDispatcher:
    """Dispatch one exact project/region scope; callers enumerate authorized scopes."""

    def __init__(
        self,
        repository: OutboxDeliveryRepository,
        handlers: Mapping[str, OutboxHandler],
        *,
        worker_id: str,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        lease_duration: timedelta = timedelta(minutes=5),
        retry_delay: Callable[[int], timedelta] = lambda attempt: timedelta(
            seconds=min(300, 2 ** min(attempt, 8))
        ),
    ) -> None:
        if not worker_id:
            raise ValueError("outbox worker_id is required")
        if lease_duration <= timedelta(0):
            raise ValueError("outbox lease duration must be positive")
        self._repository = repository
        self._handlers = dict(handlers)
        self._worker_id = worker_id
        self._clock = clock
        self._lease_duration = lease_duration
        self._retry_delay = retry_delay

    async def dispatch_one(self, *, project_id: str, region_code: str) -> bool:
        now = self._clock()
        claim = self._repository.claim_next(
            project_id=project_id,
            region_code=region_code,
            worker_id=self._worker_id,
            now=now,
            claimed_until=now + self._lease_duration,
        )
        if claim is None:
            return False
        handler = self._handlers.get(claim.event.event_type)
        if handler is None:
            self._repository.mark_retry(
                claim,
                error_code="OUTBOX_HANDLER_NOT_CONFIGURED",
                retry_at=now + self._retry_delay(claim.attempt),
                occurred_at=now,
            )
            return True
        try:
            result = handler(claim.event)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            code = stable_error_code(exc)
            self._repository.mark_retry(
                claim,
                error_code=code,
                retry_at=now + self._retry_delay(claim.attempt),
                occurred_at=now,
            )
        else:
            self._repository.mark_dispatched(claim, occurred_at=now)
        return True


def stable_error_code(error: BaseException) -> str:
    candidate = getattr(error, "code", None)
    if not isinstance(candidate, str):
        problem = getattr(error, "problem", None)
        candidate = getattr(problem, "code", None)
    if isinstance(candidate, str):
        normalized = candidate.upper().replace("-", "_")
        if _ERROR_CODE.fullmatch(normalized):
            return normalized
    return "OUTBOX_DELIVERY_FAILED"


class InMemoryOutboxDeliveryRepository:
    def __init__(self, events: tuple[DomainEventEnvelope, ...] = ()) -> None:
        self._events = {event.event_id: event for event in events}
        self._state: dict[str, dict[str, Any]] = {
            event.event_id: {
                "attempts": 0,
                "available_at": event.occurred_at,
                "published_at": None,
                "claim_token": None,
                "claimed_until": None,
                "error_code": None,
            }
            for event in events
        }
        self.audit: list[dict[str, object]] = []
        self._lock = RLock()

    def claim_next(
        self,
        *,
        project_id: str,
        region_code: str,
        worker_id: str,
        now: datetime,
        claimed_until: datetime,
    ) -> OutboxClaim | None:
        del worker_id
        with self._lock:
            candidates = sorted(
                (
                    event
                    for event in self._events.values()
                    if event.project_id == project_id
                    and event.region_code == region_code
                    and self._claimable(self._state[event.event_id], now)
                ),
                key=lambda event: (event.occurred_at, event.event_id),
            )
            if not candidates:
                return None
            event = candidates[0]
            state = self._state[event.event_id]
            token = str(uuid4())
            state["attempts"] += 1
            state["claim_token"] = token
            state["claimed_until"] = claimed_until
            return OutboxClaim(event=event, claim_token=token, attempt=state["attempts"])

    def mark_dispatched(self, claim: OutboxClaim, *, occurred_at: datetime) -> None:
        with self._lock:
            state = self._owned(claim)
            state.update(
                published_at=occurred_at,
                claim_token=None,
                claimed_until=None,
                error_code=None,
            )
            self.audit.append(self._audit(claim, "workflow.dispatch.accepted", None))

    def mark_retry(
        self,
        claim: OutboxClaim,
        *,
        error_code: str,
        retry_at: datetime,
        occurred_at: datetime,
    ) -> None:
        del occurred_at
        with self._lock:
            state = self._owned(claim)
            state.update(
                available_at=retry_at,
                claim_token=None,
                claimed_until=None,
                error_code=error_code,
            )
            self.audit.append(self._audit(claim, "workflow.dispatch.retry", error_code))

    def state(self, event_id: str) -> Mapping[str, Any]:
        with self._lock:
            return dict(self._state[event_id])

    @staticmethod
    def _claimable(state: Mapping[str, Any], now: datetime) -> bool:
        return (
            state["published_at"] is None
            and state["available_at"] <= now
            and (state["claimed_until"] is None or state["claimed_until"] <= now)
        )

    def _owned(self, claim: OutboxClaim) -> dict[str, Any]:
        state = self._state[claim.event.event_id]
        if state["claim_token"] != claim.claim_token:
            raise RuntimeError("outbox claim is no longer owned")
        return state

    @staticmethod
    def _audit(claim: OutboxClaim, action: str, error_code: str | None) -> dict[str, object]:
        return {
            "project_id": claim.event.project_id,
            "region_code": claim.event.region_code,
            "resource_id": claim.event.aggregate_id,
            "workflow_id": claim.event.payload.get("workflow_id"),
            "action": action,
            "error_code": error_code,
            "attempt": claim.attempt,
        }


class PostgresOutboxDeliveryRepository:
    """DB-API implementation; the connection factory must install exact RLS scope."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def claim_next(
        self,
        *,
        project_id: str,
        region_code: str,
        worker_id: str,
        now: datetime,
        claimed_until: datetime,
    ) -> OutboxClaim | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT event_id, envelope
                    FROM core.outbox_events
                    WHERE project_id = %s AND region_code = %s
                      AND published_at IS NULL AND available_at <= %s
                      AND (claimed_until IS NULL OR claimed_until <= %s)
                    ORDER BY available_at, occurred_at, event_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """,
                    (project_id, region_code, now, now),
                )
                raw = cursor.fetchone()
                if raw is None:
                    connection.commit()
                    return None
                event_id = str(raw[0])
                event = DomainEventEnvelope.model_validate(
                    json.loads(raw[1]) if isinstance(raw[1], str) else raw[1]
                )
                if event.event_id != event_id:
                    raise RuntimeError("outbox envelope identity mismatch")
                claim_token = str(uuid4())
                cursor.execute(
                    """
                    UPDATE core.outbox_events
                    SET claim_token = %s, claimed_by = %s, claimed_until = %s,
                        publish_attempts = publish_attempts + 1
                    WHERE event_id = %s
                    RETURNING publish_attempts
                    """,
                    (claim_token, worker_id, claimed_until, event_id),
                )
                attempt = int(cursor.fetchone()[0])
            connection.commit()
            return OutboxClaim(event=event, claim_token=claim_token, attempt=attempt)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_dispatched(self, claim: OutboxClaim, *, occurred_at: datetime) -> None:
        self._finish(claim, occurred_at=occurred_at, error_code=None, retry_at=None)

    def mark_retry(
        self,
        claim: OutboxClaim,
        *,
        error_code: str,
        retry_at: datetime,
        occurred_at: datetime,
    ) -> None:
        if not _ERROR_CODE.fullmatch(error_code):
            raise ValueError("outbox error_code must be stable and safe")
        self._finish(
            claim,
            occurred_at=occurred_at,
            error_code=error_code,
            retry_at=retry_at,
        )

    def _finish(
        self,
        claim: OutboxClaim,
        *,
        occurred_at: datetime,
        error_code: str | None,
        retry_at: datetime | None,
    ) -> None:
        event = claim.event
        status = "DISPATCHED" if error_code is None else "RETRY_WAIT"
        action = "workflow.dispatch.accepted" if error_code is None else "workflow.dispatch.retry"
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE core.outbox_events
                    SET published_at = %s,
                        available_at = COALESCE(%s, available_at),
                        last_error = %s,
                        last_error_code = %s,
                        claim_token = NULL, claimed_by = NULL, claimed_until = NULL
                    WHERE event_id = %s AND claim_token = %s AND published_at IS NULL
                    RETURNING event_id
                    """,
                    (
                        occurred_at if error_code is None else None,
                        retry_at,
                        error_code,
                        error_code,
                        event.event_id,
                        claim.claim_token,
                    ),
                )
                if cursor.fetchone() is None:
                    raise RuntimeError("outbox claim is no longer owned")
                cursor.execute(
                    """
                    UPDATE ingest.workflow_triggers
                    SET status = %s, attempts = %s, last_error_code = %s, updated_at = %s
                    WHERE event_id = %s AND project_id = %s AND region_code = %s
                    """,
                    (
                        status,
                        claim.attempt,
                        error_code,
                        occurred_at,
                        event.event_id,
                        event.project_id,
                        event.region_code,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("ingest workflow trigger is missing")
                workflow_id = str(event.payload.get("workflow_id", ""))
                details: dict[str, object] = {
                    "workflow_id": workflow_id,
                    "attempt": claim.attempt,
                }
                if error_code is not None:
                    details["error_code"] = error_code
                audit_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"{event.event_id}:{action}:{claim.attempt}",
                    )
                )
                cursor.execute(
                    """
                    INSERT INTO core.audit_events (
                        audit_id, project_id, region_code, actor_id, action,
                        resource_type, resource_id, request_id, before_hash,
                        after_hash, details, occurred_at
                    ) VALUES (%s, %s, %s, 'outbox-dispatcher', %s,
                              %s, %s, %s, NULL, %s, %s::jsonb, %s)
                    ON CONFLICT (audit_id) DO NOTHING
                    """,
                    (
                        audit_id,
                        event.project_id,
                        event.region_code,
                        action,
                        event.aggregate_type,
                        event.aggregate_id,
                        event.trace_id or event.event_id,
                        canonical_hash({"status": status, **details}),
                        json.dumps(details, sort_keys=True),
                        occurred_at,
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
