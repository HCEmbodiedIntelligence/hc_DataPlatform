"""CLI roundtrip: real file repository/signatures/catalog and disposable restore targets.

Payloads are synthetic. This validates shared command composition, not production
Vault/KMS infrastructure or restoring a real platform.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import boto3
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from hc_data_platform.backup.catalog import (
    BackupCatalogAuditContext,
    BackupCatalogService,
    PostgresBackupCatalogRepository,
)
from hc_data_platform.backup.composition import BackupInfrastructure
from hc_data_platform.backup.contracts import BackupContractError, canonical_json_bytes
from hc_data_platform.backup.whole import (
    assemble_whole_backup,
    prepare_postgresql_artifact,
    publish_whole_backup,
)
from hc_data_platform.backup.whole_cli import run
from tests.backup.test_backup_whole import _evidence, _fixtures
from tests.backup.test_restore_plan import _request


def _private_json(path, model):
    path.write_bytes(canonical_json_bytes(model.model_dump(mode="json")))
    path.chmod(0o600)


@pytest.mark.integration
def test_commands_share_exact_signed_backup_with_real_catalog_and_restore_preflight(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    dsn = os.environ.get("HC_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("disposable PostgreSQL/MinIO services required")
    staging = tmp_path / "staging"
    repository_root = tmp_path / "hc-migration-unit001-repository"
    verification = tmp_path / "verification"
    for directory in [staging, repository_root, verification]:
        directory.mkdir(mode=0o700)
    plan, postgres, objects, configuration, temporal, _ = _fixtures(staging)
    key = tmp_path / "hc-migration-unit001-signing.key"
    key.write_bytes(bytes(range(32)))
    key.chmod(0o600)
    settings = {
        "HC_BACKUP_RUNTIME_PROFILE": "compose_functional",
        "HC_BACKUP_FUNCTIONAL_RUN_ID": "unit001",
        "HC_BACKUP_FUNCTIONAL_SIGNING_KEY_FILE": str(key),
        "HC_BACKUP_FUNCTIONAL_REPOSITORY_DIRECTORY": str(repository_root),
        "HC_BACKUP_CATALOG_DSN": dsn,
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    infrastructure = BackupInfrastructure(settings)
    signer, repository = infrastructure.signer(plan), infrastructure.repository(plan)
    prepared = prepare_postgresql_artifact(plan, postgres, staging_directory=staging)
    assembly = assemble_whole_backup(
        plan,
        postgresql_receipt=postgres,
        postgresql_upload=prepared,
        object_receipt=objects,
        configuration_receipt=configuration,
        temporal_receipt=temporal,
        verification=_evidence(plan),
        signer=signer,
        staging_directory=staging,
    )
    catalog = BackupCatalogService(
        PostgresBackupCatalogRepository.from_dsn(dsn),
        trusted_signing_key_sha256=frozenset({assembly.manifest.signature.public_key_sha256}),
        trusted_repository_ids=frozenset({plan.backup_repository.repository_id}),
    )
    publish_whole_backup(
        assembly,
        repository=repository,
        catalog=catalog,
        audit=BackupCatalogAuditContext(
            actor_id=plan.verifier_identity, request_id=plan.operation_id
        ),
        repository_checkpoint_path=staging / "repository.checkpoint.json",
        verification_staging_directory=verification,
    )
    plan_path = tmp_path / "plan.json"
    _private_json(plan_path, plan)
    assert (
        run(
            [
                "backup",
                "verify",
                "--plan",
                str(plan_path),
                "--operation-id",
                "composition-verify",
                "--verified-at",
                "2026-10-08T00:00:00Z",
                "--staging-directory",
                str(staging),
            ]
        )
        == 0
    )
    verified = json.loads(capsys.readouterr().out)
    assert verified["backup_status"] == "INTEGRITY_VERIFIED"
    assert verified["backup_id"] == plan.backup_id

    target_name = "composition_" + uuid4().hex[:12]
    connection = conninfo_to_dict(dsn)
    with psycopg.connect(dsn, autocommit=True) as db:
        db.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_name)))
        major = int(db.execute("SHOW server_version_num").fetchone()[0]) // 10000
    # Target policy must match the source; use the existing immutable source major.
    assert major == assembly.manifest.postgresql.major_version
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["HC_MINIO_ENDPOINT"],
        aws_access_key_id=os.environ["HC_MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["HC_MINIO_SECRET_KEY"],
    )
    bucket = target_name.replace("_", "-")
    client.create_bucket(Bucket=bucket)
    client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    request = _request(assembly.manifest)
    now = datetime.now(timezone.utc)
    request = request.model_copy(
        update={
            "requested_at": now,
            "target_postgresql_database": target_name,
            "readiness": request.readiness.model_copy(
                update={
                    "observed_at": now - timedelta(minutes=1),
                    "valid_until": now + timedelta(hours=1),
                }
            ),
        }
    )
    target = tmp_path / "target.json"
    _private_json(target, request)
    restore_settings = {
        "HC_RESTORE_POSTGRES_DATABASE": target_name,
        "HC_RESTORE_POSTGRES_HOST": connection["host"],
        "HC_RESTORE_POSTGRES_PORT": connection.get("port", "5432"),
        "HC_RESTORE_POSTGRES_USERNAME": connection["user"],
        "HC_RESTORE_POSTGRES_PASSWORD": connection["password"],
        "HC_RESTORE_POSTGRES_SSLMODE": "disable",
        "HC_RESTORE_OBJECT_STORE_ENDPOINT": os.environ["HC_MINIO_ENDPOINT"],
        "HC_RESTORE_OBJECT_STORE_ACCESS_KEY": os.environ["HC_MINIO_ACCESS_KEY"],
        "HC_RESTORE_OBJECT_STORE_SECRET_KEY": os.environ["HC_MINIO_SECRET_KEY"],
        "HC_RESTORE_OBJECT_STORE_BUCKET": bucket,
        "HC_RESTORE_OBJECT_STORE_BUCKET_REFERENCE": request.target_object_store_bucket_reference,
        "HC_RESTORE_OBJECT_STORE_PREFIX": request.target_object_store_prefix,
    }
    for name, value in restore_settings.items():
        monkeypatch.setenv(name, value)
    argv = [
        "restore",
        "plan",
        plan.backup_id,
        "--target",
        request.target.target_environment_id,
        "--backup-plan",
        str(plan_path),
        "--target-document",
        str(target),
        "--staging-directory",
        str(staging),
        "--dry-run",
    ]
    assert run(argv) == 0
    restored = json.loads(capsys.readouterr().out)
    assert restored["status"] == "PREFLIGHT_PASSED"
    assert restored["payloads_downloaded"] is False
    assert restored["writes_enabled"] is False
    assert restored["manifest_sha256"] == assembly.signed_manifest.manifest_sha256
    # Repeated planning preserves the frozen checkpoint; changing artifact bytes
    # must fail in the same public command before inspection or restore writes.
    assert run(argv) == 0
    assert json.loads(capsys.readouterr().out)["checkpoint_sha256"] == restored["checkpoint_sha256"]
    candidates = [
        p
        for p in repository_root.rglob("*")
        if p.is_file() and p.read_bytes() == assembly.signed_manifest.manifest_bytes
    ]
    assert len(candidates) == 1
    tampered = json.loads(candidates[0].read_bytes())
    tampered["backup_id"] = "tampered-backup-001"
    candidates[0].write_bytes(canonical_json_bytes(tampered))
    with pytest.raises(BackupContractError) as rejected:
        run(argv)
    assert rejected.value.code == "BACKUP_SIGNATURE_MANIFEST_HASH_MISMATCH"
