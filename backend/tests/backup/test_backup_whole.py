from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hc_data_platform.backup.catalog import (
    BackupCatalogAuditContext,
    BackupCatalogService,
    InMemoryBackupCatalogRepository,
)
from hc_data_platform.backup.contracts import (
    BackupManifestV1,
    canonical_json_bytes,
    verify_signed_manifest,
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
from hc_data_platform.backup.repository import (
    FunctionalFileBackupRepository,
    FunctionalFileRepositoryConfig,
    RepositoryStoredObject,
    RepositoryVerifiedBackup,
)
from hc_data_platform.backup.temporal import TemporalPolicyArtifact
from hc_data_platform.backup.whole import (
    WholeBackupError,
    WholeBackupPlanV1,
    assemble_whole_backup,
    policy_verification_evidence,
    prepare_postgresql_artifact,
    publish_whole_backup,
)

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "backend/tests/backup/fixtures/hc-platform-backup-v1.golden.json"
NOW = datetime(2026, 8, 29, 1, 0, tzinfo=timezone.utc)
COMPLETED = datetime(2026, 8, 29, 1, 5, tzinfo=timezone.utc)
DOMAINS = (
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


class _LocalSigner:
    def __init__(self, key_reference: str) -> None:
        self._private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
        self.key_reference = key_reference
        self.public_key = self._private_key.public_key()

    def sign(self, message: bytes) -> bytes:
        return self._private_key.sign(message)


class _PublishingRepository:
    def __init__(self, signer: _LocalSigner) -> None:
        self.signer = signer
        self.assembly: object | None = None
        self.preflight_count = 0
        self.upload_count = 0

    def preflight(self) -> None:
        self.preflight_count += 1

    def upload_file(
        self,
        backup_id: str,
        source: object,
        *,
        checkpoint_path: Path,
    ) -> RepositoryStoredObject:
        del backup_id, checkpoint_path
        self.upload_count += 1
        logical_path = source.logical_path
        return RepositoryStoredObject(
            logical_path=logical_path,
            version_id=f"version-{self.upload_count}",
            size_bytes=source.size_bytes,
            sha256=source.sha256,
            resumed=False,
        )

    def verify_backup(
        self,
        backup_id: str,
        *,
        public_keys_by_sha256: object,
        staging_directory: Path,
    ) -> RepositoryVerifiedBackup:
        del backup_id, public_keys_by_sha256, staging_directory
        assembly = self.assembly
        assert assembly is not None
        verified = verify_signed_manifest(
            assembly.signed_manifest.manifest_bytes,
            assembly.signed_manifest.signature_bytes,
            public_key=self.signer.public_key,
        )
        return RepositoryVerifiedBackup(
            verified_manifest=verified,
            artifact_paths={},
            manifest_version_id="manifest-version-1",
            signature_version_id="signature-version-1",
        )


def _private(path: Path, payload: bytes) -> tuple[Path, str]:
    path.write_bytes(payload)
    path.chmod(0o600)
    return path, hashlib.sha256(payload).hexdigest()


def _fixtures(
    staging: Path,
) -> tuple[
    WholeBackupPlanV1,
    LogicalDumpArtifact,
    ObjectBackupArtifact,
    ConfigurationBackupArtifact,
    TemporalPolicyArtifact,
    _LocalSigner,
]:
    golden = json.loads(GOLDEN.read_bytes())
    manifest = BackupManifestV1.model_validate(golden["manifest"])
    repository = manifest.backup_repository.model_copy(
        update={"retention_until": datetime(2027, 8, 29, tzinfo=timezone.utc)}
    )
    plan = WholeBackupPlanV1(
        operation_id="whole-create-001",
        backup_id="whole-backup-001",
        mode="snapshot",
        created_at=NOW,
        completed_at=COMPLETED,
        source_platform_id="platform-cn",
        source_environment_id="acceptance-cn",
        verifier_identity="whole-backup-controller",
        release=manifest.release,
        backup_tool=manifest.backup_tool,
        postgresql={
            "physical_recovery": "provider_managed_pitr",
            "pitr_coordinate": "provider-pitr://acceptance-cn/2026-08-29T01:05:00Z",
        },
        object_store={
            "provider": "s3_compatible",
            "bucket_reference": "business-object-store-cn",
            "prefix": "datasets/",
        },
        temporal={
            "cluster_reference": "temporal-cluster://acceptance-cn",
            "namespace": "hc-platform",
            "service_version": "1.29.1",
        },
        deployment_configuration={
            "gitops_repository_reference": "gitops://hc/platform@release-2026.08.29"
        },
        secrets_and_kms={
            "repository_key_reference": repository.kms_key_reference,
            "signing_key_reference": manifest.secrets_and_kms.signing_key_reference,
            "portable_recipient_key_references": (),
        },
        backup_repository=repository,
        external_evidence={
            "runtime_logs_evidence_sha256": "8" * 64,
            "external_dependencies_evidence_sha256": "9" * 64,
        },
    )
    dump_path, dump_sha = _private(staging / "postgresql.dump", b"postgresql custom dump")
    postgres = LogicalDumpArtifact(
        path=dump_path,
        size_bytes=dump_path.stat().st_size,
        sha256=dump_sha,
        server_major=16,
        wal_anchor=PostgresWalAnchor(
            system_identifier=123456789,
            timeline_id=1,
            wal_lsn="0/16B6C50",
            server_version="16.10",
            server_major=16,
            database_name="hc_data",
            in_recovery=False,
        ),
        source_evidence=DatabaseVerificationEvidence(
            database_size_bytes=16_777_216,
            migration_count=105,
            migration_sha256=manifest.release.migration_manifest_sha256,
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
    inventory_path, inventory_sha = _private(staging / "inventory.zst", b"inventory")
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
            manifest.secrets_and_kms.dependencies,
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
            provider_reference="acceptance-vault",
            hmac_key_reference=manifest.secrets_and_kms.signing_key_reference,
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
    return (
        plan,
        postgres,
        objects,
        configuration,
        temporal,
        _LocalSigner(plan.secrets_and_kms.signing_key_reference),
    )


def _evidence(plan: WholeBackupPlanV1) -> tuple[object, ...]:
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
    return tuple(
        policy_verification_evidence(
            domain,  # type: ignore[arg-type]
            policy={"domain": domain, "backup_id": plan.backup_id},
            evidence_sha256=policy_hashes.get(domain, hashlib.sha256(domain.encode()).hexdigest()),
            verifier_identity=plan.verifier_identity,
            verified_at=plan.completed_at,
        )
        for domain in DOMAINS
    )


def test_whole_backup_assembly_is_deterministic_integrity_verified_and_redacted(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    plan, postgres, objects, configuration, temporal, signer = _fixtures(staging)
    prepared = prepare_postgresql_artifact(plan, postgres, staging_directory=staging)

    first = assemble_whole_backup(
        plan,
        postgresql_receipt=postgres,
        postgresql_upload=prepared,
        object_receipt=objects,
        configuration_receipt=configuration,
        temporal_receipt=temporal,
        verification=_evidence(plan),  # type: ignore[arg-type]
        signer=signer,
        staging_directory=staging,
    )
    second = assemble_whole_backup(
        plan,
        postgresql_receipt=postgres,
        postgresql_upload=prepared,
        object_receipt=objects,
        configuration_receipt=configuration,
        temporal_receipt=temporal,
        verification=_evidence(plan),  # type: ignore[arg-type]
        signer=signer,
        staging_directory=staging,
    )

    assert first.signed_manifest == second.signed_manifest
    assert first.manifest.status == "INTEGRITY_VERIFIED"
    assert all(fact.kind == "integrity" for fact in first.manifest.verification_facts)
    assert {item.domain for item in first.checksums.verification_evidence} == set(DOMAINS)
    assert {item.logical_path for item in first.upload_sources} == {
        *(item.path for item in first.manifest.artifacts),
        "manifest.json",
        "signature.sig",
    }
    serialized = first.signed_manifest.manifest_bytes + first.signed_manifest.signature_bytes
    assert b"physical-bucket" not in serialized
    assert b"private_key" not in serialized
    assert b"token" not in serialized.lower()
    for name in ("manifest.json", "signature.sig", "checksums.sha256"):
        assert (staging / name).stat().st_mode & 0o077 == 0


def test_whole_backup_rejects_tampered_domain_bytes_before_signing(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    plan, postgres, objects, configuration, temporal, signer = _fixtures(staging)
    prepared = prepare_postgresql_artifact(plan, postgres, staging_directory=staging)
    objects.inventory.path.write_bytes(b"tampered!")

    with pytest.raises(WholeBackupError) as captured:
        assemble_whole_backup(
            plan,
            postgresql_receipt=postgres,
            postgresql_upload=prepared,
            object_receipt=objects,
            configuration_receipt=configuration,
            temporal_receipt=temporal,
            verification=_evidence(plan),  # type: ignore[arg-type]
            signer=signer,
            staging_directory=staging,
        )
    assert captured.value.code == "BACKUP_WHOLE_ARTIFACT_MISMATCH"
    assert not (staging / "manifest.json").exists()


def test_whole_backup_rejects_incomplete_state_domain_evidence(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    plan, postgres, objects, configuration, temporal, signer = _fixtures(staging)
    prepared = prepare_postgresql_artifact(plan, postgres, staging_directory=staging)

    with pytest.raises(WholeBackupError) as captured:
        assemble_whole_backup(
            plan,
            postgresql_receipt=postgres,
            postgresql_upload=prepared,
            object_receipt=objects,
            configuration_receipt=configuration,
            temporal_receipt=temporal,
            verification=_evidence(plan)[:-1],  # type: ignore[arg-type]
            signer=signer,
            staging_directory=staging,
        )
    assert captured.value.code == "BACKUP_WHOLE_VERIFICATION_INCOMPLETE"


def test_whole_publish_verifies_repository_before_atomic_catalog_creation(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    verification_staging = staging / "verification"
    staging.mkdir(mode=0o700)
    verification_staging.mkdir(mode=0o700)
    plan, postgres, objects, configuration, temporal, signer = _fixtures(staging)
    prepared = prepare_postgresql_artifact(plan, postgres, staging_directory=staging)
    assembly = assemble_whole_backup(
        plan,
        postgresql_receipt=postgres,
        postgresql_upload=prepared,
        object_receipt=objects,
        configuration_receipt=configuration,
        temporal_receipt=temporal,
        verification=_evidence(plan),  # type: ignore[arg-type]
        signer=signer,
        staging_directory=staging,
    )
    repository = _PublishingRepository(signer)
    repository.assembly = assembly
    catalog_repository = InMemoryBackupCatalogRepository(clock=lambda: COMPLETED)
    catalog = BackupCatalogService(
        catalog_repository,
        trusted_signing_key_sha256=frozenset({assembly.manifest.signature.public_key_sha256}),
        trusted_repository_ids=frozenset({assembly.manifest.backup_repository.repository_id}),
    )

    result = publish_whole_backup(
        assembly,
        repository=repository,  # type: ignore[arg-type]
        catalog=catalog,
        audit=BackupCatalogAuditContext(
            actor_id=plan.verifier_identity,
            request_id=plan.operation_id,
        ),
        repository_checkpoint_path=staging / "repository.checkpoint.json",
        verification_staging_directory=verification_staging,
    )

    assert repository.preflight_count == 1
    assert repository.upload_count == len(assembly.upload_sources)
    assert result.catalog_record.current_status == "INTEGRITY_VERIFIED"
    assert [fact.status for fact in result.catalog_record.status_facts] == [
        "CREATING",
        "CREATED",
        "INTEGRITY_VERIFIED",
    ]
    assert all(fact.status != "RESTORE_VERIFIED" for fact in result.catalog_record.status_facts)


def test_functional_file_repository_publishes_and_resumes_exact_backup(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    first_verification = tmp_path / "verify-first"
    second_verification = tmp_path / "verify-second"
    repository_root = tmp_path / "hc-migration-unit001-repository"
    for directory in (staging, first_verification, second_verification, repository_root):
        directory.mkdir(mode=0o700)
    plan, postgres, objects, configuration, temporal, signer = _fixtures(staging)
    prepared = prepare_postgresql_artifact(plan, postgres, staging_directory=staging)
    assembly = assemble_whole_backup(
        plan,
        postgresql_receipt=postgres,
        postgresql_upload=prepared,
        object_receipt=objects,
        configuration_receipt=configuration,
        temporal_receipt=temporal,
        verification=_evidence(plan),  # type: ignore[arg-type]
        signer=signer,
        staging_directory=staging,
    )
    repository = FunctionalFileBackupRepository(
        FunctionalFileRepositoryConfig(
            repository_id=plan.backup_repository.repository_id,
            provider=plan.backup_repository.provider,
            bucket_reference=plan.backup_repository.bucket_reference,
            kms_key_reference=plan.backup_repository.kms_key_reference,
            retention_until=plan.backup_repository.retention_until,
            cross_site_replica_reference=(plan.backup_repository.cross_site_replica_reference),
            root_directory=repository_root,
            run_id="unit001",
        )
    )
    catalog = BackupCatalogService(
        InMemoryBackupCatalogRepository(clock=lambda: COMPLETED),
        trusted_signing_key_sha256=frozenset({assembly.manifest.signature.public_key_sha256}),
        trusted_repository_ids=frozenset({plan.backup_repository.repository_id}),
    )
    audit = BackupCatalogAuditContext(
        actor_id=plan.verifier_identity,
        request_id=plan.operation_id,
    )

    first = publish_whole_backup(
        assembly,
        repository=repository,  # type: ignore[arg-type]
        catalog=catalog,
        audit=audit,
        repository_checkpoint_path=staging / "repository.checkpoint.json",
        verification_staging_directory=first_verification,
    )
    second = publish_whole_backup(
        assembly,
        repository=repository,  # type: ignore[arg-type]
        catalog=catalog,
        audit=audit,
        repository_checkpoint_path=staging / "repository.checkpoint.json",
        verification_staging_directory=second_verification,
    )

    assert first.catalog_record == second.catalog_record
    assert all(not item.resumed for item in first.stored_objects)
    assert all(item.resumed for item in second.stored_objects)
    assert first.manifest_version_id.startswith("functional-sha256-")
    assert first.signature_version_id.startswith("functional-sha256-")
