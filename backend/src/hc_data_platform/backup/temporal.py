"""Temporal recovery policy and unfinished-work inventory artifacts.

The production adapter is deliberately limited to an externally managed
Temporal namespace.  It uses supported Namespace, Cluster, Schedule, and
Visibility APIs and never reads workflow payloads, workflow histories, or
Temporal persistence tables.  Development servers can be probed for API
compatibility, but a development probe can never become backup evidence.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import ipaddress
import json
import os
import re
import stat
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Annotated, Any, BinaryIO, Literal, Protocol, TypeVar, cast
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretBytes,
    SecretStr,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from hc_data_platform.backup.contracts import (
    Sha256,
    canonical_json_bytes,
    find_plaintext_secret_material,
)
from hc_data_platform.backup.objects import AgeDecryptedReader, AgeEncryptedWriter
from hc_data_platform.backup.postgresql import MaintenanceBackupLease

TemporalNamespace = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$",
    ),
]
TemporalIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=1024, pattern=r"^[^\x00\r\n]+$"),
]
TemporalVersion = Annotated[
    str,
    StringConstraints(min_length=1, max_length=63, pattern=r"^[A-Za-z0-9][A-Za-z0-9.+_-]*$"),
]
TemporalReference = Annotated[
    str,
    StringConstraints(min_length=1, max_length=1024, pattern=r"^[^\x00\r\n]+$"),
]
TemporalTimestamp = Annotated[
    str,
    StringConstraints(
        pattern=(
            r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
            r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
            r"(?:\.[0-9]{1,6})?Z$"
        )
    ),
]

_MAX_POLICY_BYTES = 64 * 1024 * 1024
_STREAM_CHUNK_BYTES = 1024 * 1024
_REFERENCE_SCHEMES = frozenset({"evidence", "runbook", "s3", "https"})
_TARGET = re.compile(r"^(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9][A-Za-z0-9.-]{0,252}):[0-9]{1,5}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TemporalBackupError(RuntimeError):
    """Stable, redacted Temporal backup failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class TemporalConnectionConfig(_StrictModel):
    deployment_mode: Literal[
        "external_managed",
        "external_self_hosted",
        "development_only",
        "compose_functional",
    ]
    target: str = Field(min_length=3, max_length=255)
    namespace: TemporalNamespace
    cluster_reference: TemporalReference
    tls_enabled: bool
    api_key: SecretStr | None = None
    server_root_ca_cert: SecretBytes | None = None
    client_certificate: SecretBytes | None = None
    client_private_key: SecretBytes | None = None
    verification_server_name: str | None = Field(default=None, max_length=253)
    rpc_timeout_seconds: int = Field(default=15, ge=1, le=120)
    maximum_schedules: int = Field(default=100_000, ge=1, le=1_000_000)
    maximum_open_workflows: int = Field(default=1_000_000, ge=1, le=5_000_000)
    stability_attempts: int = Field(default=5, ge=2, le=10)
    stability_interval_milliseconds: int = Field(default=100, ge=10, le=1000)

    @model_validator(mode="after")
    def validate_transport(self) -> TemporalConnectionConfig:
        host = _target_host(self.target)
        loopback = _is_loopback(host)
        certificate_pair = (
            self.client_certificate is not None or self.client_private_key is not None
        )
        if certificate_pair and (
            self.client_certificate is None or self.client_private_key is None
        ):
            raise ValueError("Temporal mTLS requires both client certificate and private key")
        if self.deployment_mode == "external_managed":
            if not self.tls_enabled or loopback:
                raise ValueError("external managed Temporal requires non-loopback TLS")
            if self.api_key is None and not certificate_pair:
                raise ValueError("external managed Temporal requires API-key or mTLS identity")
        elif self.deployment_mode in {"development_only", "compose_functional"}:
            if not loopback or self.tls_enabled or self.api_key is not None or certificate_pair:
                raise ValueError("local Temporal must be plaintext loopback without credentials")
        if self.verification_server_name is not None and not self.tls_enabled:
            raise ValueError("a Temporal TLS server name requires TLS")
        _validate_temporal_cluster_reference(self.cluster_reference)
        return self


class TemporalScheduleInventoryRecord(_StrictModel):
    schedule_id: TemporalIdentifier
    workflow_type: TemporalIdentifier
    workflow_id: TemporalIdentifier
    task_queue: TemporalIdentifier
    definition_sha256: Sha256
    paused: bool
    limited_actions: bool
    remaining_actions: int = Field(ge=0)
    running_action_workflow_ids: tuple[TemporalIdentifier, ...]
    next_action_times: tuple[TemporalTimestamp, ...]
    action_count: int = Field(ge=0)
    missed_catchup_count: int = Field(ge=0)
    skipped_overlap_count: int = Field(ge=0)
    created_at: TemporalTimestamp
    updated_at: TemporalTimestamp | None

    @model_validator(mode="after")
    def require_sorted_values(self) -> TemporalScheduleInventoryRecord:
        if self.running_action_workflow_ids != tuple(sorted(set(self.running_action_workflow_ids))):
            raise ValueError("running schedule action identifiers must be sorted and unique")
        if self.next_action_times != tuple(sorted(set(self.next_action_times))):
            raise ValueError("next schedule action timestamps must be sorted and unique")
        return self


class TemporalOpenWorkflowInventoryRecord(_StrictModel):
    workflow_id: TemporalIdentifier
    run_id: TemporalIdentifier
    workflow_type: TemporalIdentifier
    task_queue: TemporalIdentifier
    status: Literal["running"] = "running"
    start_time: TemporalTimestamp
    execution_time: TemporalTimestamp | None
    history_length: int = Field(ge=0)
    parent_workflow_id: TemporalIdentifier | None = None
    parent_run_id: TemporalIdentifier | None = None
    root_workflow_id: TemporalIdentifier | None = None
    root_run_id: TemporalIdentifier | None = None


class TemporalInventorySnapshot(_StrictModel):
    namespace: TemporalNamespace
    namespace_identity_sha256: Sha256
    cluster_identity_sha256: Sha256
    service_version: TemporalVersion
    namespace_retention_seconds: int = Field(gt=0, le=10 * 365 * 24 * 60 * 60)
    schedules_supported: Literal[True] = True
    captured_at: TemporalTimestamp
    schedules: tuple[TemporalScheduleInventoryRecord, ...]
    open_workflows: tuple[TemporalOpenWorkflowInventoryRecord, ...]

    @model_validator(mode="after")
    def require_sorted_inventories(self) -> TemporalInventorySnapshot:
        if self.schedules != tuple(sorted(self.schedules, key=lambda item: item.schedule_id)):
            raise ValueError("Temporal schedules must be sorted")
        schedule_ids = [item.schedule_id for item in self.schedules]
        if len(schedule_ids) != len(set(schedule_ids)):
            raise ValueError("Temporal schedule identifiers must be unique")
        if self.open_workflows != tuple(
            sorted(self.open_workflows, key=lambda item: (item.workflow_id, item.run_id))
        ):
            raise ValueError("open Temporal workflows must be sorted")
        workflow_runs = [(item.workflow_id, item.run_id) for item in self.open_workflows]
        if len(workflow_runs) != len(set(workflow_runs)):
            raise ValueError("open Temporal workflow runs must be unique")
        return self


class TemporalProviderRecoveryContract(_StrictModel):
    format_version: Literal["hc-temporal-provider-recovery/v1"] = "hc-temporal-provider-recovery/v1"
    deployment_mode: Literal["external_managed"] = "external_managed"
    provider: str = Field(min_length=1, max_length=127, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    cluster_reference: TemporalReference
    namespace: TemporalNamespace
    expected_cluster_identity_sha256: Sha256
    expected_namespace_identity_sha256: Sha256
    expected_service_version: TemporalVersion
    history_owner: Literal["temporal_operations"] = "temporal_operations"
    history_protection_method: Literal["provider_supported_ha_backup"] = (
        "provider_supported_ha_backup"
    )
    restore_method: Literal["provider_supported_namespace_or_history_restore"] = (
        "provider_supported_namespace_or_history_restore"
    )
    reconnect_method: Literal["platform_read_only_reconcile_then_worker_enable"] = (
        "platform_read_only_reconcile_then_worker_enable"
    )
    internal_database_access: Literal[False] = False
    platform_history_export: Literal[False] = False
    maximum_rpo_seconds: int = Field(gt=0, le=900)
    maximum_rto_seconds: int = Field(gt=0, le=7200)
    protection_reference: TemporalReference
    protection_observed_at: TemporalTimestamp
    evidence_reference: TemporalReference
    evidence_sha256: Sha256
    restore_runbook_reference: TemporalReference
    verified_at: TemporalTimestamp
    valid_until: TemporalTimestamp

    @model_validator(mode="after")
    def validate_references_and_times(self) -> TemporalProviderRecoveryContract:
        _validate_temporal_cluster_reference(self.cluster_reference)
        for value in (
            self.protection_reference,
            self.evidence_reference,
            self.restore_runbook_reference,
        ):
            _validate_opaque_reference(value)
        observed = _parse_timestamp(self.protection_observed_at)
        verified = _parse_timestamp(self.verified_at)
        valid_until = _parse_timestamp(self.valid_until)
        if observed > verified or verified >= valid_until:
            raise ValueError("Temporal recovery evidence timestamps are not ordered")
        return self


class TemporalPolicyDocument(_StrictModel):
    format_version: Literal["hc-temporal-backup-policy/v1"] = "hc-temporal-backup-policy/v1"
    source_environment_id: str = Field(min_length=1, max_length=255)
    deployment_mode: Literal["external_managed"] = "external_managed"
    cluster_reference: TemporalReference
    namespace: TemporalNamespace
    namespace_identity_sha256: Sha256
    cluster_identity_sha256: Sha256
    service_version: TemporalVersion
    namespace_retention_seconds: int = Field(gt=0, le=10 * 365 * 24 * 60 * 60)
    recovery_policy: Literal["provider_supported_ha_backup"] = "provider_supported_ha_backup"
    internal_database_in_business_dump: Literal[False] = False
    workflow_histories_exported_by_platform: Literal[False] = False
    captured_at: TemporalTimestamp
    provider_recovery: TemporalProviderRecoveryContract
    schedules: tuple[TemporalScheduleInventoryRecord, ...]
    open_workflows: tuple[TemporalOpenWorkflowInventoryRecord, ...]
    schedule_inventory_sha256: Sha256
    open_workflow_inventory_sha256: Sha256
    consistency_coordinate: str = Field(pattern=r"^temporal-policy/v1:sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def bind_document(self) -> TemporalPolicyDocument:
        if self.cluster_reference != self.provider_recovery.cluster_reference:
            raise ValueError("Temporal provider contract has a different cluster reference")
        if self.namespace != self.provider_recovery.namespace:
            raise ValueError("Temporal provider contract has a different namespace")
        if (
            self.cluster_identity_sha256 != self.provider_recovery.expected_cluster_identity_sha256
            or self.namespace_identity_sha256
            != self.provider_recovery.expected_namespace_identity_sha256
            or self.service_version != self.provider_recovery.expected_service_version
        ):
            raise ValueError("Temporal provider contract does not bind the live service identity")
        if self.schedules != tuple(sorted(self.schedules, key=lambda item: item.schedule_id)):
            raise ValueError("Temporal schedules must be sorted")
        if any(not item.paused for item in self.schedules):
            raise ValueError("every Temporal schedule must be paused")
        if self.open_workflows != tuple(
            sorted(self.open_workflows, key=lambda item: (item.workflow_id, item.run_id))
        ):
            raise ValueError("open Temporal workflows must be sorted")
        schedule_hash = _inventory_hash(self.schedules)
        workflow_hash = _inventory_hash(self.open_workflows)
        if schedule_hash != self.schedule_inventory_sha256:
            raise ValueError("Temporal schedule inventory hash does not match")
        if workflow_hash != self.open_workflow_inventory_sha256:
            raise ValueError("Temporal open-workflow inventory hash does not match")
        if _policy_coordinate(self) != self.consistency_coordinate:
            raise ValueError("Temporal policy consistency coordinate does not match")
        findings = find_plaintext_secret_material(self.model_dump(mode="json"))
        if findings:
            raise ValueError("Temporal policy contains Secret-shaped material")
        return self


class TemporalPolicySourceArtifact(_StrictModel):
    path: Path
    mode: Literal["snapshot", "portable"]
    media_type: Literal[
        "application/vnd.hc.temporal-policy.v1+json",
        "application/vnd.hc.temporal-policy.v1+json+age",
    ]
    client_side_encryption: Literal["repository_kms_only", "age_x25519_v1"]
    size_bytes: int = Field(gt=0)
    sha256: Sha256

    @model_validator(mode="after")
    def bind_encryption_mode(self) -> TemporalPolicySourceArtifact:
        expected = (
            ("application/vnd.hc.temporal-policy.v1+json+age", "age_x25519_v1")
            if self.mode == "portable"
            else (
                "application/vnd.hc.temporal-policy.v1+json",
                "repository_kms_only",
            )
        )
        if (self.media_type, self.client_side_encryption) != expected:
            raise ValueError("Temporal policy encryption metadata differs from its mode")
        return self


class TemporalPolicyArtifact(TemporalPolicySourceArtifact):
    logical_path: Literal["temporal/policy.json"] = "temporal/policy.json"
    schedule_count: int = Field(ge=0)
    open_workflow_count: int = Field(ge=0)
    schedule_inventory_sha256: Sha256
    open_workflow_inventory_sha256: Sha256
    consistency_coordinate: str = Field(pattern=r"^temporal-policy/v1:sha256:[0-9a-f]{64}$")


class TemporalVerificationReport(_StrictModel):
    policy_sha256: Sha256
    schedule_count: int = Field(ge=0)
    open_workflow_count: int = Field(ge=0)
    schedule_inventory_sha256: Sha256
    open_workflow_inventory_sha256: Sha256
    consistency_coordinate: str = Field(pattern=r"^temporal-policy/v1:sha256:[0-9a-f]{64}$")
    provider_preflight_coordinate: str = Field(
        pattern=r"^temporal-provider-preflight/v1:sha256:[0-9a-f]{64}$"
    )


class TemporalDevelopmentProbeReport(_StrictModel):
    evidence_status: Literal["development_probe_only"] = "development_probe_only"
    internal_database_access: Literal[False] = False
    workflow_histories_read: Literal[False] = False
    namespace: TemporalNamespace
    service_version: TemporalVersion
    schedule_count: int = Field(ge=0)
    open_workflow_count: int = Field(ge=0)
    schedule_inventory_sha256: Sha256
    open_workflow_inventory_sha256: Sha256
    payload_material_exported: Literal[False] = False


class TemporalAdminVisibilityPort(Protocol):
    config: TemporalConnectionConfig

    async def capture_inventory(self) -> TemporalInventorySnapshot: ...

    async def preflight(self, policy: TemporalPolicyDocument) -> str: ...

    async def development_probe(self) -> TemporalDevelopmentProbeReport: ...


class _TemporalProviderState(_StrictModel):
    namespace: TemporalNamespace
    namespace_identity_sha256: Sha256
    cluster_identity_sha256: Sha256
    service_version: TemporalVersion
    namespace_retention_seconds: int = Field(gt=0, le=10 * 365 * 24 * 60 * 60)
    schedules_supported: Literal[True] = True
    schedules: tuple[TemporalScheduleInventoryRecord, ...]
    open_workflows: tuple[TemporalOpenWorkflowInventoryRecord, ...]


T = TypeVar("T")


class TemporalSdkAdminAdapter:
    """Read-only Temporal SDK adapter over supported admin/visibility APIs."""

    def __init__(
        self,
        config: TemporalConnectionConfig,
        *,
        client: Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._client = client
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def connect(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from temporalio.client import Client, TLSConfig

            tls: bool | TLSConfig
            if self.config.tls_enabled:
                tls = TLSConfig(
                    server_root_ca_cert=_secret_bytes(self.config.server_root_ca_cert),
                    client_cert=_secret_bytes(self.config.client_certificate),
                    client_private_key=_secret_bytes(self.config.client_private_key),
                    verification_server_name=self.config.verification_server_name,
                )
            else:
                tls = False
            self._client = await Client.connect(
                self.config.target,
                namespace=self.config.namespace,
                api_key=(
                    self.config.api_key.get_secret_value()
                    if self.config.api_key is not None
                    else None
                ),
                tls=tls,
                identity="hc-temporal-backup",
            )
        except Exception as exc:
            raise _map_provider_error(exc) from exc
        return self._client

    async def capture_inventory(self) -> TemporalInventorySnapshot:
        if self.config.deployment_mode == "external_self_hosted":
            raise TemporalBackupError(
                "BACKUP_TEMPORAL_SELF_HOSTED_NOT_AUTHORIZED",
                "self-hosted Temporal has no approved cold-snapshot recovery contract",
            )
        if self.config.deployment_mode == "development_only":
            raise TemporalBackupError(
                "BACKUP_TEMPORAL_DEVELOPMENT_ONLY",
                "a development Temporal service cannot produce backup evidence",
            )
        state = await self._stable_state(require_paused=True)
        return TemporalInventorySnapshot(
            **state.model_dump(mode="python"),
            captured_at=_timestamp(self._clock()),
        )

    async def preflight(self, policy: TemporalPolicyDocument) -> str:
        if self.config.deployment_mode not in {"external_managed", "compose_functional"}:
            code = (
                "BACKUP_TEMPORAL_SELF_HOSTED_NOT_AUTHORIZED"
                if self.config.deployment_mode == "external_self_hosted"
                else "BACKUP_TEMPORAL_DEVELOPMENT_ONLY"
            )
            raise TemporalBackupError(
                code,
                "the configured Temporal mode cannot satisfy production recovery preflight",
            )
        if (
            policy.cluster_reference != self.config.cluster_reference
            or policy.namespace != self.config.namespace
        ):
            raise TemporalBackupError(
                "RESTORE_TEMPORAL_TARGET_MISMATCH",
                "the Temporal target differs from the recorded recovery policy",
            )
        _validate_recovery_contract(policy.provider_recovery, self._clock())
        state = await self._stable_state(require_paused=True)
        if (
            state.cluster_identity_sha256 != policy.cluster_identity_sha256
            or state.namespace_identity_sha256 != policy.namespace_identity_sha256
            or state.service_version != policy.service_version
        ):
            raise TemporalBackupError(
                "RESTORE_TEMPORAL_SERVICE_IDENTITY_MISMATCH",
                "the Temporal service identity differs from the recorded policy",
            )
        material = {
            "cluster_reference": policy.cluster_reference,
            "namespace": policy.namespace,
            "cluster_identity_sha256": state.cluster_identity_sha256,
            "namespace_identity_sha256": state.namespace_identity_sha256,
            "service_version": state.service_version,
            "provider_evidence_sha256": policy.provider_recovery.evidence_sha256,
            "protection_reference": policy.provider_recovery.protection_reference,
        }
        return "temporal-provider-preflight/v1:sha256:" + _sha256_json(material)

    async def development_probe(self) -> TemporalDevelopmentProbeReport:
        if self.config.deployment_mode == "external_self_hosted":
            raise TemporalBackupError(
                "BACKUP_TEMPORAL_SELF_HOSTED_NOT_AUTHORIZED",
                "self-hosted Temporal has no approved cold-snapshot recovery contract",
            )
        if self.config.deployment_mode != "development_only":
            raise TemporalBackupError(
                "BACKUP_TEMPORAL_PROBE_MODE_INVALID",
                "the development probe is only available for development Temporal",
            )
        state = await self._stable_state(require_paused=False)
        return TemporalDevelopmentProbeReport(
            namespace=state.namespace,
            service_version=state.service_version,
            schedule_count=len(state.schedules),
            open_workflow_count=len(state.open_workflows),
            schedule_inventory_sha256=_inventory_hash(state.schedules),
            open_workflow_inventory_sha256=_inventory_hash(state.open_workflows),
        )

    async def _stable_state(self, *, require_paused: bool) -> _TemporalProviderState:
        previous = await self._read_state(require_paused=require_paused)
        for _ in range(1, self.config.stability_attempts):
            await asyncio.sleep(self.config.stability_interval_milliseconds / 1000)
            current = await self._read_state(require_paused=require_paused)
            if current == previous:
                return current
            previous = current
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_INVENTORY_CHANGED",
            "the Temporal namespace changed during the fenced inventory window",
        )

    async def _read_state(self, *, require_paused: bool) -> _TemporalProviderState:
        client = await self.connect()
        (
            namespace,
            namespace_id,
            cluster_id,
            service_version,
            retention,
            supports,
        ) = await self._service_identity(client)
        schedules = await self._schedules(client, require_paused=require_paused)
        workflows = await self._open_workflows(client)
        return _TemporalProviderState(
            namespace=namespace,
            namespace_identity_sha256=_sha256_text(namespace_id),
            cluster_identity_sha256=_sha256_text(cluster_id),
            service_version=service_version,
            namespace_retention_seconds=retention,
            schedules_supported=supports,
            schedules=schedules,
            open_workflows=workflows,
        )

    async def _service_identity(self, client: Any) -> tuple[str, str, str, str, int, Literal[True]]:
        try:
            from temporalio.api.enums.v1 import NamespaceState
            from temporalio.api.workflowservice.v1 import (
                DescribeNamespaceRequest,
                GetClusterInfoRequest,
                GetSystemInfoRequest,
            )

            service = client.service_client.workflow_service
            namespace_response = await service.describe_namespace(
                DescribeNamespaceRequest(namespace=self.config.namespace),
                timeout=_seconds(self.config.rpc_timeout_seconds),
            )
            cluster_response = await service.get_cluster_info(
                GetClusterInfoRequest(), timeout=_seconds(self.config.rpc_timeout_seconds)
            )
            system_response = await service.get_system_info(
                GetSystemInfoRequest(), timeout=_seconds(self.config.rpc_timeout_seconds)
            )
        except Exception as exc:
            raise _map_provider_error(exc) from exc
        info = namespace_response.namespace_info
        state_name = NamespaceState.Name(info.state)
        retention_value = namespace_response.config.workflow_execution_retention_ttl
        retention = int(retention_value.seconds)
        server_version = str(cluster_response.server_version)
        if (
            str(info.name) != self.config.namespace
            or state_name != "NAMESPACE_STATE_REGISTERED"
            or not bool(info.id)
            or not bool(cluster_response.cluster_id)
            or not server_version
            or server_version != str(system_response.server_version)
            or retention < 1
            or retention_value.nanos != 0
            or not bool(info.supports_schedules)
            or not bool(system_response.capabilities.supports_schedules)
        ):
            raise TemporalBackupError(
                "BACKUP_TEMPORAL_SERVICE_IDENTITY_INVALID",
                "the Temporal namespace or cluster identity violates the recovery contract",
            )
        try:
            TemporalVersionAdapter.validate_python(server_version)
        except ValidationError as exc:
            raise TemporalBackupError(
                "BACKUP_TEMPORAL_SERVICE_IDENTITY_INVALID",
                "the Temporal service version is not a bounded immutable value",
            ) from exc
        return (
            str(info.name),
            str(info.id),
            str(cluster_response.cluster_id),
            server_version,
            retention,
            True,
        )

    async def _schedule_ids(self, client: Any) -> tuple[str, ...]:
        try:
            iterator_value = client.list_schedules(
                page_size=min(1000, self.config.maximum_schedules + 1),
                rpc_timeout=_seconds(self.config.rpc_timeout_seconds),
            )
            iterator = await _maybe_await(iterator_value)
            identifiers: list[str] = []
            async for item in cast(AsyncIterator[Any], iterator):
                identifiers.append(str(item.id))
                if len(identifiers) > self.config.maximum_schedules:
                    raise TemporalBackupError(
                        "BACKUP_TEMPORAL_INVENTORY_LIMIT_EXCEEDED",
                        "the Temporal schedule inventory exceeds its configured bound",
                    )
        except TemporalBackupError:
            raise
        except Exception as exc:
            raise _map_provider_error(exc) from exc
        if len(identifiers) != len(set(identifiers)) or any(not value for value in identifiers):
            raise TemporalBackupError(
                "BACKUP_TEMPORAL_PROVIDER_RESPONSE_INVALID",
                "the Temporal schedule listing contains invalid identities",
            )
        return tuple(sorted(identifiers))

    async def _schedules(
        self, client: Any, *, require_paused: bool
    ) -> tuple[TemporalScheduleInventoryRecord, ...]:
        try:
            from temporalio.api.workflowservice.v1 import DescribeScheduleRequest

            service = client.service_client.workflow_service
            records: list[TemporalScheduleInventoryRecord] = []
            for schedule_id in await self._schedule_ids(client):
                response = await service.describe_schedule(
                    DescribeScheduleRequest(
                        namespace=self.config.namespace,
                        schedule_id=schedule_id,
                    ),
                    timeout=_seconds(self.config.rpc_timeout_seconds),
                )
                records.append(
                    _schedule_record(
                        schedule_id,
                        response,
                        require_paused=require_paused,
                    )
                )
        except TemporalBackupError:
            raise
        except Exception as exc:
            raise _map_provider_error(exc) from exc
        return tuple(sorted(records, key=lambda item: item.schedule_id))

    async def _open_workflows(self, client: Any) -> tuple[TemporalOpenWorkflowInventoryRecord, ...]:
        try:
            iterator_value = client.list_workflows(
                query='ExecutionStatus = "Running"',
                limit=self.config.maximum_open_workflows + 1,
                page_size=min(1000, self.config.maximum_open_workflows + 1),
                rpc_timeout=_seconds(self.config.rpc_timeout_seconds),
            )
            iterator = await _maybe_await(iterator_value)
            records: list[TemporalOpenWorkflowInventoryRecord] = []
            async for execution in cast(AsyncIterator[Any], iterator):
                records.append(_workflow_record(execution, namespace=self.config.namespace))
                if len(records) > self.config.maximum_open_workflows:
                    raise TemporalBackupError(
                        "BACKUP_TEMPORAL_INVENTORY_LIMIT_EXCEEDED",
                        "the Temporal open-workflow inventory exceeds its configured bound",
                    )
        except TemporalBackupError:
            raise
        except Exception as exc:
            raise _map_provider_error(exc) from exc
        records.sort(key=lambda item: (item.workflow_id, item.run_id))
        identities = [(item.workflow_id, item.run_id) for item in records]
        if len(identities) != len(set(identities)):
            raise TemporalBackupError(
                "BACKUP_TEMPORAL_PROVIDER_RESPONSE_INVALID",
                "the Temporal visibility response contains duplicate workflow runs",
            )
        return tuple(records)


class TemporalBackupAdapter:
    """Create fenced Temporal recovery-policy artifacts."""

    def __init__(
        self,
        provider: TemporalAdminVisibilityPort,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def create(
        self,
        *,
        provider_recovery: TemporalProviderRecoveryContract,
        lease: MaintenanceBackupLease,
        lease_verifier: Callable[[MaintenanceBackupLease], None],
        staging_directory: Path,
        mode: Literal["snapshot", "portable"],
        encryptor: AgeEncryptedWriter | None = None,
    ) -> TemporalPolicyArtifact:
        if (mode == "portable") != (encryptor is not None):
            raise TemporalBackupError(
                "BACKUP_TEMPORAL_ENCRYPTION_INVALID",
                "portable Temporal policy requires exactly one age encryptor",
            )
        if self._provider.config.deployment_mode not in {
            "external_managed",
            "compose_functional",
        }:
            code = (
                "BACKUP_TEMPORAL_SELF_HOSTED_NOT_AUTHORIZED"
                if self._provider.config.deployment_mode == "external_self_hosted"
                else "BACKUP_TEMPORAL_DEVELOPMENT_ONLY"
            )
            raise TemporalBackupError(
                code,
                "the configured Temporal mode cannot produce production backup evidence",
            )
        _validate_recovery_contract(provider_recovery, self._clock())
        self._verify_lease(lease, lease_verifier)
        snapshot = await self._provider.capture_inventory()
        self._verify_lease(lease, lease_verifier)
        if any(not item.paused for item in snapshot.schedules):
            raise TemporalBackupError(
                "BACKUP_TEMPORAL_SCHEDULE_NOT_PAUSED",
                "every Temporal schedule must be paused before backup inventory",
            )
        _bind_provider_contract(provider_recovery, snapshot, self._provider.config)
        schedule_hash = _inventory_hash(snapshot.schedules)
        workflow_hash = _inventory_hash(snapshot.open_workflows)
        base = {
            "source_environment_id": lease.environment_id,
            "cluster_reference": self._provider.config.cluster_reference,
            "namespace": snapshot.namespace,
            "namespace_identity_sha256": snapshot.namespace_identity_sha256,
            "cluster_identity_sha256": snapshot.cluster_identity_sha256,
            "service_version": snapshot.service_version,
            "namespace_retention_seconds": snapshot.namespace_retention_seconds,
            "captured_at": snapshot.captured_at,
            "provider_recovery": provider_recovery,
            "schedules": snapshot.schedules,
            "open_workflows": snapshot.open_workflows,
            "schedule_inventory_sha256": schedule_hash,
            "open_workflow_inventory_sha256": workflow_hash,
        }
        coordinate = _policy_coordinate_from_values(base)
        try:
            document = TemporalPolicyDocument(**base, consistency_coordinate=coordinate)
        except (ValueError, ValidationError) as exc:
            raise TemporalBackupError(
                "BACKUP_TEMPORAL_POLICY_INVALID",
                "the captured Temporal inventory violates its strict policy contract",
            ) from exc
        self._verify_lease(lease, lease_verifier)
        staging = _secure_staging(staging_directory)
        temporal_directory = _private_directory(staging, "temporal")
        filename = "policy.json.age" if mode == "portable" else "policy.json"
        destination = temporal_directory / filename
        temporary = temporal_directory / f".{filename}.{os.getpid()}.partial"
        if any(path.exists() or path.is_symlink() for path in (destination, temporary)):
            raise TemporalBackupError(
                "BACKUP_TEMPORAL_ARTIFACT_EXISTS",
                "a Temporal policy artifact already exists",
            )
        published = False
        try:
            writer = (
                encryptor.open(temporary, cwd=temporal_directory)
                if encryptor is not None
                else _private_writer(temporary)
            )
            with writer as stream:
                stream.write(canonical_json_bytes(document.model_dump(mode="json")))
                stream.write(b"\n")
            _publish_no_replace(temporary, destination)
            published = True
            _fsync_directory(temporal_directory)
            _fsync_directory(staging)
        except BaseException:
            with suppress(FileNotFoundError):
                temporary.unlink()
            if published:
                with suppress(FileNotFoundError):
                    destination.unlink()
            raise
        size, digest = _sha256_file(destination)
        return TemporalPolicyArtifact(
            path=destination,
            mode=mode,
            media_type=(
                "application/vnd.hc.temporal-policy.v1+json+age"
                if mode == "portable"
                else "application/vnd.hc.temporal-policy.v1+json"
            ),
            client_side_encryption=(
                "age_x25519_v1" if mode == "portable" else "repository_kms_only"
            ),
            size_bytes=size,
            sha256=digest,
            schedule_count=len(snapshot.schedules),
            open_workflow_count=len(snapshot.open_workflows),
            schedule_inventory_sha256=schedule_hash,
            open_workflow_inventory_sha256=workflow_hash,
            consistency_coordinate=coordinate,
        )

    @staticmethod
    def _verify_lease(
        lease: MaintenanceBackupLease,
        verifier: Callable[[MaintenanceBackupLease], None],
    ) -> None:
        try:
            verifier(lease)
        except TemporalBackupError:
            raise
        except Exception as exc:
            raise TemporalBackupError(
                "BACKUP_TEMPORAL_FENCE_REJECTED",
                "the Temporal backup Job does not own the maintenance fence",
            ) from exc


def load_temporal_policy_document(
    artifact: TemporalPolicySourceArtifact,
    *,
    decryptor: AgeDecryptedReader | None = None,
) -> TemporalPolicyDocument:
    """Strictly load one authenticated policy artifact without provider mutation."""

    _verify_artifact_file(artifact)
    source: AbstractContextManager[BinaryIO]
    if artifact.mode == "portable":
        if decryptor is None:
            raise TemporalBackupError(
                "BACKUP_TEMPORAL_AGE_IDENTITY_REQUIRED",
                "portable Temporal policy verification requires an age identity",
            )
        source = decryptor.open(artifact.path, cwd=artifact.path.parent)
    else:
        if decryptor is not None:
            raise TemporalBackupError(
                "BACKUP_TEMPORAL_ENCRYPTION_INVALID",
                "snapshot Temporal policy must not use an age decryptor",
            )
        source = artifact.path.open("rb")
    try:
        with source as stream:
            payload = _read_bounded(stream, _MAX_POLICY_BYTES)
        value = _strict_json(payload)
        return TemporalPolicyDocument.model_validate(value)
    except TemporalBackupError:
        raise
    except Exception as exc:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_POLICY_INVALID",
            "the Temporal policy artifact violates its strict contract",
        ) from exc


class TemporalBackupVerifier:
    """Verify the policy artifact and current external-provider preflight."""

    async def verify(
        self,
        artifact: TemporalPolicyArtifact,
        *,
        provider: TemporalAdminVisibilityPort,
        decryptor: AgeDecryptedReader | None = None,
    ) -> TemporalVerificationReport:
        document = load_temporal_policy_document(artifact, decryptor=decryptor)
        if (
            len(document.schedules) != artifact.schedule_count
            or len(document.open_workflows) != artifact.open_workflow_count
            or document.schedule_inventory_sha256 != artifact.schedule_inventory_sha256
            or document.open_workflow_inventory_sha256 != artifact.open_workflow_inventory_sha256
            or document.consistency_coordinate != artifact.consistency_coordinate
        ):
            raise TemporalBackupError(
                "BACKUP_TEMPORAL_RECEIPT_MISMATCH",
                "the Temporal policy differs from its artifact receipt",
            )
        preflight = await provider.preflight(document)
        return TemporalVerificationReport(
            policy_sha256=artifact.sha256,
            schedule_count=len(document.schedules),
            open_workflow_count=len(document.open_workflows),
            schedule_inventory_sha256=document.schedule_inventory_sha256,
            open_workflow_inventory_sha256=document.open_workflow_inventory_sha256,
            consistency_coordinate=document.consistency_coordinate,
            provider_preflight_coordinate=preflight,
        )


def _schedule_record(
    schedule_id: str,
    response: Any,
    *,
    require_paused: bool,
) -> TemporalScheduleInventoryRecord:
    schedule = response.schedule
    action = schedule.action
    if action.WhichOneof("action") != "start_workflow":
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_PROVIDER_RESPONSE_INVALID",
            "a Temporal schedule has an unsupported action",
        )
    workflow = action.start_workflow
    paused = bool(schedule.state.paused)
    if require_paused and not paused:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_SCHEDULE_NOT_PAUSED",
            "every Temporal schedule must be paused before backup inventory",
        )
    safe_definition = {
        "workflow_type": str(workflow.workflow_type.name),
        "workflow_id": str(workflow.workflow_id),
        "task_queue": str(workflow.task_queue.name),
        "spec_sha256": _sha256_bytes(schedule.spec.SerializeToString(deterministic=True)),
        "policies": {
            "overlap_policy": int(schedule.policies.overlap_policy),
            "catchup_window_seconds": int(schedule.policies.catchup_window.seconds),
            "catchup_window_nanos": int(schedule.policies.catchup_window.nanos),
            "pause_on_failure": bool(schedule.policies.pause_on_failure),
        },
        "timeouts": {
            "execution": _duration_pair(workflow.workflow_execution_timeout),
            "run": _duration_pair(workflow.workflow_run_timeout),
            "task": _duration_pair(workflow.workflow_task_timeout),
        },
        "retry": _retry_policy(workflow.retry_policy),
    }
    info = response.info
    running = tuple(
        sorted(
            {
                str(item.workflow_execution.workflow_id)
                for item in info.running_workflows
                if item.workflow_execution.workflow_id
            }
        )
    )
    next_times = tuple(
        sorted({_proto_timestamp(item.ToDatetime()) for item in info.future_action_times})
    )
    created = _proto_timestamp(info.create_time.ToDatetime())
    updated = (
        _proto_timestamp(info.update_time.ToDatetime()) if info.HasField("update_time") else None
    )
    try:
        return TemporalScheduleInventoryRecord(
            schedule_id=schedule_id,
            workflow_type=str(workflow.workflow_type.name),
            workflow_id=str(workflow.workflow_id),
            task_queue=str(workflow.task_queue.name),
            definition_sha256=_sha256_json(safe_definition),
            paused=paused,
            limited_actions=bool(schedule.state.limited_actions),
            remaining_actions=int(schedule.state.remaining_actions),
            running_action_workflow_ids=running,
            next_action_times=next_times,
            action_count=int(info.action_count),
            missed_catchup_count=int(info.missed_catchup_window),
            skipped_overlap_count=int(info.overlap_skipped),
            created_at=created,
            updated_at=updated,
        )
    except (ValueError, ValidationError) as exc:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_PROVIDER_RESPONSE_INVALID",
            "a Temporal schedule response violates the bounded inventory contract",
        ) from exc


def _workflow_record(execution: Any, *, namespace: str) -> TemporalOpenWorkflowInventoryRecord:
    status = execution.status
    status_name = str(getattr(status, "name", status)).upper()
    if str(execution.namespace) != namespace or status_name != "RUNNING":
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_PROVIDER_RESPONSE_INVALID",
            "the Temporal visibility response includes an unexpected workflow",
        )
    try:
        return TemporalOpenWorkflowInventoryRecord(
            workflow_id=str(execution.id),
            run_id=str(execution.run_id),
            workflow_type=str(execution.workflow_type),
            task_queue=str(execution.task_queue),
            start_time=_timestamp(execution.start_time),
            execution_time=(
                _timestamp(execution.execution_time)
                if execution.execution_time is not None
                else None
            ),
            history_length=int(execution.history_length),
            parent_workflow_id=(str(execution.parent_id) if execution.parent_id else None),
            parent_run_id=(str(execution.parent_run_id) if execution.parent_run_id else None),
            root_workflow_id=(str(execution.root_id) if execution.root_id else None),
            root_run_id=(str(execution.root_run_id) if execution.root_run_id else None),
        )
    except (ValueError, ValidationError) as exc:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_PROVIDER_RESPONSE_INVALID",
            "a Temporal workflow response violates the bounded inventory contract",
        ) from exc


def _bind_provider_contract(
    contract: TemporalProviderRecoveryContract,
    snapshot: TemporalInventorySnapshot,
    config: TemporalConnectionConfig,
) -> None:
    if (
        contract.cluster_reference != config.cluster_reference
        or contract.namespace != snapshot.namespace
        or contract.expected_cluster_identity_sha256 != snapshot.cluster_identity_sha256
        or contract.expected_namespace_identity_sha256 != snapshot.namespace_identity_sha256
        or contract.expected_service_version != snapshot.service_version
    ):
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_PROVIDER_POLICY_MISMATCH",
            "the provider recovery contract differs from the live Temporal identity",
        )
    captured = _parse_timestamp(snapshot.captured_at)
    observed = _parse_timestamp(contract.protection_observed_at)
    if captured < observed or (captured - observed).total_seconds() > contract.maximum_rpo_seconds:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_PROVIDER_RPO_EXCEEDED",
            "the provider recovery point exceeds the frozen Temporal RPO",
        )


def _validate_recovery_contract(
    contract: TemporalProviderRecoveryContract,
    now: datetime,
) -> None:
    current = _utc(now)
    verified = _parse_timestamp(contract.verified_at)
    valid_until = _parse_timestamp(contract.valid_until)
    observed = _parse_timestamp(contract.protection_observed_at)
    if verified > current or observed > current or current >= valid_until:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_PROVIDER_EVIDENCE_EXPIRED",
            "the external Temporal recovery evidence is absent, future-dated, or expired",
        )
    if (current - observed).total_seconds() > contract.maximum_rpo_seconds:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_PROVIDER_RPO_EXCEEDED",
            "the provider recovery point exceeds the frozen Temporal RPO",
        )


def _inventory_hash(items: Sequence[BaseModel]) -> str:
    return _sha256_json([item.model_dump(mode="json") for item in items])


def _policy_coordinate(document: TemporalPolicyDocument) -> str:
    return _policy_coordinate_from_values(document.model_dump(mode="python"))


def _policy_coordinate_from_values(values: Mapping[str, Any]) -> str:
    provider = values["provider_recovery"]
    provider_value: Mapping[str, Any]
    if isinstance(provider, BaseModel):
        provider_value = provider.model_dump(mode="json")
    else:
        provider_value = cast(Mapping[str, Any], provider)
    material = {
        "source_environment_id": values["source_environment_id"],
        "cluster_reference": values["cluster_reference"],
        "namespace": values["namespace"],
        "namespace_identity_sha256": values["namespace_identity_sha256"],
        "cluster_identity_sha256": values["cluster_identity_sha256"],
        "service_version": values["service_version"],
        "captured_at": values["captured_at"],
        "provider_evidence_sha256": provider_value["evidence_sha256"],
        "protection_reference": provider_value["protection_reference"],
        "protection_observed_at": provider_value["protection_observed_at"],
        "schedule_inventory_sha256": values["schedule_inventory_sha256"],
        "open_workflow_inventory_sha256": values["open_workflow_inventory_sha256"],
    }
    return "temporal-policy/v1:sha256:" + _sha256_json(material)


def _duration_pair(value: Any) -> Mapping[str, int]:
    return {"seconds": int(value.seconds), "nanos": int(value.nanos)}


def _retry_policy(value: Any) -> Mapping[str, Any]:
    return {
        "initial_interval": _duration_pair(value.initial_interval),
        "backoff_coefficient": float(value.backoff_coefficient),
        "maximum_interval": _duration_pair(value.maximum_interval),
        "maximum_attempts": int(value.maximum_attempts),
        "non_retryable_error_types": sorted(str(item) for item in value.non_retryable_error_types),
    }


def _target_host(value: str) -> str:
    if _TARGET.fullmatch(value) is None:
        raise ValueError("Temporal target must be an explicit host and port")
    host, separator, port = value.rpartition(":")
    if not separator or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("Temporal target port is invalid")
    return host[1:-1] if host.startswith("[") and host.endswith("]") else host


def _is_loopback(host: str) -> bool:
    lowered = host.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False


def _validate_temporal_cluster_reference(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "temporal"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.port is not None
        or parsed.path in {"", "/"}
        or ".." in parsed.path.split("/")
    ):
        raise ValueError("Temporal cluster reference must be a redacted provider identity")


def _validate_opaque_reference(value: str) -> None:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Temporal recovery reference has an invalid port") from exc
    if (
        parsed.scheme not in _REFERENCE_SCHEMES
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
        or parsed.path in {"", "/"}
        or any(part in {".", ".."} for part in parsed.path.split("/"))
    ):
        raise ValueError("Temporal recovery reference is not a safe immutable reference")


def _secret_bytes(value: SecretBytes | None) -> bytes | None:
    return value.get_secret_value() if value is not None else None


async def _maybe_await(value: T | Awaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await cast(Awaitable[T], value)
    return value


def _seconds(value: int) -> Any:
    from datetime import timedelta

    return timedelta(seconds=value)


def _map_provider_error(error: BaseException) -> TemporalBackupError:
    try:
        from temporalio.service import RPCError, RPCStatusCode

        if isinstance(error, RPCError):
            if error.status in {
                RPCStatusCode.PERMISSION_DENIED,
                RPCStatusCode.UNAUTHENTICATED,
            }:
                return TemporalBackupError(
                    "BACKUP_TEMPORAL_PROVIDER_REJECTED",
                    "the Temporal provider rejected the read-only inventory request",
                )
            if error.status is RPCStatusCode.NOT_FOUND:
                return TemporalBackupError(
                    "BACKUP_TEMPORAL_NAMESPACE_UNAVAILABLE",
                    "the configured Temporal namespace is unavailable",
                )
    except ImportError:
        pass
    return TemporalBackupError(
        "BACKUP_TEMPORAL_PROVIDER_UNAVAILABLE",
        "the Temporal provider is unavailable",
    )


def _timestamp(value: datetime) -> str:
    current = _utc(value)
    if current.microsecond:
        return current.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return current.isoformat(timespec="seconds").replace("+00:00", "Z")


def _proto_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return _timestamp(value)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_TIME_INVALID",
            "Temporal recovery evidence timestamps must include a timezone",
        )
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ValueError("Temporal timestamp is invalid") from exc
    return parsed.astimezone(timezone.utc)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(canonical_json_bytes(value))


def _sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_STREAM_CHUNK_BYTES):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _secure_staging(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_STAGING_UNSAFE",
            "the Temporal backup staging directory is unavailable",
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_STAGING_UNSAFE",
            "the Temporal backup staging directory must be owner-only",
        )
    return resolved


def _private_directory(parent: Path, name: str) -> Path:
    path = parent / name
    with suppress(FileExistsError):
        path.mkdir(mode=0o700)
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_STAGING_UNSAFE",
            "the Temporal artifact directory is unsafe",
        )
    return resolved


def _private_writer(path: Path) -> AbstractContextManager[BinaryIO]:
    @contextmanager
    def writer() -> Iterator[BinaryIO]:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb", buffering=0) as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())

    return writer()


def _publish_no_replace(temporary: Path, destination: Path) -> None:
    try:
        os.link(temporary, destination, follow_symlinks=False)
    except FileExistsError as exc:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_ARTIFACT_EXISTS",
            "a Temporal policy artifact already exists",
        ) from exc
    temporary.unlink()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_bounded(stream: IO[bytes], maximum: int) -> bytes:
    payload = stream.read(maximum + 1)
    if not payload or len(payload) > maximum:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_POLICY_INVALID",
            "the Temporal policy is absent or exceeds its bounded size",
        )
    if stream.read(1):
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_POLICY_INVALID",
            "the Temporal policy exceeds its bounded size",
        )
    return payload


def _strict_json(payload: bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise TemporalBackupError(
                    "BACKUP_TEMPORAL_POLICY_INVALID",
                    "the Temporal policy contains duplicate JSON members",
                )
            result[key] = value
        return result

    try:
        return json.loads(payload, object_pairs_hook=pairs)
    except TemporalBackupError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_POLICY_INVALID",
            "the Temporal policy is malformed JSON",
        ) from exc


def _verify_artifact_file(artifact: TemporalPolicySourceArtifact) -> None:
    try:
        resolved = artifact.path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_ARTIFACT_MISSING",
            "the Temporal policy artifact is unavailable",
        ) from exc
    if (
        artifact.path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_ARTIFACT_UNSAFE",
            "the Temporal policy artifact is not an owner-only regular file",
        )
    size, digest = _sha256_file(resolved)
    if size != artifact.size_bytes or digest != artifact.sha256:
        raise TemporalBackupError(
            "BACKUP_TEMPORAL_ARTIFACT_HASH_MISMATCH",
            "the Temporal policy artifact differs from its receipt",
        )


TemporalVersionAdapter = TypeAdapter(TemporalVersion)


__all__ = [
    "TemporalAdminVisibilityPort",
    "TemporalBackupAdapter",
    "TemporalBackupError",
    "TemporalBackupVerifier",
    "TemporalConnectionConfig",
    "TemporalDevelopmentProbeReport",
    "TemporalInventorySnapshot",
    "TemporalOpenWorkflowInventoryRecord",
    "TemporalPolicyArtifact",
    "TemporalPolicySourceArtifact",
    "TemporalPolicyDocument",
    "TemporalProviderRecoveryContract",
    "TemporalScheduleInventoryRecord",
    "TemporalSdkAdminAdapter",
    "TemporalVerificationReport",
    "load_temporal_policy_document",
]
