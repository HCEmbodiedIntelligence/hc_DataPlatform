from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg.conninfo import conninfo_to_dict

from hc_data_platform.backup.contracts import (
    BackupManifestV1,
    BackupRepositoryV1,
    RestoreTargetV1,
    canonical_json_bytes,
    verify_signed_manifest,
)
from hc_data_platform.backup.dependencies import (
    ConfigurationBackupArtifact,
    PublicConfigArtifact,
    SecretsEnvelopeArtifact,
)
from hc_data_platform.backup.objects import S3ObjectBackupAdapter, S3ObjectRestoreAdapter
from hc_data_platform.backup.postgresql import (
    MaintenanceBackupLease,
    PostgresEndpoint,
    PostgresLogicalBackupAdapter,
    PostgresToolchain,
    assert_current_backup_lease,
    collect_database_evidence,
)
from hc_data_platform.backup.repository import LockedRepositoryConfig, S3LockedBackupRepository
from hc_data_platform.backup.restore import (
    CompositeRestoreTargetInspector,
    PostgreSQLRestoreTargetAdapter,
    RestorePlanRequestV1,
    RestoreReadinessEvidenceV1,
    RestoreTemporalReadinessV1,
    S3RestoreTargetAdapter,
    create_restore_plan,
)
from hc_data_platform.backup.restore_execution import (
    FileRestoreCheckpointStore,
    RestoreApprovalSignatureV1,
    RestoreApprovalV1,
    RestoreExecutionError,
    execute_restore,
    verify_restore_approval,
)
from hc_data_platform.backup.restore_reconciliation import (
    load_restore_reconciliation_report,
    publish_restore_reconciliation_report,
    reconcile_restore,
)
from hc_data_platform.backup.restore_reconciliation_adapters import (
    ReadOnlyRuntimeInspector,
    RestoredObjectInventoryInspector,
    TemporalWorkflowInspector,
    authenticated_object_records,
    inspect_postgresql_restore,
    reconciliation_inspector_map,
)
from hc_data_platform.backup.restore_runtime import PostgresRestoreTargetFence
from hc_data_platform.backup.restore_steps import (
    ObjectsRestoredStep,
    PayloadVerifiedStep,
    PostgreSQLRestoredStep,
    RestorePayloadResolver,
    ServicesReadOnlyStep,
    TemporalReadyStep,
)
from hc_data_platform.backup.signing import (
    VaultTransitEd25519Signer,
    VaultTransitSignerConfig,
)
from hc_data_platform.backup.temporal import (
    TemporalBackupAdapter,
    TemporalBackupError,
    TemporalConnectionConfig,
    TemporalPolicyDocument,
    TemporalProviderRecoveryContract,
    TemporalSdkAdminAdapter,
    load_temporal_policy_document,
)
from hc_data_platform.backup.whole import (
    WholeBackupPlanV1,
    assemble_whole_backup,
    prepare_postgresql_artifact,
)
from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.platform_control.maintenance_contract import (
    MaintenanceCommandV1,
    MaintenanceState,
)
from hc_data_platform.platform_ops.maintenance import (
    MaintenanceOperation,
    PostgresMaintenanceRepository,
)
from tests.backup.test_backup_whole import _evidence, _LocalSigner

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "backend/tests/backup/fixtures/hc-platform-backup-v1.golden.json"
OWNER = UUID("77777777-7777-4777-8777-777777777777")


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required")
    return value


def _private(path: Path, payload: bytes) -> tuple[Path, str]:
    path.write_bytes(payload)
    path.chmod(0o600)
    return path, hashlib.sha256(payload).hexdigest()


def _endpoint(dsn: str, *, tool_host: str, tool_port: int) -> PostgresEndpoint:
    parsed = conninfo_to_dict(normalize_postgres_dsn(dsn))
    return PostgresEndpoint(
        host=tool_host,
        port=tool_port,
        database=parsed["dbname"],
        username=parsed["user"],
        password=parsed["password"],
        sslmode="disable",
    )


def _container_tool(container: str, program: str) -> tuple[str, ...]:
    exported = (
        "LANG",
        "LC_ALL",
        "PGCONNECT_TIMEOUT",
        "PGDATABASE",
        "PGHOST",
        "PGPASSFILE",
        "PGPORT",
        "PGSSLMODE",
        "PGUSER",
    )
    arguments = ["docker", "exec"]
    for name in exported:
        arguments.extend(("-e", name))
    arguments.extend((container, program))
    return tuple(arguments)


def _command(
    operation: MaintenanceOperation,
    next_state: MaintenanceState,
) -> MaintenanceCommandV1:
    assert operation.owner_instance_id is not None
    assert operation.fencing_token is not None
    return MaintenanceCommandV1(
        operation_id=operation.operation_id,
        environment_id=operation.environment_id,
        owner_instance_id=str(operation.owner_instance_id),
        fencing_token=operation.fencing_token,
        expected_state=operation.state,
        expected_state_version=operation.state_version,
        next_state=next_state,
    )


def _approval(plan: Any, *, now: datetime) -> tuple[Any, bytes, bytes, str, str]:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32, 64)))
    public_key = private_key.public_key()
    raw_key = public_key.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    key_sha = hashlib.sha256(raw_key).hexdigest()
    approval = RestoreApprovalV1(
        approval_id="rst302-real-approval",
        approval_reference="approval://control-plane/rst302-real",
        plan_id=plan.plan_id,
        plan_sha256=hashlib.sha256(canonical_json_bytes(plan.model_dump(mode="json"))).hexdigest(),
        plan_checkpoint_sha256=plan.checkpoint_sha256,
        operation_id=plan.operation_id,
        backup_id=plan.backup_id,
        target_instance_id=plan.target_instance_id,
        target_environment_id=plan.target_environment_id,
        approver_identity="rst302-separate-duty-approver",
        approval_key_sha256=key_sha,
        approved_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
    )
    approval_bytes = canonical_json_bytes(approval.model_dump(mode="json"))
    signature = RestoreApprovalSignatureV1(
        approval_key_sha256=key_sha,
        approval_sha256=hashlib.sha256(approval_bytes).hexdigest(),
        signature_base64url=base64.urlsafe_b64encode(private_key.sign(approval_bytes))
        .rstrip(b"=")
        .decode("ascii"),
    )
    signature_bytes = canonical_json_bytes(signature.model_dump(mode="json"))
    verified = verify_restore_approval(
        approval_bytes,
        signature_bytes,
        plan=plan,
        public_keys_by_sha256={key_sha: public_key},
        now=now,
    )
    encoded_public_key = base64.urlsafe_b64encode(raw_key).rstrip(b"=").decode("ascii")
    return verified, approval_bytes, signature_bytes, encoded_public_key, key_sha


class _FailOnceTemporal:
    def __init__(self, delegate: TemporalSdkAdminAdapter) -> None:
        self.delegate = delegate
        self.calls = 0

    async def preflight(self, policy: TemporalPolicyDocument) -> str:
        self.calls += 1
        if self.calls == 1:
            raise TemporalBackupError(
                "RST302_TEMPORAL_INJECTED",
                "injected provider interruption before a safe checkpoint",
            )
        return await self.delegate.preflight(policy)


class _DirectReadOnlyRuntime:
    """No-service integration fence; Kubernetes activation is tested separately."""

    def __init__(self, target_fence: PostgresRestoreTargetFence) -> None:
        self.target_fence = target_fence
        self.activation_calls = 0

    def assert_read_only(
        self,
        plan: Any,
        *,
        execution_owner_id: str,
        fencing_token: int,
    ) -> str:
        state = self.target_fence.inspect(plan.target_environment_id)
        if state.state not in {"database_unrestored", "target_fence_absent", "read_only"}:
            raise RestoreExecutionError("RESTORE_FENCE_OPEN", "target fence is not read-only")
        return hashlib.sha256(
            canonical_json_bytes(
                {
                    "plan_id": plan.plan_id,
                    "owner": execution_owner_id,
                    "token": fencing_token,
                    "state": state.state,
                    "database_evidence": state.evidence_sha256,
                    "services_running": False,
                }
            )
        ).hexdigest()

    def ensure_read_only_services(self, plan: Any, request: Any) -> str:
        assert request.readiness.api_writes_disabled is True
        assert request.readiness.worker_replicas_zero is True
        self.activation_calls += 1
        return self.target_fence.install(plan.target_environment_id).evidence_sha256


def _configuration(
    staging: Path,
    manifest: BackupManifestV1,
    *,
    hmac_key_reference: str,
) -> ConfigurationBackupArtifact:
    public_path, public_sha = _private(
        staging / "public.yaml",
        b"apiVersion: hc.platform/v1\nmode: restore-integration\n",
    )
    envelope_path, envelope_sha = _private(
        staging / "secrets-envelope.json",
        b'{"provider":"external","plaintext_secret_material":false}\n',
    )
    dependencies = tuple(
        sorted(
            manifest.secrets_and_kms.dependencies,
            key=lambda item: (
                item.environment_variable,
                item.secret_reference,
                item.secret_key_name,
                item.version,
            ),
        )
    )
    return ConfigurationBackupArtifact(
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
            provider_reference="rst302-disposable-vault",
            hmac_key_reference=hmac_key_reference,
        ),
        manifest_dependencies=dependencies,
        preflight_coordinate="secret-dependency-set/v1:sha256:" + "7" * 64,
    )


@pytest.mark.integration
def test_real_restore_interrupts_at_temporal_resumes_and_finishes_read_only(
    tmp_path: Path,
) -> None:
    assert _required("HC_RST302_ALLOW_MUTATION") == "disposable-only"
    source_dsn = normalize_postgres_dsn(_required("HC_RST302_SOURCE_DSN"))
    target_dsn = normalize_postgres_dsn(_required("HC_RST302_TARGET_DSN"))
    tool_host = _required("HC_RST302_POSTGRES_TOOL_HOST")
    tool_port = int(_required("HC_RST302_POSTGRES_TOOL_PORT"))
    client_container = _required("HC_RST302_POSTGRES_CLIENT_CONTAINER")
    source = _endpoint(source_dsn, tool_host=tool_host, tool_port=tool_port)
    target = _endpoint(target_dsn, tool_host=tool_host, tool_port=tool_port)
    assert source.database != target.database

    boto3 = pytest.importorskip("boto3")
    s3 = boto3.client(
        "s3",
        endpoint_url=_required("HC_RST302_S3_ENDPOINT"),
        aws_access_key_id=_required("HC_RST302_S3_ACCESS_KEY"),
        aws_secret_access_key=_required("HC_RST302_S3_SECRET_KEY"),
        region_name="us-east-1",
    )
    source_bucket = _required("HC_RST302_SOURCE_BUCKET")
    target_bucket = _required("HC_RST302_TARGET_BUCKET")
    repository_bucket = _required("HC_RST302_REPOSITORY_BUCKET")
    source_prefix = _required("HC_RST302_SOURCE_PREFIX").strip("/") + "/"
    target_prefix = _required("HC_RST302_TARGET_PREFIX").strip("/")
    source_bucket_reference = "rst302-business-source"
    target_bucket_reference = "rst302-business-target"
    source_objects = {
        f"{source_prefix}alpha.bin": b"rst302-alpha-current",
        f"{source_prefix}nested/empty.bin": b"",
    }

    asyncio.run(apply_migrations(source_dsn))
    with psycopg.connect(source_dsn) as connection:
        connection.execute("CREATE SCHEMA rst302_evidence")
        connection.execute(
            """
            CREATE TABLE rst302_evidence.records (
                record_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                label text NOT NULL UNIQUE,
                payload jsonb NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO rst302_evidence.records (label, payload)
            VALUES ('alpha', '{"verified":true,"ordinal":1}'::jsonb),
                   ('beta', '{"verified":true,"ordinal":2}'::jsonb)
            """
        )
    s3.put_object(Bucket=source_bucket, Key=f"{source_prefix}alpha.bin", Body=b"superseded")
    for key, payload in source_objects.items():
        s3.put_object(Bucket=source_bucket, Key=key, Body=payload)

    environment_id = "rst302-source-environment"
    backup_operation_id = "rst302-backup-operation"
    maintenance = PostgresMaintenanceRepository.from_dsn(source_dsn)
    operation = maintenance.request_operation(
        operation_id=backup_operation_id,
        environment_id=environment_id,
        operation_kind="BACKUP",
        plan_digest="sha256:" + "a" * 64,
        requested_by="rst302-backup-controller",
    )
    operation = maintenance.acquire(operation.operation_id, owner_instance_id=OWNER)
    for next_state in (
        MaintenanceState.READ_ONLY,
        MaintenanceState.DRAINING,
        MaintenanceState.FENCED,
        MaintenanceState.EXECUTING,
    ):
        operation = maintenance.transition(_command(operation, next_state))
    assert operation.fencing_token is not None
    lease = MaintenanceBackupLease(
        environment_id=environment_id,
        operation_id=backup_operation_id,
        owner_instance_id=OWNER,
        fencing_token=operation.fencing_token,
    )

    dsns = {source.database: source_dsn, target.database: target_dsn}

    def connect(endpoint: PostgresEndpoint) -> Any:
        return psycopg.connect(dsns[endpoint.database])

    postgresql_adapter = PostgresLogicalBackupAdapter(
        toolchain=PostgresToolchain(
            pg_dump=_container_tool(client_container, "pg_dump"),
            pg_restore=_container_tool(client_container, "pg_restore"),
        ),
        connection_factory=connect,
        command_timeout_seconds=300,
    )
    backup_staging = tmp_path / "backup"
    backup_staging.mkdir(mode=0o700)
    operation = maintenance.renew(
        operation.operation_id,
        owner_instance_id=OWNER,
        fencing_token=lease.fencing_token,
    )
    postgresql = postgresql_adapter.create_dump(
        source,
        lease=lease,
        staging_directory=backup_staging,
    )
    operation = maintenance.renew(
        operation.operation_id,
        owner_instance_id=OWNER,
        fencing_token=lease.fencing_token,
    )
    objects = S3ObjectBackupAdapter(
        s3,
        bucket=source_bucket,
        bucket_reference=source_bucket_reference,
    ).create_snapshot(
        lease=lease,
        lease_verifier=lambda current: assert_current_backup_lease(
            source,
            current,
            connection_factory=connect,
        ),
        staging_directory=backup_staging,
        prefix=source_prefix,
    )

    temporal_target = _required("HC_RST302_TEMPORAL_TARGET")
    temporal_namespace = os.environ.get("HC_RST302_TEMPORAL_NAMESPACE", "default")

    async def temporal_artifact() -> tuple[TemporalSdkAdminAdapter, Any]:
        from temporalio.client import Client

        client = await Client.connect(temporal_target, namespace=temporal_namespace)
        temporal_config = TemporalConnectionConfig(
            deployment_mode="external_managed",
            target="managed-temporal.example.test:7233",
            namespace=temporal_namespace,
            cluster_reference="temporal://managed/rst302-disposable",
            tls_enabled=True,
            api_key="test-only-not-exported",
            stability_attempts=10,
            stability_interval_milliseconds=200,
        )
        provider = TemporalSdkAdminAdapter(temporal_config, client=client)
        snapshot = await provider.capture_inventory()
        observed = datetime.now(timezone.utc).replace(microsecond=0)

        def timestamp(value: datetime) -> str:
            return value.isoformat().replace("+00:00", "Z")

        contract = TemporalProviderRecoveryContract(
            provider="rst302_disposable_protocol",
            cluster_reference=temporal_config.cluster_reference,
            namespace=temporal_namespace,
            expected_cluster_identity_sha256=snapshot.cluster_identity_sha256,
            expected_namespace_identity_sha256=snapshot.namespace_identity_sha256,
            expected_service_version=snapshot.service_version,
            maximum_rpo_seconds=900,
            maximum_rto_seconds=7200,
            protection_reference="evidence://temporal/rst302-protection-point",
            protection_observed_at=timestamp(observed - timedelta(minutes=1)),
            evidence_reference="evidence://temporal/rst302-provider-receipt",
            evidence_sha256="b" * 64,
            restore_runbook_reference="runbook://temporal/rst302-restore",
            verified_at=timestamp(observed - timedelta(seconds=30)),
            valid_until=timestamp(observed + timedelta(hours=1)),
        )
        artifact = await TemporalBackupAdapter(provider, clock=lambda: observed).create(
            provider_recovery=contract,
            lease=lease,
            lease_verifier=lambda current: assert_current_backup_lease(
                source,
                current,
                connection_factory=connect,
            ),
            staging_directory=backup_staging,
            mode="snapshot",
        )
        return provider, artifact

    operation = maintenance.renew(
        operation.operation_id,
        owner_instance_id=OWNER,
        fencing_token=lease.fencing_token,
    )
    temporal_provider, temporal = asyncio.run(temporal_artifact())
    temporal_document = load_temporal_policy_document(temporal)

    golden = BackupManifestV1.model_validate(json.loads(GOLDEN.read_bytes())["manifest"])
    release = golden.release.model_copy(
        update={"migration_manifest_sha256": postgresql.source_evidence.migration_sha256}
    )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    retain_until = now + timedelta(days=2)
    logical_repository = BackupRepositoryV1(
        repository_id="rst302-locked-repository",
        provider="s3_compatible",
        bucket_reference="rst302-locked-logical",
        kms_key_reference="kms://rst302/repository/versions/v1",
        retention_until=retain_until,
        cross_site_replica_reference="rst302-cross-site-replica",
    )
    vault_endpoint = os.environ.get("HC_RST302_VAULT_ENDPOINT")
    if vault_endpoint:
        vault_key_name = _required("HC_RST302_VAULT_KEY_NAME")
        signer = VaultTransitEd25519Signer(
            VaultTransitSignerConfig(
                endpoint_url=vault_endpoint,
                token=_required("HC_RST302_VAULT_TOKEN"),
                provider_reference="rst302",
                key_name=vault_key_name,
                key_version=1,
                key_reference=(f"kms://vault/rst302/transit/{vault_key_name}/versions/v1"),
            )
        )
    else:
        signer = _LocalSigner(golden.secrets_and_kms.signing_key_reference)
    whole_plan = WholeBackupPlanV1(
        operation_id=backup_operation_id,
        backup_id=f"rst302-real-backup-{now.strftime('%H%M%S')}",
        mode="snapshot",
        created_at=now,
        completed_at=now + timedelta(seconds=1),
        source_platform_id="rst302-source-platform",
        source_environment_id=environment_id,
        verifier_identity="rst302-whole-controller",
        release=release,
        backup_tool=golden.backup_tool,
        postgresql={
            "physical_recovery": "provider_managed_pitr",
            "pitr_coordinate": "provider-pitr://rst302/source",
        },
        object_store={
            "provider": "s3_compatible",
            "bucket_reference": source_bucket_reference,
            "prefix": source_prefix,
        },
        temporal={
            "cluster_reference": temporal_document.cluster_reference,
            "namespace": temporal_document.namespace,
            "service_version": temporal_document.service_version,
        },
        deployment_configuration={"gitops_repository_reference": "gitops://rst302/exact-release"},
        secrets_and_kms={
            "repository_key_reference": logical_repository.kms_key_reference,
            "signing_key_reference": signer.key_reference,
            "portable_recipient_key_references": (),
        },
        backup_repository=logical_repository,
        external_evidence={
            "runtime_logs_evidence_sha256": "8" * 64,
            "external_dependencies_evidence_sha256": "9" * 64,
        },
    )
    configuration = _configuration(
        backup_staging,
        golden,
        hmac_key_reference=signer.key_reference,
    )
    prepared = prepare_postgresql_artifact(
        whole_plan,
        postgresql,
        staging_directory=backup_staging,
    )
    assembly = assemble_whole_backup(
        whole_plan,
        postgresql_receipt=postgresql,
        postgresql_upload=prepared,
        object_receipt=objects,
        configuration_receipt=configuration,
        temporal_receipt=temporal,
        verification=_evidence(whole_plan),  # type: ignore[arg-type]
        signer=signer,
        staging_directory=backup_staging,
    )
    repository = S3LockedBackupRepository(
        s3,
        LockedRepositoryConfig(
            repository_id=logical_repository.repository_id,
            provider="s3_compatible",
            bucket=repository_bucket,
            bucket_reference=logical_repository.bucket_reference,
            prefix=_required("HC_RST302_REPOSITORY_PREFIX").strip("/"),
            kms_key_reference=logical_repository.kms_key_reference,
            provider_kms_key_id=_required("HC_RST302_REPOSITORY_KMS_KEY_ID"),
            provider_kms_observed_key_id=_required("HC_RST302_REPOSITORY_KMS_OBSERVED_KEY_ID"),
            provider_replica_destination=_required("HC_RST302_REPOSITORY_REPLICA_DESTINATION"),
            retention_until=retain_until,
            cross_site_replica_reference=logical_repository.cross_site_replica_reference,
            send_small_object_checksum=False,
        ),
        clock=lambda: now,
    )
    for upload in assembly.upload_sources:
        repository.upload_file(
            whole_plan.backup_id,
            upload,
            checkpoint_path=backup_staging / "repository-upload-checkpoint.json",
        )

    verified = verify_signed_manifest(
        assembly.signed_manifest.manifest_bytes,
        assembly.signed_manifest.signature_bytes,
        public_key=signer.public_key,
    )
    dependencies = tuple(
        sorted(
            verified.manifest.secrets_and_kms.dependencies,
            key=lambda item: (
                item.environment_variable,
                item.secret_reference,
                item.secret_key_name,
                item.version,
            ),
        )
    )
    target_identity = RestoreTargetV1(
        operation_id="rst302-restore-operation",
        expected_backup_id=whole_plan.backup_id,
        expected_source_platform_id=whole_plan.source_platform_id,
        expected_source_environment_id=whole_plan.source_environment_id,
        target_instance_id="rst302-isolated-instance",
        target_environment_id="rst302-target-environment",
        purpose="rehearsal",
        target_is_empty=True,
        requested_release=release,
        postgresql_major=postgresql.server_major,
        trusted_signing_key_sha256={verified.manifest.signature.public_key_sha256},
        available_kms_key_references={
            verified.manifest.secrets_and_kms.repository_key_reference,
            verified.manifest.secrets_and_kms.signing_key_reference,
        },
    )
    requested_at = now + timedelta(seconds=2)
    request = RestorePlanRequestV1(
        request_id="rst302-restore-request",
        requested_at=requested_at,
        target=target_identity,
        target_postgresql_database=target.database,
        object_restore_mode="copy_referenced",
        target_object_store_bucket_reference=target_bucket_reference,
        target_object_store_prefix=target_prefix,
        readiness=RestoreReadinessEvidenceV1(
            target_instance_id=target_identity.target_instance_id,
            target_environment_id=target_identity.target_environment_id,
            observed_at=requested_at - timedelta(minutes=1),
            valid_until=requested_at + timedelta(hours=1),
            evidence_reference="provider://rst302/disposable-readiness",
            evidence_sha256="d" * 64,
            postgresql_available_bytes=2 * 1024 * 1024 * 1024,
            object_store_available_bytes=2 * 1024 * 1024 * 1024,
            deployed_release=release,
            available_kms_key_references=target_identity.available_kms_key_references,
            temporal=RestoreTemporalReadinessV1(
                cluster_reference=temporal_document.cluster_reference,
                namespace=temporal_document.namespace,
                service_version=temporal_document.service_version,
                ready=True,
                evidence_sha256="e" * 64,
            ),
            external_dependencies=dependencies,
            external_dependencies_ready=True,
            api_writes_disabled=True,
            worker_replicas_zero=True,
            domain_names_ready=True,
            certificates_ready=True,
        ),
    )
    restore_staging = tmp_path / "restore"
    restore_staging.mkdir(mode=0o700)
    inspector = CompositeRestoreTargetInspector(
        PostgreSQLRestoreTargetAdapter(target, connection_factory=connect),
        S3RestoreTargetAdapter(
            s3,
            bucket=target_bucket,
            bucket_reference=target_bucket_reference,
            prefix=target_prefix,
        ),
        staging_directory=restore_staging,
    )
    restore_plan = create_restore_plan(
        verified,
        request,
        inspector=inspector,
        clock=lambda: requested_at,
    )
    (
        approval,
        approval_bytes,
        approval_signature_bytes,
        approval_public_key,
        approval_key_sha256,
    ) = _approval(restore_plan, now=requested_at)
    evidence_directory_value = os.environ.get("HC_RST302_EVIDENCE_DIR")
    if evidence_directory_value:
        evidence_directory = Path(evidence_directory_value).resolve(strict=True)
        if evidence_directory.stat().st_mode & 0o077:
            raise AssertionError("RST3-02 evidence directory must be owner-only")
        evidence_documents = {
            "whole-backup-plan.json": canonical_json_bytes(whole_plan.model_dump(mode="json")),
            "restore-target.json": canonical_json_bytes(request.model_dump(mode="json")),
            "restore-plan.json": canonical_json_bytes(restore_plan.model_dump(mode="json")),
            "restore-approval.json": approval_bytes,
            "restore-approval.sig": approval_signature_bytes,
            "approval-public-key.base64url": approval_public_key.encode("ascii"),
            "approval-key.sha256": approval_key_sha256.encode("ascii"),
            "backup-id.txt": whole_plan.backup_id.encode("ascii"),
            "target-environment.txt": target_identity.target_environment_id.encode("ascii"),
        }
        for name, payload in evidence_documents.items():
            _private(evidence_directory / name, payload)
    resolver = RestorePayloadResolver(
        plan=restore_plan,
        request=request,
        repository=repository,
        public_keys_by_sha256={verified.manifest.signature.public_key_sha256: signer.public_key},
        staging_directory=restore_staging,
        execution_started_at=requested_at,
    )
    object_step = ObjectsRestoredStep(
        resolver,
        S3ObjectRestoreAdapter(
            s3,
            s3,
            source_bucket=source_bucket,
            source_bucket_reference=source_bucket_reference,
            target_bucket=target_bucket,
            target_bucket_reference=target_bucket_reference,
            target_prefix=target_prefix,
            restore_plan_id=restore_plan.plan_id,
        ),
    )
    postgres_step = PostgreSQLRestoredStep(
        resolver,
        postgresql_adapter,
        target,
    )
    fail_once_temporal = _FailOnceTemporal(temporal_provider)
    target_fence = PostgresRestoreTargetFence(target, connection_factory=connect)
    runtime = _DirectReadOnlyRuntime(target_fence)
    steps = {
        "payload_verified": PayloadVerifiedStep(resolver),
        "objects_restored": object_step,
        "postgresql_restored": postgres_step,
        "temporal_ready": TemporalReadyStep(resolver, fail_once_temporal),  # type: ignore[arg-type]
        "services_read_only": ServicesReadOnlyStep(restore_plan, request, runtime),
    }
    checkpoint_path = tmp_path / "restore-checkpoint.json"
    checkpoints = FileRestoreCheckpointStore(checkpoint_path)

    with pytest.raises(TemporalBackupError) as interrupted:
        execute_restore(
            restore_plan,
            approval=approval,
            execution_owner_id=restore_plan.operation_id,
            fencing_token=302,
            fence=runtime,
            steps=steps,  # type: ignore[arg-type]
            checkpoints=checkpoints,
            clock=lambda: requested_at,
        )
    assert interrupted.value.code == "RST302_TEMPORAL_INJECTED"
    safe = checkpoints.load()
    assert safe is not None
    assert safe.checkpoint.state == "POSTGRESQL_RESTORED"
    assert safe.checkpoint.writes_enabled is False
    assert safe.checkpoint.restore_verified is False
    with psycopg.connect(target_dsn) as connection:
        restored_evidence = collect_database_evidence(connection)
        assert (
            restored_evidence.model_copy(
                update={"database_size_bytes": postgresql.source_evidence.database_size_bytes}
            )
            == postgresql.source_evidence
        )
        assert connection.execute(
            "SELECT label FROM rst302_evidence.records ORDER BY record_id"
        ).fetchall() == [("alpha",), ("beta",)]

    target_versions_before = s3.list_object_versions(
        Bucket=target_bucket,
        Prefix=f"{target_prefix}/",
    )["Versions"]
    assert len(target_versions_before) == len(source_objects)
    assert object_step.execute(restore_plan, safe.checkpoint).step == "objects_restored"
    assert postgres_step.execute(restore_plan, safe.checkpoint).step == "postgresql_restored"
    target_versions_after = s3.list_object_versions(
        Bucket=target_bucket,
        Prefix=f"{target_prefix}/",
    )["Versions"]
    assert len(target_versions_after) == len(target_versions_before)

    completed = execute_restore(
        restore_plan,
        approval=approval,
        execution_owner_id=restore_plan.operation_id,
        fencing_token=302,
        fence=runtime,
        steps=steps,  # type: ignore[arg-type]
        checkpoints=checkpoints,
        clock=lambda: requested_at,
    )
    repeated = execute_restore(
        restore_plan,
        approval=approval,
        execution_owner_id=restore_plan.operation_id,
        fencing_token=302,
        fence=runtime,
        steps=steps,  # type: ignore[arg-type]
        checkpoints=checkpoints,
        clock=lambda: requested_at,
    )
    assert repeated == completed
    assert completed.checkpoint.state == "READ_ONLY_READY"
    assert completed.checkpoint.writes_enabled is False
    assert completed.checkpoint.reconciliation_completed is False
    assert completed.checkpoint.restore_verified is False
    assert runtime.activation_calls == 1
    assert fail_once_temporal.calls == 2
    assert target_fence.inspect(target_identity.target_environment_id).state == "read_only"
    with psycopg.connect(target_dsn) as connection:
        rows = connection.execute(
            """
            SELECT environment_id, mode
            FROM platform.environment_fences
            WHERE environment_id IN (%s, %s)
            ORDER BY environment_id
            """,
            (environment_id, target_identity.target_environment_id),
        ).fetchall()
        assert rows == [
            (environment_id, "READ_ONLY_MAINTENANCE"),
            (target_identity.target_environment_id, "READ_ONLY_MAINTENANCE"),
        ]
    for source_key, expected in source_objects.items():
        relative = source_key.removeprefix(source_prefix)
        restored = s3.get_object(Bucket=target_bucket, Key=f"{target_prefix}/{relative}")
        try:
            assert restored["VersionId"] not in {None, "null"}
            assert restored["Body"].read() == expected
        finally:
            restored["Body"].close()

    records = authenticated_object_records(
        objects,
        source_bucket_reference=source_bucket_reference,
        source_prefix=source_prefix,
    )
    postgres_reconciliation = inspect_postgresql_restore(
        restore_plan,
        completed,
        endpoint=target,
        source_evidence=postgresql.source_evidence,
        source_server_major=postgresql.server_major,
        object_records=records,
        source_object_prefix=source_prefix,
        target_object_prefix=target_prefix,
        connection_factory=connect,
    )
    report = reconcile_restore(
        restore_plan,
        completed,
        inspectors=reconciliation_inspector_map(
            postgres=postgres_reconciliation,
            objects=RestoredObjectInventoryInspector(
                S3ObjectRestoreAdapter(
                    s3,
                    s3,
                    source_bucket=source_bucket,
                    source_bucket_reference=source_bucket_reference,
                    target_bucket=target_bucket,
                    target_bucket_reference=target_bucket_reference,
                    target_prefix=target_prefix,
                    restore_plan_id=restore_plan.plan_id,
                ),
                objects,
                source_prefix=source_prefix,
            ),
            temporal=TemporalWorkflowInspector(
                temporal_provider,
                temporal_document,
                postgres_reconciliation.workflow,
            ),
            runtime=ReadOnlyRuntimeInspector(runtime),
        ),
        verifier_identity="rst303-read-only-verifier",
        reconciled_at=requested_at,
    )
    report_path = tmp_path / "restore-reconciliation-report.json"
    report_sha256 = publish_restore_reconciliation_report(report, report_path)
    assert report.overall_status == "PASS"
    assert report.reconciliation_passed is True
    assert report.writes_enabled is False
    assert report.workers_enabled is False
    assert report.restore_verified is False
    assert load_restore_reconciliation_report(report_path) == report
    assert len(report_sha256) == 64
    if evidence_directory_value:
        _private(
            evidence_directory / "restore-checkpoint.json",
            canonical_json_bytes(completed.checkpoint.model_dump(mode="json")),
        )
        _private(
            evidence_directory / "restore-reconciliation-report.json",
            canonical_json_bytes(report.model_dump(mode="json")),
        )
        _private(
            evidence_directory / "restore-reconciliation-report.sha256",
            report_sha256.encode("ascii"),
        )
