"""Kubernetes Job CLI for whole-platform backup create/list/verify operations."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol, cast

from cryptography.hazmat.primitives import serialization
from pydantic import BaseModel

from hc_data_platform.backup.catalog import (
    BackupCatalogAuditContext,
    BackupCatalogError,
    BackupCatalogService,
    PostgresBackupCatalogRepository,
)
from hc_data_platform.backup.contracts import (
    BackupContractError,
    ManifestSigningPort,
    canonical_json_bytes,
    find_plaintext_secret_material,
)
from hc_data_platform.backup.dependencies import (
    ConfigurationBackupArtifact,
    ConfigurationBackupError,
)
from hc_data_platform.backup.dependency_cli import run as run_configuration_stage
from hc_data_platform.backup.object_cli import run as run_object_stage
from hc_data_platform.backup.objects import (
    AgeCliDecryptor,
    AgeCliEncryptor,
    AgeToolchain,
    ObjectBackupArtifact,
    ObjectBackupError,
)
from hc_data_platform.backup.planned_migration import PlannedMigrationError
from hc_data_platform.backup.postgresql import LogicalDumpArtifact, PostgresBackupError
from hc_data_platform.backup.postgresql_cli import run as run_postgresql_stage
from hc_data_platform.backup.repository import (
    BackupRepositoryError,
    FunctionalFileBackupRepository,
    FunctionalFileRepositoryConfig,
    LockedRepositoryConfig,
    S3LockedBackupRepository,
)
from hc_data_platform.backup.restore import RestorePlanError
from hc_data_platform.backup.restore_execution import RestoreExecutionError
from hc_data_platform.backup.restore_reconciliation import RestoreReconciliationError
from hc_data_platform.backup.signing import (
    FunctionalFileEd25519Signer,
    VaultSigningError,
    VaultTransitEd25519Signer,
    VaultTransitSignerConfig,
)
from hc_data_platform.backup.temporal import TemporalBackupError, TemporalPolicyArtifact
from hc_data_platform.backup.temporal_cli import run as run_temporal_stage
from hc_data_platform.backup.whole import (
    VerificationDomain,
    WholeBackupError,
    WholeBackupPlanV1,
    WholeVerificationEvidence,
    assemble_whole_backup,
    load_domain_receipt,
    load_whole_backup_plan,
    policy_verification_evidence,
    prepare_postgresql_artifact,
    publish_whole_backup,
    verification_evidence,
    verify_published_whole_backup,
)

_STAGE_RUNNERS: Mapping[str, Callable[[Sequence[str] | None], int]] = {
    "postgresql": run_postgresql_stage,
    "objects": run_object_stage,
    "configuration": run_configuration_stage,
    "temporal": run_temporal_stage,
}


class StageInvoker(Protocol):
    def invoke(self, component: str, argv: Sequence[str]) -> Mapping[str, object]: ...


class InProcessStageInvoker:
    """Invoke existing domain CLIs without a shell or credential-bearing argv."""

    def invoke(self, component: str, argv: Sequence[str]) -> Mapping[str, object]:
        runner = _STAGE_RUNNERS.get(component)
        if runner is None:
            raise WholeBackupError(
                "BACKUP_WHOLE_STAGE_INVALID", "an unknown domain backup stage was requested"
            )
        output = io.StringIO()
        with redirect_stdout(output):
            result = runner(argv)
        if result != 0:
            raise WholeBackupError(
                "BACKUP_WHOLE_STAGE_FAILED", "a domain backup stage did not complete"
            )
        raw = output.getvalue().encode("utf-8")
        if not raw or len(raw) > 1024 * 1024:
            raise WholeBackupError(
                "BACKUP_WHOLE_STAGE_RESPONSE_INVALID",
                "a domain backup stage returned an invalid summary",
            )
        try:
            value = json.loads(raw, object_pairs_hook=_unique_json_object)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise WholeBackupError(
                "BACKUP_WHOLE_STAGE_RESPONSE_INVALID",
                "a domain backup stage returned a malformed summary",
            ) from exc
        if not isinstance(value, dict) or value.get("status") not in {"created", "verified"}:
            raise WholeBackupError(
                "BACKUP_WHOLE_STAGE_RESPONSE_INVALID",
                "a domain backup stage returned an unexpected summary",
            )
        if find_plaintext_secret_material(value):
            raise WholeBackupError(
                "BACKUP_WHOLE_STAGE_RESPONSE_INVALID",
                "a domain backup stage returned Secret-shaped summary material",
            )
        return value


def _environment(name: str, *, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or not value or any(char in value for char in ("\x00", "\r", "\n")):
        raise WholeBackupError(
            "BACKUP_WHOLE_CONFIGURATION_INVALID",
            "a required whole-backup Job setting is absent or invalid",
        )
    return value


def _integer(name: str, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(_environment(name, default=str(default)))
    except ValueError as exc:
        raise WholeBackupError(
            "BACKUP_WHOLE_CONFIGURATION_INVALID",
            "a whole-backup Job integer setting is invalid",
        ) from exc
    if not minimum <= value <= maximum:
        raise WholeBackupError(
            "BACKUP_WHOLE_CONFIGURATION_INVALID",
            "a whole-backup Job integer setting is outside its bound",
        )
    return value


def _number(name: str, *, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(_environment(name, default=str(default)))
    except ValueError as exc:
        raise WholeBackupError(
            "BACKUP_WHOLE_CONFIGURATION_INVALID",
            "a whole-backup Job numeric setting is invalid",
        ) from exc
    if not minimum <= value <= maximum:
        raise WholeBackupError(
            "BACKUP_WHOLE_CONFIGURATION_INVALID",
            "a whole-backup Job numeric setting is outside its bound",
        )
    return value


def _boolean(name: str, *, default: bool) -> bool:
    value = _environment(name, default="true" if default else "false").lower()
    if value not in {"true", "false"}:
        raise WholeBackupError(
            "BACKUP_WHOLE_CONFIGURATION_INVALID",
            "a whole-backup Job Boolean setting is invalid",
        )
    return value == "true"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hc-platform")
    groups = parser.add_subparsers(dest="group", required=True)
    backup = groups.add_parser("backup")
    commands = backup.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create")
    create.add_argument("--plan", type=Path, required=True)
    create.add_argument("--staging-directory", type=Path, required=True)
    create.add_argument("--helm-rendered-manifest", type=Path, required=True)

    listing = commands.add_parser("list")
    listing.add_argument("--source-environment-id")
    listing.add_argument(
        "--status",
        choices=("CREATING", "CREATED", "INTEGRITY_VERIFIED", "RESTORE_VERIFIED", "FAILED"),
    )
    listing.add_argument("--cursor")
    listing.add_argument("--limit", type=int, default=50)

    verify = commands.add_parser("verify")
    verify.add_argument("--plan", type=Path, required=True)
    verify.add_argument("--operation-id", required=True)
    verify.add_argument("--verified-at", required=True)
    verify.add_argument("--staging-directory", type=Path, required=True)

    restore = groups.add_parser("restore")
    restore_commands = restore.add_subparsers(dest="command", required=True)
    plan = restore_commands.add_parser("plan")
    plan.add_argument("backup_id")
    plan.add_argument("--target", required=True)
    plan.add_argument("--backup-plan", type=Path, required=True)
    plan.add_argument("--target-document", type=Path, required=True)
    plan.add_argument("--staging-directory", type=Path, required=True)
    plan.add_argument("--dry-run", action="store_true")
    execute = restore_commands.add_parser("execute")
    execute.add_argument("backup_id")
    execute.add_argument("--target", required=True)
    execute.add_argument("--backup-plan", type=Path, required=True)
    execute.add_argument("--target-document", type=Path, required=True)
    execute.add_argument("--restore-plan", type=Path, required=True)
    execute.add_argument("--approval", type=Path, required=True)
    execute.add_argument("--approval-signature", type=Path, required=True)
    execute.add_argument("--checkpoint", type=Path, required=True)
    execute.add_argument("--staging-directory", type=Path, required=True)
    reconcile = restore_commands.add_parser("reconcile")
    reconcile.add_argument("backup_id")
    reconcile.add_argument("--target", required=True)
    reconcile.add_argument("--backup-plan", type=Path, required=True)
    reconcile.add_argument("--target-document", type=Path, required=True)
    reconcile.add_argument("--restore-plan", type=Path, required=True)
    reconcile.add_argument("--checkpoint", type=Path, required=True)
    reconcile.add_argument("--staging-directory", type=Path, required=True)
    reconcile.add_argument("--report", type=Path, required=True)
    reconcile.add_argument("--reconciled-at", required=True)

    migration = groups.add_parser("migration")
    migration_commands = migration.add_subparsers(dest="command", required=True)
    migration_plan = migration_commands.add_parser("plan")
    migration_plan.add_argument("--document", type=Path, required=True)
    migration_plan.add_argument("--validated-at", required=True)
    migration_advance = migration_commands.add_parser("advance")
    migration_advance.add_argument("--plan", type=Path, required=True)
    migration_advance.add_argument("--approval", type=Path, required=True)
    migration_advance.add_argument("--approval-signature", type=Path, required=True)
    migration_advance.add_argument("--observation", type=Path, required=True)
    migration_advance.add_argument("--observation-signature", type=Path, required=True)
    migration_advance.add_argument("--checkpoint", type=Path, required=True)
    migration_advance.add_argument("--execution-owner-id", required=True)
    migration_advance.add_argument("--fencing-token", type=int, required=True)
    migration_advance.add_argument("--advanced-at", required=True)
    migration_status = migration_commands.add_parser("status")
    migration_status.add_argument("--checkpoint", type=Path, required=True)
    return parser


def _assert_plan_binding(plan: WholeBackupPlanV1) -> None:
    if (
        _environment("HC_BACKUP_POSTGRES_OPERATION_ID") != plan.operation_id
        or _environment("HC_BACKUP_POSTGRES_ENVIRONMENT_ID") != plan.source_environment_id
    ):
        raise WholeBackupError(
            "BACKUP_WHOLE_PLAN_BINDING_MISMATCH",
            "the immutable backup plan differs from the maintenance fence",
        )


def _runtime_profile() -> str:
    profile = _environment("HC_BACKUP_RUNTIME_PROFILE", default="production")
    if profile not in {"production", "compose_functional"}:
        raise WholeBackupError(
            "BACKUP_WHOLE_CONFIGURATION_INVALID", "the backup runtime profile is invalid"
        )
    return profile


def _repository(plan: WholeBackupPlanV1) -> S3LockedBackupRepository:
    if _runtime_profile() == "compose_functional":
        try:
            repository = FunctionalFileBackupRepository(
                FunctionalFileRepositoryConfig(
                    repository_id=plan.backup_repository.repository_id,
                    provider=plan.backup_repository.provider,
                    bucket_reference=plan.backup_repository.bucket_reference,
                    kms_key_reference=plan.backup_repository.kms_key_reference,
                    retention_until=plan.backup_repository.retention_until,
                    cross_site_replica_reference=(
                        plan.backup_repository.cross_site_replica_reference
                    ),
                    root_directory=Path(_environment("HC_BACKUP_FUNCTIONAL_REPOSITORY_DIRECTORY")),
                    run_id=_environment("HC_BACKUP_FUNCTIONAL_RUN_ID"),
                )
            )
        except ValueError as exc:
            raise WholeBackupError(
                "BACKUP_WHOLE_CONFIGURATION_INVALID",
                "the functional repository configuration is invalid",
            ) from exc
        return cast(S3LockedBackupRepository, repository)
    try:
        config = LockedRepositoryConfig(
            repository_id=plan.backup_repository.repository_id,
            provider=plan.backup_repository.provider,
            bucket=_environment("HC_BACKUP_REPOSITORY_BUCKET"),
            bucket_reference=plan.backup_repository.bucket_reference,
            prefix=_environment("HC_BACKUP_REPOSITORY_PREFIX", default="whole-platform-backups"),
            kms_key_reference=plan.backup_repository.kms_key_reference,
            provider_kms_key_id=_environment("HC_BACKUP_REPOSITORY_KMS_KEY_ID"),
            provider_kms_observed_key_id=_environment("HC_BACKUP_REPOSITORY_KMS_OBSERVED_KEY_ID"),
            provider_replica_destination=_environment("HC_BACKUP_REPOSITORY_REPLICA_DESTINATION"),
            retention_until=plan.backup_repository.retention_until,
            cross_site_replica_reference=(plan.backup_repository.cross_site_replica_reference),
            multipart_threshold_bytes=_integer(
                "HC_BACKUP_REPOSITORY_MULTIPART_THRESHOLD_BYTES",
                default=64 * 1024 * 1024,
                minimum=5 * 1024 * 1024,
                maximum=5 * 1024 * 1024 * 1024,
            ),
            multipart_part_bytes=_integer(
                "HC_BACKUP_REPOSITORY_MULTIPART_PART_BYTES",
                default=64 * 1024 * 1024,
                minimum=5 * 1024 * 1024,
                maximum=5 * 1024 * 1024 * 1024,
            ),
            send_small_object_checksum=_boolean(
                "HC_BACKUP_REPOSITORY_SEND_SMALL_OBJECT_CHECKSUM",
                default=True,
            ),
        )
    except ValueError as exc:
        raise WholeBackupError(
            "BACKUP_WHOLE_CONFIGURATION_INVALID",
            "the locked backup repository configuration is invalid",
        ) from exc
    import boto3
    from botocore.config import Config

    endpoint = os.environ.get("HC_BACKUP_REPOSITORY_ENDPOINT")
    if endpoint is not None and any(char in endpoint for char in ("\x00", "\r", "\n")):
        raise WholeBackupError(
            "BACKUP_WHOLE_CONFIGURATION_INVALID",
            "the backup repository endpoint is invalid",
        )
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=_environment("HC_BACKUP_REPOSITORY_REGION", default="us-east-1"),
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 1, "mode": "standard"},
            s3={
                "addressing_style": _environment(
                    "HC_BACKUP_REPOSITORY_ADDRESSING_STYLE", default="path"
                )
            },
        ),
    )
    return S3LockedBackupRepository(client, config)


def _signer(plan: WholeBackupPlanV1) -> ManifestSigningPort:
    if _runtime_profile() == "compose_functional":
        signer = FunctionalFileEd25519Signer(
            Path(_environment("HC_BACKUP_FUNCTIONAL_SIGNING_KEY_FILE")),
            key_reference=plan.secrets_and_kms.signing_key_reference,
            run_id=_environment("HC_BACKUP_FUNCTIONAL_RUN_ID"),
        )
        return signer
    return VaultTransitEd25519Signer(
        VaultTransitSignerConfig(
            endpoint_url=_environment("HC_BACKUP_SIGNER_VAULT_ENDPOINT"),
            token=_environment("HC_BACKUP_SIGNER_VAULT_TOKEN"),
            provider_reference=_environment("HC_BACKUP_SIGNER_PROVIDER_REFERENCE"),
            transit_mount=_environment("HC_BACKUP_SIGNER_TRANSIT_MOUNT", default="transit"),
            key_name=_environment("HC_BACKUP_SIGNER_KEY_NAME"),
            key_version=_integer(
                "HC_BACKUP_SIGNER_KEY_VERSION",
                default=0,
                minimum=1,
                maximum=2**31 - 1,
            ),
            key_reference=plan.secrets_and_kms.signing_key_reference,
            namespace=os.environ.get("HC_BACKUP_SIGNER_VAULT_NAMESPACE"),
            timeout_seconds=_number(
                "HC_BACKUP_SIGNER_TIMEOUT_SECONDS",
                default=10,
                minimum=0.1,
                maximum=120,
            ),
        )
    )


def _catalog(
    plan: WholeBackupPlanV1,
    signer: ManifestSigningPort,
) -> BackupCatalogService:
    public_bytes = signer.public_key.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    fingerprint = hashlib.sha256(public_bytes).hexdigest()
    return BackupCatalogService(
        PostgresBackupCatalogRepository.from_dsn(_environment("HC_BACKUP_CATALOG_DSN")),
        trusted_signing_key_sha256=frozenset({fingerprint}),
        trusted_repository_ids=frozenset({plan.backup_repository.repository_id}),
    )


def _load_receipt(path: Path, model: type[BaseModel]) -> BaseModel:
    return load_domain_receipt(path, model)


def _create(
    plan_path: Path,
    staging: Path,
    helm_rendered: Path,
    *,
    invoker: StageInvoker,
) -> Mapping[str, object]:
    plan = load_whole_backup_plan(plan_path)
    _assert_plan_binding(plan)
    staging = staging.resolve(strict=True)
    mode_command = f"{plan.mode}-create"
    receipts = {
        "postgresql": staging / "postgresql-logical-receipt.json",
        "objects": staging / f"object-{plan.mode}-receipt.json",
        "configuration": staging / f"configuration-{plan.mode}-receipt.json",
        "temporal": staging / f"temporal-{plan.mode}-receipt.json",
    }
    create_argv = {
        "postgresql": (
            "logical-create",
            "--staging-directory",
            str(staging),
            "--artifact-name",
            "postgresql.dump",
            "--receipt-name",
            receipts["postgresql"].name,
        ),
        "objects": (
            mode_command,
            "--staging-directory",
            str(staging),
            "--receipt-name",
            receipts["objects"].name,
        ),
        "configuration": (
            mode_command,
            "--helm-rendered-manifest",
            str(helm_rendered),
            "--staging-directory",
            str(staging),
            "--receipt-name",
            receipts["configuration"].name,
        ),
        "temporal": (
            mode_command,
            "--staging-directory",
            str(staging),
            "--receipt-name",
            receipts["temporal"].name,
        ),
    }
    for component in ("postgresql", "objects", "configuration", "temporal"):
        if not receipts[component].exists() and not receipts[component].is_symlink():
            invoker.invoke(component, create_argv[component])

    postgresql = cast(
        LogicalDumpArtifact,
        _load_receipt(receipts["postgresql"], LogicalDumpArtifact),
    )
    objects = cast(
        ObjectBackupArtifact,
        _load_receipt(receipts["objects"], ObjectBackupArtifact),
    )
    configuration = cast(
        ConfigurationBackupArtifact,
        _load_receipt(receipts["configuration"], ConfigurationBackupArtifact),
    )
    temporal = cast(
        TemporalPolicyArtifact,
        _load_receipt(receipts["temporal"], TemporalPolicyArtifact),
    )
    object_summary = invoker.invoke("objects", ("verify", "--receipt", str(receipts["objects"])))
    configuration_summary = invoker.invoke(
        "configuration",
        ("verify", "--receipt", str(receipts["configuration"])),
    )
    temporal_summary = invoker.invoke(
        "temporal", ("verify", "--receipt", str(receipts["temporal"]))
    )
    postgresql_summary: Mapping[str, object] = {
        "status": "verified",
        "archive_sha256": postgresql.sha256,
        "source_aggregate_sha256": postgresql.source_evidence.aggregate_sha256,
        "server_major": postgresql.server_major,
    }
    repository = _repository(plan)
    repository.preflight()
    signer = _signer(plan)
    encryptor = None
    decryptor = None
    if plan.mode == "portable":
        toolchain = AgeToolchain(
            age=(_environment("HC_BACKUP_AGE_BINARY", default="age"),),
            required_version=_environment("HC_BACKUP_AGE_REQUIRED_VERSION", default="1.3.1"),
        )
        timeout = _integer(
            "HC_BACKUP_COMMAND_TIMEOUT_SECONDS",
            default=7_200,
            minimum=1,
            maximum=86_400,
        )
        encryptor = AgeCliEncryptor(
            _environment("HC_BACKUP_WHOLE_AGE_RECIPIENT"),
            toolchain,
            command_timeout_seconds=timeout,
        )
        decryptor = AgeCliDecryptor(
            Path(_environment("HC_BACKUP_AGE_IDENTITY_FILE")),
            toolchain,
            command_timeout_seconds=timeout,
        )
    prepared_postgresql = prepare_postgresql_artifact(
        plan,
        postgresql,
        staging_directory=staging,
        encryptor=encryptor,
        decryptor=decryptor,
    )
    evidence: list[WholeVerificationEvidence] = [
        verification_evidence(
            "postgresql_business",
            receipt_path=receipts["postgresql"],
            verification_summary=postgresql_summary,
            verifier_identity=plan.verifier_identity,
            verified_at=plan.completed_at,
        ),
        verification_evidence(
            "object_store",
            receipt_path=receipts["objects"],
            verification_summary=object_summary,
            verifier_identity=plan.verifier_identity,
            verified_at=plan.completed_at,
        ),
        verification_evidence(
            "deployment_configuration",
            receipt_path=receipts["configuration"],
            verification_summary=configuration_summary,
            verifier_identity=plan.verifier_identity,
            verified_at=plan.completed_at,
        ),
        verification_evidence(
            "secrets_and_kms",
            receipt_path=receipts["configuration"],
            verification_summary=configuration_summary,
            verifier_identity=plan.verifier_identity,
            verified_at=plan.completed_at,
        ),
        verification_evidence(
            "temporal_workflows",
            receipt_path=receipts["temporal"],
            verification_summary=temporal_summary,
            verifier_identity=plan.verifier_identity,
            verified_at=plan.completed_at,
        ),
    ]
    evidence.extend(_policy_evidence(plan))
    assembly = assemble_whole_backup(
        plan,
        postgresql_receipt=postgresql,
        postgresql_upload=prepared_postgresql,
        object_receipt=objects,
        configuration_receipt=configuration,
        temporal_receipt=temporal,
        verification=evidence,
        signer=signer,
        staging_directory=staging,
    )
    catalog = _catalog(plan, signer)
    with TemporaryDirectory(prefix="repository-verification-", dir=staging) as temporary:
        result = publish_whole_backup(
            assembly,
            repository=repository,
            catalog=catalog,
            audit=BackupCatalogAuditContext(
                actor_id=plan.verifier_identity,
                request_id=plan.operation_id,
            ),
            repository_checkpoint_path=staging / "repository-upload-checkpoint.json",
            verification_staging_directory=Path(temporary),
        )
    return {
        "status": "created",
        "backup_id": plan.backup_id,
        "backup_status": result.catalog_record.current_status,
        "manifest_sha256": assembly.signed_manifest.manifest_sha256,
        "repository_id": plan.backup_repository.repository_id,
        "resumed_object_count": sum(item.resumed for item in result.stored_objects),
        "stored_object_count": len(result.stored_objects),
    }


def _policy_evidence(plan: WholeBackupPlanV1) -> tuple[WholeVerificationEvidence, ...]:
    values: tuple[tuple[VerificationDomain, Mapping[str, object], str], ...] = (
        (
            "release_artifacts",
            plan.release.model_dump(mode="json"),
            hashlib.sha256(canonical_json_bytes(plan.release.model_dump(mode="json"))).hexdigest(),
        ),
        (
            "backup_repository",
            plan.backup_repository.model_dump(mode="json"),
            hashlib.sha256(
                canonical_json_bytes(plan.backup_repository.model_dump(mode="json"))
            ).hexdigest(),
        ),
        (
            "runtime_logs",
            {"source_environment_id": plan.source_environment_id},
            plan.external_evidence.runtime_logs_evidence_sha256,
        ),
        (
            "external_dependencies",
            {"source_environment_id": plan.source_environment_id},
            plan.external_evidence.external_dependencies_evidence_sha256,
        ),
    )
    return tuple(
        policy_verification_evidence(
            domain,
            policy=policy,
            evidence_sha256=digest,
            verifier_identity=plan.verifier_identity,
            verified_at=plan.completed_at,
        )
        for domain, policy, digest in values
    )


def _list_backups(arguments: argparse.Namespace) -> Mapping[str, object]:
    source_environment_id = arguments.source_environment_id or _environment(
        "HC_BACKUP_SOURCE_ENVIRONMENT_ID"
    )
    repository = PostgresBackupCatalogRepository.from_dsn(_environment("HC_BACKUP_CATALOG_DSN"))
    service = BackupCatalogService(
        repository,
        trusted_signing_key_sha256=frozenset({"0" * 64}),
        trusted_repository_ids=frozenset({"catalog-query-only"}),
    )
    page = service.list_page(
        source_environment_id=source_environment_id,
        status=cast(Any, arguments.status),
        cursor=arguments.cursor,
        limit=arguments.limit,
    )
    return {"status": "listed", **page.model_dump(mode="json")}


def _parse_utc_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WholeBackupError(
            "BACKUP_WHOLE_VERIFICATION_INVALID", "the verification timestamp is invalid"
        ) from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise WholeBackupError(
            "BACKUP_WHOLE_VERIFICATION_INVALID", "the verification timestamp must be UTC"
        )
    return parsed


def _verify(
    plan_path: Path,
    *,
    operation_id: str,
    verified_at: str,
    staging: Path,
) -> Mapping[str, object]:
    plan = load_whole_backup_plan(plan_path)
    repository = _repository(plan)
    signer = _signer(plan)
    catalog = _catalog(plan, signer)
    staging = staging.resolve(strict=True)
    verifier_identity = _environment("HC_BACKUP_VERIFIER_IDENTITY", default=plan.verifier_identity)
    with TemporaryDirectory(prefix="repository-reverification-", dir=staging) as temporary:
        record = verify_published_whole_backup(
            plan,
            operation_id=operation_id,
            verifier_identity=verifier_identity,
            public_key=signer.public_key,
            repository=repository,
            catalog=catalog,
            audit=BackupCatalogAuditContext(
                actor_id=verifier_identity,
                request_id=operation_id,
            ),
            verification_staging_directory=Path(temporary),
            verified_at=_parse_utc_timestamp(verified_at),
        )
    return {
        "status": "verified",
        "backup_id": plan.backup_id,
        "backup_status": record.current_status,
        "operation_id": operation_id,
        "repository_id": plan.backup_repository.repository_id,
    }


def run(
    argv: Sequence[str] | None = None,
    *,
    invoker: StageInvoker | None = None,
) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.group == "migration":
        from hc_data_platform.backup import migration_cli

        if arguments.command == "plan":
            result = migration_cli.validate_plan(
                plan_path=arguments.document,
                validated_at=arguments.validated_at,
            )
        elif arguments.command == "advance":
            result = migration_cli.advance(
                plan_path=arguments.plan,
                approval_path=arguments.approval,
                approval_signature_path=arguments.approval_signature,
                observation_path=arguments.observation,
                observation_signature_path=arguments.observation_signature,
                checkpoint_path=arguments.checkpoint,
                execution_owner_id=arguments.execution_owner_id,
                fencing_token=arguments.fencing_token,
                advanced_at=arguments.advanced_at,
            )
        else:
            result = migration_cli.status(checkpoint_path=arguments.checkpoint)
    elif arguments.group == "restore":
        if arguments.command == "plan":
            from hc_data_platform.backup.restore_cli import plan as restore_plan

            result = restore_plan(
                backup_id=arguments.backup_id,
                target_environment=arguments.target,
                backup_plan_path=arguments.backup_plan,
                target_document_path=arguments.target_document,
                staging_directory=arguments.staging_directory,
                dry_run=arguments.dry_run,
            )
        elif arguments.command == "execute":
            from hc_data_platform.backup.restore_cli import execute as restore_execute

            result = restore_execute(
                backup_id=arguments.backup_id,
                target_environment=arguments.target,
                backup_plan_path=arguments.backup_plan,
                target_document_path=arguments.target_document,
                restore_plan_path=arguments.restore_plan,
                approval_path=arguments.approval,
                approval_signature_path=arguments.approval_signature,
                checkpoint_path=arguments.checkpoint,
                staging_directory=arguments.staging_directory,
            )
        else:
            from hc_data_platform.backup.restore_cli import reconcile as restore_reconcile

            result = restore_reconcile(
                backup_id=arguments.backup_id,
                target_environment=arguments.target,
                backup_plan_path=arguments.backup_plan,
                target_document_path=arguments.target_document,
                restore_plan_path=arguments.restore_plan,
                checkpoint_path=arguments.checkpoint,
                staging_directory=arguments.staging_directory,
                report_path=arguments.report,
                reconciled_at=arguments.reconciled_at,
            )
    elif arguments.command == "create":
        result = _create(
            arguments.plan,
            arguments.staging_directory,
            arguments.helm_rendered_manifest,
            invoker=invoker or InProcessStageInvoker(),
        )
    elif arguments.command == "list":
        result = _list_backups(arguments)
    else:
        result = _verify(
            arguments.plan,
            operation_id=arguments.operation_id,
            verified_at=arguments.verified_at,
            staging=arguments.staging_directory,
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except (
        BackupCatalogError,
        BackupContractError,
        BackupRepositoryError,
        ConfigurationBackupError,
        ObjectBackupError,
        PostgresBackupError,
        TemporalBackupError,
        VaultSigningError,
        RestorePlanError,
        RestoreExecutionError,
        RestoreReconciliationError,
        PlannedMigrationError,
        WholeBackupError,
    ) as exc:
        print(
            json.dumps(
                {"status": "failed", "code": exc.code},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    except Exception:
        print('{"code":"BACKUP_WHOLE_INTERNAL","status":"failed"}', file=sys.stderr)
        raise SystemExit(3) from None


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


if __name__ == "__main__":
    main()
