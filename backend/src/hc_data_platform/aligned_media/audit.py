from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from hc_data_platform.core.context import current_request_context


@dataclass(frozen=True, slots=True)
class AlignedMediaAuthorizationAuditEvent:
    project_id: str
    region_code: str
    actor_id: str
    request_id: str
    artifact_id: str
    dataset_id: str
    rollout_id: str
    dataset_version: int
    camera_id: str
    grant_expires_at: datetime
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    audit_id: str = field(default_factory=lambda: str(uuid4()))


class AlignedMediaAuditRecorder(Protocol):
    def append_authorization(self, event: AlignedMediaAuthorizationAuditEvent) -> None: ...


class InMemoryAlignedMediaAuditRecorder:
    def __init__(self) -> None:
        self.events: list[AlignedMediaAuthorizationAuditEvent] = []

    def append_authorization(self, event: AlignedMediaAuthorizationAuditEvent) -> None:
        self.events.append(event)


class PostgresAlignedMediaAuditRecorder:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def append_authorization(self, event: AlignedMediaAuthorizationAuditEvent) -> None:
        context = current_request_context()
        if context.organization_id is None:
            raise RuntimeError("aligned media audit requires an organization scope")
        if context.project_id != event.project_id or context.region_code != event.region_code:
            raise RuntimeError("aligned media audit event does not match the request scope")
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO core.audit_events (
                    audit_id, organization_id, project_id, region_code,
                    actor_id, action, resource_type, resource_id, request_id,
                    before_hash, after_hash, details, occurred_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'aligned_media.authorized',
                    'ALIGNED_MEDIA_ARTIFACT', %s, %s, NULL, NULL, %s::jsonb, %s
                )
                """,
                (
                    event.audit_id,
                    context.organization_id,
                    event.project_id,
                    event.region_code,
                    event.actor_id,
                    event.artifact_id,
                    event.request_id,
                    json.dumps(
                        {
                            "camera_id": event.camera_id,
                            "dataset_id": event.dataset_id,
                            "dataset_version": event.dataset_version,
                            "grant_expires_at": event.grant_expires_at.isoformat(),
                            "outcome": "AUTHORIZED",
                            "rollout_id": event.rollout_id,
                        },
                        sort_keys=True,
                    ),
                    event.occurred_at,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("aligned media audit insert did not affect one row")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
