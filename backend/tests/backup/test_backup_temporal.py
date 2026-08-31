from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID

import pytest
from pydantic import ValidationError

from hc_data_platform.backup.postgresql import MaintenanceBackupLease
from hc_data_platform.backup.temporal import (
    TemporalBackupAdapter,
    TemporalBackupError,
    TemporalBackupVerifier,
    TemporalConnectionConfig,
    TemporalDevelopmentProbeReport,
    TemporalInventorySnapshot,
    TemporalOpenWorkflowInventoryRecord,
    TemporalPolicyDocument,
    TemporalProviderRecoveryContract,
    TemporalScheduleInventoryRecord,
)

NOW = datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc)
SECRET_SENTINEL = b"temporal-api-key-never-in-artifact"


def _connection(
    mode: str = "external_managed",
) -> TemporalConnectionConfig:
    if mode == "development_only":
        return TemporalConnectionConfig(
            deployment_mode=mode,
            target="127.0.0.1:7233",
            namespace="hc-bak205",
            cluster_reference="temporal://development/hc-bak205",
            tls_enabled=False,
        )
    return TemporalConnectionConfig(
        deployment_mode=mode,
        target="temporal.example.test:7233",
        namespace="hc-bak205",
        cluster_reference="temporal://managed/hc-bak205",
        tls_enabled=True,
        api_key=SECRET_SENTINEL.decode(),
    )


def _schedule(*, paused: bool = True) -> TemporalScheduleInventoryRecord:
    return TemporalScheduleInventoryRecord(
        schedule_id="backup-schedule",
        workflow_type="BackupWorkflow",
        workflow_id="backup-workflow",
        task_queue="hc-data-pipeline",
        definition_sha256="1" * 64,
        paused=paused,
        limited_actions=False,
        remaining_actions=0,
        running_action_workflow_ids=(),
        next_action_times=("2026-08-29T00:00:00Z",),
        action_count=12,
        missed_catchup_count=0,
        skipped_overlap_count=0,
        created_at="2026-08-20T00:00:00Z",
        updated_at="2026-08-28T13:50:00Z",
    )


def _workflow() -> TemporalOpenWorkflowInventoryRecord:
    return TemporalOpenWorkflowInventoryRecord(
        workflow_id="ingest:v1:project:rollout",
        run_id="11111111-1111-4111-8111-111111111111",
        workflow_type="IngestRolloutWorkflow",
        task_queue="hc-data-pipeline",
        start_time="2026-08-28T13:59:00Z",
        execution_time="2026-08-28T13:59:00Z",
        history_length=7,
        root_workflow_id="ingest:v1:project:rollout",
        root_run_id="11111111-1111-4111-8111-111111111111",
    )


def _snapshot(*, paused: bool = True) -> TemporalInventorySnapshot:
    return TemporalInventorySnapshot(
        namespace="hc-bak205",
        namespace_identity_sha256="a" * 64,
        cluster_identity_sha256="b" * 64,
        service_version="1.31.2",
        namespace_retention_seconds=2_592_000,
        captured_at="2026-08-28T14:00:00Z",
        schedules=(_schedule(paused=paused),),
        open_workflows=(_workflow(),),
    )


def _contract(**updates: Any) -> TemporalProviderRecoveryContract:
    values: dict[str, Any] = {
        "provider": "temporal_cloud",
        "cluster_reference": "temporal://managed/hc-bak205",
        "namespace": "hc-bak205",
        "expected_cluster_identity_sha256": "b" * 64,
        "expected_namespace_identity_sha256": "a" * 64,
        "expected_service_version": "1.31.2",
        "maximum_rpo_seconds": 900,
        "maximum_rto_seconds": 7200,
        "protection_reference": "evidence://temporal/protection-point-204",
        "protection_observed_at": "2026-08-28T13:55:00Z",
        "evidence_reference": "s3://backup-evidence/temporal/provider-204.json",
        "evidence_sha256": "c" * 64,
        "restore_runbook_reference": "runbook://temporal/namespace-restore-v1",
        "verified_at": "2026-08-28T13:56:00Z",
        "valid_until": "2026-08-28T15:00:00Z",
    }
    values.update(updates)
    return TemporalProviderRecoveryContract.model_validate(values)


def _lease() -> MaintenanceBackupLease:
    return MaintenanceBackupLease(
        environment_id="env-bak205",
        operation_id="backup-bak205",
        owner_instance_id=UUID("22222222-2222-4222-8222-222222222222"),
        fencing_token=205,
    )


class FakeProvider:
    def __init__(
        self,
        *,
        config: TemporalConnectionConfig | None = None,
        snapshot: TemporalInventorySnapshot | None = None,
    ) -> None:
        self.config = config or _connection()
        self.snapshot = snapshot or _snapshot()
        self.capture_calls = 0
        self.preflight_calls = 0

    async def capture_inventory(self) -> TemporalInventorySnapshot:
        self.capture_calls += 1
        return self.snapshot

    async def preflight(self, policy: TemporalPolicyDocument) -> str:
        self.preflight_calls += 1
        assert policy.namespace == self.snapshot.namespace
        return "temporal-provider-preflight/v1:sha256:" + "d" * 64

    async def development_probe(self) -> TemporalDevelopmentProbeReport:
        return TemporalDevelopmentProbeReport(
            namespace=self.snapshot.namespace,
            service_version=self.snapshot.service_version,
            schedule_count=len(self.snapshot.schedules),
            open_workflow_count=len(self.snapshot.open_workflows),
            schedule_inventory_sha256="e" * 64,
            open_workflow_inventory_sha256="f" * 64,
        )


class PassthroughAge:
    @contextmanager
    def open(self, path: Path, *, cwd: Path) -> Iterator[BinaryIO]:
        assert path.parent == cwd
        descriptor = os.fdopen(
            os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
            "wb",
        )
        try:
            yield descriptor
        finally:
            descriptor.close()


class PassthroughAgeReader:
    @contextmanager
    def open(self, path: Path, *, cwd: Path) -> Iterator[BinaryIO]:
        assert path.parent == cwd
        with path.open("rb") as stream:
            yield stream


def _create(
    tmp_path: Path,
    *,
    provider: FakeProvider | None = None,
    mode: str = "snapshot",
    verifier: Any | None = None,
):
    tmp_path.chmod(0o700)
    provider = provider or FakeProvider()
    fence_calls: list[int] = []

    def fence(lease: MaintenanceBackupLease) -> None:
        fence_calls.append(lease.fencing_token)
        if verifier is not None:
            verifier(lease)

    artifact = asyncio.run(
        TemporalBackupAdapter(provider, clock=lambda: NOW).create(
            provider_recovery=_contract(),
            lease=_lease(),
            lease_verifier=fence,
            staging_directory=tmp_path,
            mode=mode,  # type: ignore[arg-type]
            encryptor=PassthroughAge() if mode == "portable" else None,  # type: ignore[arg-type]
        )
    )
    return provider, artifact, fence_calls


def test_connection_modes_fail_closed() -> None:
    with pytest.raises(ValidationError):
        TemporalConnectionConfig(
            deployment_mode="external_managed",
            target="127.0.0.1:7233",
            namespace="production",
            cluster_reference="temporal://managed/production",
            tls_enabled=True,
            api_key="key",
        )
    with pytest.raises(ValidationError):
        TemporalConnectionConfig(
            deployment_mode="external_managed",
            target="temporal.example.test:7233",
            namespace="production",
            cluster_reference="temporal://managed/production",
            tls_enabled=True,
        )
    with pytest.raises(ValidationError):
        TemporalConnectionConfig(
            deployment_mode="development_only",
            target="temporal.example.test:7233",
            namespace="default",
            cluster_reference="temporal://development/default",
            tls_enabled=False,
        )


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("external_self_hosted", "BACKUP_TEMPORAL_SELF_HOSTED_NOT_AUTHORIZED"),
        ("development_only", "BACKUP_TEMPORAL_DEVELOPMENT_ONLY"),
    ],
)
def test_non_managed_modes_cannot_create_artifacts(tmp_path: Path, mode: str, code: str) -> None:
    provider = FakeProvider(config=_connection(mode))
    tmp_path.chmod(0o700)
    with pytest.raises(TemporalBackupError, match=code):
        asyncio.run(
            TemporalBackupAdapter(provider, clock=lambda: NOW).create(
                provider_recovery=_contract(),
                lease=_lease(),
                lease_verifier=lambda _: None,
                staging_directory=tmp_path,
                mode="snapshot",
            )
        )
    assert provider.capture_calls == 0


def test_snapshot_create_binds_inventories_fence_and_no_secret(tmp_path: Path) -> None:
    provider, artifact, fence_calls = _create(tmp_path)

    assert provider.capture_calls == 1
    assert fence_calls == [205, 205, 205]
    assert artifact.mode == "snapshot"
    assert artifact.client_side_encryption == "repository_kms_only"
    assert artifact.schedule_count == 1
    assert artifact.open_workflow_count == 1
    assert artifact.path.stat().st_mode & 0o777 == 0o600
    payload = artifact.path.read_bytes()
    assert SECRET_SENTINEL not in payload
    assert b'workflow_histories_exported_by_platform":false' in payload
    assert b'internal_database_in_business_dump":false' in payload
    document = TemporalPolicyDocument.model_validate_json(payload)
    assert document.schedules[0].paused is True
    assert document.consistency_coordinate == artifact.consistency_coordinate


def test_portable_receipt_and_verifier_round_trip(tmp_path: Path) -> None:
    provider, artifact, _ = _create(tmp_path, mode="portable")

    assert artifact.media_type.endswith("+json+age")
    report = asyncio.run(
        TemporalBackupVerifier().verify(
            artifact,
            provider=provider,
            decryptor=PassthroughAgeReader(),  # type: ignore[arg-type]
        )
    )
    assert provider.preflight_calls == 1
    assert report.policy_sha256 == artifact.sha256
    assert report.provider_preflight_coordinate.endswith("d" * 64)


def test_artifact_tamper_and_receipt_mismatch_fail_closed(tmp_path: Path) -> None:
    provider, artifact, _ = _create(tmp_path)
    artifact.path.write_bytes(artifact.path.read_bytes() + b" ")

    with pytest.raises(TemporalBackupError, match="BACKUP_TEMPORAL_ARTIFACT_HASH_MISMATCH"):
        asyncio.run(TemporalBackupVerifier().verify(artifact, provider=provider))
    assert provider.preflight_calls == 0


def test_fence_failure_precedes_provider_and_artifact(tmp_path: Path) -> None:
    provider = FakeProvider()

    def reject(_: MaintenanceBackupLease) -> None:
        raise RuntimeError("stale lease with sensitive diagnostics")

    tmp_path.chmod(0o700)
    with pytest.raises(TemporalBackupError, match="BACKUP_TEMPORAL_FENCE_REJECTED"):
        asyncio.run(
            TemporalBackupAdapter(provider, clock=lambda: NOW).create(
                provider_recovery=_contract(),
                lease=_lease(),
                lease_verifier=reject,
                staging_directory=tmp_path,
                mode="snapshot",
            )
        )
    assert provider.capture_calls == 0
    assert list(tmp_path.iterdir()) == []


def test_unpaused_schedule_is_rejected_before_artifact(tmp_path: Path) -> None:
    provider = FakeProvider(snapshot=_snapshot(paused=False))
    tmp_path.chmod(0o700)

    with pytest.raises(TemporalBackupError, match="BACKUP_TEMPORAL_SCHEDULE_NOT_PAUSED"):
        asyncio.run(
            TemporalBackupAdapter(provider, clock=lambda: NOW).create(
                provider_recovery=_contract(),
                lease=_lease(),
                lease_verifier=lambda _: None,
                staging_directory=tmp_path,
                mode="snapshot",
            )
        )
    assert list(tmp_path.iterdir()) == []


def test_provider_contract_identity_and_freshness_fail_closed(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    provider = FakeProvider()
    bad_identity = _contract(expected_service_version="1.30.0")
    with pytest.raises(TemporalBackupError, match="BACKUP_TEMPORAL_PROVIDER_POLICY_MISMATCH"):
        asyncio.run(
            TemporalBackupAdapter(provider, clock=lambda: NOW).create(
                provider_recovery=bad_identity,
                lease=_lease(),
                lease_verifier=lambda _: None,
                staging_directory=tmp_path,
                mode="snapshot",
            )
        )
    assert list(tmp_path.iterdir()) == []

    stale = _contract(
        protection_observed_at="2026-08-28T13:00:00Z",
        verified_at="2026-08-28T13:01:00Z",
    )
    with pytest.raises(TemporalBackupError, match="BACKUP_TEMPORAL_PROVIDER_RPO_EXCEEDED"):
        asyncio.run(
            TemporalBackupAdapter(provider, clock=lambda: NOW).create(
                provider_recovery=stale,
                lease=_lease(),
                lease_verifier=lambda _: None,
                staging_directory=tmp_path,
                mode="snapshot",
            )
        )


def test_policy_rejects_unpaused_schedule_and_unknown_fields() -> None:
    snapshot = _snapshot(paused=True)
    schedule_hash = hashlib.sha256(b"unused").hexdigest()
    del schedule_hash
    provider = _contract()
    values = {
        "source_environment_id": "env-bak205",
        "cluster_reference": provider.cluster_reference,
        "namespace": snapshot.namespace,
        "namespace_identity_sha256": snapshot.namespace_identity_sha256,
        "cluster_identity_sha256": snapshot.cluster_identity_sha256,
        "service_version": snapshot.service_version,
        "namespace_retention_seconds": snapshot.namespace_retention_seconds,
        "captured_at": snapshot.captured_at,
        "provider_recovery": provider,
        "schedules": (_schedule(paused=False),),
        "open_workflows": snapshot.open_workflows,
        "schedule_inventory_sha256": "0" * 64,
        "open_workflow_inventory_sha256": "0" * 64,
        "consistency_coordinate": "temporal-policy/v1:sha256:" + "0" * 64,
    }
    with pytest.raises(ValidationError, match="every Temporal schedule must be paused"):
        TemporalPolicyDocument.model_validate(values)
    with pytest.raises(ValidationError):
        TemporalProviderRecoveryContract.model_validate(
            {**provider.model_dump(mode="json"), "unexpected": True}
        )


def test_recovery_references_reject_credentials_and_queries() -> None:
    with pytest.raises(ValidationError):
        _contract(evidence_reference="https://user:password@example.test/evidence")
    with pytest.raises(ValidationError):
        _contract(evidence_reference="https://example.test/evidence?token=value")
