from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

from hc_data_platform.core.context import (  # noqa: E402
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import (  # noqa: E402
    normalize_postgres_dsn,
    psycopg_connection_factory,
)
from hc_data_platform.preview.audit import (  # noqa: E402
    PostgresPreviewAuditRecorder,
    PreviewDescriptorAuditEvent,
)


@pytest.mark.integration
def test_postgres_preview_descriptor_audit_is_scoped_and_excludes_capabilities() -> None:
    raw_dsn = os.getenv("HC_TEST_POSTGRES_DSN")
    if not raw_dsn:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    dsn = normalize_postgres_dsn(raw_dsn)
    migration = Path(__file__).parents[2] / "migrations" / "security" / "001_core.sql"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(migration.read_text(encoding="utf-8"))

    suffix = uuid4().hex[:12]
    organization_id = f"preview-audit-org-{suffix}"
    project_id = f"preview-audit-{suffix}"
    session_id = str(uuid4())
    event = PreviewDescriptorAuditEvent(
        project_id=project_id,
        region_code="cn-test",
        actor_id="preview-auditor",
        request_id=str(uuid4()),
        session_id=session_id,
        dataset_id="dataset-a",
        rollout_id="rollout-a",
        camera_id="front",
        view_mode="original",
        operation="CREATED",
        grant_expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO registry.organization_projects (organization_id, project_id) "
            "VALUES (%s, %s)",
            (organization_id, project_id),
        )
    token = bind_request_context(
        RequestContext(
            organization_id=organization_id,
            project_id=project_id,
            region_code="cn-test",
            subject_id="preview-auditor",
            request_id=event.request_id,
            service_identity=True,
        )
    )
    try:
        PostgresPreviewAuditRecorder(psycopg_connection_factory(dsn)).append_descriptor_issue(event)
    finally:
        reset_request_context(token)

    try:
        with psycopg.connect(dsn) as connection:
            row = connection.execute(
                """
                SELECT organization_id, actor_id, action, resource_type,
                       resource_id, request_id, details
                FROM core.audit_events
                WHERE audit_id = %s
                """,
                (event.audit_id,),
            ).fetchone()
        assert row is not None
        assert row[:6] == (
            organization_id,
            "preview-auditor",
            "preview.descriptor.issued",
            "PREVIEW_SESSION",
            session_id,
            event.request_id,
        )
        details = row[6] if isinstance(row[6], dict) else json.loads(row[6])
        assert details == {
            "camera_id": "front",
            "dataset_id": "dataset-a",
            "grant_expires_at": event.grant_expires_at.isoformat(),
            "operation": "CREATED",
            "outcome": "AUTHORIZED",
            "rollout_id": "rollout-a",
            "view_mode": "original",
        }
        serialized = json.dumps(details, sort_keys=True)
        assert all(forbidden not in serialized for forbidden in ("url", "uri", "artifact", "cache"))
    finally:
        with psycopg.connect(dsn) as connection:
            connection.execute(
                "DELETE FROM core.audit_integrity_entries WHERE audit_id = %s", (event.audit_id,)
            )
            connection.execute(
                "DELETE FROM core.audit_events WHERE audit_id = %s", (event.audit_id,)
            )
            connection.execute(
                "DELETE FROM core.audit_integrity_heads "
                "WHERE organization_id = %s AND project_id = %s",
                (organization_id, project_id),
            )
            connection.execute(
                "DELETE FROM registry.organization_projects "
                "WHERE organization_id = %s AND project_id = %s",
                (organization_id, project_id),
            )
