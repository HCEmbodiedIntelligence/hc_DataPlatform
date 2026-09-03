"""Redacted audit records for fresh export-download authorization decisions."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from hc_data_platform.core.context import current_request_context


@dataclass(frozen=True, slots=True)
class ExportDownloadAuditEvent:
    project_id: str
    region_code: str | None
    actor_id: str
    request_id: str
    job_id: str
    dataset_id: str
    dataset_version: str
    export_format: str
    artifact_content_hash: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    audit_id: str = field(default_factory=lambda: str(uuid4()))


class ExportAuditRecorder(Protocol):
    def append_download_authorization(self, event: ExportDownloadAuditEvent) -> None: ...


class InMemoryExportAuditRecorder:
    def __init__(self) -> None:
        self.events: list[ExportDownloadAuditEvent] = []

    def append_download_authorization(self, event: ExportDownloadAuditEvent) -> None:
        self.events.append(event)


class PostgresExportAuditRecorder:
    """Persist the authorized download decision without a locator or signed URL."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def append_download_authorization(self, event: ExportDownloadAuditEvent) -> None:
        context = current_request_context()
        if context.organization_id is None:
            raise RuntimeError("export audit writes require an exact organization scope")
        if context.project_id != event.project_id or context.region_code != event.region_code:
            raise RuntimeError("export audit event does not match the request scope")
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
                    %s, %s, %s, %s, %s, 'export.download.authorized', 'PUBLISHED_EXPORT',
                    %s, %s, NULL, NULL, %s::jsonb, %s
                )
                """,
                (
                    event.audit_id,
                    context.organization_id,
                    event.project_id,
                    event.region_code,
                    event.actor_id,
                    event.job_id,
                    event.request_id,
                    json.dumps(
                        {
                            "artifact_content_hash": event.artifact_content_hash,
                            "dataset_id": event.dataset_id,
                            "dataset_version": event.dataset_version,
                            "export_format": event.export_format,
                            "outcome": "AUTHORIZED",
                        },
                        sort_keys=True,
                    ),
                    event.occurred_at,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("export download audit insert did not affect one row")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
