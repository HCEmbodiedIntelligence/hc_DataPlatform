from __future__ import annotations

import json
import os
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
from hc_data_platform.lance_catalog.audit import (  # noqa: E402
    LanceStepWindowAuditEvent,
    PostgresLanceCatalogAuditRecorder,
)


@pytest.mark.integration
def test_postgres_lance_step_window_audit_is_scoped_and_contains_no_sample_payload() -> None:
    raw_dsn = os.getenv("HC_TEST_POSTGRES_DSN")
    if not raw_dsn:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    dsn = normalize_postgres_dsn(raw_dsn)
    migration = Path(__file__).parents[2] / "migrations" / "security" / "001_core.sql"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(migration.read_text(encoding="utf-8"))

    suffix = uuid4().hex[:12]
    organization_id = f"p06-lance-audit-org-{suffix}"
    event = LanceStepWindowAuditEvent(
        project_id=f"p06-lance-audit-{suffix}",
        region_code="cn-test",
        actor_id="p06-window-reader",
        request_id=str(uuid4()),
        dataset_id="dataset-p06",
        rollout_id="rollout-p06",
        dataset_version=7,
        start_step=10,
        end_step=20,
        returned_step_count=9,
    )
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO registry.organization_projects (organization_id, project_id) "
            "VALUES (%s, %s)",
            (organization_id, event.project_id),
        )
    context_token = bind_request_context(
        RequestContext(
            organization_id=organization_id,
            project_id=event.project_id,
            region_code=event.region_code,
            subject_id=event.actor_id,
            request_id=event.request_id,
            service_identity=True,
        )
    )
    try:
        PostgresLanceCatalogAuditRecorder(psycopg_connection_factory(dsn)).append_step_window_read(
            event
        )
    finally:
        reset_request_context(context_token)

    try:
        with psycopg.connect(dsn) as connection:
            row = connection.execute(
                """
                SELECT organization_id, project_id, region_code, actor_id, action, resource_type,
                       resource_id, request_id, details
                FROM core.audit_events
                WHERE audit_id = %s
                """,
                (event.audit_id,),
            ).fetchone()
        assert row is not None
        assert row[:8] == (
            organization_id,
            event.project_id,
            "cn-test",
            "p06-window-reader",
            "lance.step_window.read",
            "LANCE_STEP_WINDOW",
            "dataset-p06:rollout-p06:7",
            event.request_id,
        )
        details = row[8] if isinstance(row[8], dict) else json.loads(row[8])
        assert details == {
            "dataset_version": 7,
            "end_step": 20,
            "outcome": "AUTHORIZED",
            "returned_step_count": 9,
            "start_step": 10,
        }
        serialized = json.dumps(details, sort_keys=True)
        assert all(
            forbidden not in serialized
            for forbidden in ("modalit", "value", "uri", "url", "object", "sample")
        )
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
                (organization_id, event.project_id),
            )
            connection.execute(
                "DELETE FROM registry.organization_projects "
                "WHERE organization_id = %s AND project_id = %s",
                (organization_id, event.project_id),
            )
