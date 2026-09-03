"""P02 data-source API contracts.

The response models intentionally contain only safe credential metadata. Secret
input models use :class:`pydantic.SecretStr` and are consumed by the service before
any persistence or audit DTO is built.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from hc_data_platform.core.pagination import PageInfo

DecimalString: TypeAlias = Annotated[str, Field(pattern=r"^(0|[1-9]\d*)$")]
SourceType: TypeAlias = Literal["ROBOT", "EDGE_AGENT", "OSS_IMPORT"]
AdministrativeState: TypeAlias = Literal["ENABLED", "DISABLED"]
CredentialState: TypeAlias = Literal[
    "NOT_REQUIRED",
    "MISSING",
    "CONFIGURED",
    "ROTATION_DUE",
    "EXPIRED",
    "REVOKED",
    "INVALID",
]
ConnectivityState: TypeAlias = Literal[
    "UNKNOWN",
    "ONLINE",
    "DEGRADED",
    "OFFLINE",
    "AUTH_FAILED",
    "CONFIG_ERROR",
]


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DataSourceScope(_ContractModel):
    organization_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    region_code: str = Field(min_length=1, max_length=64)


class DataSourceBlockedReason(_ContractModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=512)


class DataSourceSafeError(_ContractModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=512)


class DataSourceJobError(DataSourceSafeError):
    retryable: bool
    request_id: str = Field(min_length=1, max_length=128)


class DataSourceRobotBinding(_ContractModel):
    kind: Literal["ROBOT"] = "ROBOT"
    robot_id: str = Field(min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=200)


class DataSourceEdgeAgentBinding(_ContractModel):
    kind: Literal["EDGE_AGENT"] = "EDGE_AGENT"
    agent_id: str = Field(min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=200)


class DataSourceOssImportBinding(_ContractModel):
    kind: Literal["OSS_IMPORT"] = "OSS_IMPORT"
    source_alias: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)


class DataSourceUnknownBinding(_ContractModel):
    kind: Literal["UNKNOWN"] = "UNKNOWN"
    raw: str = Field(min_length=1, max_length=100)
    display_name: str | None = Field(default=None, max_length=200)


DataSourceBinding: TypeAlias = Annotated[
    DataSourceRobotBinding
    | DataSourceEdgeAgentBinding
    | DataSourceOssImportBinding
    | DataSourceUnknownBinding,
    Field(discriminator="kind"),
]


class WritableRobotBinding(_ContractModel):
    kind: Literal["ROBOT"] = "ROBOT"
    robot_id: str = Field(min_length=1, max_length=256)


class WritableEdgeAgentBinding(_ContractModel):
    kind: Literal["EDGE_AGENT"] = "EDGE_AGENT"
    agent_id: str = Field(min_length=1, max_length=256)


class WritableOssImportBinding(_ContractModel):
    kind: Literal["OSS_IMPORT"] = "OSS_IMPORT"
    source_alias: str = Field(min_length=1, max_length=200)


WritableDataSourceBinding: TypeAlias = Annotated[
    WritableRobotBinding | WritableEdgeAgentBinding | WritableOssImportBinding,
    Field(discriminator="kind"),
]


class DataSourceRobotConfiguration(_ContractModel):
    kind: Literal["ROBOT"] = "ROBOT"
    transport: Literal["HTTPS", "MQTTS"]
    endpoint_ref: str = Field(min_length=1, max_length=256)
    safe_endpoint_hint: str | None = Field(default=None, max_length=300)
    tls_profile_id: str | None = Field(default=None, max_length=256)


class DataSourceEdgeAgentConfiguration(_ContractModel):
    kind: Literal["EDGE_AGENT"] = "EDGE_AGENT"
    agent_id: str = Field(min_length=1, max_length=256)
    transport: Literal["OUTBOUND_HTTPS", "MQTTS"]
    heartbeat_policy_id: str = Field(min_length=1, max_length=256)


class DataSourceOssImportConfiguration(_ContractModel):
    kind: Literal["OSS_IMPORT"] = "OSS_IMPORT"
    oss_account_alias: str = Field(min_length=1, max_length=100)
    bucket_alias: str = Field(min_length=1, max_length=100)
    prefix_hint: str = Field(min_length=1, max_length=300)
    role_ref: str = Field(min_length=1, max_length=256)
    source_region_code: str = Field(min_length=1, max_length=64)


class DataSourceUnknownConfiguration(_ContractModel):
    kind: Literal["UNKNOWN"] = "UNKNOWN"
    raw_source_type: str = Field(min_length=1, max_length=100)
    safe_projection: dict[str, str | None]


DataSourceConfiguration: TypeAlias = Annotated[
    DataSourceRobotConfiguration
    | DataSourceEdgeAgentConfiguration
    | DataSourceOssImportConfiguration
    | DataSourceUnknownConfiguration,
    Field(discriminator="kind"),
]


class WritableRobotConfiguration(_ContractModel):
    """The endpoint is resolved server-side from a robot binding."""

    kind: Literal["ROBOT"] = "ROBOT"
    transport: Literal["HTTPS", "MQTTS"]


class WritableEdgeAgentConfiguration(_ContractModel):
    kind: Literal["EDGE_AGENT"] = "EDGE_AGENT"
    agent_id: str = Field(min_length=1, max_length=256)
    transport: Literal["OUTBOUND_HTTPS", "MQTTS"]
    heartbeat_policy_id: str = Field(min_length=1, max_length=256)


class WritableOssImportConfiguration(_ContractModel):
    kind: Literal["OSS_IMPORT"] = "OSS_IMPORT"
    oss_account_alias: str = Field(min_length=1, max_length=100)
    bucket_alias: str = Field(min_length=1, max_length=100)
    prefix_hint: str = Field(min_length=1, max_length=300)
    role_ref: str = Field(min_length=1, max_length=256)
    source_region_code: str = Field(min_length=1, max_length=64)


WritableConnectorConfiguration: TypeAlias = Annotated[
    WritableRobotConfiguration | WritableEdgeAgentConfiguration | WritableOssImportConfiguration,
    Field(discriminator="kind"),
]


class CredentialSummary(_ContractModel):
    kind: str = Field(min_length=1, max_length=64)
    state: CredentialState
    credential_ref: str | None = Field(default=None, min_length=1, max_length=256)
    masked_hint: str | None = Field(default=None, max_length=200)
    version: DecimalString
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    rotation_due_at: datetime | None = None


class ConnectivitySummary(_ContractModel):
    state: ConnectivityState
    last_check_state: str = Field(min_length=1, max_length=64)
    observed_config_version: DecimalString | None = None
    observed_credential_version: DecimalString | None = None
    checked_at: datetime | None = None
    safe_error: DataSourceSafeError | None = None


class HeartbeatSummary(_ContractModel):
    state: Literal["ONLINE", "DEGRADED", "OFFLINE", "NOT_APPLICABLE"]
    last_seen_at: datetime | None = None


class UploadPolicySummary(_ContractModel):
    code: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=200)
    max_object_size_bytes: DecimalString


class LastUploadSummary(_ContractModel):
    upload_id: str = Field(min_length=1, max_length=128)
    completed_at: datetime
    verified_bytes: DecimalString
    lifecycle_status: str = Field(min_length=1, max_length=64)


class DataSourceSummary(_ContractModel):
    id: str = Field(min_length=1, max_length=256)
    scope: DataSourceScope
    name: str = Field(min_length=1, max_length=128)
    source_type: str = Field(min_length=1, max_length=128)
    source_format: str = Field(min_length=1, max_length=128)
    source_format_version: str | None = Field(default=None, max_length=128)
    adapter_version: str | None = Field(default=None, max_length=128)
    binding: DataSourceBinding
    administrative_state: AdministrativeState
    credential: CredentialSummary
    connectivity: ConnectivitySummary
    heartbeat: HeartbeatSummary | None = None
    upload_policy: UploadPolicySummary
    last_upload: LastUploadSummary | None = None
    config_version: DecimalString
    credential_version: DecimalString
    etag: str = Field(min_length=1, max_length=256)
    allowed_actions: tuple[str, ...] = ()
    blocked_reasons: tuple[DataSourceBlockedReason, ...] = ()
    created_at: datetime
    updated_at: datetime


class DataSourceDetail(DataSourceSummary):
    configuration: DataSourceConfiguration


class DataSourceFacet(_ContractModel):
    value: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=200)
    count: DecimalString


class DataSourceRobotFacet(_ContractModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    count: DecimalString


class DataSourceFacets(_ContractModel):
    source_types: tuple[DataSourceFacet, ...] = ()
    source_formats: tuple[DataSourceFacet, ...] = ()
    robots: tuple[DataSourceRobotFacet, ...] = ()
    upload_policies: tuple[DataSourceFacet, ...] = ()
    administrative_states: tuple[DataSourceFacet, ...] = ()
    connectivity_states: tuple[DataSourceFacet, ...] = ()
    credential_states: tuple[DataSourceFacet, ...] = ()
    heartbeat_states: tuple[DataSourceFacet, ...] = ()


class DataSourceMetrics(_ContractModel):
    total_count: DecimalString
    online_count: DecimalString
    verified_bytes_today: DecimalString
    abnormal_count: DecimalString
    as_of: datetime
    timezone: str = "UTC"
    definition_version: str = "p02-data-source-page/v1"


class DataSourceComponentError(_ContractModel):
    component: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=512)
    request_id: str = Field(min_length=1, max_length=128)


class DataSourcePage(_ContractModel):
    summary: DataSourceMetrics
    facets: DataSourceFacets
    items: tuple[DataSourceSummary, ...]
    page_info: PageInfo
    snapshot_at: datetime
    allowed_actions: tuple[str, ...] = ()
    component_errors: tuple[DataSourceComponentError, ...] = ()
    scope: DataSourceScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "ingest.v1alpha1"


class DataSourceEnvelope(_ContractModel):
    data: DataSourceDetail
    scope: DataSourceScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "ingest.v1alpha1"


class TokenCredentialInput(_ContractModel):
    kind: Literal["TOKEN"] = "TOKEN"
    token: SecretStr = Field(min_length=1, max_length=16_384)


class BasicCredentialInput(_ContractModel):
    kind: Literal["BASIC"] = "BASIC"
    username: str = Field(min_length=1, max_length=256)
    password: SecretStr = Field(min_length=1, max_length=16_384)


class DeviceCertificateCredentialInput(_ContractModel):
    kind: Literal["DEVICE_CERTIFICATE"] = "DEVICE_CERTIFICATE"
    certificate_pem: SecretStr = Field(min_length=1, max_length=32_768)
    private_key_pem: SecretStr = Field(min_length=1, max_length=32_768)


CredentialInput: TypeAlias = Annotated[
    TokenCredentialInput | BasicCredentialInput | DeviceCertificateCredentialInput,
    Field(discriminator="kind"),
]


class CreateDataSourceCommand(_ContractModel):
    name: str = Field(min_length=1, max_length=128)
    source_type: SourceType
    source_format: str = Field(min_length=1, max_length=128)
    source_format_version: str | None = Field(default=None, max_length=128)
    binding: WritableDataSourceBinding
    configuration: WritableConnectorConfiguration
    upload_policy_code: str = Field(min_length=1, max_length=128)
    credential_input: CredentialInput | None = None

    @model_validator(mode="after")
    def matching_connector_kinds(self) -> CreateDataSourceCommand:
        if self.source_type != self.binding.kind or self.source_type != self.configuration.kind:
            raise ValueError("source_type, binding.kind, and configuration.kind must match")
        return self


class UpdateDataSourceCommand(_ContractModel):
    name: str = Field(min_length=1, max_length=128)
    source_format: str = Field(min_length=1, max_length=128)
    source_format_version: str | None = Field(default=None, max_length=128)
    binding: WritableDataSourceBinding
    configuration: WritableConnectorConfiguration
    upload_policy_code: str = Field(min_length=1, max_length=128)
    change_reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def matching_connector_kinds(self) -> UpdateDataSourceCommand:
        if self.binding.kind != self.configuration.kind:
            raise ValueError("binding.kind and configuration.kind must match")
        return self


class RotateCredentialCommand(_ContractModel):
    credential_input: CredentialInput
    reason: str = Field(min_length=1, max_length=500)


class TestConnectionCommand(_ContractModel):
    observed_config_version: DecimalString
    observed_credential_version: DecimalString


class SourceStateCommand(_ContractModel):
    reason: str = Field(min_length=1, max_length=500)
    expected_administrative_state: AdministrativeState


class DataSourceJobProgress(_ContractModel):
    completed: DecimalString
    total: DecimalString | None
    unit: str = Field(min_length=1, max_length=64)


class DataSourceResourceReference(_ContractModel):
    resource_type: Literal["DATA_SOURCE"] = "DATA_SOURCE"
    resource_id: str = Field(min_length=1, max_length=256)


class DataSourceObservedVersions(_ContractModel):
    config_version: DecimalString
    credential_version: DecimalString


class DataSourceConnectionTestJob(_ContractModel):
    id: str = Field(min_length=1, max_length=256)
    type: Literal["DATA_SOURCE_CONNECTION_TEST"] = "DATA_SOURCE_CONNECTION_TEST"
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
    stage: str = Field(min_length=1, max_length=128)
    progress: DataSourceJobProgress
    resource_ref: DataSourceResourceReference
    observed_versions: DataSourceObservedVersions
    result_ref: dict[str, str] | None = None
    safe_error: DataSourceJobError | None = None
    etag: str = Field(min_length=1, max_length=256)
    allowed_actions: tuple[str, ...] = ()
    created_at: datetime
    updated_at: datetime


class DataSourceAsyncJob(_ContractModel):
    """Canonical P02 projection of the platform's polling job resource."""

    job_id: str = Field(min_length=1, max_length=256)
    job_type: Literal["DATA_SOURCE_CONNECTION_TEST"] = "DATA_SOURCE_CONNECTION_TEST"
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
    resource_type: Literal["DATA_SOURCE"] = "DATA_SOURCE"
    resource_id: str = Field(min_length=1, max_length=256)
    progress: dict[str, str] | None = None
    result_ref: dict[str, str] | None = None
    error: DataSourceJobError | None = None
    created_at: datetime
    updated_at: datetime
    resource_version: DecimalString


class ConnectionTestJobEnvelope(_ContractModel):
    data: DataSourceConnectionTestJob
    job: DataSourceAsyncJob
    scope: DataSourceScope
    request_id: str = Field(min_length=1, max_length=128)
    contract_version: str = "ingest.v1alpha1"


class DataSourceMutationRecord(_ContractModel):
    """Durably replayable internal mutation outcome; never sent directly to a browser."""

    source: DataSourceDetail | None = None
    connection_test: DataSourceConnectionTestJob | None = None
    async_job: DataSourceAsyncJob | None = None
