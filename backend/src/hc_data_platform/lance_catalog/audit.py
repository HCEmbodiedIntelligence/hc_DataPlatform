"""Redacted audit records for scoped Lance step-window reads.

The catalog API intentionally exposes only logical values, but a P06 non-camera
panel is still an access to potentially sensitive collection data.  Record the
authorized read at the authenticated boundary without copying sample values,
modality payloads, object locations, or any browser capability into the ledger.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from hc_data_platform.core.context import current_request_context


@dataclass(frozen=True, slots=True)
class LanceStepWindowAuditEvent:
    project_id: str
    region_code: str | None
    actor_id: str
    request_id: str
    dataset_id: str
    rollout_id: str
    dataset_version: int
    start_step: int
    end_step: int
    returned_step_count: int
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    audit_id: str = field(default_factory=lambda: str(uuid4()))


class LanceCatalogAuditRecorder(Protocol):
    def append_step_window_read(self, event: LanceStepWindowAuditEvent) -> None: ...


class InMemoryLanceCatalogAuditRecorder:
    def __init__(self) -> None:
        self.events: list[LanceStepWindowAuditEvent] = []

    def append_step_window_read(self, event: LanceStepWindowAuditEvent) -> None:
        self.events.append(event)


class PostgresLanceCatalogAuditRecorder:
    """Persist one redacted successful read fact in the core audit ledger."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def append_step_window_read(self, event: LanceStepWindowAuditEvent) -> None:
        context = current_request_context()
        if context.organization_id is None:
            raise RuntimeError("Lance audit writes require an exact organization scope")
        if context.project_id != event.project_id or context.region_code != event.region_code:
            raise RuntimeError("Lance audit event does not match the request scope")
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO core.audit_events (
                    audit_id, organization_id, project_id, region_code,
                    actor_id, action, resource_type,
                    resource_id, request_id, before_hash, after_hash, details, occurred_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'lance.step_window.read', 'LANCE_STEP_WINDOW',
                    %s, %s, NULL, NULL, %s::jsonb, %s
                )
                """,
                (
                    event.audit_id,
                    context.organization_id,
                    event.project_id,
                    event.region_code,
                    event.actor_id,
                    f"{event.dataset_id}:{event.rollout_id}:{event.dataset_version}",
                    event.request_id,
                    json.dumps(
                        {
                            "dataset_version": event.dataset_version,
                            "end_step": event.end_step,
                            "outcome": "AUTHORIZED",
                            "returned_step_count": event.returned_step_count,
                            "start_step": event.start_step,
                        },
                        sort_keys=True,
                    ),
                    event.occurred_at,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Lance step-window audit insert did not affect one row")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
