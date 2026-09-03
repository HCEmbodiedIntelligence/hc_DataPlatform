from __future__ import annotations

import base64
import json
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hc_data_platform.backup.catalog import (
    BackupCatalogAuditContext,
    BackupCatalogError,
    BackupCatalogService,
    InMemoryBackupCatalogRepository,
)
from hc_data_platform.backup.contracts import (
    VerifiedManifestV1,
    canonical_json_bytes,
    verify_signed_manifest,
)

ROOT = Path(__file__).resolve().parents[3]
GOLDEN_MANIFEST = ROOT / "backend/tests/backup/fixtures/hc-platform-backup-v1.golden.json"
MIGRATION = ROOT / "backend/migrations/platform/003_backup_catalog.sql"


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


def _service(
    repository: InMemoryBackupCatalogRepository,
    public_key_sha256: str,
) -> BackupCatalogService:
    return BackupCatalogService(
        repository,
        trusted_signing_key_sha256=frozenset({public_key_sha256}),
        trusted_repository_ids=frozenset({"backup-repository-cn"}),
    )


def test_authenticated_repository_manifest_rebuild_is_idempotent_and_redacted() -> None:
    manifest_bytes, signature_bytes, public_key = _golden_artifacts()
    authenticated = verify_signed_manifest(
        manifest_bytes,
        signature_bytes,
        public_key=public_key,
    )
    repository = InMemoryBackupCatalogRepository()
    service = _service(repository, authenticated.manifest.signature.public_key_sha256)
    audit = BackupCatalogAuditContext(
        actor_id="backup-catalog-controller",
        request_id="catalog-rebuild-001",
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
    assert first.current_status == "INTEGRITY_VERIFIED"
    assert len(first.status_facts) == 1
    assert first.entry.manifest_uri == (
        "backup-repository://backup-repository-cn/backup-20260828-001/manifest.json"
    )
    serialized = json.dumps(first.model_dump(mode="json"), sort_keys=True)
    assert "isolated-backups" not in serialized
    assert "kms://" not in serialized
    assert len(repository.platform_audit_events) == 1
    assert repository.platform_audit_events[0].safe_details == {
        "format_version": "hc-platform-backup/v1",
        "repository_id": "backup-repository-cn",
        "source_environment_id": "production-cn-east",
        "status": "INTEGRITY_VERIFIED",
    }


def test_catalog_rebuild_rejects_unpinned_signer_and_repository() -> None:
    manifest_bytes, signature_bytes, public_key = _golden_artifacts()
    authenticated = verify_signed_manifest(
        manifest_bytes,
        signature_bytes,
        public_key=public_key,
    )
    audit = BackupCatalogAuditContext(actor_id="catalog", request_id="rebuild-002")

    untrusted_signer = BackupCatalogService(
        InMemoryBackupCatalogRepository(),
        trusted_signing_key_sha256=frozenset({"0" * 64}),
        trusted_repository_ids=frozenset({"backup-repository-cn"}),
    )
    with pytest.raises(BackupCatalogError) as signer_error:
        untrusted_signer.rebuild_from_repository_artifacts(
            manifest_bytes,
            signature_bytes,
            public_key=public_key,
            audit=audit,
        )
    assert signer_error.value.code == "BACKUP_CATALOG_SIGNER_UNTRUSTED"

    untrusted_repository = BackupCatalogService(
        InMemoryBackupCatalogRepository(),
        trusted_signing_key_sha256=frozenset({authenticated.manifest.signature.public_key_sha256}),
        trusted_repository_ids=frozenset({"another-repository"}),
    )
    with pytest.raises(BackupCatalogError) as repository_error:
        untrusted_repository.rebuild_from_repository_artifacts(
            manifest_bytes,
            signature_bytes,
            public_key=public_key,
            audit=audit,
        )
    assert repository_error.value.code == "BACKUP_CATALOG_REPOSITORY_UNTRUSTED"


def test_catalog_adapter_rejects_divergent_immutable_identity() -> None:
    manifest_bytes, signature_bytes, public_key = _golden_artifacts()
    verified = verify_signed_manifest(
        manifest_bytes,
        signature_bytes,
        public_key=public_key,
    )
    repository = InMemoryBackupCatalogRepository()
    fact = verified.manifest.verification_facts[0]
    first_audit = BackupCatalogAuditContext(actor_id="catalog", request_id="rebuild-003")
    repository.import_rebuilt_manifest(
        verified,
        verification_fact=fact,
        audit=first_audit,
    )
    divergent = VerifiedManifestV1(
        manifest=verified.manifest.model_copy(
            update={"source_environment_id": "another-environment"}
        ),
        manifest_sha256=verified.manifest_sha256,
        signature_sha256=verified.signature_sha256,
    )

    with pytest.raises(BackupCatalogError) as captured:
        repository.import_rebuilt_manifest(
            divergent,
            verification_fact=fact,
            audit=BackupCatalogAuditContext(actor_id="catalog", request_id="rebuild-004"),
        )
    assert captured.value.code == "BACKUP_CATALOG_IDENTITY_CONFLICT"


def test_created_catalog_flow_is_append_only_pageable_and_repeat_verifiable() -> None:
    manifest_bytes, signature_bytes, public_key = _golden_artifacts()
    verified = verify_signed_manifest(
        manifest_bytes,
        signature_bytes,
        public_key=public_key,
    )
    repository = InMemoryBackupCatalogRepository(clock=lambda: verified.manifest.completed_at)
    service = _service(repository, verified.manifest.signature.public_key_sha256)
    audit = BackupCatalogAuditContext(actor_id="backup-job", request_id="create-request-001")

    first = service.import_created_manifest(
        verified,
        creation_operation_id="backup-create-001",
        audit=audit,
    )
    second = service.import_created_manifest(
        verified,
        creation_operation_id="backup-create-001",
        audit=audit,
    )

    assert first == second
    assert [fact.status for fact in first.status_facts] == [
        "CREATING",
        "CREATED",
        "INTEGRITY_VERIFIED",
    ]
    assert [fact.source_kind for fact in first.status_facts] == ["CREATE", "CREATE", "VERIFY"]
    page = service.list_page(source_environment_id="production-cn-east", limit=1)
    assert page.count == 1
    assert page.next_cursor is None
    assert page.items[0].model_dump() == {
        "backup_id": "backup-20260828-001",
        "format_version": "hc-platform-backup/v1",
        "mode": "portable",
        "status": "INTEGRITY_VERIFIED",
        "source_environment_id": "production-cn-east",
        "repository_id": "backup-repository-cn",
        "release_manifest_sha256": verified.manifest.release.release_manifest_sha256,
        "backup_created_at": verified.manifest.created_at,
        "backup_completed_at": verified.manifest.completed_at,
        "status_occurred_at": verified.manifest.verification_facts[0].verified_at,
    }
    assert "manifest_uri" not in page.model_dump_json()
    assert "signature_sha256" not in page.model_dump_json()

    verified_again = service.append_integrity_verification(
        verified.manifest.backup_id,
        operation_id="integrity-verification-002",
        evidence_uri=(
            "backup-repository://backup-repository-cn/backup-20260828-001/"
            "verification/integrity-verification-002.json"
        ),
        evidence_sha256="d" * 64,
        verifier_identity="backup-verifier",
        verified_at=verified.manifest.completed_at + timedelta(minutes=1),
        audit=BackupCatalogAuditContext(
            actor_id="backup-verifier",
            request_id="verify-request-002",
        ),
    )
    assert verified_again.current_status == "INTEGRITY_VERIFIED"
    assert len(verified_again.status_facts) == 4
    assert verified_again.status_facts[-1].source_kind == "VERIFY"
    assert [event.action for event in repository.platform_audit_events] == [
        "platform.backup.catalog.created",
        "platform.backup.catalog.verified",
    ]


def test_catalog_query_rejects_malformed_cursor_without_changing_state() -> None:
    manifest_bytes, signature_bytes, public_key = _golden_artifacts()
    verified = verify_signed_manifest(
        manifest_bytes,
        signature_bytes,
        public_key=public_key,
    )
    repository = InMemoryBackupCatalogRepository()
    service = _service(repository, verified.manifest.signature.public_key_sha256)
    service.import_created_manifest(
        verified,
        creation_operation_id="backup-create-002",
        audit=BackupCatalogAuditContext(actor_id="backup-job", request_id="create-request-002"),
    )

    with pytest.raises(BackupCatalogError) as captured:
        service.list_page(
            source_environment_id="production-cn-east",
            cursor="not+a+base64url",
        )

    assert captured.value.code == "BACKUP_CATALOG_CURSOR_INVALID"
    assert repository.get(verified.manifest.backup_id) is not None


def test_catalog_migration_freezes_append_only_and_terminal_state_contract() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "backup_catalog_entries_immutable" in sql
    assert "backup_catalog_status_facts_immutable" in sql
    assert "PLATFORM_BACKUP_CATALOG_TRANSITION_INVALID" in sql
    assert "CREATE OR REPLACE VIEW platform.backup_catalog_current" in sql
    assert "RESTORE_VERIFIED', 'CORRUPT', 'EXPIRED" in sql
    repeat_verification = (
        ROOT / "backend/migrations/platform/004_backup_catalog_verification_and_query.sql"
    ).read_text(encoding="utf-8")
    assert "NEW.source_kind = 'VERIFY'" in repeat_verification
    assert "backup_catalog_environment_completed_keyset_idx" in repeat_verification
