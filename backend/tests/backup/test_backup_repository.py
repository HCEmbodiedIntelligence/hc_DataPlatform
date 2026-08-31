from __future__ import annotations

import base64
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hc_data_platform.backup.catalog import (
    BackupCatalogAuditContext,
    BackupCatalogError,
    BackupCatalogService,
    InMemoryBackupCatalogRepository,
)
from hc_data_platform.backup.contracts import canonical_json_bytes, verify_signed_manifest
from hc_data_platform.backup.repository import (
    BackupRepositoryError,
    S3BackupManifestRepository,
)

ROOT = Path(__file__).resolve().parents[3]
GOLDEN_MANIFEST = ROOT / "backend/tests/backup/fixtures/hc-platform-backup-v1.golden.json"
NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)
RETAIN_UNTIL = datetime(2027, 8, 28, tzinfo=timezone.utc)


class _S3Fixture:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.versioning_status = "Enabled"
        self.lock_mode = "COMPLIANCE"
        self.retain_until = RETAIN_UNTIL

    def get_bucket_versioning(self, **_: object) -> dict[str, object]:
        return {"Status": self.versioning_status}

    def list_objects_v2(self, **_: object) -> dict[str, object]:
        return {
            "Contents": [{"Key": key} for key in sorted(self.objects)],
            "IsTruncated": False,
        }

    def get_object(self, **kwargs: object) -> dict[str, Any]:
        key = kwargs["Key"]
        assert isinstance(key, str)
        body = self.objects[key]
        return {
            "Body": io.BytesIO(body),
            "ContentLength": len(body),
            "VersionId": f"version-{key.rsplit('/', 1)[-1]}",
            "ObjectLockMode": self.lock_mode,
            "ObjectLockRetainUntilDate": self.retain_until,
        }


def _golden() -> tuple[bytes, bytes, Ed25519PublicKey, str]:
    fixture = json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    public_key = Ed25519PublicKey.from_public_bytes(
        base64.urlsafe_b64decode(fixture["public_key_base64url"] + "==")
    )
    manifest_bytes = canonical_json_bytes(fixture["manifest"])
    signature_bytes = canonical_json_bytes(fixture["signature"])
    authenticated = verify_signed_manifest(
        manifest_bytes,
        signature_bytes,
        public_key=public_key,
    )
    return (
        manifest_bytes,
        signature_bytes,
        public_key,
        authenticated.manifest.signature.public_key_sha256,
    )


def _objects(backup_id: str = "backup-20260828-001") -> dict[str, bytes]:
    manifest_bytes, signature_bytes, _public_key, _fingerprint = _golden()
    prefix = f"whole-platform-backups/{backup_id}"
    return {
        f"{prefix}/manifest.json": manifest_bytes,
        f"{prefix}/signature.sig": signature_bytes,
        f"{prefix}/db/platform.dump": b"ignored-by-catalog-rebuild",
    }


def test_s3_repository_rebuilds_empty_catalog_from_locked_versioned_artifacts() -> None:
    _manifest_bytes, _signature_bytes, public_key, fingerprint = _golden()
    s3 = _S3Fixture(_objects())
    manifest_repository = S3BackupManifestRepository(
        s3,
        bucket="physical-bucket-is-adapter-private",
        repository_id="backup-repository-cn",
        clock=lambda: NOW,
    )
    catalog = InMemoryBackupCatalogRepository()
    service = BackupCatalogService(
        catalog,
        trusted_signing_key_sha256=frozenset({fingerprint}),
        trusted_repository_ids=frozenset({"backup-repository-cn"}),
    )

    records = service.rebuild_repository(
        manifest_repository,
        public_keys_by_sha256={fingerprint: public_key},
        audit=BackupCatalogAuditContext(actor_id="catalog", request_id="s3-rebuild-001"),
    )

    assert len(records) == 1
    assert records[0].current_status == "INTEGRITY_VERIFIED"
    assert catalog.get("backup-20260828-001") == records[0]
    assert "physical-bucket" not in records[0].model_dump_json()


@pytest.mark.parametrize(
    ("versioning", "lock_mode", "retain_until", "expected_code"),
    [
        ("Suspended", "COMPLIANCE", RETAIN_UNTIL, "BACKUP_REPOSITORY_VERSIONING_REQUIRED"),
        ("Enabled", "GOVERNANCE", RETAIN_UNTIL, "BACKUP_REPOSITORY_OBJECT_POLICY_INVALID"),
        ("Enabled", "COMPLIANCE", NOW, "BACKUP_REPOSITORY_OBJECT_POLICY_INVALID"),
    ],
)
def test_s3_repository_fails_closed_on_version_or_object_lock_drift(
    versioning: str,
    lock_mode: str,
    retain_until: datetime,
    expected_code: str,
) -> None:
    s3 = _S3Fixture(_objects())
    s3.versioning_status = versioning
    s3.lock_mode = lock_mode
    s3.retain_until = retain_until
    repository = S3BackupManifestRepository(
        s3,
        bucket="private",
        repository_id="backup-repository-cn",
        clock=lambda: NOW,
    )

    with pytest.raises(BackupRepositoryError) as captured:
        repository.list_signed_manifests()
    assert captured.value.code == expected_code


def test_s3_repository_rejects_incomplete_pair_and_path_identity_before_catalog_write() -> None:
    manifest_bytes, _signature_bytes, public_key, fingerprint = _golden()
    incomplete = _S3Fixture(
        {"whole-platform-backups/backup-20260828-001/manifest.json": manifest_bytes}
    )
    with pytest.raises(BackupRepositoryError) as incomplete_error:
        S3BackupManifestRepository(
            incomplete,
            bucket="private",
            repository_id="backup-repository-cn",
            clock=lambda: NOW,
        ).list_signed_manifests()
    assert incomplete_error.value.code == "BACKUP_REPOSITORY_ARTIFACT_PAIR_INCOMPLETE"

    mismatched = S3BackupManifestRepository(
        _S3Fixture(_objects("different-backup-id")),
        bucket="private",
        repository_id="backup-repository-cn",
        clock=lambda: NOW,
    )
    catalog = InMemoryBackupCatalogRepository()
    service = BackupCatalogService(
        catalog,
        trusted_signing_key_sha256=frozenset({fingerprint}),
        trusted_repository_ids=frozenset({"backup-repository-cn"}),
    )
    with pytest.raises(BackupCatalogError) as path_error:
        service.rebuild_repository(
            mismatched,
            public_keys_by_sha256={fingerprint: public_key},
            audit=BackupCatalogAuditContext(actor_id="catalog", request_id="s3-rebuild-002"),
        )
    assert path_error.value.code == "BACKUP_CATALOG_REPOSITORY_PATH_MISMATCH"
    assert catalog.get("backup-20260828-001") is None
