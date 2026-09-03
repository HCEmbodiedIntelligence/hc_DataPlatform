from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from hc_data_platform.backup.postgresql import MaintenanceBackupLease
from hc_data_platform.backup.temporal import (
    TemporalBackupAdapter,
    TemporalBackupVerifier,
    TemporalConnectionConfig,
    TemporalProviderRecoveryContract,
    TemporalSdkAdminAdapter,
)


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required")
    return value


async def _exercise_real_temporal(tmp_path: Path) -> None:
    if _required("HC_BACKUP_TEMPORAL_INTEGRATION_ALLOW_MUTATION") != "yes":
        pytest.skip("Temporal integration mutation requires an exact opt-in")
    target = _required("HC_BACKUP_TEMPORAL_INTEGRATION_TARGET")
    namespace = os.environ.get("HC_BACKUP_TEMPORAL_INTEGRATION_NAMESPACE", "default")
    from temporalio.client import (
        Client,
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleIntervalSpec,
        ScheduleSpec,
        ScheduleState,
    )

    client = await Client.connect(target, namespace=namespace)
    config = TemporalConnectionConfig(
        deployment_mode="development_only",
        target=target,
        namespace=namespace,
        cluster_reference="temporal://development/bak205-integration",
        tls_enabled=False,
        stability_attempts=10,
        stability_interval_milliseconds=200,
    )
    adapter = TemporalSdkAdminAdapter(config, client=client)
    baseline = await adapter.development_probe()
    suffix = uuid4().hex
    schedule_id = f"hc-bak205-{suffix}-schedule"
    open_workflow_id = f"hc-bak205-{suffix}-open"
    task_queue = f"hc-bak205-{suffix}-queue"
    secret_payload = f"hc-bak205-payload-{uuid4().hex}"
    schedule_handle = None
    workflow_handle = None
    try:
        workflow_handle = await client.start_workflow(
            "HcBak205OpenWorkflow",
            {"credential": secret_payload},
            id=open_workflow_id,
            task_queue=task_queue,
        )
        schedule_handle = await client.create_schedule(
            schedule_id,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    "HcBak205ScheduledWorkflow",
                    {"credential": secret_payload},
                    id=f"hc-bak205-{suffix}-scheduled",
                    task_queue=task_queue,
                ),
                spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(hours=24))]),
                state=ScheduleState(paused=True),
            ),
        )
        report = await adapter.development_probe()
        assert report.evidence_status == "development_probe_only"
        assert report.schedule_count == baseline.schedule_count + 1
        assert report.open_workflow_count == baseline.open_workflow_count + 1
        assert report.workflow_histories_read is False
        assert report.internal_database_access is False
        assert report.payload_material_exported is False
        assert secret_payload not in report.model_dump_json()
        assert report.schedule_inventory_sha256 != baseline.schedule_inventory_sha256
        assert report.open_workflow_inventory_sha256 != baseline.open_workflow_inventory_sha256

        fixed_now = datetime(2026, 8, 28, 16, 0, tzinfo=timezone.utc)
        managed_config = TemporalConnectionConfig(
            deployment_mode="external_managed",
            target="managed-temporal.example.test:7233",
            namespace=namespace,
            cluster_reference="temporal://managed/bak205-protocol-integration",
            tls_enabled=True,
            api_key=secret_payload,
            stability_attempts=10,
            stability_interval_milliseconds=200,
        )
        managed_adapter = TemporalSdkAdminAdapter(
            managed_config,
            client=client,
            clock=lambda: fixed_now,
        )
        live_inventory = await managed_adapter.capture_inventory()
        provider_contract = TemporalProviderRecoveryContract(
            provider="integration_protocol_only",
            cluster_reference=managed_config.cluster_reference,
            namespace=namespace,
            expected_cluster_identity_sha256=live_inventory.cluster_identity_sha256,
            expected_namespace_identity_sha256=live_inventory.namespace_identity_sha256,
            expected_service_version=live_inventory.service_version,
            maximum_rpo_seconds=900,
            maximum_rto_seconds=7200,
            protection_reference="evidence://temporal/integration-protection-point",
            protection_observed_at="2026-08-28T15:59:00Z",
            evidence_reference="evidence://temporal/integration-provider-receipt",
            evidence_sha256="a" * 64,
            restore_runbook_reference="runbook://temporal/integration-restore",
            verified_at="2026-08-28T15:59:30Z",
            valid_until="2026-08-28T17:00:00Z",
        )
        staging = tmp_path / "managed-protocol-artifact"
        staging.mkdir(mode=0o700)
        lease = MaintenanceBackupLease(
            environment_id="bak205-integration",
            operation_id="bak205-integration",
            owner_instance_id=UUID("55555555-5555-4555-8555-555555555555"),
            fencing_token=205,
        )
        artifact = await TemporalBackupAdapter(
            managed_adapter,
            clock=lambda: fixed_now,
        ).create(
            provider_recovery=provider_contract,
            lease=lease,
            lease_verifier=lambda _: None,
            staging_directory=staging,
            mode="snapshot",
        )
        verification = await TemporalBackupVerifier().verify(
            artifact,
            provider=managed_adapter,
        )
        payload = artifact.path.read_text(encoding="utf-8")
        assert secret_payload not in payload
        assert managed_config.target not in payload
        assert verification.schedule_count == baseline.schedule_count + 1
        assert verification.open_workflow_count == baseline.open_workflow_count + 1
    finally:
        if schedule_handle is not None:
            await schedule_handle.delete()
        if workflow_handle is not None:
            with suppress(Exception):
                await workflow_handle.terminate(reason="hc-bak205 integration cleanup")

    restored = await adapter.development_probe()
    assert restored.schedule_count == baseline.schedule_count
    assert restored.open_workflow_count == baseline.open_workflow_count


@pytest.mark.integration
def test_real_temporal_schedule_and_visibility_probe_is_metadata_only(tmp_path: Path) -> None:
    asyncio.run(_exercise_real_temporal(tmp_path))
