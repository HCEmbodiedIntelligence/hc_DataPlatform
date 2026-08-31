"""Runtime wiring for ``hc-platform restore plan``."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import stat
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ValidationError

from hc_data_platform.backup.objects import (
    AgeCliDecryptor,
    AgeToolchain,
    S3ObjectRestoreAdapter,
)
from hc_data_platform.backup.postgresql import (
    PostgresEndpoint,
    PostgresLogicalBackupAdapter,
    compose_functional_postgres_toolchain,
)
from hc_data_platform.backup.restore import (
    CompositeRestoreTargetInspector,
    PostgreSQLRestoreTargetAdapter,
    RestorePlanError,
    RestorePlanRequestV1,
    S3RestoreTargetAdapter,
    create_restore_plan,
    load_restore_plan,
    load_restore_plan_request,
)
from hc_data_platform.backup.restore_execution import (
    FileRestoreCheckpointStore,
    RestoreExecutionError,
    execute_restore,
    verify_restore_approval_files,
)
from hc_data_platform.backup.restore_reconciliation import (
    RestoreReconciliationError,
    publish_restore_reconciliation_report,
    reconcile_restore,
)
from hc_data_platform.backup.restore_reconciliation_adapters import (
    ReadOnlyRuntimeInspector,
    RestoredObjectInventoryInspector,
    TemporalWorkflowInspector,
    authenticated_object_records,
    failed_postgresql_reconciliation,
    inspect_postgresql_restore,
    reconciliation_inspector_map,
)
from hc_data_platform.backup.restore_runtime import (
    ComposeRestoreRuntime,
    ComposeRestoreRuntimeConfig,
    KubernetesRestoreRuntime,
    KubernetesRestoreRuntimeConfig,
    PostgresRestoreTargetFence,
)
from hc_data_platform.backup.restore_steps import (
    ObjectsRestoredStep,
    PayloadVerifiedStep,
    PostgreSQLRestoredStep,
    RestorePayloadResolver,
    ServicesReadOnlyStep,
    TemporalReadyStep,
    _database_evidence,
    _object_artifact,
    _temporal_artifact,
)
from hc_data_platform.backup.temporal import (
    TemporalConnectionConfig,
    TemporalSdkAdminAdapter,
    load_temporal_policy_document,
)
from hc_data_platform.backup.whole import load_whole_backup_plan


def _environment(name: str, *, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or not value or any(char in value for char in ("\x00", "\r", "\n")):
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "a required restore setting is absent or invalid"
        )
    return value


def _integer(name: str, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(_environment(name, default=str(default)))
    except ValueError as exc:
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "a restore integer setting is invalid"
        ) from exc
    if not minimum <= value <= maximum:
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "a restore integer setting is outside its bound"
        )
    return value


def _number(name: str, *, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(_environment(name, default=str(default)))
    except ValueError as exc:
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "a restore numeric setting is invalid"
        ) from exc
    if not minimum <= value <= maximum:
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "a restore numeric setting is outside its bound"
        )
    return value


def _boolean(name: str, *, default: bool) -> bool:
    value = _environment(name, default="true" if default else "false").lower()
    if value not in {"true", "false"}:
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "a restore Boolean setting is invalid"
        )
    return value == "true"


def _bounded_file(path: Path, *, maximum_bytes: int, private: bool) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "a restore runtime file is unavailable"
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or not 0 < info.st_size <= maximum_bytes
        or (private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077))
    ):
        raise RestorePlanError("RESTORE_CONFIGURATION_INVALID", "a restore runtime file is unsafe")
    return resolved.read_bytes()


def _postgresql_endpoint(database: str) -> PostgresEndpoint:
    configured_database = _environment("HC_RESTORE_POSTGRES_DATABASE")
    if configured_database != database:
        raise RestorePlanError(
            "RESTORE_POSTGRES_TARGET_MISMATCH",
            "the configured PostgreSQL database differs from the explicit target",
        )
    try:
        return PostgresEndpoint(
            host=_environment("HC_RESTORE_POSTGRES_HOST"),
            port=_integer("HC_RESTORE_POSTGRES_PORT", default=5432, minimum=1, maximum=65535),
            database=configured_database,
            username=_environment("HC_RESTORE_POSTGRES_USERNAME"),
            password=_environment("HC_RESTORE_POSTGRES_PASSWORD"),
            sslmode=_environment("HC_RESTORE_POSTGRES_SSLMODE", default="verify-full"),
        )
    except (ValueError, ValidationError) as exc:
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "the restore PostgreSQL endpoint is invalid"
        ) from exc


def _target_object_store(request: RestorePlanRequestV1) -> S3RestoreTargetAdapter:
    bucket_reference = request.target_object_store_bucket_reference
    prefix = request.target_object_store_prefix
    if (
        _environment("HC_RESTORE_OBJECT_STORE_BUCKET_REFERENCE") != bucket_reference
        or _environment("HC_RESTORE_OBJECT_STORE_PREFIX") != prefix
    ):
        raise RestorePlanError(
            "RESTORE_OBJECT_STORE_TARGET_MISMATCH",
            "the configured object target differs from the explicit target",
        )
    return S3RestoreTargetAdapter(
        cast(Any, _restore_s3_client("HC_RESTORE_OBJECT_STORE")),
        bucket=_environment("HC_RESTORE_OBJECT_STORE_BUCKET"),
        bucket_reference=bucket_reference,
        prefix=prefix,
    )


def _restore_s3_client(prefix: str) -> object:
    import boto3
    from botocore.config import Config

    endpoint = os.environ.get(f"{prefix}_ENDPOINT")
    if endpoint is not None and any(char in endpoint for char in ("\x00", "\r", "\n")):
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "a restore object-store endpoint is invalid"
        )
    access_key = os.environ.get(f"{prefix}_ACCESS_KEY")
    secret_key = os.environ.get(f"{prefix}_SECRET_KEY")
    if (access_key is None) != (secret_key is None):
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "restore object-store credentials are incomplete"
        )
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=_environment(f"{prefix}_REGION", default="us-east-1"),
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 1, "mode": "standard"},
            s3={"addressing_style": _environment(f"{prefix}_ADDRESSING_STYLE", default="path")},
        ),
    )


def _approval_public_key() -> tuple[str, Ed25519PublicKey]:
    encoded = _environment("HC_RESTORE_APPROVAL_PUBLIC_KEY_BASE64URL")
    try:
        padding = "=" * (-len(encoded) % 4)
        raw = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
        key = Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError, binascii.Error) as exc:
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "the restore approval public key is invalid"
        ) from exc
    fingerprint = hashlib.sha256(raw).hexdigest()
    if fingerprint != _environment("HC_RESTORE_APPROVAL_KEY_SHA256"):
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "the restore approval key pin differs"
        )
    return fingerprint, key


def _age_decryptor(required: bool) -> AgeCliDecryptor | None:
    identity = os.environ.get("HC_RESTORE_AGE_IDENTITY_FILE")
    if not required:
        if identity:
            return AgeCliDecryptor(
                Path(identity),
                AgeToolchain(
                    age=(_environment("HC_RESTORE_AGE_BINARY", default="age"),),
                    required_version=_environment(
                        "HC_RESTORE_AGE_REQUIRED_VERSION", default="1.3.1"
                    ),
                ),
            )
        return None
    if not identity:
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "portable restore requires an age identity"
        )
    return AgeCliDecryptor(
        Path(identity),
        AgeToolchain(
            age=(_environment("HC_RESTORE_AGE_BINARY", default="age"),),
            required_version=_environment("HC_RESTORE_AGE_REQUIRED_VERSION", default="1.3.1"),
        ),
        command_timeout_seconds=_integer(
            "HC_RESTORE_COMMAND_TIMEOUT_SECONDS",
            default=7200,
            minimum=1,
            maximum=86400,
        ),
    )


def _optional_runtime_file(name: str, *, private: bool) -> bytes | None:
    value = os.environ.get(name)
    if not value:
        return None
    return _bounded_file(Path(value), maximum_bytes=8 * 1024 * 1024, private=private)


def _temporal_provider() -> TemporalSdkAdminAdapter:
    mode = _environment("HC_RESTORE_TEMPORAL_DEPLOYMENT_MODE", default="external_managed")
    try:
        config = TemporalConnectionConfig(
            deployment_mode=mode,
            target=_environment("HC_RESTORE_TEMPORAL_TARGET"),
            namespace=_environment("HC_RESTORE_TEMPORAL_NAMESPACE"),
            cluster_reference=_environment("HC_RESTORE_TEMPORAL_CLUSTER_REFERENCE"),
            tls_enabled=_boolean(
                "HC_RESTORE_TEMPORAL_TLS_ENABLED",
                default=mode == "external_managed",
            ),
            api_key=os.environ.get("HC_RESTORE_TEMPORAL_API_KEY") or None,
            server_root_ca_cert=_optional_runtime_file(
                "HC_RESTORE_TEMPORAL_TLS_CA_FILE", private=False
            ),
            client_certificate=_optional_runtime_file(
                "HC_RESTORE_TEMPORAL_TLS_CERT_FILE", private=False
            ),
            client_private_key=_optional_runtime_file(
                "HC_RESTORE_TEMPORAL_TLS_KEY_FILE", private=True
            ),
            verification_server_name=os.environ.get("HC_RESTORE_TEMPORAL_TLS_SERVER_NAME") or None,
            rpc_timeout_seconds=_integer(
                "HC_RESTORE_TEMPORAL_RPC_TIMEOUT_SECONDS",
                default=15,
                minimum=1,
                maximum=120,
            ),
            maximum_schedules=_integer(
                "HC_RESTORE_TEMPORAL_MAXIMUM_SCHEDULES",
                default=100_000,
                minimum=1,
                maximum=1_000_000,
            ),
            maximum_open_workflows=_integer(
                "HC_RESTORE_TEMPORAL_MAXIMUM_OPEN_WORKFLOWS",
                default=1_000_000,
                minimum=1,
                maximum=5_000_000,
            ),
            stability_attempts=_integer(
                "HC_RESTORE_TEMPORAL_STABILITY_ATTEMPTS",
                default=5,
                minimum=2,
                maximum=10,
            ),
            stability_interval_milliseconds=_integer(
                "HC_RESTORE_TEMPORAL_STABILITY_INTERVAL_MILLISECONDS",
                default=100,
                minimum=10,
                maximum=1000,
            ),
        )
    except (ValueError, ValidationError) as exc:
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "the restore Temporal endpoint is invalid"
        ) from exc
    return TemporalSdkAdminAdapter(config)


def _kubernetes_runtime(target: PostgresEndpoint) -> KubernetesRestoreRuntime:
    token = _bounded_file(
        Path(_environment("HC_RESTORE_KUBERNETES_TOKEN_FILE")),
        maximum_bytes=1024 * 1024,
        private=True,
    ).decode("utf-8")
    workers = tuple(
        sorted(
            set(
                item.strip()
                for item in _environment("HC_RESTORE_KUBERNETES_WORKER_DEPLOYMENTS").split(",")
                if item.strip()
            )
        )
    )
    try:
        config = KubernetesRestoreRuntimeConfig(
            api_server=_environment("HC_RESTORE_KUBERNETES_API_SERVER"),
            namespace=_environment("HC_RESTORE_KUBERNETES_NAMESPACE"),
            api_deployment=_environment("HC_RESTORE_KUBERNETES_API_DEPLOYMENT"),
            worker_deployments=workers,
            api_replicas=_integer(
                "HC_RESTORE_KUBERNETES_API_REPLICAS",
                default=1,
                minimum=1,
                maximum=100,
            ),
            bearer_token=token,
            ca_bundle_path=_environment("HC_RESTORE_KUBERNETES_CA_FILE"),
            timeout_seconds=_integer(
                "HC_RESTORE_KUBERNETES_TIMEOUT_SECONDS",
                default=15,
                minimum=1,
                maximum=120,
            ),
            readiness_attempts=_integer(
                "HC_RESTORE_KUBERNETES_READINESS_ATTEMPTS",
                default=60,
                minimum=1,
                maximum=600,
            ),
            readiness_interval_seconds=_number(
                "HC_RESTORE_KUBERNETES_READINESS_INTERVAL_SECONDS",
                default=1,
                minimum=0.1,
                maximum=10,
            ),
        )
    except (ValueError, ValidationError) as exc:
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "the restore Kubernetes runtime is invalid"
        ) from exc
    return KubernetesRestoreRuntime(config, PostgresRestoreTargetFence(target))


def _restore_runtime(
    target: PostgresEndpoint,
) -> KubernetesRestoreRuntime | ComposeRestoreRuntime:
    profile = _environment("HC_RESTORE_RUNTIME_PROFILE", default="kubernetes")
    if profile == "kubernetes":
        return _kubernetes_runtime(target)
    if profile != "compose_functional":
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "the restore runtime profile is invalid"
        )
    try:
        config = ComposeRestoreRuntimeConfig(
            run_id=_environment("HC_RESTORE_FUNCTIONAL_RUN_ID"),
            project_name=_environment("HC_RESTORE_COMPOSE_PROJECT_NAME"),
            compose_file=Path(_environment("HC_RESTORE_COMPOSE_FILE")),
            env_file=Path(_environment("HC_RESTORE_COMPOSE_ENV_FILE")),
            start_read_only_api=_boolean("HC_RESTORE_COMPOSE_START_READ_ONLY_API", default=True),
            command_timeout_seconds=_integer(
                "HC_RESTORE_COMPOSE_COMMAND_TIMEOUT_SECONDS",
                default=300,
                minimum=1,
                maximum=1800,
            ),
            readiness_attempts=_integer(
                "HC_RESTORE_COMPOSE_READINESS_ATTEMPTS",
                default=60,
                minimum=1,
                maximum=600,
            ),
            readiness_interval_seconds=_number(
                "HC_RESTORE_COMPOSE_READINESS_INTERVAL_SECONDS",
                default=1,
                minimum=0.1,
                maximum=10,
            ),
        )
    except (ValueError, ValidationError) as exc:
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "the restore Compose runtime is invalid"
        ) from exc
    return ComposeRestoreRuntime(config, PostgresRestoreTargetFence(target))


def plan(
    *,
    backup_id: str,
    target_environment: str,
    backup_plan_path: Path,
    target_document_path: Path,
    staging_directory: Path,
    dry_run: bool,
) -> Mapping[str, object]:
    # A restore plan is always dry-run.  The flag is accepted for automation symmetry.
    del dry_run
    backup_plan = load_whole_backup_plan(backup_plan_path)
    request = load_restore_plan_request(target_document_path)
    if backup_plan.backup_id != backup_id or request.target.expected_backup_id != backup_id:
        raise RestorePlanError(
            "BACKUP_TARGET_IDENTITY_MISMATCH", "the requested backup identity is inconsistent"
        )
    if request.target.target_environment_id != target_environment:
        raise RestorePlanError(
            "RESTORE_TARGET_ENVIRONMENT_MISMATCH",
            "the command target differs from the signed restore target document",
        )

    # Imported lazily to keep the root CLI free from a module initialization cycle.
    from hc_data_platform.backup.whole_cli import _repository, _signer

    signer = _signer(backup_plan)
    repository = _repository(backup_plan)
    public_bytes = signer.public_key.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    fingerprint = hashlib.sha256(public_bytes).hexdigest()
    authenticated = repository.verify_manifest(
        backup_id,
        public_keys_by_sha256={fingerprint: signer.public_key},
    )
    # Repository lookup is fingerprint keyed; never key it by a provider reference.
    if fingerprint not in request.target.trusted_signing_key_sha256:
        raise RestorePlanError(
            "BACKUP_SIGNER_UNTRUSTED", "the repository signer is not trusted by the target"
        )

    inspector = CompositeRestoreTargetInspector(
        PostgreSQLRestoreTargetAdapter(_postgresql_endpoint(request.target_postgresql_database)),
        _target_object_store(request),
        staging_directory=staging_directory,
    )
    result = create_restore_plan(
        authenticated.verified_manifest,
        request,
        inspector=inspector,
    )
    return result.model_dump(mode="json")


def execute(
    *,
    backup_id: str,
    target_environment: str,
    backup_plan_path: Path,
    target_document_path: Path,
    restore_plan_path: Path,
    approval_path: Path,
    approval_signature_path: Path,
    checkpoint_path: Path,
    staging_directory: Path,
) -> Mapping[str, object]:
    backup_plan = load_whole_backup_plan(backup_plan_path)
    request = load_restore_plan_request(target_document_path)
    restore_plan = load_restore_plan(restore_plan_path)
    if (
        backup_plan.backup_id != backup_id
        or request.target.expected_backup_id != backup_id
        or restore_plan.backup_id != backup_id
        or request.target.target_environment_id != target_environment
        or restore_plan.target_environment_id != target_environment
        or request.request_id != restore_plan.request_id
        or request.target.operation_id != restore_plan.operation_id
    ):
        raise RestoreExecutionError(
            "RESTORE_EXECUTION_INPUT_MISMATCH",
            "restore command inputs do not bind one exact operation",
        )

    started_at = datetime.now(timezone.utc)
    fingerprint, approval_key = _approval_public_key()
    verified_approval = verify_restore_approval_files(
        approval_path,
        approval_signature_path,
        plan=restore_plan,
        public_keys_by_sha256={fingerprint: approval_key},
        now=started_at,
    )

    # Imported lazily to keep the root CLI free from a module initialization cycle.
    from hc_data_platform.backup.whole_cli import _repository, _signer

    signer = _signer(backup_plan)
    repository = _repository(backup_plan)
    backup_public_raw = signer.public_key.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    backup_fingerprint = hashlib.sha256(backup_public_raw).hexdigest()
    authenticated = repository.verify_manifest(
        backup_id,
        public_keys_by_sha256={backup_fingerprint: signer.public_key},
    )
    manifest = authenticated.verified_manifest.manifest
    if (
        authenticated.verified_manifest.manifest_sha256 != restore_plan.manifest_sha256
        or authenticated.verified_manifest.signature_sha256 != restore_plan.signature_sha256
        or manifest.signature.public_key_sha256 != restore_plan.backup_signing_key_sha256
    ):
        raise RestoreExecutionError(
            "RESTORE_EXECUTION_INPUT_MISMATCH",
            "the authenticated repository manifest differs from the approved plan",
        )

    staging = staging_directory.resolve(strict=True)
    target_postgres = _postgresql_endpoint(request.target_postgresql_database)
    resolver = RestorePayloadResolver(
        plan=restore_plan,
        request=request,
        repository=repository,
        public_keys_by_sha256={backup_fingerprint: signer.public_key},
        staging_directory=staging,
        execution_started_at=started_at,
    )
    source_reference = _environment("HC_RESTORE_SOURCE_OBJECT_STORE_BUCKET_REFERENCE")
    target_reference = _environment("HC_RESTORE_OBJECT_STORE_BUCKET_REFERENCE")
    if (
        source_reference != manifest.object_store.bucket_reference
        or target_reference != request.target_object_store_bucket_reference
        or _environment("HC_RESTORE_OBJECT_STORE_PREFIX") != request.target_object_store_prefix
    ):
        raise RestoreExecutionError(
            "RESTORE_OBJECT_STORE_TARGET_MISMATCH",
            "configured object-store identities differ from the approved restore",
        )
    source_client = cast(Any, _restore_s3_client("HC_RESTORE_SOURCE_OBJECT_STORE"))
    target_client = cast(Any, _restore_s3_client("HC_RESTORE_OBJECT_STORE"))
    object_adapter = S3ObjectRestoreAdapter(
        source_client,
        target_client,
        source_bucket=_environment("HC_RESTORE_SOURCE_OBJECT_STORE_BUCKET"),
        source_bucket_reference=source_reference,
        target_bucket=_environment("HC_RESTORE_OBJECT_STORE_BUCKET"),
        target_bucket_reference=target_reference,
        target_prefix=request.target_object_store_prefix,
        restore_plan_id=restore_plan.plan_id,
    )
    portable = any(
        artifact.client_side_encryption == "age_x25519_v1" for artifact in manifest.artifacts
    )
    decryptor = _age_decryptor(portable)
    runtime = _restore_runtime(target_postgres)
    owner = _environment(
        "HC_RESTORE_EXECUTION_OWNER_ID",
        default=restore_plan.operation_id,
    )
    if (
        len(owner) > 128
        or not owner[0].isalnum()
        or any(not (char.isalnum() or char in "._:-") for char in owner)
    ):
        raise RestorePlanError(
            "RESTORE_CONFIGURATION_INVALID", "the restore execution owner ID is invalid"
        )
    fencing_token = _integer(
        "HC_RESTORE_EXECUTION_FENCING_TOKEN",
        default=0,
        minimum=1,
        maximum=2**63 - 1,
    )
    steps = {
        "payload_verified": PayloadVerifiedStep(resolver),
        "objects_restored": ObjectsRestoredStep(
            resolver,
            object_adapter,
            decryptor=decryptor,
        ),
        "postgresql_restored": PostgreSQLRestoredStep(
            resolver,
            PostgresLogicalBackupAdapter(
                toolchain=(
                    compose_functional_postgres_toolchain(
                        staging,
                        run_id=_environment("HC_RESTORE_FUNCTIONAL_RUN_ID"),
                    )
                    if _environment("HC_RESTORE_RUNTIME_PROFILE", default="kubernetes")
                    == "compose_functional"
                    else None
                ),
                command_timeout_seconds=_integer(
                    "HC_RESTORE_COMMAND_TIMEOUT_SECONDS",
                    default=7200,
                    minimum=1,
                    maximum=86400,
                ),
            ),
            target_postgres,
            decryptor=decryptor,
        ),
        "temporal_ready": TemporalReadyStep(
            resolver,
            _temporal_provider(),
            decryptor=decryptor,
        ),
        "services_read_only": ServicesReadOnlyStep(restore_plan, request, runtime),
    }
    completed = execute_restore(
        restore_plan,
        approval=verified_approval,
        execution_owner_id=owner,
        fencing_token=fencing_token,
        fence=runtime,
        steps=cast(Any, steps),
        checkpoints=FileRestoreCheckpointStore(checkpoint_path),
    )
    checkpoint = completed.checkpoint
    return {
        "status": checkpoint.state,
        "backup_id": checkpoint.backup_id,
        "operation_id": checkpoint.operation_id,
        "plan_id": checkpoint.plan_id,
        "checkpoint_sha256": completed.sha256,
        "completed_steps": list(checkpoint.completed_steps),
        "writes_enabled": checkpoint.writes_enabled,
        "reconciliation_completed": checkpoint.reconciliation_completed,
        "restore_verified": checkpoint.restore_verified,
        "next_action": "restore_reconcile_required",
    }


def reconcile(
    *,
    backup_id: str,
    target_environment: str,
    backup_plan_path: Path,
    target_document_path: Path,
    restore_plan_path: Path,
    checkpoint_path: Path,
    staging_directory: Path,
    report_path: Path,
    reconciled_at: str,
) -> Mapping[str, object]:
    """Produce external RST3-03 evidence without advancing maintenance state."""

    backup_plan = load_whole_backup_plan(backup_plan_path)
    request = load_restore_plan_request(target_document_path)
    restore_plan = load_restore_plan(restore_plan_path)
    if (
        backup_plan.backup_id != backup_id
        or request.target.expected_backup_id != backup_id
        or restore_plan.backup_id != backup_id
        or request.target.target_environment_id != target_environment
        or restore_plan.target_environment_id != target_environment
        or request.request_id != restore_plan.request_id
        or request.target.operation_id != restore_plan.operation_id
    ):
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_INPUT_MISMATCH",
            "reconciliation inputs do not bind one exact restore operation",
        )
    loaded_checkpoint = FileRestoreCheckpointStore(checkpoint_path).load()
    if loaded_checkpoint is None:
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_CHECKPOINT_INCOMPLETE",
            "the completed restore checkpoint is unavailable",
        )

    from hc_data_platform.backup.whole_cli import _repository, _signer

    signer = _signer(backup_plan)
    repository = _repository(backup_plan)
    backup_public_raw = signer.public_key.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    backup_fingerprint = hashlib.sha256(backup_public_raw).hexdigest()
    authenticated = repository.verify_manifest(
        backup_id,
        public_keys_by_sha256={backup_fingerprint: signer.public_key},
    )
    manifest = authenticated.verified_manifest.manifest
    if (
        authenticated.verified_manifest.manifest_sha256 != restore_plan.manifest_sha256
        or authenticated.verified_manifest.signature_sha256 != restore_plan.signature_sha256
        or manifest.signature.public_key_sha256 != restore_plan.backup_signing_key_sha256
    ):
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_INPUT_MISMATCH",
            "the authenticated repository manifest differs from the restore plan",
        )

    staging = staging_directory.resolve(strict=True)
    resolver = RestorePayloadResolver(
        plan=restore_plan,
        request=request,
        repository=repository,
        public_keys_by_sha256={backup_fingerprint: signer.public_key},
        staging_directory=staging,
        execution_started_at=loaded_checkpoint.checkpoint.receipts[0].completed_at,
    )
    payload = resolver.resolve()
    portable = any(
        artifact.client_side_encryption == "age_x25519_v1" for artifact in manifest.artifacts
    )
    decryptor = _age_decryptor(portable)
    object_artifact = _object_artifact(
        manifest,
        payload.artifact_paths,
        decryptor=decryptor,
    )
    records = authenticated_object_records(
        object_artifact,
        source_bucket_reference=manifest.object_store.bucket_reference,
        source_prefix=manifest.object_store.prefix,
        decryptor=decryptor,
    )
    source_reference = _environment("HC_RESTORE_SOURCE_OBJECT_STORE_BUCKET_REFERENCE")
    target_reference = _environment("HC_RESTORE_OBJECT_STORE_BUCKET_REFERENCE")
    if (
        source_reference != manifest.object_store.bucket_reference
        or target_reference != request.target_object_store_bucket_reference
        or _environment("HC_RESTORE_OBJECT_STORE_PREFIX") != request.target_object_store_prefix
    ):
        raise RestoreReconciliationError(
            "RESTORE_OBJECT_STORE_TARGET_MISMATCH",
            "configured object-store identities differ from the restore plan",
        )
    object_adapter = S3ObjectRestoreAdapter(
        cast(Any, _restore_s3_client("HC_RESTORE_SOURCE_OBJECT_STORE")),
        cast(Any, _restore_s3_client("HC_RESTORE_OBJECT_STORE")),
        source_bucket=_environment("HC_RESTORE_SOURCE_OBJECT_STORE_BUCKET"),
        source_bucket_reference=source_reference,
        target_bucket=_environment("HC_RESTORE_OBJECT_STORE_BUCKET"),
        target_bucket_reference=target_reference,
        target_prefix=request.target_object_store_prefix,
        restore_plan_id=restore_plan.plan_id,
    )
    object_inspector = RestoredObjectInventoryInspector(
        object_adapter,
        object_artifact,
        source_prefix=manifest.object_store.prefix,
        decryptor=decryptor,
    )

    target_postgres = _postgresql_endpoint(request.target_postgresql_database)
    if manifest.postgresql.content_evidence is None:
        raise RestoreReconciliationError(
            "RESTORE_POSTGRES_EVIDENCE_MISSING",
            "the authenticated backup lacks deterministic PostgreSQL evidence",
        )
    source_database_evidence = _database_evidence(
        manifest.postgresql.content_evidence,
        manifest,
    )
    try:
        postgres = inspect_postgresql_restore(
            restore_plan,
            loaded_checkpoint,
            endpoint=target_postgres,
            source_evidence=source_database_evidence,
            source_server_major=manifest.postgresql.major_version,
            object_records=records,
            source_object_prefix=manifest.object_store.prefix,
            target_object_prefix=request.target_object_store_prefix,
        )
    except Exception as exc:
        postgres = failed_postgresql_reconciliation(exc)

    temporal_artifact = _temporal_artifact(
        manifest,
        payload.artifact_paths,
        decryptor=decryptor,
    )
    temporal_policy = load_temporal_policy_document(
        temporal_artifact,
        decryptor=decryptor,
    )
    temporal_inspector = TemporalWorkflowInspector(
        _temporal_provider(),
        temporal_policy,
        postgres.workflow,
    )
    runtime_inspector = ReadOnlyRuntimeInspector(_restore_runtime(target_postgres))
    report = reconcile_restore(
        restore_plan,
        loaded_checkpoint,
        inspectors=reconciliation_inspector_map(
            postgres=postgres,
            objects=object_inspector,
            temporal=temporal_inspector,
            runtime=runtime_inspector,
        ),
        verifier_identity=_environment("HC_RESTORE_RECONCILIATION_VERIFIER_IDENTITY"),
        reconciled_at=_parse_reconciliation_timestamp(reconciled_at),
    )
    report_sha256 = publish_restore_reconciliation_report(report, report_path)
    if not report.reconciliation_passed:
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_FAILED",
            "one or more read-only reconciliation gates failed; the report was retained",
        )
    return {
        "status": "RECONCILIATION_PASSED",
        "backup_id": report.backup_id,
        "operation_id": report.operation_id,
        "plan_id": report.plan_id,
        "restore_checkpoint_sha256": report.restore_checkpoint_sha256,
        "report_id": report.report_id,
        "report_sha256": report_sha256,
        "writes_enabled": report.writes_enabled,
        "workers_enabled": report.workers_enabled,
        "restore_verified": report.restore_verified,
        "next_action": report.next_action,
    }


def _parse_reconciliation_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_TIME_INVALID",
            "the reconciliation timestamp is invalid",
        ) from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise RestoreReconciliationError(
            "RESTORE_RECONCILIATION_TIME_INVALID",
            "the reconciliation timestamp must be UTC",
        )
    return parsed
