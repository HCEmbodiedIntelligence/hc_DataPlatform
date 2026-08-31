from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest
from cryptography.hazmat.primitives import serialization

from hc_data_platform.backup.catalog import (
    BackupCatalogAuditContext,
    BackupCatalogService,
    PostgresBackupCatalogRepository,
)
from hc_data_platform.backup.contracts import (
    BackupContractError,
    BackupManifestV1,
    BackupRepositoryV1,
    canonical_json_bytes,
)
from hc_data_platform.backup.dependencies import (
    ConfigurationBackupArtifact,
    PublicConfigArtifact,
    SecretsEnvelopeArtifact,
)
from hc_data_platform.backup.objects import ObjectBackupArtifact, ObjectInventoryArtifact
from hc_data_platform.backup.postgresql import (
    DatabaseVerificationEvidence,
    LogicalDumpArtifact,
    PostgresWalAnchor,
)
from hc_data_platform.backup.repository import LockedRepositoryConfig, S3LockedBackupRepository
from hc_data_platform.backup.signing import (
    VaultTransitEd25519Signer,
    VaultTransitSignerConfig,
)
from hc_data_platform.backup.temporal import TemporalPolicyArtifact
from hc_data_platform.backup.whole import (
    VerificationDomain,
    WholeBackupPlanV1,
    WholeVerificationEvidence,
    assemble_whole_backup,
    policy_verification_evidence,
    prepare_postgresql_artifact,
    publish_whole_backup,
    verify_published_whole_backup,
)
from hc_data_platform.backup.whole_cli import run as run_whole_cli
from hc_data_platform.core.dbapi import normalize_postgres_dsn

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "backend/tests/backup/fixtures/hc-platform-backup-v1.golden.json"


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required")
    return value


def _vault_request(
    endpoint: str,
    token: str,
    path: str,
    body: Mapping[str, object],
) -> None:
    request = Request(
        f"{endpoint}{path}",
        data=json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Vault-Token": token,
        },
        method="POST",
    )
    with urlopen(request, timeout=10) as response:  # noqa: S310
        assert response.status in {200, 204}


def _private(path: Path, payload: bytes) -> tuple[Path, str]:
    path.write_bytes(payload)
    path.chmod(0o600)
    return path, hashlib.sha256(payload).hexdigest()


def _domain_artifacts(
    staging: Path,
    *,
    plan: WholeBackupPlanV1,
    golden_manifest: BackupManifestV1,
) -> tuple[
    LogicalDumpArtifact,
    ObjectBackupArtifact,
    ConfigurationBackupArtifact,
    TemporalPolicyArtifact,
]:
    dump_path, dump_sha = _private(staging / "postgresql.dump", b"real whole postgres")
    postgresql = LogicalDumpArtifact(
        path=dump_path,
        size_bytes=dump_path.stat().st_size,
        sha256=dump_sha,
        server_major=16,
        wal_anchor=PostgresWalAnchor(
            system_identifier=206206,
            timeline_id=1,
            wal_lsn="0/2062060",
            server_version="16.10",
            server_major=16,
            database_name="bak206_whole_integration",
            in_recovery=False,
        ),
        source_evidence=DatabaseVerificationEvidence(
            database_size_bytes=16_777_216,
            migration_count=105,
            migration_sha256=plan.release.migration_manifest_sha256,
            schema_object_count=1,
            schema_sha256="1" * 64,
            constraint_count=1,
            constraint_sha256="2" * 64,
            sequence_count=1,
            sequence_sha256="3" * 64,
            tables=(),
            aggregate_sha256="4" * 64,
        ),
    )
    inventory_path, inventory_sha = _private(staging / "inventory.zst", b"real inventory")
    objects = ObjectBackupArtifact(
        inventory=ObjectInventoryArtifact(
            path=inventory_path,
            logical_path="objects/inventory.jsonl.zst",
            mode="snapshot",
            media_type="application/vnd.hc.object-inventory.v1+jsonl+zstd",
            client_side_encryption="repository_kms_only",
            size_bytes=inventory_path.stat().st_size,
            sha256=inventory_sha,
            consistency_coordinate=f"s3-version-set/v1:sha256:{'5' * 64}",
            object_count=0,
            total_bytes=0,
        ),
        resumed_part_count=0,
    )
    public_path, public_sha = _private(staging / "public.yaml", b"apiVersion: v1\n")
    envelope_path, envelope_sha = _private(staging / "envelope.json", b'{"opaque":true}')
    dependencies = tuple(
        sorted(
            golden_manifest.secrets_and_kms.dependencies,
            key=lambda item: item.environment_variable,
        )
    )
    configuration = ConfigurationBackupArtifact(
        public_config=PublicConfigArtifact(
            path=public_path,
            size_bytes=public_path.stat().st_size,
            sha256=public_sha,
            helm_render_sha256="6" * 64,
        ),
        secrets_envelope=SecretsEnvelopeArtifact(
            path=envelope_path,
            logical_path="secrets/envelope.json",
            mode="snapshot",
            media_type="application/vnd.hc.secret-dependencies.v1+json",
            client_side_encryption="repository_kms_only",
            size_bytes=envelope_path.stat().st_size,
            sha256=envelope_sha,
            dependency_count=len(dependencies),
            provider_reference="bak206-vault",
            hmac_key_reference=golden_manifest.secrets_and_kms.signing_key_reference,
        ),
        manifest_dependencies=dependencies,
        preflight_coordinate=f"secret-dependency-set/v1:sha256:{'7' * 64}",
    )
    temporal_path, temporal_sha = _private(staging / "temporal.json", b'{"policy":true}')
    temporal = TemporalPolicyArtifact(
        path=temporal_path,
        mode="snapshot",
        media_type="application/vnd.hc.temporal-policy.v1+json",
        client_side_encryption="repository_kms_only",
        size_bytes=temporal_path.stat().st_size,
        sha256=temporal_sha,
        schedule_count=0,
        open_workflow_count=0,
        schedule_inventory_sha256="a" * 64,
        open_workflow_inventory_sha256="b" * 64,
        consistency_coordinate=f"temporal-policy/v1:sha256:{'c' * 64}",
    )
    return postgresql, objects, configuration, temporal


def _evidence(plan: WholeBackupPlanV1) -> tuple[WholeVerificationEvidence, ...]:
    policy_hashes = {
        "backup_repository": hashlib.sha256(
            canonical_json_bytes(plan.backup_repository.model_dump(mode="json"))
        ).hexdigest(),
        "release_artifacts": hashlib.sha256(
            canonical_json_bytes(plan.release.model_dump(mode="json"))
        ).hexdigest(),
        "runtime_logs": plan.external_evidence.runtime_logs_evidence_sha256,
        "external_dependencies": (plan.external_evidence.external_dependencies_evidence_sha256),
    }
    domains: tuple[VerificationDomain, ...] = (
        "backup_repository",
        "deployment_configuration",
        "external_dependencies",
        "object_store",
        "postgresql_business",
        "release_artifacts",
        "runtime_logs",
        "secrets_and_kms",
        "temporal_workflows",
    )
    return tuple(
        policy_verification_evidence(
            domain,
            policy={"domain": domain, "backup_id": plan.backup_id},
            evidence_sha256=policy_hashes.get(domain, hashlib.sha256(domain.encode()).hexdigest()),
            verifier_identity=plan.verifier_identity,
            verified_at=plan.completed_at,
        )
        for domain in domains
    )


@pytest.mark.integration
def test_real_whole_backup_vault_kms_repository_catalog_and_tamper_fence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _required("HC_BACKUP_WHOLE_INTEGRATION_ALLOW_MUTATION") == "disposable-only"
    endpoint = _required("HC_BACKUP_WHOLE_INTEGRATION_ENDPOINT")
    replica_endpoint = _required("HC_BACKUP_WHOLE_INTEGRATION_REPLICA_ENDPOINT")
    bucket = _required("HC_BACKUP_WHOLE_INTEGRATION_BUCKET")
    access_key = _required("HC_BACKUP_WHOLE_INTEGRATION_ACCESS_KEY")
    secret_key = _required("HC_BACKUP_WHOLE_INTEGRATION_SECRET_KEY")
    kms_key_id = _required("HC_BACKUP_WHOLE_INTEGRATION_KMS_KEY_ID")
    observed_kms_key_id = _required("HC_BACKUP_WHOLE_INTEGRATION_KMS_OBSERVED_KEY_ID")
    replica_destination = _required("HC_BACKUP_WHOLE_INTEGRATION_REPLICA_DESTINATION")
    vault_endpoint = _required("HC_BACKUP_WHOLE_INTEGRATION_VAULT_ENDPOINT")
    vault_token = _required("HC_BACKUP_WHOLE_INTEGRATION_VAULT_TOKEN")
    catalog_dsn = normalize_postgres_dsn(_required("HC_BACKUP_WHOLE_INTEGRATION_DSN"))
    key_name = f"bak206-whole-ed25519-{int(time.time())}"
    _vault_request(
        vault_endpoint,
        vault_token,
        f"/v1/transit/keys/{quote(key_name, safe='')}",
        {
            "type": "ed25519",
            "derived": False,
            "exportable": False,
            "allow_plaintext_backup": False,
        },
    )
    key_reference = f"kms://vault/bak206/transit/{key_name}/versions/v1"
    signer = VaultTransitEd25519Signer(
        VaultTransitSignerConfig(
            endpoint_url=vault_endpoint,
            token=vault_token,
            provider_reference="bak206",
            key_name=key_name,
            key_version=1,
            key_reference=key_reference,
        )
    )
    golden = json.loads(GOLDEN.read_bytes())
    golden_manifest = BackupManifestV1.model_validate(golden["manifest"])
    migration_sha = hashlib.sha256(
        (ROOT / "backend/migrations/manifest.txt").read_bytes()
    ).hexdigest()
    release = golden_manifest.release.model_copy(
        update={
            "migration_manifest_sha256": migration_sha,
            "release_manifest_sha256": "d" * 64,
        }
    )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    retain_until = now + timedelta(days=2)
    suffix = str(int(time.time()))
    backup_id = f"bak206-whole-{suffix}"
    operation_id = f"bak206-create-{suffix}"
    logical_repository = BackupRepositoryV1(
        repository_id="bak206-whole-repository",
        provider="s3_compatible",
        bucket_reference="bak206-logical-bucket",
        kms_key_reference="kms://bak206/repository/versions/v1",
        retention_until=retain_until,
        cross_site_replica_reference="bak206-cross-site-replica",
    )
    plan = WholeBackupPlanV1(
        operation_id=operation_id,
        backup_id=backup_id,
        mode="snapshot",
        created_at=now,
        completed_at=now + timedelta(seconds=1),
        source_platform_id="bak206-platform",
        source_environment_id=f"bak206-environment-{suffix}",
        verifier_identity="bak206-whole-controller",
        release=release,
        backup_tool=golden_manifest.backup_tool,
        postgresql={
            "physical_recovery": "provider_managed_pitr",
            "pitr_coordinate": f"provider-pitr://{backup_id}",
        },
        object_store={
            "provider": "s3_compatible",
            "bucket_reference": "bak206-business-objects",
            "prefix": "datasets/",
        },
        temporal={
            "cluster_reference": "temporal-cluster://bak206",
            "namespace": "bak206",
            "service_version": "1.25.2",
        },
        deployment_configuration={"gitops_repository_reference": "gitops://bak206/release"},
        secrets_and_kms={
            "repository_key_reference": logical_repository.kms_key_reference,
            "signing_key_reference": key_reference,
            "portable_recipient_key_references": (),
        },
        backup_repository=logical_repository,
        external_evidence={
            "runtime_logs_evidence_sha256": "8" * 64,
            "external_dependencies_evidence_sha256": "9" * 64,
        },
    )
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    postgresql, objects, configuration, temporal = _domain_artifacts(
        staging,
        plan=plan,
        golden_manifest=golden_manifest,
    )
    prepared = prepare_postgresql_artifact(plan, postgresql, staging_directory=staging)
    assembly = assemble_whole_backup(
        plan,
        postgresql_receipt=postgresql,
        postgresql_upload=prepared,
        object_receipt=objects,
        configuration_receipt=configuration,
        temporal_receipt=temporal,
        verification=_evidence(plan),
        signer=signer,
        staging_directory=staging,
    )
    boto3 = pytest.importorskip("boto3")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
    )
    replica = boto3.client(
        "s3",
        endpoint_url=replica_endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
    )
    prefix = "bak206-whole-e2e"
    repository = S3LockedBackupRepository(
        client,
        LockedRepositoryConfig(
            repository_id=logical_repository.repository_id,
            provider="s3_compatible",
            bucket=bucket,
            bucket_reference=logical_repository.bucket_reference,
            prefix=prefix,
            kms_key_reference=logical_repository.kms_key_reference,
            provider_kms_key_id=kms_key_id,
            provider_kms_observed_key_id=observed_kms_key_id,
            provider_replica_destination=replica_destination,
            retention_until=retain_until,
            cross_site_replica_reference=logical_repository.cross_site_replica_reference,
            send_small_object_checksum=False,
        ),
    )
    public_bytes = signer.public_key.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    signer_sha = hashlib.sha256(public_bytes).hexdigest()
    catalog = BackupCatalogService(
        PostgresBackupCatalogRepository.from_dsn(catalog_dsn),
        trusted_signing_key_sha256=frozenset({signer_sha}),
        trusted_repository_ids=frozenset({logical_repository.repository_id}),
    )
    audit = BackupCatalogAuditContext(
        actor_id=plan.verifier_identity,
        request_id=plan.operation_id,
    )
    first_verify_staging = tmp_path / "verify-first"
    first_verify_staging.mkdir(mode=0o700)
    first = publish_whole_backup(
        assembly,
        repository=repository,
        catalog=catalog,
        audit=audit,
        repository_checkpoint_path=staging / "repository-checkpoint.json",
        verification_staging_directory=first_verify_staging,
    )
    second_verify_staging = tmp_path / "verify-second"
    second_verify_staging.mkdir(mode=0o700)
    second = publish_whole_backup(
        assembly,
        repository=repository,
        catalog=catalog,
        audit=audit,
        repository_checkpoint_path=staging / "repository-checkpoint.json",
        verification_staging_directory=second_verify_staging,
    )
    assert first.catalog_record == second.catalog_record
    assert all(item.resumed for item in second.stored_objects)
    assert first.catalog_record.current_status == "INTEGRITY_VERIFIED"
    assert [fact.status for fact in first.catalog_record.status_facts] == [
        "CREATING",
        "CREATED",
        "INTEGRITY_VERIFIED",
    ]

    reverify_staging = tmp_path / "reverify"
    reverify_staging.mkdir(mode=0o700)
    reverified = verify_published_whole_backup(
        plan,
        operation_id=f"bak206-reverify-{suffix}",
        verifier_identity="bak206-independent-verifier",
        public_key=signer.public_key,
        repository=repository,
        catalog=catalog,
        audit=BackupCatalogAuditContext(
            actor_id="bak206-independent-verifier",
            request_id=f"bak206-reverify-{suffix}",
        ),
        verification_staging_directory=reverify_staging,
        verified_at=plan.completed_at + timedelta(seconds=1),
    )
    assert reverified.current_status == "INTEGRITY_VERIFIED"
    assert all(fact.status != "RESTORE_VERIFIED" for fact in reverified.status_facts)
    plan_path = tmp_path / "whole-backup-plan.json"
    plan_path.write_bytes(canonical_json_bytes(plan.model_dump(mode="json")))
    plan_path.chmod(0o600)
    cli_environment = {
        "AWS_ACCESS_KEY_ID": access_key,
        "AWS_SECRET_ACCESS_KEY": secret_key,
        "HC_BACKUP_REPOSITORY_BUCKET": bucket,
        "HC_BACKUP_REPOSITORY_PREFIX": prefix,
        "HC_BACKUP_REPOSITORY_KMS_KEY_ID": kms_key_id,
        "HC_BACKUP_REPOSITORY_KMS_OBSERVED_KEY_ID": observed_kms_key_id,
        "HC_BACKUP_REPOSITORY_REPLICA_DESTINATION": replica_destination,
        "HC_BACKUP_REPOSITORY_ENDPOINT": endpoint,
        "HC_BACKUP_REPOSITORY_REGION": "us-east-1",
        "HC_BACKUP_REPOSITORY_ADDRESSING_STYLE": "path",
        "HC_BACKUP_REPOSITORY_SEND_SMALL_OBJECT_CHECKSUM": "false",
        "HC_BACKUP_SIGNER_VAULT_ENDPOINT": vault_endpoint,
        "HC_BACKUP_SIGNER_VAULT_TOKEN": vault_token,
        "HC_BACKUP_SIGNER_PROVIDER_REFERENCE": "bak206",
        "HC_BACKUP_SIGNER_TRANSIT_MOUNT": "transit",
        "HC_BACKUP_SIGNER_KEY_NAME": key_name,
        "HC_BACKUP_SIGNER_KEY_VERSION": "1",
        "HC_BACKUP_CATALOG_DSN": catalog_dsn,
        "HC_BACKUP_SOURCE_ENVIRONMENT_ID": plan.source_environment_id,
        "HC_BACKUP_VERIFIER_IDENTITY": "bak206-cli-verifier",
    }
    for name, value in cli_environment.items():
        monkeypatch.setenv(name, value)
    cli_operation = f"bak206-cli-reverify-{suffix}"
    cli_arguments = [
        "backup",
        "verify",
        "--plan",
        str(plan_path),
        "--operation-id",
        cli_operation,
        "--verified-at",
        (plan.completed_at + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
        "--staging-directory",
        str(staging),
    ]
    assert run_whole_cli(cli_arguments) == 0
    assert run_whole_cli(cli_arguments) == 0
    assert run_whole_cli(["backup", "list", "--limit", "1"]) == 0
    cli_record = catalog.get(backup_id)
    assert cli_record is not None
    assert cli_record.current_status == "INTEGRITY_VERIFIED"
    assert len(cli_record.status_facts) == 5
    assert all(fact.status != "RESTORE_VERIFIED" for fact in cli_record.status_facts)
    page = catalog.list_page(source_environment_id=plan.source_environment_id, limit=10)
    assert page.count == 1
    assert page.items[0].backup_id == backup_id
    serialized_page = page.model_dump_json()
    for private_value in (
        endpoint,
        replica_endpoint,
        access_key,
        secret_key,
        observed_kms_key_id,
        replica_destination,
    ):
        assert private_value not in serialized_page

    replica_key = f"{prefix}/{backup_id}/manifest.json"
    for _ in range(60):
        try:
            replica_head = replica.head_object(Bucket=bucket, Key=replica_key)
            break
        except Exception:
            time.sleep(0.25)
    else:
        raise AssertionError("whole-backup manifest did not reach the cross-site replica")
    assert replica_head["ServerSideEncryption"] == "aws:kms"
    assert replica_head["SSEKMSKeyId"] == observed_kms_key_id

    tampered = b"{}"
    client.put_object(
        Bucket=bucket,
        Key=replica_key,
        Body=tampered,
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=kms_key_id,
        ObjectLockMode="COMPLIANCE",
        ObjectLockRetainUntilDate=retain_until,
        Metadata={"hc-sha256": hashlib.sha256(tampered).hexdigest()},
    )
    tamper_staging = tmp_path / "tamper"
    tamper_staging.mkdir(mode=0o700)
    with pytest.raises(BackupContractError):
        verify_published_whole_backup(
            plan,
            operation_id=f"bak206-tamper-{suffix}",
            verifier_identity="bak206-independent-verifier",
            public_key=signer.public_key,
            repository=repository,
            catalog=catalog,
            audit=BackupCatalogAuditContext(
                actor_id="bak206-independent-verifier",
                request_id=f"bak206-tamper-{suffix}",
            ),
            verification_staging_directory=tamper_staging,
            verified_at=plan.completed_at + timedelta(seconds=2),
        )
    unchanged = catalog.get(backup_id)
    assert unchanged is not None
    assert unchanged.status_facts == cli_record.status_facts
