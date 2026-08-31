"""Deterministic whole-platform backup assembly from verified domain receipts."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypeVar

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from hc_data_platform.backup.catalog import (
    BackupCatalogAuditContext,
    BackupCatalogRecord,
    BackupCatalogService,
)
from hc_data_platform.backup.contracts import (
    ArtifactV1,
    BackupManifestV1,
    BackupRepositoryV1,
    BackupToolIdentityV1,
    CompatibilityV1,
    DeploymentConfigurationBackupV1,
    ManifestSigningPort,
    ObjectStoreBackupV1,
    PostgreSQLBackupV1,
    PostgreSQLContentEvidenceV1,
    PostgreSQLTableContentV1,
    ReleaseIdentityV1,
    SecretsAndKMSBackupV1,
    SignaturePolicyV1,
    SignedManifestV1,
    StateDomainEvidenceV1,
    StateDomainsV1,
    TemporalBackupV1,
    VerificationFactV1,
    canonical_json_bytes,
    sign_backup_manifest,
)
from hc_data_platform.backup.dependencies import ConfigurationBackupArtifact
from hc_data_platform.backup.objects import (
    AgeDecryptedReader,
    AgeEncryptedWriter,
    ObjectBackupArtifact,
)
from hc_data_platform.backup.postgresql import LogicalDumpArtifact
from hc_data_platform.backup.repository import (
    RepositoryStoredObject,
    RepositoryUploadSource,
    S3LockedBackupRepository,
)
from hc_data_platform.backup.temporal import TemporalPolicyArtifact

_MAX_PLAN_BYTES = 4 * 1024 * 1024
_MAX_RECEIPT_BYTES = 32 * 1024 * 1024
_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_VERIFICATION_DOMAINS = frozenset(
    {
        "postgresql_business",
        "object_store",
        "temporal_workflows",
        "deployment_configuration",
        "secrets_and_kms",
        "release_artifacts",
        "backup_repository",
        "runtime_logs",
        "external_dependencies",
    }
)


class WholeBackupError(RuntimeError):
    """Stable, redacted whole-backup orchestration failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WholePostgreSQLPolicy(_StrictModel):
    physical_recovery: Literal["baseline_plus_wal_pitr", "provider_managed_pitr"]
    pitr_coordinate: str = Field(min_length=1, max_length=255)


class WholeObjectStorePolicy(_StrictModel):
    provider: Literal["s3_compatible", "aws_s3", "aliyun_oss"]
    bucket_reference: str = Field(min_length=1, max_length=255)
    prefix: str = Field(max_length=512)


class WholeTemporalPolicy(_StrictModel):
    cluster_reference: str = Field(min_length=1, max_length=255)
    namespace: str = Field(pattern=_IDENTIFIER)
    service_version: str = Field(min_length=1, max_length=63)


class WholeDeploymentPolicy(_StrictModel):
    gitops_repository_reference: str = Field(min_length=1, max_length=255)


class WholeSecretsPolicy(_StrictModel):
    repository_key_reference: str = Field(
        pattern=r"^kms://[A-Za-z0-9][A-Za-z0-9_.:/-]*/versions/v[1-9][0-9]*$"
    )
    signing_key_reference: str = Field(
        pattern=r"^kms://[A-Za-z0-9][A-Za-z0-9_.:/-]*/versions/v[1-9][0-9]*$"
    )
    portable_recipient_key_references: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_sorted_recipients(self) -> WholeSecretsPolicy:
        if self.portable_recipient_key_references != tuple(
            sorted(set(self.portable_recipient_key_references))
        ):
            raise ValueError("portable recipient key references must be sorted and unique")
        return self


class WholeExternalEvidence(_StrictModel):
    runtime_logs_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    external_dependencies_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class WholeBackupPlanV1(_StrictModel):
    """Immutable, non-secret controller input for one retryable backup operation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        title="hc-whole-backup-plan/v1",
        json_schema_extra={
            "$id": (
                "https://hc-data-platform.invalid/contracts/hc-whole-backup-plan-v1.schema.json"
            ),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    format_version: Literal["hc-whole-backup-plan/v1"] = "hc-whole-backup-plan/v1"
    operation_id: str = Field(pattern=_IDENTIFIER)
    backup_id: str = Field(pattern=_IDENTIFIER)
    mode: Literal["snapshot", "portable"]
    created_at: AwareDatetime
    completed_at: AwareDatetime
    source_platform_id: str = Field(pattern=_IDENTIFIER)
    source_environment_id: str = Field(pattern=_IDENTIFIER)
    verifier_identity: str = Field(min_length=1, max_length=255)
    release: ReleaseIdentityV1
    backup_tool: BackupToolIdentityV1
    postgresql: WholePostgreSQLPolicy
    object_store: WholeObjectStorePolicy
    temporal: WholeTemporalPolicy
    deployment_configuration: WholeDeploymentPolicy
    secrets_and_kms: WholeSecretsPolicy
    backup_repository: BackupRepositoryV1
    external_evidence: WholeExternalEvidence

    @model_validator(mode="after")
    def bind_immutable_policy(self) -> WholeBackupPlanV1:
        if len(self.operation_id) > 118:
            raise ValueError("whole-backup operation ID leaves no room for fact suffixes")
        if self.created_at.utcoffset() != timezone.utc.utcoffset(self.created_at) or (
            self.completed_at.utcoffset() != timezone.utc.utcoffset(self.completed_at)
        ):
            raise ValueError("whole-backup timestamps must be UTC")
        if self.completed_at < self.created_at:
            raise ValueError("whole-backup completion cannot precede creation")
        if self.backup_repository.retention_until <= self.completed_at:
            raise ValueError("repository retention must extend beyond completion")
        if self.backup_repository.retention_until.microsecond != 0:
            raise ValueError("repository retention must use whole-second precision")
        if (
            self.backup_repository.kms_key_reference
            != self.secrets_and_kms.repository_key_reference
        ):
            raise ValueError("repository KMS references differ")
        if self.mode == "portable" and not self.secrets_and_kms.portable_recipient_key_references:
            raise ValueError("portable whole backup requires an external recipient key")
        if self.mode == "snapshot" and self.secrets_and_kms.portable_recipient_key_references:
            raise ValueError("snapshot whole backup cannot name a portable recipient key")
        return self


class PreparedPostgreSQLArtifact(_StrictModel):
    format_version: Literal["hc-prepared-postgresql-backup/v1"] = "hc-prepared-postgresql-backup/v1"
    mode: Literal["snapshot", "portable"]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    path: Path
    logical_path: str = Field(pattern=r"^db/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    media_type: Literal[
        "application/vnd.postgresql.custom",
        "application/vnd.postgresql.custom+age",
    ]
    client_side_encryption: Literal["repository_kms_only", "age_x25519_v1"]
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def bind_mode(self) -> PreparedPostgreSQLArtifact:
        expected = (
            (
                "db/platform.dump.age",
                "application/vnd.postgresql.custom+age",
                "age_x25519_v1",
            )
            if self.mode == "portable"
            else (
                "db/platform.dump",
                "application/vnd.postgresql.custom",
                "repository_kms_only",
            )
        )
        if (self.logical_path, self.media_type, self.client_side_encryption) != expected:
            raise ValueError("prepared PostgreSQL metadata differs from its mode")
        return self


VerificationDomain = Literal[
    "postgresql_business",
    "object_store",
    "temporal_workflows",
    "deployment_configuration",
    "secrets_and_kms",
    "release_artifacts",
    "backup_repository",
    "runtime_logs",
    "external_dependencies",
]


class WholeVerificationEvidence(_StrictModel):
    domain: VerificationDomain
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verification_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_identity: str = Field(min_length=1, max_length=255)
    verified_at: AwareDatetime
    result: Literal["passed"] = "passed"


class WholeChecksumEntry(_StrictModel):
    path: str = Field(min_length=1, max_length=1024)
    role: str = Field(min_length=1, max_length=64)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    client_side_encryption: Literal[
        "age_x25519_v1",
        "repository_kms_only",
        "public_integrity_metadata",
    ]


class WholeBackupChecksumsV1(_StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        title="hc-platform-checksums/v1",
        json_schema_extra={
            "$id": (
                "https://hc-data-platform.invalid/contracts/hc-platform-checksums-v1.schema.json"
            ),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    format_version: Literal["hc-platform-checksums/v1"] = "hc-platform-checksums/v1"
    backup_id: str = Field(pattern=_IDENTIFIER)
    operation_id: str = Field(pattern=_IDENTIFIER)
    mode: Literal["snapshot", "portable"]
    generated_at: AwareDatetime
    artifacts: tuple[WholeChecksumEntry, ...] = Field(min_length=1)
    verification_evidence: tuple[WholeVerificationEvidence, ...] = Field(min_length=9)

    @model_validator(mode="after")
    def require_closed_evidence(self) -> WholeBackupChecksumsV1:
        paths = [item.path for item in self.artifacts]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("checksum artifact paths must be sorted and unique")
        domains = [item.domain for item in self.verification_evidence]
        if domains != sorted(domains) or set(domains) != _VERIFICATION_DOMAINS:
            raise ValueError("whole-backup verification domains do not close")
        return self


@dataclass(frozen=True)
class WholeBackupAssembly:
    plan: WholeBackupPlanV1
    manifest: BackupManifestV1
    signed_manifest: SignedManifestV1
    checksums: WholeBackupChecksumsV1
    upload_sources: tuple[RepositoryUploadSource, ...]
    public_key: Ed25519PublicKey


@dataclass(frozen=True)
class WholeBackupPublishResult:
    catalog_record: BackupCatalogRecord
    stored_objects: tuple[RepositoryStoredObject, ...]
    manifest_version_id: str
    signature_version_id: str


def load_whole_backup_plan(path: Path) -> WholeBackupPlanV1:
    return _load_private_model(
        path,
        WholeBackupPlanV1,
        maximum_bytes=_MAX_PLAN_BYTES,
        error_code="BACKUP_WHOLE_PLAN_INVALID",
    )


def load_domain_receipt(path: Path, model: type[BaseModel]) -> BaseModel:
    return _load_private_model(
        path,
        model,
        maximum_bytes=_MAX_RECEIPT_BYTES,
        error_code="BACKUP_WHOLE_RECEIPT_INVALID",
    )


def prepare_postgresql_artifact(
    plan: WholeBackupPlanV1,
    artifact: LogicalDumpArtifact,
    *,
    staging_directory: Path,
    encryptor: AgeEncryptedWriter | None = None,
    decryptor: AgeDecryptedReader | None = None,
) -> PreparedPostgreSQLArtifact:
    staging = _secure_directory(staging_directory)
    _verify_local_artifact(
        artifact.path,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        staging=staging,
    )
    receipt_path = staging / "prepared-postgresql-receipt.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        prepared = _load_private_model(
            receipt_path,
            PreparedPostgreSQLArtifact,
            maximum_bytes=_MAX_RECEIPT_BYTES,
            error_code="BACKUP_WHOLE_POSTGRESQL_PREPARE_INVALID",
        )
        if prepared.mode != plan.mode or prepared.source_sha256 != artifact.sha256:
            raise WholeBackupError(
                "BACKUP_WHOLE_POSTGRESQL_PREPARE_MISMATCH",
                "the prepared PostgreSQL receipt belongs to different source bytes",
            )
        _verify_local_artifact(
            prepared.path,
            size_bytes=prepared.size_bytes,
            sha256=prepared.sha256,
            staging=staging,
        )
        return prepared

    if plan.mode == "snapshot":
        prepared = PreparedPostgreSQLArtifact(
            mode="snapshot",
            source_sha256=artifact.sha256,
            path=artifact.path,
            logical_path="db/platform.dump",
            media_type="application/vnd.postgresql.custom",
            client_side_encryption="repository_kms_only",
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
        )
    else:
        if encryptor is None or decryptor is None:
            raise WholeBackupError(
                "BACKUP_WHOLE_AGE_REQUIRED",
                "portable PostgreSQL preparation requires external age encrypt/decrypt adapters",
            )
        destination = staging / "platform.dump.age"
        try:
            if destination.exists() or destination.is_symlink():
                _verify_local_artifact(
                    destination,
                    size_bytes=destination.stat().st_size,
                    sha256=_file_sha256(destination),
                    staging=staging,
                )
            else:
                with (
                    artifact.path.open("rb") as source,
                    encryptor.open(destination, cwd=staging) as encrypted,
                ):
                    while chunk := source.read(1024 * 1024):
                        encrypted.write(chunk)
            _verify_age_round_trip(
                destination,
                decryptor=decryptor,
                staging=staging,
                expected_size=artifact.size_bytes,
                expected_sha256=artifact.sha256,
            )
            prepared = PreparedPostgreSQLArtifact(
                mode="portable",
                source_sha256=artifact.sha256,
                path=destination,
                logical_path="db/platform.dump.age",
                media_type="application/vnd.postgresql.custom+age",
                client_side_encryption="age_x25519_v1",
                size_bytes=destination.stat().st_size,
                sha256=_file_sha256(destination),
            )
        except BaseException:
            with suppress(FileNotFoundError):
                destination.unlink()
            raise
    _write_private_model(receipt_path, prepared)
    return prepared


def verification_evidence(
    domain: VerificationDomain,
    *,
    receipt_path: Path,
    verification_summary: Mapping[str, object],
    verifier_identity: str,
    verified_at: datetime,
) -> WholeVerificationEvidence:
    receipt = _read_private_bytes(receipt_path, maximum_bytes=_MAX_RECEIPT_BYTES)
    return WholeVerificationEvidence(
        domain=domain,
        receipt_sha256=hashlib.sha256(receipt).hexdigest(),
        verification_sha256=hashlib.sha256(
            canonical_json_bytes(dict(verification_summary))
        ).hexdigest(),
        verifier_identity=verifier_identity,
        verified_at=verified_at,
    )


def policy_verification_evidence(
    domain: VerificationDomain,
    *,
    policy: Mapping[str, object],
    evidence_sha256: str,
    verifier_identity: str,
    verified_at: datetime,
) -> WholeVerificationEvidence:
    policy_bytes = canonical_json_bytes(dict(policy))
    return WholeVerificationEvidence(
        domain=domain,
        receipt_sha256=hashlib.sha256(policy_bytes).hexdigest(),
        verification_sha256=evidence_sha256,
        verifier_identity=verifier_identity,
        verified_at=verified_at,
    )


def assemble_whole_backup(
    plan: WholeBackupPlanV1,
    *,
    postgresql_receipt: LogicalDumpArtifact,
    postgresql_upload: PreparedPostgreSQLArtifact,
    object_receipt: ObjectBackupArtifact,
    configuration_receipt: ConfigurationBackupArtifact,
    temporal_receipt: TemporalPolicyArtifact,
    verification: Sequence[WholeVerificationEvidence],
    signer: ManifestSigningPort,
    staging_directory: Path,
) -> WholeBackupAssembly:
    staging = _secure_directory(staging_directory)
    _validate_receipt_closure(
        plan,
        postgresql_receipt=postgresql_receipt,
        postgresql_upload=postgresql_upload,
        object_receipt=object_receipt,
        configuration_receipt=configuration_receipt,
        temporal_receipt=temporal_receipt,
        staging=staging,
    )
    verification_items = tuple(sorted(verification, key=lambda item: item.domain))
    if {item.domain for item in verification_items} != _VERIFICATION_DOMAINS or len(
        verification_items
    ) != len(_VERIFICATION_DOMAINS):
        raise WholeBackupError(
            "BACKUP_WHOLE_VERIFICATION_INCOMPLETE",
            "every state domain requires one verification evidence record",
        )
    if any(
        item.verifier_identity != plan.verifier_identity or item.verified_at != plan.completed_at
        for item in verification_items
    ):
        raise WholeBackupError(
            "BACKUP_WHOLE_VERIFICATION_MISMATCH",
            "verification evidence differs from the immutable backup operation",
        )
    by_domain = {item.domain: item for item in verification_items}
    expected_policy_evidence: dict[VerificationDomain, str] = {
        "release_artifacts": hashlib.sha256(
            canonical_json_bytes(plan.release.model_dump(mode="json"))
        ).hexdigest(),
        "backup_repository": hashlib.sha256(
            canonical_json_bytes(plan.backup_repository.model_dump(mode="json"))
        ).hexdigest(),
        "runtime_logs": plan.external_evidence.runtime_logs_evidence_sha256,
        "external_dependencies": (plan.external_evidence.external_dependencies_evidence_sha256),
    }
    if any(
        by_domain[domain].verification_sha256 != expected
        for domain, expected in expected_policy_evidence.items()
    ):
        raise WholeBackupError(
            "BACKUP_WHOLE_EXTERNAL_EVIDENCE_MISMATCH",
            "external or policy evidence differs from the immutable backup plan",
        )

    artifacts = _domain_artifacts(
        postgresql_upload,
        object_receipt,
        configuration_receipt,
        temporal_receipt,
    )
    checksums = WholeBackupChecksumsV1(
        backup_id=plan.backup_id,
        operation_id=plan.operation_id,
        mode=plan.mode,
        generated_at=plan.completed_at,
        artifacts=tuple(
            WholeChecksumEntry(
                path=item.path,
                role=item.role,
                size_bytes=item.size_bytes,
                sha256=item.sha256,
                client_side_encryption=item.client_side_encryption,
            )
            for item in artifacts
        ),
        verification_evidence=verification_items,
    )
    checksums_path = staging / "checksums.sha256"
    _write_or_compare_private_model(checksums_path, checksums)
    checksums_artifact = ArtifactV1(
        path="checksums.sha256",
        role="checksums",
        media_type="application/vnd.hc.platform-checksums.v1+json",
        size_bytes=checksums_path.stat().st_size,
        sha256=_file_sha256(checksums_path),
        client_side_encryption="public_integrity_metadata",
    )
    all_artifacts = tuple((*artifacts, checksums_artifact))
    public_key_bytes = signer.public_key.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    public_key_sha256 = hashlib.sha256(public_key_bytes).hexdigest()
    manifest = BackupManifestV1(
        backup_id=plan.backup_id,
        mode=plan.mode,
        status="INTEGRITY_VERIFIED",
        created_at=plan.created_at,
        completed_at=plan.completed_at,
        source_platform_id=plan.source_platform_id,
        source_environment_id=plan.source_environment_id,
        release=plan.release,
        backup_tool=plan.backup_tool,
        postgresql=PostgreSQLBackupV1(
            major_version=postgresql_receipt.server_major,
            server_version=postgresql_receipt.wal_anchor.server_version,
            database_size_bytes=postgresql_receipt.source_evidence.database_size_bytes,
            database_name=postgresql_receipt.wal_anchor.database_name,
            dump_path=postgresql_upload.logical_path,
            dump_sha256=postgresql_upload.sha256,
            migration_count=postgresql_receipt.source_evidence.migration_count,
            migration_manifest_sha256=postgresql_receipt.source_evidence.migration_sha256,
            content_evidence=PostgreSQLContentEvidenceV1(
                schema_object_count=postgresql_receipt.source_evidence.schema_object_count,
                schema_sha256=postgresql_receipt.source_evidence.schema_sha256,
                constraint_count=postgresql_receipt.source_evidence.constraint_count,
                constraint_sha256=postgresql_receipt.source_evidence.constraint_sha256,
                sequence_count=postgresql_receipt.source_evidence.sequence_count,
                sequence_sha256=postgresql_receipt.source_evidence.sequence_sha256,
                tables=tuple(
                    PostgreSQLTableContentV1(**item.model_dump(mode="python"))
                    for item in postgresql_receipt.source_evidence.tables
                ),
                aggregate_sha256=postgresql_receipt.source_evidence.aggregate_sha256,
            ),
            physical_recovery=plan.postgresql.physical_recovery,
            pitr_coordinate=plan.postgresql.pitr_coordinate,
        ),
        object_store=ObjectStoreBackupV1(
            provider=plan.object_store.provider,
            bucket_reference=plan.object_store.bucket_reference,
            prefix=plan.object_store.prefix,
            consistency_coordinate=object_receipt.inventory.consistency_coordinate,
            inventory_path=object_receipt.inventory.logical_path,
            inventory_sha256=object_receipt.inventory.sha256,
            object_count=object_receipt.inventory.object_count,
            total_bytes=object_receipt.inventory.total_bytes,
            portable_part_paths=tuple(item.logical_path for item in object_receipt.parts),
        ),
        temporal=TemporalBackupV1(
            cluster_reference=plan.temporal.cluster_reference,
            namespace=plan.temporal.namespace,
            service_version=plan.temporal.service_version,
            policy_path=temporal_receipt.logical_path,
            policy_sha256=temporal_receipt.sha256,
            schedule_inventory_sha256=temporal_receipt.schedule_inventory_sha256,
            open_workflow_inventory_sha256=temporal_receipt.open_workflow_inventory_sha256,
        ),
        deployment_configuration=DeploymentConfigurationBackupV1(
            gitops_repository_reference=(plan.deployment_configuration.gitops_repository_reference),
            public_config_path=configuration_receipt.public_config.logical_path,
            public_config_sha256=configuration_receipt.public_config.sha256,
        ),
        secrets_and_kms=SecretsAndKMSBackupV1(
            envelope_path=configuration_receipt.secrets_envelope.logical_path,
            envelope_sha256=configuration_receipt.secrets_envelope.sha256,
            dependencies=configuration_receipt.manifest_dependencies,
            repository_key_reference=plan.secrets_and_kms.repository_key_reference,
            portable_recipient_key_references=(
                plan.secrets_and_kms.portable_recipient_key_references
            ),
            signing_key_reference=plan.secrets_and_kms.signing_key_reference,
        ),
        backup_repository=plan.backup_repository,
        state_domains=_state_domains(object_receipt),
        artifacts=all_artifacts,
        verification_facts=(
            VerificationFactV1(
                operation_id=f"{plan.operation_id}:integrity",
                kind="integrity",
                verified_at=plan.completed_at,
                verifier_identity=plan.verifier_identity,
                evidence_artifacts=("checksums.sha256",),
            ),
        ),
        compatibility=CompatibilityV1(
            minimum_backup_tool_version=plan.backup_tool.version,
            maximum_backup_tool_major=max(1, int(plan.backup_tool.version.split(".", 1)[0])),
            postgresql_major=postgresql_receipt.server_major,
        ),
        signature=SignaturePolicyV1(
            key_reference=plan.secrets_and_kms.signing_key_reference,
            public_key_sha256=public_key_sha256,
        ),
    )
    signed = sign_backup_manifest(manifest, signer=signer)
    manifest_path = staging / "manifest.json"
    signature_path = staging / "signature.sig"
    _write_or_compare_private_bytes(manifest_path, signed.manifest_bytes)
    _write_or_compare_private_bytes(signature_path, signed.signature_bytes)
    sources = tuple(
        RepositoryUploadSource(
            logical_path=item.path,
            path=_path_for_artifact(
                item.path,
                postgresql_upload=postgresql_upload,
                object_receipt=object_receipt,
                configuration_receipt=configuration_receipt,
                temporal_receipt=temporal_receipt,
                checksums_path=checksums_path,
            ),
            media_type=item.media_type,
            size_bytes=item.size_bytes,
            sha256=item.sha256,
        )
        for item in all_artifacts
    ) + (
        RepositoryUploadSource(
            logical_path="manifest.json",
            path=manifest_path,
            media_type="application/vnd.hc.platform-backup.v1+json",
            size_bytes=len(signed.manifest_bytes),
            sha256=signed.manifest_sha256,
        ),
        RepositoryUploadSource(
            logical_path="signature.sig",
            path=signature_path,
            media_type="application/vnd.hc.platform-signature.v1+json",
            size_bytes=len(signed.signature_bytes),
            sha256=signed.signature_sha256,
        ),
    )
    return WholeBackupAssembly(
        plan=plan,
        manifest=manifest,
        signed_manifest=signed,
        checksums=checksums,
        upload_sources=sources,
        public_key=signer.public_key,
    )


def publish_whole_backup(
    assembly: WholeBackupAssembly,
    *,
    repository: S3LockedBackupRepository,
    catalog: BackupCatalogService,
    audit: BackupCatalogAuditContext,
    repository_checkpoint_path: Path,
    verification_staging_directory: Path,
) -> WholeBackupPublishResult:
    """Upload idempotently, read independently, then append catalog creation facts."""

    repository.preflight()
    stored = tuple(
        repository.upload_file(
            assembly.plan.backup_id,
            source,
            checkpoint_path=repository_checkpoint_path,
        )
        for source in assembly.upload_sources
    )
    signer_fingerprint = assembly.manifest.signature.public_key_sha256
    verified = repository.verify_backup(
        assembly.plan.backup_id,
        public_keys_by_sha256={signer_fingerprint: assembly.public_key},
        staging_directory=verification_staging_directory,
    )
    if (
        verified.verified_manifest.manifest != assembly.manifest
        or verified.verified_manifest.manifest_sha256 != assembly.signed_manifest.manifest_sha256
        or verified.verified_manifest.signature_sha256 != assembly.signed_manifest.signature_sha256
    ):
        raise WholeBackupError(
            "BACKUP_WHOLE_REPOSITORY_VERIFICATION_MISMATCH",
            "the independently read repository backup differs from the created manifest",
        )
    record = catalog.import_created_manifest(
        verified.verified_manifest,
        creation_operation_id=assembly.plan.operation_id,
        audit=audit,
    )
    return WholeBackupPublishResult(
        catalog_record=record,
        stored_objects=stored,
        manifest_version_id=verified.manifest_version_id,
        signature_version_id=verified.signature_version_id,
    )


def verify_published_whole_backup(
    plan: WholeBackupPlanV1,
    *,
    operation_id: str,
    verifier_identity: str,
    public_key: Ed25519PublicKey,
    repository: S3LockedBackupRepository,
    catalog: BackupCatalogService,
    audit: BackupCatalogAuditContext,
    verification_staging_directory: Path,
    verified_at: datetime,
) -> BackupCatalogRecord:
    """Re-read a backup and append only another integrity-verification fact."""

    if (
        not operation_id
        or len(operation_id) > 128
        or not verifier_identity
        or len(verifier_identity) > 255
        or verified_at.utcoffset() != timezone.utc.utcoffset(verified_at)
    ):
        raise WholeBackupError(
            "BACKUP_WHOLE_VERIFICATION_INVALID",
            "the repository verification operation identity is invalid",
        )
    public_bytes = public_key.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    fingerprint = hashlib.sha256(public_bytes).hexdigest()
    verified = repository.verify_backup(
        plan.backup_id,
        public_keys_by_sha256={fingerprint: public_key},
        staging_directory=verification_staging_directory,
    )
    manifest = verified.verified_manifest.manifest
    if (
        manifest.backup_id != plan.backup_id
        or manifest.source_platform_id != plan.source_platform_id
        or manifest.source_environment_id != plan.source_environment_id
        or manifest.backup_repository != plan.backup_repository
        or manifest.status != "INTEGRITY_VERIFIED"
    ):
        raise WholeBackupError(
            "BACKUP_WHOLE_REPOSITORY_VERIFICATION_MISMATCH",
            "the repository backup differs from the immutable verification plan",
        )
    return catalog.append_integrity_verification(
        plan.backup_id,
        operation_id=operation_id,
        evidence_uri=(
            f"backup-repository://{plan.backup_repository.repository_id}/"
            f"{plan.backup_id}/checksums.sha256"
        ),
        evidence_sha256=verified.verified_manifest.manifest_sha256,
        verifier_identity=verifier_identity,
        verified_at=verified_at,
        audit=audit,
    )


def _domain_artifacts(
    postgresql: PreparedPostgreSQLArtifact,
    objects: ObjectBackupArtifact,
    configuration: ConfigurationBackupArtifact,
    temporal: TemporalPolicyArtifact,
) -> tuple[ArtifactV1, ...]:
    result = [
        ArtifactV1(
            path=postgresql.logical_path,
            role="postgresql_dump",
            media_type=postgresql.media_type,
            size_bytes=postgresql.size_bytes,
            sha256=postgresql.sha256,
            client_side_encryption=postgresql.client_side_encryption,
        ),
        ArtifactV1(
            path=objects.inventory.logical_path,
            role="object_inventory",
            media_type=objects.inventory.media_type,
            size_bytes=objects.inventory.size_bytes,
            sha256=objects.inventory.sha256,
            client_side_encryption=objects.inventory.client_side_encryption,
        ),
        *(
            ArtifactV1(
                path=item.logical_path,
                role="object_part",
                media_type=item.media_type,
                size_bytes=item.size_bytes,
                sha256=item.sha256,
                client_side_encryption=item.client_side_encryption,
            )
            for item in objects.parts
        ),
        ArtifactV1(
            path=temporal.logical_path,
            role="temporal_policy",
            media_type=temporal.media_type,
            size_bytes=temporal.size_bytes,
            sha256=temporal.sha256,
            client_side_encryption=temporal.client_side_encryption,
        ),
        ArtifactV1(
            path=configuration.public_config.logical_path,
            role="public_config",
            media_type=configuration.public_config.media_type,
            size_bytes=configuration.public_config.size_bytes,
            sha256=configuration.public_config.sha256,
            client_side_encryption=configuration.public_config.client_side_encryption,
        ),
        ArtifactV1(
            path=configuration.secrets_envelope.logical_path,
            role="secrets_envelope",
            media_type=configuration.secrets_envelope.media_type,
            size_bytes=configuration.secrets_envelope.size_bytes,
            sha256=configuration.secrets_envelope.sha256,
            client_side_encryption=configuration.secrets_envelope.client_side_encryption,
        ),
    ]
    return tuple(sorted(result, key=lambda item: item.path))


def _state_domains(objects: ObjectBackupArtifact) -> StateDomainsV1:
    object_evidence = (objects.inventory.logical_path,) + tuple(
        item.logical_path for item in objects.parts
    )
    return StateDomainsV1(
        postgresql_business=StateDomainEvidenceV1(
            disposition="included",
            verification_status="verified",
            owner_role="database_operations",
            evidence_artifacts=("db/platform.dump",)
            if objects.inventory.mode == "snapshot"
            else ("db/platform.dump.age",),
        ),
        object_store=StateDomainEvidenceV1(
            disposition="included",
            verification_status="verified",
            owner_role="object_storage_operations",
            evidence_artifacts=object_evidence,
        ),
        temporal_workflows=StateDomainEvidenceV1(
            disposition="external",
            verification_status="external_verified",
            owner_role="temporal_operations",
            evidence_artifacts=("temporal/policy.json",),
        ),
        deployment_configuration=StateDomainEvidenceV1(
            disposition="included",
            verification_status="verified",
            owner_role="release_engineering",
            evidence_artifacts=("config/public.yaml",),
        ),
        secrets_and_kms=StateDomainEvidenceV1(
            disposition="external",
            verification_status="external_verified",
            owner_role="security_kms_operations",
            evidence_artifacts=(
                "secrets/envelope.json.age"
                if objects.inventory.mode == "portable"
                else "secrets/envelope.json",
            ),
        ),
        release_artifacts=StateDomainEvidenceV1(
            disposition="included",
            verification_status="verified",
            owner_role="release_engineering",
            evidence_artifacts=("checksums.sha256",),
        ),
        backup_repository=StateDomainEvidenceV1(
            disposition="included",
            verification_status="verified",
            owner_role="platform_sre",
            evidence_artifacts=("checksums.sha256",),
        ),
        runtime_logs=StateDomainEvidenceV1(
            disposition="external",
            verification_status="external_verified",
            owner_role="observability_operations",
            evidence_artifacts=("checksums.sha256",),
        ),
        external_dependencies=StateDomainEvidenceV1(
            disposition="external",
            verification_status="external_verified",
            owner_role="platform_sre",
            evidence_artifacts=("checksums.sha256",),
        ),
    )


def _validate_receipt_closure(
    plan: WholeBackupPlanV1,
    *,
    postgresql_receipt: LogicalDumpArtifact,
    postgresql_upload: PreparedPostgreSQLArtifact,
    object_receipt: ObjectBackupArtifact,
    configuration_receipt: ConfigurationBackupArtifact,
    temporal_receipt: TemporalPolicyArtifact,
    staging: Path,
) -> None:
    if (
        postgresql_upload.mode != plan.mode
        or postgresql_upload.source_sha256 != postgresql_receipt.sha256
        or object_receipt.inventory.mode != plan.mode
        or configuration_receipt.secrets_envelope.mode != plan.mode
        or temporal_receipt.mode != plan.mode
        or postgresql_receipt.source_evidence.migration_sha256
        != plan.release.migration_manifest_sha256
    ):
        raise WholeBackupError(
            "BACKUP_WHOLE_RECEIPT_MISMATCH",
            "a domain receipt differs from the immutable whole-backup plan",
        )
    for path, size, digest in (
        (
            postgresql_upload.path,
            postgresql_upload.size_bytes,
            postgresql_upload.sha256,
        ),
        (
            object_receipt.inventory.path,
            object_receipt.inventory.size_bytes,
            object_receipt.inventory.sha256,
        ),
        *((item.path, item.size_bytes, item.sha256) for item in object_receipt.parts),
        (
            configuration_receipt.public_config.path,
            configuration_receipt.public_config.size_bytes,
            configuration_receipt.public_config.sha256,
        ),
        (
            configuration_receipt.secrets_envelope.path,
            configuration_receipt.secrets_envelope.size_bytes,
            configuration_receipt.secrets_envelope.sha256,
        ),
        (temporal_receipt.path, temporal_receipt.size_bytes, temporal_receipt.sha256),
    ):
        _verify_local_artifact(path, size_bytes=size, sha256=digest, staging=staging)


def _path_for_artifact(
    logical_path: str,
    *,
    postgresql_upload: PreparedPostgreSQLArtifact,
    object_receipt: ObjectBackupArtifact,
    configuration_receipt: ConfigurationBackupArtifact,
    temporal_receipt: TemporalPolicyArtifact,
    checksums_path: Path,
) -> Path:
    mapping = {
        postgresql_upload.logical_path: postgresql_upload.path,
        object_receipt.inventory.logical_path: object_receipt.inventory.path,
        temporal_receipt.logical_path: temporal_receipt.path,
        configuration_receipt.public_config.logical_path: configuration_receipt.public_config.path,
        configuration_receipt.secrets_envelope.logical_path: (
            configuration_receipt.secrets_envelope.path
        ),
        "checksums.sha256": checksums_path,
        **{item.logical_path: item.path for item in object_receipt.parts},
    }
    try:
        return mapping[logical_path]
    except KeyError as exc:
        raise WholeBackupError(
            "BACKUP_WHOLE_ARTIFACT_MISSING", "a manifest artifact has no local source"
        ) from exc


def _verify_age_round_trip(
    path: Path,
    *,
    decryptor: AgeDecryptedReader,
    staging: Path,
    expected_size: int,
    expected_sha256: str,
) -> None:
    digest = hashlib.sha256()
    size = 0
    with decryptor.open(path, cwd=staging) as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > expected_size:
                raise WholeBackupError(
                    "BACKUP_WHOLE_POSTGRESQL_ENCRYPTION_MISMATCH",
                    "the decrypted PostgreSQL artifact exceeded its source size",
                )
            digest.update(chunk)
    if size != expected_size or digest.hexdigest() != expected_sha256:
        raise WholeBackupError(
            "BACKUP_WHOLE_POSTGRESQL_ENCRYPTION_MISMATCH",
            "the encrypted PostgreSQL artifact does not round-trip to its source",
        )


ModelT = TypeVar("ModelT", bound=BaseModel)


def _load_private_model(
    path: Path,
    model: type[ModelT],
    *,
    maximum_bytes: int,
    error_code: str,
) -> ModelT:
    try:
        payload = _read_private_bytes(path, maximum_bytes=maximum_bytes)
        value = json.loads(payload, object_pairs_hook=_unique_json_object)
        return model.model_validate(value)
    except WholeBackupError:
        raise
    except (ValueError, ValidationError, json.JSONDecodeError) as exc:
        raise WholeBackupError(error_code, "a private backup document is invalid") from exc


def _read_private_bytes(path: Path, *, maximum_bytes: int) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise WholeBackupError(
            "BACKUP_WHOLE_PRIVATE_FILE_INVALID", "a required private backup file is unavailable"
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or info.st_size <= 0
        or info.st_size > maximum_bytes
    ):
        raise WholeBackupError(
            "BACKUP_WHOLE_PRIVATE_FILE_INVALID",
            "a required private backup file violates owner-only bounded policy",
        )
    return resolved.read_bytes()


def _secure_directory(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise WholeBackupError(
            "BACKUP_WHOLE_STAGING_UNSAFE", "the whole-backup staging directory is unavailable"
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise WholeBackupError(
            "BACKUP_WHOLE_STAGING_UNSAFE", "the whole-backup staging directory is not owner-only"
        )
    return resolved


def _verify_local_artifact(
    path: Path,
    *,
    size_bytes: int,
    sha256: str,
    staging: Path,
) -> None:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
        resolved.relative_to(staging)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise WholeBackupError(
            "BACKUP_WHOLE_ARTIFACT_INVALID", "a domain artifact is outside private staging"
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or info.st_size != size_bytes
        or _file_sha256(resolved) != sha256
    ):
        raise WholeBackupError(
            "BACKUP_WHOLE_ARTIFACT_MISMATCH",
            "a domain artifact differs from its verified receipt",
        )


def _write_private_model(path: Path, model: BaseModel) -> None:
    _write_or_compare_private_bytes(path, canonical_json_bytes(model.model_dump(mode="json")))


def _write_or_compare_private_model(path: Path, model: BaseModel) -> None:
    _write_or_compare_private_bytes(path, canonical_json_bytes(model.model_dump(mode="json")))


def _write_or_compare_private_bytes(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        existing = _read_private_bytes(path, maximum_bytes=max(len(payload), 1))
        if existing != payload:
            raise WholeBackupError(
                "BACKUP_WHOLE_OPERATION_CONFLICT",
                "an immutable whole-backup artifact already contains different bytes",
            )
        return
    temporary = path.parent / f".{path.name}.{os.getpid()}.partial"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", buffering=0) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result
