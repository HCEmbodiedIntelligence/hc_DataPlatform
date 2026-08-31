"""Fail-closed contracts for ``hc-platform-backup/v1``.

This module deliberately contains no backup side effects.  It freezes the data,
signature, and target-binding rules consumed by the later backup and restore
implementations.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Protocol, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[
    str,
    StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$"),
]
ArtifactPath = Annotated[
    str,
    StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,511}$"),
]
KmsKeyReference = Annotated[
    str,
    StringConstraints(
        pattern=(
            r"^kms://[a-zA-Z0-9._:-]+(?:/[a-zA-Z0-9._:-]+)*/versions/"
            r"v[1-9][0-9]*$"
        )
    ),
]
ImageDigest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
GitCommit = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]

BACKUP_FORMAT: Literal["hc-platform-backup/v1"] = "hc-platform-backup/v1"
SIGNATURE_FORMAT: Literal["hc-platform-signature/v1"] = "hc-platform-signature/v1"
RESTORE_TARGET_FORMAT: Literal["hc-platform-restore-target/v1"] = "hc-platform-restore-target/v1"
CANONICALIZATION: Literal["hc-json-c14n/v1"] = "hc-json-c14n/v1"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_SIGNATURE_ENVELOPE_BYTES = 16 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReleaseImagesV1(_StrictModel):
    api: ImageDigest
    worker: ImageDigest
    frontend: ImageDigest


class ReleaseIdentityV1(_StrictModel):
    git_commit: GitCommit
    chart_name: Literal["hc-data-platform"] = "hc-data-platform"
    chart_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
    images: ReleaseImagesV1
    migration_manifest_sha256: Sha256
    release_manifest_sha256: Sha256


class BackupToolIdentityV1(_StrictModel):
    name: Literal["hc-platform-backup"] = "hc-platform-backup"
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
    git_commit: GitCommit
    image_digest: ImageDigest


class ArtifactV1(_StrictModel):
    path: ArtifactPath
    role: Literal[
        "postgresql_dump",
        "object_inventory",
        "object_part",
        "temporal_policy",
        "public_config",
        "secrets_envelope",
        "checksums",
    ]
    media_type: str = Field(min_length=1, max_length=127)
    size_bytes: int = Field(ge=0)
    sha256: Sha256
    client_side_encryption: Literal[
        "age_x25519_v1",
        "repository_kms_only",
        "public_integrity_metadata",
    ]

    @field_validator("path")
    @classmethod
    def reject_unsafe_path(cls, value: str) -> str:
        if any(part in {"", ".", ".."} for part in value.split("/")):
            raise ValueError("artifact paths must be normalized relative paths")
        return value


class PostgreSQLTableContentV1(_StrictModel):
    schema_name: str = Field(min_length=1, max_length=128)
    table_name: str = Field(min_length=1, max_length=128)
    row_count: int = Field(ge=0)
    content_sha256: Sha256


class PostgreSQLContentEvidenceV1(_StrictModel):
    schema_object_count: int = Field(ge=0)
    schema_sha256: Sha256
    constraint_count: int = Field(ge=0)
    constraint_sha256: Sha256
    sequence_count: int = Field(ge=0)
    sequence_sha256: Sha256
    tables: tuple[PostgreSQLTableContentV1, ...]
    aggregate_sha256: Sha256

    @model_validator(mode="after")
    def require_sorted_unique_tables(self) -> PostgreSQLContentEvidenceV1:
        names = [(item.schema_name, item.table_name) for item in self.tables]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("PostgreSQL content evidence tables must be sorted and unique")
        return self


class PostgreSQLBackupV1(_StrictModel):
    major_version: int = Field(ge=14, le=99)
    server_version: str = Field(min_length=1, max_length=63)
    # Older signed V1 manifests predate RST3-01 capacity evidence and remain
    # authentic.  Restore planning rejects ``None`` instead of guessing from a
    # compressed dump size.
    database_size_bytes: int | None = Field(default=None, ge=0)
    database_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,126}[A-Za-z0-9])?$",
    )
    dump_path: ArtifactPath
    dump_sha256: Sha256
    dump_format: Literal["postgresql_custom"] = "postgresql_custom"
    migration_count: int = Field(gt=0)
    migration_manifest_sha256: Sha256
    content_evidence: PostgreSQLContentEvidenceV1 | None = None
    physical_recovery: Literal["baseline_plus_wal_pitr", "provider_managed_pitr"]
    pitr_coordinate: str = Field(min_length=1, max_length=255)


class ObjectStoreBackupV1(_StrictModel):
    provider: Literal["s3_compatible", "aws_s3", "aliyun_oss"]
    bucket_reference: str = Field(min_length=1, max_length=255)
    prefix: str = Field(max_length=512)
    versioning_status: Literal["enabled"] = "enabled"
    consistency_coordinate: str = Field(min_length=1, max_length=255)
    inventory_path: ArtifactPath
    inventory_sha256: Sha256
    object_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    portable_part_paths: tuple[ArtifactPath, ...] = ()


class TemporalBackupV1(_StrictModel):
    deployment_mode: Literal["external_managed"] = "external_managed"
    cluster_reference: str = Field(min_length=1, max_length=255)
    namespace: Identifier
    service_version: str = Field(min_length=1, max_length=63)
    recovery_policy: Literal["provider_supported_ha_backup"] = "provider_supported_ha_backup"
    policy_path: ArtifactPath
    policy_sha256: Sha256
    schedule_inventory_sha256: Sha256
    open_workflow_inventory_sha256: Sha256
    internal_database_in_business_dump: Literal[False] = False


class DeploymentConfigurationBackupV1(_StrictModel):
    gitops_repository_reference: str = Field(min_length=1, max_length=255)
    public_config_path: ArtifactPath
    public_config_sha256: Sha256
    endpoint_remapping_policy: Literal["explicit_only"] = "explicit_only"


class SecretDependencyV1(_StrictModel):
    environment_variable: str = Field(pattern=r"^HC_[A-Z0-9_]+$")
    secret_reference: str = Field(min_length=1, max_length=255)
    secret_key_name: str = Field(min_length=1, max_length=127)
    version: str = Field(min_length=1, max_length=127)
    fingerprint_sha256: Sha256


class SecretsAndKMSBackupV1(_StrictModel):
    envelope_path: ArtifactPath
    envelope_sha256: Sha256
    dependencies: tuple[SecretDependencyV1, ...] = Field(min_length=1)
    repository_key_reference: KmsKeyReference
    portable_recipient_key_references: tuple[KmsKeyReference, ...] = ()
    signing_key_reference: KmsKeyReference
    plaintext_export_allowed: Literal[False] = False

    @model_validator(mode="after")
    def require_unique_dependencies(self) -> SecretsAndKMSBackupV1:
        variables = [dependency.environment_variable for dependency in self.dependencies]
        if len(variables) != len(set(variables)):
            raise ValueError("secret dependency environment variables must be unique")
        return self


class BackupRepositoryV1(_StrictModel):
    repository_id: Identifier
    provider: Literal["s3_compatible", "aws_s3", "aliyun_oss"]
    bucket_reference: str = Field(min_length=1, max_length=255)
    server_side_encryption: Literal["kms_aes256_gcm"] = "kms_aes256_gcm"
    kms_key_reference: KmsKeyReference
    object_lock: Literal["compliance"] = "compliance"
    retention_until: AwareDatetime
    cross_site_replica_reference: str = Field(min_length=1, max_length=255)
    catalog_is_only_copy: Literal[False] = False

    @field_validator("retention_until")
    @classmethod
    def require_utc_retention(cls, value: datetime) -> datetime:
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("retention_until must be UTC")
        return value


class StateDomainEvidenceV1(_StrictModel):
    disposition: Literal["included", "external", "not_applicable"]
    verification_status: Literal["verified", "external_verified", "not_applicable"]
    owner_role: Literal[
        "platform_sre",
        "database_operations",
        "object_storage_operations",
        "temporal_operations",
        "security_kms_operations",
        "release_engineering",
        "observability_operations",
        "identity_operations",
    ]
    evidence_artifacts: tuple[ArtifactPath, ...] = Field(min_length=1)


class StateDomainsV1(_StrictModel):
    postgresql_business: StateDomainEvidenceV1
    object_store: StateDomainEvidenceV1
    temporal_workflows: StateDomainEvidenceV1
    deployment_configuration: StateDomainEvidenceV1
    secrets_and_kms: StateDomainEvidenceV1
    release_artifacts: StateDomainEvidenceV1
    backup_repository: StateDomainEvidenceV1
    runtime_logs: StateDomainEvidenceV1
    external_dependencies: StateDomainEvidenceV1


class CompatibilityV1(_StrictModel):
    schema_version: Literal[1] = 1
    minimum_backup_tool_version: str = Field(
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$"
    )
    maximum_backup_tool_major: int = Field(ge=1)
    postgresql_major: int = Field(ge=14, le=99)
    exact_recorded_release_required: Literal[True] = True
    forward_upgrade_before_restore_verification_allowed: Literal[False] = False


class SignaturePolicyV1(_StrictModel):
    format_version: Literal["hc-platform-signature/v1"] = SIGNATURE_FORMAT
    algorithm: Literal["Ed25519"] = "Ed25519"
    canonicalization: Literal["hc-json-c14n/v1"] = CANONICALIZATION
    key_reference: KmsKeyReference
    public_key_sha256: Sha256
    signature_path: Literal["signature.sig"] = "signature.sig"


class VerificationFactV1(_StrictModel):
    operation_id: Identifier
    kind: Literal["integrity", "restore"]
    result: Literal["passed"] = "passed"
    verified_at: AwareDatetime
    verifier_identity: str = Field(min_length=1, max_length=255)
    evidence_artifacts: tuple[ArtifactPath, ...] = Field(min_length=1)

    @field_validator("verified_at")
    @classmethod
    def require_utc_verification_time(cls, value: datetime) -> datetime:
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("verification timestamps must be UTC")
        return value


class BackupManifestV1(_StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        title="hc-platform-backup/v1",
        json_schema_extra={
            "$id": "https://hc-data-platform.invalid/contracts/hc-platform-backup-v1.schema.json",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    format_version: Literal["hc-platform-backup/v1"] = BACKUP_FORMAT
    backup_id: Identifier
    mode: Literal["portable", "snapshot"]
    status: Literal["INTEGRITY_VERIFIED", "RESTORE_VERIFIED"]
    created_at: AwareDatetime
    completed_at: AwareDatetime
    source_platform_id: Identifier
    source_environment_id: Identifier
    release: ReleaseIdentityV1
    backup_tool: BackupToolIdentityV1
    postgresql: PostgreSQLBackupV1
    object_store: ObjectStoreBackupV1
    temporal: TemporalBackupV1
    deployment_configuration: DeploymentConfigurationBackupV1
    secrets_and_kms: SecretsAndKMSBackupV1
    backup_repository: BackupRepositoryV1
    state_domains: StateDomainsV1
    artifacts: tuple[ArtifactV1, ...] = Field(min_length=1)
    verification_facts: tuple[VerificationFactV1, ...] = Field(min_length=1)
    compatibility: CompatibilityV1
    signature: SignaturePolicyV1

    @field_validator("created_at", "completed_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("backup timestamps must be UTC")
        return value

    @model_validator(mode="after")
    def validate_cross_contract_invariants(self) -> BackupManifestV1:
        if self.completed_at < self.created_at:
            raise ValueError("completed_at cannot precede created_at")
        if self.backup_repository.retention_until <= self.completed_at:
            raise ValueError("backup repository retention must extend beyond backup completion")
        if self.release.migration_manifest_sha256 != self.postgresql.migration_manifest_sha256:
            raise ValueError("release and PostgreSQL migration manifests must match")
        if self.compatibility.postgresql_major != self.postgresql.major_version:
            raise ValueError("compatibility PostgreSQL major must match the backup")
        if self.signature.key_reference != self.secrets_and_kms.signing_key_reference:
            raise ValueError("signature and KMS signing key references must match")
        if (
            self.backup_repository.kms_key_reference
            != self.secrets_and_kms.repository_key_reference
        ):
            raise ValueError("repository and Secret/KMS key references must match")

        by_path = {artifact.path: artifact for artifact in self.artifacts}
        if len(by_path) != len(self.artifacts):
            raise ValueError("artifact paths must be unique")
        singular_roles = {
            "postgresql_dump",
            "object_inventory",
            "temporal_policy",
            "public_config",
            "secrets_envelope",
            "checksums",
        }
        for role in singular_roles:
            matches = [artifact for artifact in self.artifacts if artifact.role == role]
            if len(matches) != 1:
                raise ValueError(f"exactly one {role} artifact is required")

        references = {
            self.postgresql.dump_path: self.postgresql.dump_sha256,
            self.object_store.inventory_path: self.object_store.inventory_sha256,
            self.temporal.policy_path: self.temporal.policy_sha256,
            self.deployment_configuration.public_config_path: (
                self.deployment_configuration.public_config_sha256
            ),
            self.secrets_and_kms.envelope_path: self.secrets_and_kms.envelope_sha256,
        }
        for path, expected_sha256 in references.items():
            artifact = by_path.get(path)
            if artifact is None or artifact.sha256 != expected_sha256:
                raise ValueError(f"referenced artifact {path!r} is absent or has the wrong hash")

        object_part_paths = {
            artifact.path for artifact in self.artifacts if artifact.role == "object_part"
        }
        if object_part_paths != set(self.object_store.portable_part_paths):
            raise ValueError("portable object part list must exactly match object_part artifacts")
        sensitive_roles = {
            "postgresql_dump",
            "object_inventory",
            "object_part",
            "temporal_policy",
            "secrets_envelope",
        }
        if any(
            artifact.client_side_encryption == "public_integrity_metadata"
            for artifact in self.artifacts
            if artifact.role in sensitive_roles
        ):
            raise ValueError("sensitive artifacts cannot be marked as public metadata")
        if self.mode == "portable":
            if not object_part_paths and self.object_store.object_count:
                raise ValueError("portable backups with objects require encrypted object parts")
            if any(
                artifact.client_side_encryption != "age_x25519_v1"
                for artifact in self.artifacts
                if artifact.role in sensitive_roles
            ):
                raise ValueError("portable sensitive artifacts require age X25519 encryption")
            if not self.secrets_and_kms.portable_recipient_key_references:
                raise ValueError("portable backups require an external age recipient key")
        elif object_part_paths:
            raise ValueError("snapshot backups cannot contain portable object parts")
        elif any(
            artifact.client_side_encryption != "repository_kms_only"
            for artifact in self.artifacts
            if artifact.role in sensitive_roles
        ):
            raise ValueError("snapshot sensitive artifacts require repository KMS encryption")

        artifact_paths = set(by_path)
        for domain_name, domain in self.state_domains:
            if not set(domain.evidence_artifacts).issubset(artifact_paths):
                raise ValueError(f"state domain {domain_name} references an absent artifact")
        operation_ids = [fact.operation_id for fact in self.verification_facts]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("verification operation IDs must be unique")
        if not any(fact.kind == "integrity" for fact in self.verification_facts):
            raise ValueError("an integrity verification fact is required")
        if self.status == "RESTORE_VERIFIED" and not any(
            fact.kind == "restore" for fact in self.verification_facts
        ):
            raise ValueError("RESTORE_VERIFIED requires a restore verification fact")
        for fact in self.verification_facts:
            if not set(fact.evidence_artifacts).issubset(artifact_paths):
                raise ValueError(
                    f"verification operation {fact.operation_id} references an absent artifact"
                )
        required_dispositions = {
            "postgresql_business": "included",
            "object_store": "included",
            "temporal_workflows": "external",
            "deployment_configuration": "included",
            "secrets_and_kms": "external",
            "release_artifacts": "included",
            "backup_repository": "included",
        }
        for name, disposition in required_dispositions.items():
            domain = cast(StateDomainEvidenceV1, getattr(self.state_domains, name))
            if domain.disposition != disposition or domain.verification_status == "not_applicable":
                raise ValueError(f"required state domain {name} is not verified")
        return self


class SignatureEnvelopeV1(_StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        title="hc-platform-signature/v1",
        json_schema_extra={
            "$id": "https://hc-data-platform.invalid/contracts/hc-platform-signature-v1.schema.json",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    format_version: Literal["hc-platform-signature/v1"] = SIGNATURE_FORMAT
    algorithm: Literal["Ed25519"] = "Ed25519"
    canonicalization: Literal["hc-json-c14n/v1"] = CANONICALIZATION
    key_reference: KmsKeyReference
    public_key_sha256: Sha256
    manifest_sha256: Sha256
    signature_base64url: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class RestoreTargetV1(_StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        title="hc-platform-restore-target/v1",
        json_schema_extra={
            "$id": (
                "https://hc-data-platform.invalid/contracts/"
                "hc-platform-restore-target-v1.schema.json"
            ),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    format_version: Literal["hc-platform-restore-target/v1"] = RESTORE_TARGET_FORMAT
    operation_id: Identifier
    expected_backup_id: Identifier
    expected_source_platform_id: Identifier
    expected_source_environment_id: Identifier
    target_instance_id: Identifier
    target_environment_id: Identifier
    purpose: Literal["disaster_recovery", "planned_migration", "rehearsal"]
    target_is_empty: bool
    temporary_restore_authorization_id: Identifier | None = None
    writes_enabled: Literal[False] = False
    supported_backup_formats: tuple[Literal["hc-platform-backup/v1"], ...] = (BACKUP_FORMAT,)
    requested_release: ReleaseIdentityV1
    postgresql_major: int = Field(ge=14, le=99)
    object_store_versioning_enabled: Literal[True] = True
    trusted_signing_key_sha256: frozenset[Sha256] = Field(min_length=1)
    available_kms_key_references: frozenset[KmsKeyReference] = Field(min_length=1)
    temporal_namespace_ready: Literal[True] = True
    required_external_dependencies_ready: Literal[True] = True

    @model_validator(mode="after")
    def fence_nonempty_targets(self) -> RestoreTargetV1:
        if not self.target_is_empty and self.temporary_restore_authorization_id is None:
            raise ValueError("non-empty targets require explicit temporary restore authorization")
        return self


@dataclass(frozen=True)
class VerifiedBackupV1:
    manifest: BackupManifestV1
    manifest_sha256: str
    target_instance_id: str


@dataclass(frozen=True)
class VerifiedManifestV1:
    """Authenticated immutable manifest before any restore target is selected."""

    manifest: BackupManifestV1
    manifest_sha256: str
    signature_sha256: str


@dataclass(frozen=True)
class SignedManifestV1:
    """Canonical manifest and detached signature-envelope artifacts."""

    manifest_bytes: bytes
    signature_bytes: bytes
    manifest_sha256: str
    signature_sha256: str


class ManifestSigningPort(Protocol):
    """External KMS/HSM signing boundary; implementations retain the private key."""

    @property
    def key_reference(self) -> str: ...

    @property
    def public_key(self) -> Ed25519PublicKey: ...

    def sign(self, message: bytes) -> bytes: ...


class BackupContractError(ValueError):
    """Stable fail-closed error raised before any restore mutation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


_FORBIDDEN_SECRET_KEYS = {
    "access_key_id",
    "api_key",
    "api_token",
    "bearer_token",
    "credential_value",
    "password",
    "password_value",
    "plaintext",
    "private_key",
    "secret_access_key",
    "secret_value",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^:/\s]+:[^@/\s]+@"),
)


def find_plaintext_secret_material(document: Any) -> tuple[str, ...]:
    """Return stable paths to forbidden plaintext secret-shaped material."""

    findings: list[str] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = str(raw_key)
                child_path = f"{path}.{key}" if path else key
                if key.casefold() in _FORBIDDEN_SECRET_KEYS:
                    findings.append(child_path)
                visit(child, child_path)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")
        elif isinstance(value, str) and any(
            pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS
        ):
            findings.append(path or "$")

    visit(document, "")
    return tuple(sorted(set(findings)))


def canonical_json_bytes(document: Any) -> bytes:
    """Serialize the integer/string-only contract deterministically for signing."""

    try:
        return json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BackupContractError("BACKUP_CANONICALIZATION_FAILED", str(exc)) from exc


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BackupContractError(
                "BACKUP_MANIFEST_DUPLICATE_KEY", f"duplicate JSON member {key!r}"
            )
        result[key] = value
    return result


def _load_json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except BackupContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupContractError(f"{label}_INVALID_JSON", str(exc)) from exc
    if not isinstance(parsed, dict):
        raise BackupContractError(f"{label}_INVALID_JSON", "top-level value must be an object")
    return cast(dict[str, Any], parsed)


def _public_key_sha256(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()


def _check_target(manifest: BackupManifestV1, target: RestoreTargetV1) -> None:
    if manifest.format_version not in target.supported_backup_formats:
        raise BackupContractError(
            "BACKUP_FORMAT_UNSUPPORTED", "target does not support the backup format"
        )
    if (
        target.expected_backup_id != manifest.backup_id
        or target.expected_source_platform_id != manifest.source_platform_id
        or target.expected_source_environment_id != manifest.source_environment_id
    ):
        raise BackupContractError(
            "BACKUP_TARGET_IDENTITY_MISMATCH", "backup is not bound to this restore operation"
        )
    if target.requested_release != manifest.release:
        raise BackupContractError(
            "BACKUP_RELEASE_MISMATCH", "the exact backup-recorded release is not selected"
        )
    if target.postgresql_major != manifest.postgresql.major_version:
        raise BackupContractError(
            "BACKUP_POSTGRESQL_MAJOR_MISMATCH", "target PostgreSQL major is incompatible"
        )
    if manifest.signature.public_key_sha256 not in target.trusted_signing_key_sha256:
        raise BackupContractError(
            "BACKUP_SIGNER_UNTRUSTED", "manifest signer is not pinned by the restore target"
        )
    required_keys = {
        manifest.secrets_and_kms.repository_key_reference,
        manifest.secrets_and_kms.signing_key_reference,
        *manifest.secrets_and_kms.portable_recipient_key_references,
    }
    if not required_keys.issubset(target.available_kms_key_references):
        raise BackupContractError(
            "BACKUP_KMS_KEY_UNAVAILABLE", "one or more exact backup key versions are unavailable"
        )


def bind_verified_manifest_to_target(
    verified: VerifiedManifestV1,
    *,
    target: RestoreTargetV1,
) -> VerifiedBackupV1:
    """Bind an already authenticated repository manifest before payload reads."""

    _check_target(verified.manifest, target)
    return VerifiedBackupV1(
        manifest=verified.manifest,
        manifest_sha256=verified.manifest_sha256,
        target_instance_id=target.target_instance_id,
    )


def parse_signature_envelope(signature_bytes: bytes) -> SignatureEnvelopeV1:
    """Parse a bounded strict envelope so a pinned public key can be selected."""

    if len(signature_bytes) > MAX_SIGNATURE_ENVELOPE_BYTES:
        raise BackupContractError("BACKUP_SIGNATURE_TOO_LARGE", "signature envelope exceeds 16 KiB")
    raw_signature = _load_json_object(signature_bytes, label="BACKUP_SIGNATURE")
    try:
        return SignatureEnvelopeV1.model_validate(raw_signature)
    except ValidationError as exc:
        raise BackupContractError("BACKUP_SIGNATURE_INVALID", str(exc)) from exc


def sign_backup_manifest(
    manifest: BackupManifestV1,
    *,
    signer: ManifestSigningPort,
) -> SignedManifestV1:
    """Canonicalize and sign a verified manifest through an external key boundary."""

    document = manifest.model_dump(mode="json")
    secret_findings = find_plaintext_secret_material(document)
    if secret_findings:
        raise BackupContractError("BACKUP_PLAINTEXT_SECRET_DETECTED", ", ".join(secret_findings))

    public_key_sha256 = _public_key_sha256(signer.public_key)
    if (
        signer.key_reference != manifest.signature.key_reference
        or public_key_sha256 != manifest.signature.public_key_sha256
    ):
        raise BackupContractError(
            "BACKUP_SIGNER_POLICY_MISMATCH",
            "external signer identity does not match the manifest signature policy",
        )

    manifest_bytes = canonical_json_bytes(document)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    signature = signer.sign(manifest_bytes)
    if len(signature) != 64:
        raise BackupContractError(
            "BACKUP_SIGNATURE_INVALID", "external signer returned an invalid Ed25519 signature"
        )
    try:
        signer.public_key.verify(signature, manifest_bytes)
    except InvalidSignature as exc:
        raise BackupContractError(
            "BACKUP_SIGNATURE_INVALID", "external signer result failed Ed25519 verification"
        ) from exc

    envelope = SignatureEnvelopeV1(
        key_reference=manifest.signature.key_reference,
        public_key_sha256=manifest.signature.public_key_sha256,
        manifest_sha256=manifest_sha256,
        signature_base64url=base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii"),
    )
    signature_bytes = canonical_json_bytes(envelope.model_dump(mode="json"))
    return SignedManifestV1(
        manifest_bytes=manifest_bytes,
        signature_bytes=signature_bytes,
        manifest_sha256=manifest_sha256,
        signature_sha256=hashlib.sha256(signature_bytes).hexdigest(),
    )


def verify_backup_for_target(
    manifest_bytes: bytes,
    signature_bytes: bytes,
    *,
    target: RestoreTargetV1,
    public_key: Ed25519PublicKey,
) -> VerifiedBackupV1:
    """Authenticate and bind a backup before any restore code may mutate a target."""

    verified = verify_signed_manifest(
        manifest_bytes,
        signature_bytes,
        public_key=public_key,
    )
    return bind_verified_manifest_to_target(verified, target=target)


def verify_signed_manifest(
    manifest_bytes: bytes,
    signature_bytes: bytes,
    *,
    public_key: Ed25519PublicKey,
) -> VerifiedManifestV1:
    """Authenticate a repository manifest without selecting a restore target."""

    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise BackupContractError("BACKUP_MANIFEST_TOO_LARGE", "manifest exceeds 4 MiB")
    raw_manifest = _load_json_object(manifest_bytes, label="BACKUP_MANIFEST")
    secret_findings = find_plaintext_secret_material(raw_manifest)
    if secret_findings:
        raise BackupContractError("BACKUP_PLAINTEXT_SECRET_DETECTED", ", ".join(secret_findings))
    if raw_manifest.get("format_version") != BACKUP_FORMAT:
        raise BackupContractError(
            "BACKUP_FORMAT_UNSUPPORTED", "only hc-platform-backup/v1 is accepted"
        )
    try:
        manifest = BackupManifestV1.model_validate(raw_manifest)
    except ValidationError as exc:
        raise BackupContractError("BACKUP_MANIFEST_INVALID", str(exc)) from exc

    envelope = parse_signature_envelope(signature_bytes)

    if (
        envelope.key_reference != manifest.signature.key_reference
        or envelope.public_key_sha256 != manifest.signature.public_key_sha256
    ):
        raise BackupContractError(
            "BACKUP_SIGNATURE_POLICY_MISMATCH", "signature envelope does not match the manifest"
        )
    actual_public_key_sha256 = _public_key_sha256(public_key)
    if actual_public_key_sha256 != envelope.public_key_sha256:
        raise BackupContractError(
            "BACKUP_SIGNING_KEY_MISMATCH", "provided public key fingerprint does not match"
        )

    canonical_manifest = canonical_json_bytes(raw_manifest)
    manifest_sha256 = hashlib.sha256(canonical_manifest).hexdigest()
    if manifest_sha256 != envelope.manifest_sha256:
        raise BackupContractError(
            "BACKUP_SIGNATURE_MANIFEST_HASH_MISMATCH", "manifest content was modified"
        )
    try:
        padding = "=" * (-len(envelope.signature_base64url) % 4)
        signature = base64.b64decode(
            envelope.signature_base64url + padding,
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error) as exc:
        raise BackupContractError("BACKUP_SIGNATURE_INVALID", "invalid base64url") from exc
    try:
        public_key.verify(signature, canonical_manifest)
    except InvalidSignature as exc:
        raise BackupContractError(
            "BACKUP_SIGNATURE_INVALID", "Ed25519 verification failed"
        ) from exc

    return VerifiedManifestV1(
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        signature_sha256=hashlib.sha256(signature_bytes).hexdigest(),
    )
