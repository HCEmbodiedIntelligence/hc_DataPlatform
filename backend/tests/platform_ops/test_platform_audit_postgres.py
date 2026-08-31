from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hc_data_platform.backup.catalog import (
    BackupCatalogAuditContext,
    BackupCatalogService,
    PostgresBackupCatalogRepository,
)
from hc_data_platform.backup.contracts import canonical_json_bytes, verify_signed_manifest
from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.platform_ops.audit import (
    PlatformAuditService,
    PostgresPlatformAuditRepository,
)
from hc_data_platform.platform_ops.maintenance import (
    OperationKind,
    PlatformAuditContext,
    PostgresMaintenanceRepository,
)
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import (
    CAPABILITY_PLATFORM_MAINTENANCE_VERIFY,
    CAPABILITY_PLATFORM_OPERATIONS_READ,
    CAPABILITY_PLATFORM_RELEASE_OPERATE,
)

DSN = os.getenv("HC_PLATFORM_AUDIT_TEST_POSTGRES_DSN")
ROOT = Path(__file__).resolve().parents[3]
GOLDEN_MANIFEST = ROOT / "backend/tests/backup/fixtures/hc-platform-backup-v1.golden.json"
pytestmark = pytest.mark.skipif(
    DSN is None, reason="HC_PLATFORM_AUDIT_TEST_POSTGRES_DSN is required"
)


def _auth(capability: str) -> AuthContext:
    return AuthContext(
        subject_id=f"obs503-{capability.replace('.', '-')}",
        project_ids=frozenset(),
        region_codes=frozenset(),
        roles=frozenset(),
        capabilities=frozenset({capability}),
    )


def test_real_postgres_platform_events_are_chained_immutable_and_redacted() -> None:
    assert DSN is not None
    repository = PostgresPlatformAuditRepository.from_dsn(DSN)
    service = PlatformAuditService(repository, cursor_secret="obs503-platform-audit-secret")
    fixture = json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    manifest_bytes = canonical_json_bytes(fixture["manifest"])
    signature_bytes = canonical_json_bytes(fixture["signature"])
    public_key = Ed25519PublicKey.from_public_bytes(
        base64.urlsafe_b64decode(fixture["public_key_base64url"] + "==")
    )
    authenticated = verify_signed_manifest(manifest_bytes, signature_bytes, public_key=public_key)
    backup_service = BackupCatalogService(
        PostgresBackupCatalogRepository.from_dsn(DSN),
        trusted_signing_key_sha256=frozenset({authenticated.manifest.signature.public_key_sha256}),
        trusted_repository_ids=frozenset({"backup-repository-cn"}),
    )
    backup = backup_service.rebuild_from_repository_artifacts(
        manifest_bytes,
        signature_bytes,
        public_key=public_key,
        audit=BackupCatalogAuditContext(
            actor_id="obs503-backup-operator",
            request_id="request-backup",
        ),
    )

    maintenance = PostgresMaintenanceRepository.from_dsn(DSN)
    maintenance_operations: tuple[tuple[str, str, OperationKind, str], ...] = (
        (
            "restore-sensitive-id",
            "obs503-restore-environment",
            "RESTORE",
            "platform.break-glass",
        ),
        (
            "release-sensitive-id",
            "obs503-release-environment",
            "RELEASE",
            CAPABILITY_PLATFORM_RELEASE_OPERATE,
        ),
    )
    for operation_id, environment_id, operation_kind, capability in maintenance_operations:
        maintenance.request_operation(
            operation_id=operation_id,
            environment_id=environment_id,
            operation_kind=operation_kind,
            plan_digest=f"sha256:{'a' * 64}",
            requested_by="obs503-maintenance-operator",
            audit=PlatformAuditContext(
                actor_id="obs503-maintenance-operator",
                request_id=f"request-{operation_kind.lower()}",
                capability_key=capability,
            ),
        )

    normalized = normalize_postgres_dsn(DSN)
    with psycopg.connect(normalized) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
                SELECT event_id FROM access_control.audit_events
                 WHERE (action = 'platform.backup.catalog.rebuilt' AND resource_id = %s)
                    OR (action = 'platform.maintenance.requested'
                        AND resource_id = ANY(%s))
                 ORDER BY occurred_at, event_id
                """,
            (
                backup.entry.backup_id,
                ["restore-sensitive-id", "release-sensitive-id"],
            ),
        )
        inserted = [row[0] for row in cursor.fetchall()]
    assert len(inserted) == 3

    integrity = service.verify_integrity(
        auth=_auth(CAPABILITY_PLATFORM_MAINTENANCE_VERIFY),
        request_id="request-integrity",
    )
    assert integrity.status == "PASSED"
    assert integrity.checked_event_count == 3
    assert integrity.checked_chain_count == 1

    page = service.list_events(
        auth=_auth(CAPABILITY_PLATFORM_OPERATIONS_READ),
        request_id="request-list",
        limit=20,
    )
    selected = [event for event in page.items if event.event_id in inserted]
    assert {event.domain for event in selected} == {"BACKUP", "RESTORE", "RELEASE"}

    with pytest.raises(ProblemException) as denied:
        service.export(
            auth=_auth(CAPABILITY_PLATFORM_RELEASE_OPERATE),
            request_id="request-wrong-capability",
        )
    assert denied.value.problem.status == 403

    exported = service.export(
        auth=_auth(CAPABILITY_PLATFORM_MAINTENANCE_VERIFY),
        request_id="request-export",
    )
    rows = [json.loads(line) for line in exported.payload.decode().splitlines()]
    selected_rows = [row for row in rows if UUID(row["event_id"]) in inserted]
    assert {row["domain"] for row in selected_rows} == {"BACKUP", "RESTORE", "RELEASE"}
    for forbidden in (
        "obs503-operator",
        "obs503-backup-operator",
        "obs503-maintenance-operator",
        "sensitive-id",
        "backup-repository-cn",
        "production-cn-east",
        "obs503-do-not-export",
        "https://",
        "signed_url",
        "object_key",
        "secret",
    ):
        assert forbidden not in exported.payload.decode()

    with psycopg.connect(normalized) as connection:
        with connection.cursor() as cursor:
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                cursor.execute(
                    "UPDATE access_control.audit_events SET outcome = 'FAILED' WHERE event_id = %s",
                    (inserted[0],),
                )
            connection.rollback()
            with pytest.raises(
                psycopg.errors.RaiseException,
                match="PLATFORM_AUDIT_INTEGRITY_ENTRY_IMMUTABLE",
            ):
                cursor.execute(
                    """
                    UPDATE platform.platform_audit_integrity_entries
                       SET event_hash = repeat('0', 64)
                     WHERE event_id = %s
                    """,
                    (inserted[0],),
                )
            connection.rollback()
            cursor.execute(
                """
                UPDATE platform.platform_audit_integrity_head
                   SET last_sequence = last_sequence + 1
                 WHERE singleton
                """
            )
        connection.commit()

    assert repository.integrity().status == "FAILED"
    with pytest.raises(ProblemException) as blocked:
        service.export(
            auth=_auth(CAPABILITY_PLATFORM_MAINTENANCE_VERIFY),
            request_id="request-tampered-export",
        )
    assert blocked.value.problem.status == 409
    assert blocked.value.problem.code == "PLATFORM_AUDIT_INTEGRITY_FAILED"
    if os.getenv("HC_PLATFORM_AUDIT_PRINT_EVIDENCE") == "1":
        print(
            json.dumps(
                {
                    "actual_production_actions": [
                        "platform.backup.catalog.rebuilt",
                        "platform.maintenance.requested:RESTORE",
                        "platform.maintenance.requested:RELEASE",
                    ],
                    "business_event_ids": [str(event_id) for event_id in inserted],
                    "business_event_count": len(inserted),
                    "domains": sorted({row["domain"] for row in selected_rows}),
                    "export_event_count": exported.event_count,
                    "export_sha256": hashlib.sha256(exported.payload).hexdigest(),
                    "integrity_before_tamper": integrity.status,
                    "integrity_event_count_before_first_verification": (
                        integrity.checked_event_count
                    ),
                    "integrity_after_head_tamper": repository.integrity().status,
                    "source_update_rejected": True,
                    "integrity_entry_update_rejected": True,
                    "wrong_capability_http_status": denied.value.problem.status,
                    "tampered_export_http_status": blocked.value.problem.status,
                    "forbidden_export_match_count": 0,
                },
                sort_keys=True,
            )
        )
