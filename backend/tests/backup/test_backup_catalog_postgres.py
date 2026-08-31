from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from psycopg.rows import dict_row

from hc_data_platform.backup.catalog import (
    BackupCatalogAuditContext,
    BackupCatalogService,
    PostgresBackupCatalogRepository,
)
from hc_data_platform.backup.contracts import canonical_json_bytes, verify_signed_manifest
from hc_data_platform.core.dbapi import normalize_postgres_dsn

ROOT = Path(__file__).resolve().parents[3]
GOLDEN_MANIFEST = ROOT / "backend/tests/backup/fixtures/hc-platform-backup-v1.golden.json"


def _catalog_dsn() -> str:
    value = os.environ.get("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is required")
    return normalize_postgres_dsn(value)


def _golden_artifacts() -> tuple[bytes, bytes, Ed25519PublicKey]:
    fixture = json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    public_key = Ed25519PublicKey.from_public_bytes(
        base64.urlsafe_b64decode(fixture["public_key_base64url"] + "==")
    )
    return (
        canonical_json_bytes(fixture["manifest"]),
        canonical_json_bytes(fixture["signature"]),
        public_key,
    )


@pytest.mark.integration
def test_postgres_catalog_rebuild_is_atomic_append_only_and_redacted() -> None:
    dsn = _catalog_dsn()
    manifest_bytes, signature_bytes, public_key = _golden_artifacts()
    authenticated = verify_signed_manifest(
        manifest_bytes,
        signature_bytes,
        public_key=public_key,
    )
    service = BackupCatalogService(
        PostgresBackupCatalogRepository.from_dsn(dsn),
        trusted_signing_key_sha256=frozenset({authenticated.manifest.signature.public_key_sha256}),
        trusted_repository_ids=frozenset({"backup-repository-cn"}),
    )
    audit = BackupCatalogAuditContext(
        actor_id="backup-catalog-controller",
        request_id="catalog-rebuild-pg-001",
    )

    first = service.rebuild_from_repository_artifacts(
        manifest_bytes,
        signature_bytes,
        public_key=public_key,
        audit=audit,
    )
    second = service.rebuild_from_repository_artifacts(
        manifest_bytes,
        signature_bytes,
        public_key=public_key,
        audit=audit,
    )
    assert first == second
    verification_audit = BackupCatalogAuditContext(
        actor_id="backup-integrity-verifier",
        request_id="catalog-reverify-pg-001",
    )
    verified_again = service.append_integrity_verification(
        first.entry.backup_id,
        operation_id="catalog-reverify-pg-001",
        evidence_uri=(
            "backup-repository://backup-repository-cn/backup-20260828-001/checksums.sha256"
        ),
        evidence_sha256=authenticated.manifest_sha256,
        verifier_identity="backup-integrity-verifier",
        verified_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        audit=verification_audit,
    )
    assert (
        service.append_integrity_verification(
            first.entry.backup_id,
            operation_id="catalog-reverify-pg-001",
            evidence_uri=(
                "backup-repository://backup-repository-cn/backup-20260828-001/checksums.sha256"
            ),
            evidence_sha256=authenticated.manifest_sha256,
            verifier_identity="backup-integrity-verifier",
            verified_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
            audit=verification_audit,
        )
        == verified_again
    )

    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT backup_id, status, status_operation_id
            FROM platform.backup_catalog_current
            WHERE backup_id = %s
            """,
            (first.entry.backup_id,),
        )
        assert cursor.fetchone() == {
            "backup_id": "backup-20260828-001",
            "status": "INTEGRITY_VERIFIED",
            "status_operation_id": "catalog-reverify-pg-001",
        }
        cursor.execute(
            """
            SELECT count(*) AS count
            FROM platform.backup_catalog_status_facts
            WHERE backup_id = %s
            """,
            (first.entry.backup_id,),
        )
        assert cursor.fetchone() == {"count": 2}
        cursor.execute(
            """
            SELECT outcome, safe_details
            FROM access_control.audit_events
            WHERE action = 'platform.backup.catalog.rebuilt'
              AND resource_id = %s
            """,
            (first.entry.backup_id,),
        )
        audit_rows = cursor.fetchall()
        assert audit_rows == [
            {
                "outcome": "SUCCEEDED",
                "safe_details": {
                    "format_version": "hc-platform-backup/v1",
                    "repository_id": "backup-repository-cn",
                    "source_environment_id": "production-cn-east",
                    "status": "INTEGRITY_VERIFIED",
                },
            }
        ]
        assert "manifest_uri" not in json.dumps(audit_rows, sort_keys=True)
        assert "sha256" not in json.dumps(audit_rows, sort_keys=True)

    with (
        pytest.raises(psycopg.Error, match="PLATFORM_BACKUP_CATALOG_IMMUTABLE"),
        psycopg.connect(dsn) as connection,
    ):
        connection.execute(
            """
            UPDATE platform.backup_catalog_entries
            SET source_environment_id = 'tampered'
            WHERE backup_id = %s
            """,
            (first.entry.backup_id,),
        )

    with (
        pytest.raises(psycopg.Error, match="PLATFORM_BACKUP_CATALOG_IMMUTABLE"),
        psycopg.connect(dsn) as connection,
    ):
        connection.execute(
            "DELETE FROM platform.backup_catalog_status_facts WHERE backup_id = %s",
            (first.entry.backup_id,),
        )

    with (
        pytest.raises(
            psycopg.Error,
            match="PLATFORM_BACKUP_CATALOG_TRANSITION_INVALID",
        ),
        psycopg.connect(dsn) as connection,
    ):
        connection.execute(
            """
            INSERT INTO platform.backup_catalog_status_facts (
                backup_id, status, source_kind, operation_id,
                recorded_by, request_id
            ) VALUES (%s, 'CREATED', 'LIFECYCLE', %s, %s, %s)
            """,
            (
                first.entry.backup_id,
                "illegal-status-regression-001",
                "backup-catalog-controller",
                "catalog-rebuild-pg-002",
            ),
        )
