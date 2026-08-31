"""Concrete, independently idempotent RST3-02 restore execution steps."""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hc_data_platform.backup.contracts import (
    ArtifactV1,
    BackupManifestV1,
    PostgreSQLContentEvidenceV1,
    canonical_json_bytes,
)
from hc_data_platform.backup.objects import (
    AgeDecryptedReader,
    ObjectBackupArtifact,
    ObjectBackupVerifier,
    ObjectInventoryArtifact,
    PortableObjectPartArtifact,
    S3ObjectRestoreAdapter,
)
from hc_data_platform.backup.postgresql import (
    DatabaseVerificationEvidence,
    ManifestLogicalDumpArtifact,
    PostgresEndpoint,
    PostgresLogicalBackupAdapter,
    TableContentDigest,
)
from hc_data_platform.backup.repository import (
    RepositoryVerifiedBackup,
    S3LockedBackupRepository,
)
from hc_data_platform.backup.restore import RestorePlanRequestV1, RestorePlanV1
from hc_data_platform.backup.restore_execution import (
    RestoreExecutionCheckpointV1,
    RestoreExecutionError,
    RestoreStepResultV1,
)
from hc_data_platform.backup.temporal import (
    TemporalAdminVisibilityPort,
    TemporalBackupVerifier,
    TemporalPolicyArtifact,
    TemporalPolicySourceArtifact,
    load_temporal_policy_document,
)


class RestoreReadOnlyServicePort(Protocol):
    def ensure_read_only_services(
        self,
        plan: RestorePlanV1,
        request: RestorePlanRequestV1,
    ) -> str: ...


@dataclass(frozen=True)
class RestorePayloadResolver:
    plan: RestorePlanV1
    request: RestorePlanRequestV1
    repository: S3LockedBackupRepository
    public_keys_by_sha256: Mapping[str, Ed25519PublicKey]
    staging_directory: Path
    execution_started_at: datetime

    def resolve(self) -> RepositoryVerifiedBackup:
        payload = self.repository.restore_backup_payload(
            self.plan.backup_id,
            public_keys_by_sha256=self.public_keys_by_sha256,
            staging_directory=self.staging_directory,
        )
        _bind_execution_inputs(
            self.plan,
            self.request,
            payload,
            execution_started_at=self.execution_started_at,
        )
        return payload


class PayloadVerifiedStep:
    def __init__(self, resolver: RestorePayloadResolver) -> None:
        self._resolver = resolver

    def execute(
        self,
        plan: RestorePlanV1,
        checkpoint: RestoreExecutionCheckpointV1 | None,
    ) -> RestoreStepResultV1:
        del checkpoint
        _require_plan(plan, self._resolver.plan)
        payload = self._resolver.resolve()
        evidence = _payload_evidence(payload)
        return RestoreStepResultV1(
            step="payload_verified",
            evidence_sha256=evidence,
            executor_identity="hc-platform:restore-payload",
            target_mutated=False,
        )


class ObjectsRestoredStep:
    def __init__(
        self,
        resolver: RestorePayloadResolver,
        adapter: S3ObjectRestoreAdapter,
        *,
        decryptor: AgeDecryptedReader | None = None,
    ) -> None:
        self._resolver = resolver
        self._adapter = adapter
        self._decryptor = decryptor

    def execute(
        self,
        plan: RestorePlanV1,
        checkpoint: RestoreExecutionCheckpointV1 | None,
    ) -> RestoreStepResultV1:
        del checkpoint
        _require_plan(plan, self._resolver.plan)
        payload = self._resolver.resolve()
        manifest = payload.verified_manifest.manifest
        artifact = _object_artifact(
            manifest,
            payload.artifact_paths,
            decryptor=self._decryptor,
        )
        if plan.object_restore_mode == "reuse_external":
            report = self._adapter.verify_external(
                artifact,
                source_prefix=manifest.object_store.prefix,
                decryptor=self._decryptor,
            )
        else:
            report = self._adapter.restore(
                artifact,
                source_prefix=manifest.object_store.prefix,
                staging_directory=self._resolver.staging_directory,
                decryptor=self._decryptor,
            )
        return RestoreStepResultV1(
            step="objects_restored",
            evidence_sha256=_sha_json(report.model_dump(mode="json")),
            executor_identity="hc-platform:restore-objects",
            target_mutated=(
                False
                if plan.object_restore_mode == "reuse_external"
                else report.object_count > report.resumed_object_count
            ),
        )


class PostgreSQLRestoredStep:
    def __init__(
        self,
        resolver: RestorePayloadResolver,
        adapter: PostgresLogicalBackupAdapter,
        target: PostgresEndpoint,
        *,
        decryptor: AgeDecryptedReader | None = None,
    ) -> None:
        self._resolver = resolver
        self._adapter = adapter
        self._target = target
        self._decryptor = decryptor

    def execute(
        self,
        plan: RestorePlanV1,
        checkpoint: RestoreExecutionCheckpointV1 | None,
    ) -> RestoreStepResultV1:
        del checkpoint
        _require_plan(plan, self._resolver.plan)
        payload = self._resolver.resolve()
        manifest = payload.verified_manifest.manifest
        source = _postgresql_artifact(
            manifest,
            payload.artifact_paths,
            staging=self._resolver.staging_directory,
            decryptor=self._decryptor,
        )
        signed_dump = _artifact_by_path(manifest, manifest.postgresql.dump_path)
        temporary_plaintext = (
            source.path if signed_dump.client_side_encryption == "age_x25519_v1" else None
        )
        try:
            report = self._adapter.restore_manifest_dump_and_verify(
                source,
                self._target,
                staging_directory=self._resolver.staging_directory,
            )
        finally:
            if temporary_plaintext is not None:
                with suppress(FileNotFoundError):
                    temporary_plaintext.unlink()
        return RestoreStepResultV1(
            step="postgresql_restored",
            evidence_sha256=_sha_json(report.model_dump(mode="json")),
            executor_identity="hc-platform:restore-postgresql",
            target_mutated=True,
        )


class TemporalReadyStep:
    def __init__(
        self,
        resolver: RestorePayloadResolver,
        provider: TemporalAdminVisibilityPort,
        *,
        decryptor: AgeDecryptedReader | None = None,
    ) -> None:
        self._resolver = resolver
        self._provider = provider
        self._decryptor = decryptor

    def execute(
        self,
        plan: RestorePlanV1,
        checkpoint: RestoreExecutionCheckpointV1 | None,
    ) -> RestoreStepResultV1:
        del checkpoint
        _require_plan(plan, self._resolver.plan)
        payload = self._resolver.resolve()
        artifact = _temporal_artifact(
            payload.verified_manifest.manifest,
            payload.artifact_paths,
            decryptor=self._decryptor,
        )
        report = asyncio.run(
            TemporalBackupVerifier().verify(
                artifact,
                provider=self._provider,
                decryptor=self._decryptor,
            )
        )
        return RestoreStepResultV1(
            step="temporal_ready",
            evidence_sha256=_sha_json(report.model_dump(mode="json")),
            executor_identity="hc-platform:restore-temporal",
            target_mutated=False,
        )


class ServicesReadOnlyStep:
    def __init__(
        self,
        plan: RestorePlanV1,
        request: RestorePlanRequestV1,
        controller: RestoreReadOnlyServicePort,
    ) -> None:
        self._plan = plan
        self._request = request
        self._controller = controller

    def execute(
        self,
        plan: RestorePlanV1,
        checkpoint: RestoreExecutionCheckpointV1 | None,
    ) -> RestoreStepResultV1:
        del checkpoint
        _require_plan(plan, self._plan)
        evidence = self._controller.ensure_read_only_services(plan, self._request)
        _require_sha(evidence, code="RESTORE_SERVICE_EVIDENCE_INVALID")
        return RestoreStepResultV1(
            step="services_read_only",
            evidence_sha256=evidence,
            executor_identity="hc-platform:restore-services-read-only",
            target_mutated=True,
        )


def _bind_execution_inputs(
    plan: RestorePlanV1,
    request: RestorePlanRequestV1,
    payload: RepositoryVerifiedBackup,
    *,
    execution_started_at: datetime,
) -> None:
    authenticated = payload.verified_manifest
    manifest = authenticated.manifest
    release_sha = _sha_json(manifest.release.model_dump(mode="json"))
    if execution_started_at.utcoffset() != timezone.utc.utcoffset(execution_started_at):
        raise RestoreExecutionError(
            "RESTORE_EXECUTION_INPUT_MISMATCH",
            "restore execution timestamp must be UTC",
        )
    started_at = execution_started_at
    if (
        request.request_id != plan.request_id
        or request.target.operation_id != plan.operation_id
        or request.target.expected_backup_id != plan.backup_id
        or request.target.expected_source_platform_id != plan.source_platform_id
        or request.target.expected_source_environment_id != plan.source_environment_id
        or request.target.target_instance_id != plan.target_instance_id
        or request.target.target_environment_id != plan.target_environment_id
        or request.target_postgresql_database != plan.target_postgresql_database
        or request.object_restore_mode != plan.object_restore_mode
        or request.target_object_store_bucket_reference != plan.target_object_store_bucket_reference
        or request.target_object_store_prefix != plan.target_object_store_prefix
        or manifest.backup_id != plan.backup_id
        or manifest.source_platform_id != plan.source_platform_id
        or manifest.source_environment_id != plan.source_environment_id
        or authenticated.manifest_sha256 != plan.manifest_sha256
        or authenticated.signature_sha256 != plan.signature_sha256
        or manifest.signature.public_key_sha256 != plan.backup_signing_key_sha256
        or release_sha != plan.exact_release_sha256
        or request.readiness.evidence_sha256 != plan.readiness_evidence_sha256
        or not request.target.target_is_empty
        or request.target.temporary_restore_authorization_id is not None
        or not request.readiness.api_writes_disabled
        or not request.readiness.worker_replicas_zero
        or not request.readiness.observed_at <= started_at < request.readiness.valid_until
    ):
        raise RestoreExecutionError(
            "RESTORE_EXECUTION_INPUT_MISMATCH",
            "restore request, plan, and authenticated payload differ",
        )


def _payload_evidence(payload: RepositoryVerifiedBackup) -> str:
    manifest = payload.verified_manifest.manifest
    values = [
        {
            "path": artifact.path,
            "sha256": artifact.sha256,
            "size_bytes": artifact.size_bytes,
        }
        for artifact in manifest.artifacts
    ]
    return _sha_json(
        {
            "manifest_sha256": payload.verified_manifest.manifest_sha256,
            "signature_sha256": payload.verified_manifest.signature_sha256,
            "artifacts": values,
        }
    )


def _object_artifact(
    manifest: BackupManifestV1,
    paths: Mapping[str, Path],
    *,
    decryptor: AgeDecryptedReader | None,
) -> ObjectBackupArtifact:
    signed_inventory = _artifact_by_path(manifest, manifest.object_store.inventory_path)
    mode = "portable" if signed_inventory.client_side_encryption == "age_x25519_v1" else "snapshot"
    inventory = ObjectInventoryArtifact(
        path=_payload_path(paths, signed_inventory.path),
        logical_path=signed_inventory.path,
        mode=mode,
        media_type=signed_inventory.media_type,
        client_side_encryption=signed_inventory.client_side_encryption,
        size_bytes=signed_inventory.size_bytes,
        sha256=signed_inventory.sha256,
        consistency_coordinate=manifest.object_store.consistency_coordinate,
        object_count=manifest.object_store.object_count,
        total_bytes=manifest.object_store.total_bytes,
    )
    if mode == "snapshot":
        if manifest.object_store.portable_part_paths:
            raise RestoreExecutionError(
                "RESTORE_OBJECT_MANIFEST_INVALID",
                "snapshot object evidence unexpectedly names portable parts",
            )
        return ObjectBackupArtifact(inventory=inventory, resumed_part_count=0)

    header, records = ObjectBackupVerifier().inventory_document(
        inventory,
        decryptor=decryptor,
        expected_bucket_reference=manifest.object_store.bucket_reference,
        expected_prefix=manifest.object_store.prefix,
    )
    segments = [
        (record, segment_number, segment)
        for record in records
        for segment_number, segment in enumerate(record.segments)
    ]
    expected_paths = tuple(segment.part_path for _record, _number, segment in segments)
    if expected_paths != manifest.object_store.portable_part_paths:
        raise RestoreExecutionError(
            "RESTORE_OBJECT_MANIFEST_INVALID",
            "portable inventory paths differ from the authenticated manifest",
        )
    parts: list[PortableObjectPartArtifact] = []
    for part_number, (record, segment_number, segment) in enumerate(segments, start=1):
        signed_part = _artifact_by_path(manifest, segment.part_path)
        if (
            signed_part.role != "object_part"
            or signed_part.size_bytes != segment.part_size_bytes
            or signed_part.sha256 != segment.part_sha256
        ):
            raise RestoreExecutionError(
                "RESTORE_OBJECT_MANIFEST_INVALID",
                "portable part evidence differs from its signed artifact",
            )
        parts.append(
            PortableObjectPartArtifact(
                path=_payload_path(paths, signed_part.path),
                logical_path=signed_part.path,
                part_number=part_number,
                object_ordinal=record.ordinal,
                segment_number=segment_number,
                member_name=segment.member_name,
                source_offset=segment.source_offset,
                source_size_bytes=segment.size_bytes,
                source_sha256=segment.sha256,
                size_bytes=signed_part.size_bytes,
                sha256=signed_part.sha256,
                media_type=signed_part.media_type,
                client_side_encryption=signed_part.client_side_encryption,
            )
        )
    assert header.max_part_bytes is not None
    assert header.age_recipient_sha256 is not None
    return ObjectBackupArtifact(
        inventory=inventory,
        parts=tuple(parts),
        resumed_part_count=0,
        max_part_bytes=header.max_part_bytes,
        age_recipient_sha256=header.age_recipient_sha256,
    )


def _postgresql_artifact(
    manifest: BackupManifestV1,
    paths: Mapping[str, Path],
    *,
    staging: Path,
    decryptor: AgeDecryptedReader | None,
) -> ManifestLogicalDumpArtifact:
    postgres = manifest.postgresql
    if postgres.database_name is None or postgres.content_evidence is None:
        raise RestoreExecutionError(
            "RESTORE_POSTGRES_EVIDENCE_MISSING",
            "the authenticated backup predates deterministic restore evidence",
        )
    signed = _artifact_by_path(manifest, postgres.dump_path)
    encrypted = signed.client_side_encryption == "age_x25519_v1"
    if encrypted:
        if decryptor is None:
            raise RestoreExecutionError(
                "RESTORE_POSTGRES_AGE_IDENTITY_REQUIRED",
                "portable PostgreSQL restore requires an age identity",
            )
        path = _decrypt_postgresql_dump(
            _payload_path(paths, signed.path),
            staging=staging,
            decryptor=decryptor,
            expected_sha256=postgres.dump_sha256,
        )
        size = path.stat().st_size
    else:
        path = _payload_path(paths, signed.path)
        if signed.sha256 != postgres.dump_sha256:
            raise RestoreExecutionError(
                "RESTORE_POSTGRES_MANIFEST_INVALID",
                "snapshot dump hash differs from PostgreSQL evidence",
            )
        size = signed.size_bytes
    return ManifestLogicalDumpArtifact(
        path=path,
        size_bytes=size,
        sha256=postgres.dump_sha256,
        server_major=postgres.major_version,
        server_version=postgres.server_version,
        source_database=postgres.database_name,
        source_evidence=_database_evidence(postgres.content_evidence, manifest),
    )


def _database_evidence(
    content: PostgreSQLContentEvidenceV1,
    manifest: BackupManifestV1,
) -> DatabaseVerificationEvidence:
    size = manifest.postgresql.database_size_bytes
    if size is None:
        raise RestoreExecutionError(
            "RESTORE_POSTGRES_EVIDENCE_MISSING",
            "the authenticated backup lacks database size evidence",
        )
    return DatabaseVerificationEvidence(
        database_size_bytes=size,
        migration_count=manifest.postgresql.migration_count,
        migration_sha256=manifest.postgresql.migration_manifest_sha256,
        schema_object_count=content.schema_object_count,
        schema_sha256=content.schema_sha256,
        constraint_count=content.constraint_count,
        constraint_sha256=content.constraint_sha256,
        sequence_count=content.sequence_count,
        sequence_sha256=content.sequence_sha256,
        tables=tuple(
            TableContentDigest(**table.model_dump(mode="python")) for table in content.tables
        ),
        aggregate_sha256=content.aggregate_sha256,
    )


def _decrypt_postgresql_dump(
    encrypted: Path,
    *,
    staging: Path,
    decryptor: AgeDecryptedReader,
    expected_sha256: str,
) -> Path:
    destination = staging / "restore-postgresql.dump"
    temporary = staging / f".{destination.name}.{os.getpid()}.partial"
    for value in (destination, temporary):
        if value.exists() or value.is_symlink():
            raise RestoreExecutionError(
                "RESTORE_POSTGRES_STAGING_CONFLICT",
                "a PostgreSQL plaintext restore artifact already exists",
            )
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        digest = hashlib.sha256()
        size = 0
        with (
            decryptor.open(encrypted, cwd=encrypted.parent) as source,
            os.fdopen(descriptor, "wb", buffering=0) as output,
        ):
            while chunk := source.read(1024 * 1024):
                if not isinstance(chunk, bytes):
                    raise RestoreExecutionError(
                        "RESTORE_POSTGRES_ARTIFACT_INVALID",
                        "the PostgreSQL decryptor returned non-byte content",
                    )
                size += len(chunk)
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size <= 0 or digest.hexdigest() != expected_sha256:
            raise RestoreExecutionError(
                "RESTORE_POSTGRES_ARTIFACT_INVALID",
                "the decrypted PostgreSQL dump differs from signed evidence",
            )
        temporary.replace(destination)
        return destination
    except BaseException:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise


def _temporal_artifact(
    manifest: BackupManifestV1,
    paths: Mapping[str, Path],
    *,
    decryptor: AgeDecryptedReader | None,
) -> TemporalPolicyArtifact:
    signed = _artifact_by_path(manifest, manifest.temporal.policy_path)
    mode = "portable" if signed.client_side_encryption == "age_x25519_v1" else "snapshot"
    source = TemporalPolicySourceArtifact(
        path=_payload_path(paths, signed.path),
        mode=mode,
        media_type=signed.media_type,
        client_side_encryption=signed.client_side_encryption,
        size_bytes=signed.size_bytes,
        sha256=signed.sha256,
    )
    document = load_temporal_policy_document(source, decryptor=decryptor)
    if (
        document.source_environment_id != manifest.source_environment_id
        or document.cluster_reference != manifest.temporal.cluster_reference
        or document.namespace != manifest.temporal.namespace
        or document.service_version != manifest.temporal.service_version
        or document.schedule_inventory_sha256 != manifest.temporal.schedule_inventory_sha256
        or document.open_workflow_inventory_sha256
        != manifest.temporal.open_workflow_inventory_sha256
    ):
        raise RestoreExecutionError(
            "RESTORE_TEMPORAL_MANIFEST_INVALID",
            "Temporal policy differs from authenticated manifest evidence",
        )
    return TemporalPolicyArtifact(
        **source.model_dump(mode="python"),
        schedule_count=len(document.schedules),
        open_workflow_count=len(document.open_workflows),
        schedule_inventory_sha256=document.schedule_inventory_sha256,
        open_workflow_inventory_sha256=document.open_workflow_inventory_sha256,
        consistency_coordinate=document.consistency_coordinate,
    )


def _artifact_by_path(manifest: BackupManifestV1, path: str) -> ArtifactV1:
    matches = [artifact for artifact in manifest.artifacts if artifact.path == path]
    if len(matches) != 1:
        raise RestoreExecutionError(
            "RESTORE_ARTIFACT_MANIFEST_INVALID",
            "an authenticated domain artifact is absent or duplicated",
        )
    return matches[0]


def _payload_path(paths: Mapping[str, Path], logical_path: str) -> Path:
    path = paths.get(logical_path)
    if path is None:
        raise RestoreExecutionError(
            "RESTORE_ARTIFACT_MISSING", "a verified restore artifact path is absent"
        )
    return path


def _require_plan(observed: RestorePlanV1, expected: RestorePlanV1) -> None:
    if observed != expected:
        raise RestoreExecutionError(
            "RESTORE_EXECUTION_PLAN_MISMATCH", "restore step received another plan"
        )


def _require_sha(value: str, *, code: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise RestoreExecutionError(code, "restore evidence digest is invalid")


def _sha_json(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
