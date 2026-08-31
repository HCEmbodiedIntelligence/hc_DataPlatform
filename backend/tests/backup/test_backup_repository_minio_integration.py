from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from hc_data_platform.backup.catalog import (
    BackupCatalogAuditContext,
    BackupCatalogService,
    PostgresBackupCatalogRepository,
)
from hc_data_platform.backup.contracts import (
    BackupContractError,
    BackupManifestV1,
    SignedManifestV1,
    sign_backup_manifest,
)
from hc_data_platform.backup.repository import S3BackupManifestRepository
from hc_data_platform.core.dbapi import normalize_postgres_dsn

ROOT = Path(__file__).resolve().parents[3]
GOLDEN_MANIFEST = ROOT / "backend/tests/backup/fixtures/hc-platform-backup-v1.golden.json"


class _IsolatedSigningAdapter:
    def __init__(self, private_key: Ed25519PrivateKey, key_reference: str) -> None:
        self._private_key = private_key
        self.key_reference = key_reference
        self.public_key = private_key.public_key()

    def sign(self, message: bytes) -> bytes:
        return self._private_key.sign(message)


def _required_environment() -> tuple[str, str, str, str, str]:
    values = (
        os.environ.get("HC_BACKUP_REPOSITORY_TEST_ENDPOINT"),
        os.environ.get("HC_BACKUP_REPOSITORY_TEST_BUCKET"),
        os.environ.get("HC_BACKUP_REPOSITORY_TEST_ACCESS_KEY"),
        os.environ.get("HC_BACKUP_REPOSITORY_TEST_SECRET_KEY"),
        os.environ.get("HC_TEST_POSTGRES_DSN"),
    )
    if not all(values):
        pytest.skip("backup repository MinIO and PostgreSQL variables are required")
    return cast(tuple[str, str, str, str, str], tuple(str(value) for value in values))


def _signed_manifest(
    backup_id: str,
    operation_id: str,
) -> tuple[SignedManifestV1, Ed25519PublicKey, BackupManifestV1]:
    fixture = json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    document = deepcopy(fixture["manifest"])
    document["backup_id"] = backup_id
    document["verification_facts"][0]["operation_id"] = operation_id
    manifest = BackupManifestV1.model_validate(document)
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    signer = _IsolatedSigningAdapter(private_key, manifest.signature.key_reference)
    return sign_backup_manifest(manifest, signer=signer), signer.public_key, manifest


@pytest.mark.integration
def test_minio_locked_versioned_repository_rebuilds_postgres_catalog_and_rejects_tamper() -> None:
    endpoint, bucket, access_key, secret_key, raw_dsn = _required_environment()
    boto3 = pytest.importorskip("boto3")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
    )
    retain_until = datetime.now(timezone.utc) + timedelta(days=2)
    valid_id = "backup-minio-catalog-001"
    valid_operation = "integrity-minio-catalog-001"
    signed, public_key, manifest = _signed_manifest(valid_id, valid_operation)
    prefix = "bak201-catalog-proof"
    for name, payload in (
        ("manifest.json", signed.manifest_bytes),
        ("signature.sig", signed.signature_bytes),
    ):
        client.put_object(
            Bucket=bucket,
            Key=f"{prefix}/{valid_id}/{name}",
            Body=payload,
            ContentType="application/json",
            ObjectLockMode="COMPLIANCE",
            ObjectLockRetainUntilDate=retain_until,
        )

    repository = S3BackupManifestRepository(
        client,
        bucket=bucket,
        repository_id=manifest.backup_repository.repository_id,
        prefix=prefix,
    )
    dsn = normalize_postgres_dsn(raw_dsn)
    catalog_repository = PostgresBackupCatalogRepository.from_dsn(dsn)
    service = BackupCatalogService(
        catalog_repository,
        trusted_signing_key_sha256=frozenset({manifest.signature.public_key_sha256}),
        trusted_repository_ids=frozenset({manifest.backup_repository.repository_id}),
    )
    audit = BackupCatalogAuditContext(
        actor_id="backup-catalog-minio-controller",
        request_id="minio-catalog-rebuild-001",
    )

    first = service.rebuild_repository(
        repository,
        public_keys_by_sha256={manifest.signature.public_key_sha256: public_key},
        audit=audit,
    )
    second = service.rebuild_repository(
        repository,
        public_keys_by_sha256={manifest.signature.public_key_sha256: public_key},
        audit=audit,
    )
    assert first == second
    assert first[0].entry.backup_id == valid_id
    assert first[0].current_status == "INTEGRITY_VERIFIED"
    listed = repository.list_signed_manifests()
    assert listed[0].manifest_version_id != "null"
    assert listed[0].signature_version_id != "null"

    tampered_id = "backup-minio-catalog-tampered"
    tampered_operation = "integrity-minio-catalog-tampered"
    tampered_signed, _tampered_key, tampered_manifest = _signed_manifest(
        tampered_id,
        tampered_operation,
    )
    tampered_document = json.loads(tampered_signed.manifest_bytes)
    tampered_document["source_environment_id"] = "attacker-environment"
    tampered_prefix = "bak201-catalog-tamper-proof"
    for name, payload in (
        (
            "manifest.json",
            json.dumps(tampered_document, separators=(",", ":"), sort_keys=True).encode(),
        ),
        ("signature.sig", tampered_signed.signature_bytes),
    ):
        client.put_object(
            Bucket=bucket,
            Key=f"{tampered_prefix}/{tampered_id}/{name}",
            Body=payload,
            ContentType="application/json",
            ObjectLockMode="COMPLIANCE",
            ObjectLockRetainUntilDate=retain_until,
        )
    tampered_repository = S3BackupManifestRepository(
        client,
        bucket=bucket,
        repository_id=tampered_manifest.backup_repository.repository_id,
        prefix=tampered_prefix,
    )
    with pytest.raises(BackupContractError) as captured:
        service.rebuild_repository(
            tampered_repository,
            public_keys_by_sha256={tampered_manifest.signature.public_key_sha256: public_key},
            audit=BackupCatalogAuditContext(
                actor_id="backup-catalog-minio-controller",
                request_id="minio-catalog-rebuild-002",
            ),
        )
    assert captured.value.code == "BACKUP_SIGNATURE_MANIFEST_HASH_MISMATCH"

    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM platform.backup_catalog_entries WHERE backup_id = %s",
            (tampered_id,),
        ).fetchone() == (0,)
