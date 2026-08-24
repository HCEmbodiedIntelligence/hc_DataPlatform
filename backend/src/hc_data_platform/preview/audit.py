"""Durable, redacted audit facts for preview capability issuance.

The media gateway itself intentionally accepts a short-lived HMAC capability
because HLS element fetches cannot attach a Bearer header.  The authenticated
descriptor issue/refresh endpoints are therefore the durable access decision
boundary: record that decision here, without ever persisting a capability URL,
artifact URI, object locator, or cache key.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Protocol
from uuid import uuid4

from hc_data_platform.core.context import current_request_context


@dataclass(frozen=True, slots=True)
class PreviewDescriptorAuditEvent:
    project_id: str
    region_code: str | None
    actor_id: str
    request_id: str
    session_id: str
    dataset_id: str
    rollout_id: str
    camera_id: str
    view_mode: str
    operation: Literal["CREATED", "REFRESHED"]
    grant_expires_at: datetime
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    audit_id: str = field(default_factory=lambda: str(uuid4()))


class PreviewAuditRecorder(Protocol):
    def append_descriptor_issue(self, event: PreviewDescriptorAuditEvent) -> None: ...


class InMemoryPreviewAuditRecorder:
    def __init__(self) -> None:
        self.events: list[PreviewDescriptorAuditEvent] = []

    def append_descriptor_issue(self, event: PreviewDescriptorAuditEvent) -> None:
        self.events.append(event)


class PostgresPreviewAuditRecorder:
    """Write the access decision through the same scoped core audit ledger."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def append_descriptor_issue(self, event: PreviewDescriptorAuditEvent) -> None:
        context = current_request_context()
        if context.organization_id is None:
            raise RuntimeError("preview audit writes require an exact organization scope")
        if context.project_id != event.project_id or context.region_code != event.region_code:
            raise RuntimeError("preview audit event does not match the request scope")
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
                    %s, %s, %s, %s, %s, 'preview.descriptor.issued', 'PREVIEW_SESSION',
                    %s, %s, NULL, NULL, %s::jsonb, %s
                )
                """,
                (
                    event.audit_id,
                    context.organization_id,
                    event.project_id,
                    event.region_code,
                    event.actor_id,
                    event.session_id,
                    event.request_id,
                    json.dumps(
                        {
                            "camera_id": event.camera_id,
                            "dataset_id": event.dataset_id,
                            "grant_expires_at": event.grant_expires_at.isoformat(),
                            "operation": event.operation,
                            "outcome": "AUTHORIZED",
                            "rollout_id": event.rollout_id,
                            "view_mode": event.view_mode,
                        },
                        sort_keys=True,
                    ),
                    event.occurred_at,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("preview descriptor audit insert did not affect one row")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
