from __future__ import annotations

import os

import psycopg
import pytest

from hc_data_platform.backup.restore_reconciliation_adapters import (
    _audit_integrity_check,
    _database_object_references,
    _outbox_check,
    _permissions_check,
    _workflow_database_facts,
)
from hc_data_platform.core.dbapi import normalize_postgres_dsn


@pytest.mark.integration
def test_real_postgres_reconciliation_queries_share_one_read_only_snapshot() -> None:
    configured = os.environ.get("HC_TEST_POSTGRES_DSN")
    if not configured:
        pytest.skip("HC_TEST_POSTGRES_DSN is required")
    connection = psycopg.connect(normalize_postgres_dsn(configured))
    try:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY, DEFERRABLE")
        audit = _audit_integrity_check(connection)
        outbox = _outbox_check(connection)
        workflow = _workflow_database_facts(connection)
        permissions = _permissions_check(connection)
        references = _database_object_references(connection)
        read_only = connection.execute(
            "SELECT current_setting('transaction_read_only')::boolean"
        ).fetchone()
        connection.rollback()
    finally:
        connection.close()

    assert read_only == (True,)
    assert audit.status == "PASS"
    assert outbox.status == "PASS"
    assert permissions.status == "PASS"
    assert workflow.total_fact_count >= 0
    assert isinstance(references, tuple)
