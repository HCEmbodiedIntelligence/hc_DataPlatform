"""Helm configuration and versioned Secret/KMS dependency backup artifacts.

The adapter records public rendered configuration and opaque, provider-backed
fingerprints for every required Kubernetes ``secretKeyRef``. Secret values are
held only long enough to ask Vault Transit for an HMAC and are never returned,
logged, or persisted by this module.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import stat
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, suppress
from datetime import datetime
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Annotated, Any, BinaryIO, Literal, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

from hc_data_platform.backup.contracts import (
    ArtifactPath,
    KmsKeyReference,
    SecretDependencyV1,
    Sha256,
    canonical_json_bytes,
    find_plaintext_secret_material,
)
from hc_data_platform.backup.objects import AgeDecryptedReader, AgeEncryptedWriter
from hc_data_platform.backup.postgresql import MaintenanceBackupLease

EnvironmentVariable = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")]
SecretEnvironmentVariable = Annotated[str, StringConstraints(pattern=r"^HC_[A-Z0-9_]{1,124}$")]
WorkloadReference = Annotated[
    str,
    StringConstraints(
        max_length=1024,
        pattern=(
            r"^(?:Deployment|StatefulSet|DaemonSet|Job|CronJob)/"
            r"[a-z0-9][a-z0-9.-]{0,252}/[a-z0-9][a-z0-9.-]{0,252}$"
        ),
    ),
]
WorkloadConsumer = Annotated[
    str,
    StringConstraints(
        max_length=1280,
        pattern=(
            r"^(?:Deployment|StatefulSet|DaemonSet|Job|CronJob)/"
            r"[a-z0-9][a-z0-9.-]{0,252}/[a-z0-9][a-z0-9.-]{0,252}:"
            r"[a-z0-9][a-z0-9.-]{0,252}$"
        ),
    ),
]
KubernetesName = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=253,
        pattern=r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$",
    ),
]
KubernetesSecretKey = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=127,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,125}[A-Za-z0-9])?$",
    ),
]
VaultPath = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=512,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)*$",
    ),
]
VaultMount = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    ),
]
VaultTimestamp = Annotated[
    str,
    StringConstraints(
        pattern=(
            r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
            r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
            r"(?:\.[0-9]{1,9})?Z$"
        )
    ),
]

_KUBERNETES_NAME_ADAPTER = TypeAdapter(KubernetesName)

_MAX_RENDERED_HELM_BYTES = 32 * 1024 * 1024
_MAX_PROVIDER_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_SECRET_VALUE_BYTES = 4 * 1024 * 1024
_MAX_ENVELOPE_BYTES = 64 * 1024 * 1024
_STREAM_CHUNK_BYTES = 1024 * 1024
_REQUIRED_SECRET_ENVIRONMENT = frozenset(
    {
        "HC_POSTGRES_DSN",
        "HC_OBJECT_STORE_ACCESS_KEY",
        "HC_OBJECT_STORE_SECRET_KEY",
        "HC_CURSOR_SECRET",
        "HC_DATA_SOURCE_CREDENTIAL_KEY",
        "HC_AUTH_ABUSE_HMAC_SECRET",
        "HC_AUTH_TURNSTILE_SECRET",
    }
)
_SECRET_NAME_SUFFIXES = (
    "_PASSWORD",
    "_PASSWORD_VALUE",
    "_SECRET",
    "_SECRET_KEY",
    "_TOKEN",
    "_CREDENTIAL",
    "_CREDENTIAL_KEY",
    "_PRIVATE_KEY",
    "_ACCESS_KEY",
    "_API_KEY",
    "_POSTGRES_DSN",
)
_PUBLIC_KEY_SUFFIXES = ("SITE_KEY", "PUBLIC_KEY", "KEY_REFERENCE")
_VAULT_HMAC = re.compile(r"^vault:v(?P<version>[1-9][0-9]*):(?P<digest>[A-Za-z0-9+/=]+)$")


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        del req, fp, code, msg, headers, newurl
        return None


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConfigurationBackupError(RuntimeError):
    """Stable, redacted configuration-backup failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class PublicConfigEntry(_StrictModel):
    name: EnvironmentVariable
    value: str = Field(max_length=1_048_576)


class PublicConfigMapRecord(_StrictModel):
    namespace: KubernetesName
    name: KubernetesName
    entries: tuple[PublicConfigEntry, ...]

    @model_validator(mode="after")
    def require_sorted_entries(self) -> PublicConfigMapRecord:
        names = [entry.name for entry in self.entries]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("public ConfigMap entries must be sorted and unique")
        return self


class WorkloadPublicEnvironment(_StrictModel):
    workload: WorkloadReference
    container: KubernetesName
    name: EnvironmentVariable
    value: str = Field(max_length=1_048_576)


class RuntimeFieldDependency(_StrictModel):
    workload: WorkloadReference
    container: KubernetesName
    name: EnvironmentVariable
    field_path: str = Field(min_length=1, max_length=255)


class HelmSecretReference(_StrictModel):
    environment_variable: SecretEnvironmentVariable
    kubernetes_secret_name: KubernetesName
    secret_key_name: KubernetesSecretKey
    workloads: tuple[WorkloadConsumer, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_sorted_workloads(self) -> HelmSecretReference:
        if self.workloads != tuple(sorted(set(self.workloads))):
            raise ValueError("Secret dependency workloads must be sorted and unique")
        return self


class DeploymentPublicConfigDocument(_StrictModel):
    format_version: Literal["hc-deployment-public-config/v1"] = "hc-deployment-public-config/v1"
    helm_render_sha256: Sha256
    config_maps: tuple[PublicConfigMapRecord, ...]
    workload_environment: tuple[WorkloadPublicEnvironment, ...]
    runtime_fields: tuple[RuntimeFieldDependency, ...]

    @model_validator(mode="after")
    def require_sorted_records(self) -> DeploymentPublicConfigDocument:
        if self.config_maps != tuple(
            sorted(self.config_maps, key=lambda item: (item.namespace, item.name))
        ):
            raise ValueError("public ConfigMap records must be sorted")
        if self.workload_environment != tuple(
            sorted(
                self.workload_environment,
                key=lambda item: (item.workload, item.container, item.name),
            )
        ):
            raise ValueError("public workload environment records must be sorted")
        if self.runtime_fields != tuple(
            sorted(
                self.runtime_fields,
                key=lambda item: (item.workload, item.container, item.name),
            )
        ):
            raise ValueError("runtime field dependencies must be sorted")
        return self


class VaultProviderConfig(_StrictModel):
    endpoint_url: str = Field(min_length=1, max_length=2048)
    token: SecretStr = Field(repr=False)
    provider_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    kv_mount: VaultMount = "secret"
    kv_prefix: VaultPath = "hc-data-platform"
    transit_mount: VaultMount = "transit"
    hmac_key_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    hmac_key_version: int = Field(gt=0)
    hmac_key_reference: KmsKeyReference
    namespace: str | None = Field(default=None, min_length=1, max_length=255)
    timeout_seconds: float = Field(default=10, gt=0, le=120)

    @field_validator("endpoint_url")
    @classmethod
    def require_safe_endpoint(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            _port = parsed.port
        except ValueError as exc:
            raise ValueError("Vault endpoint has an invalid port") from exc
        del _port
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Vault endpoint must be an origin URL without credentials")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("non-loopback Vault endpoints must use HTTPS")
        return value.rstrip("/")

    @field_validator("token")
    @classmethod
    def require_token(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if not token or any(char in token for char in ("\x00", "\r", "\n")):
            raise ValueError("Vault token is invalid")
        return value

    @model_validator(mode="after")
    def bind_hmac_reference(self) -> VaultProviderConfig:
        expected = (
            f"kms://vault/{self.provider_reference}/{self.transit_mount}/"
            f"{self.hmac_key_name}/versions/v{self.hmac_key_version}"
        )
        if self.hmac_key_reference != expected:
            raise ValueError("Vault Transit key reference does not match its exact key version")
        return self


class SecretDependencyEvidence(_StrictModel):
    environment_variable: SecretEnvironmentVariable
    kubernetes_secret_name: KubernetesName
    secret_key_name: KubernetesSecretKey
    workloads: tuple[WorkloadConsumer, ...] = Field(min_length=1)
    provider_secret_reference: str = Field(
        min_length=1,
        max_length=255,
        pattern=(r"^vault-kv://[A-Za-z0-9][A-Za-z0-9_.:/-]*/versions/v[1-9][0-9]*$"),
    )
    secret_version: int = Field(gt=0)
    version_created_at: VaultTimestamp
    fingerprint_algorithm: Literal["vault_transit_hmac_sha2_256"] = "vault_transit_hmac_sha2_256"
    fingerprint_sha256: Sha256
    hmac_key_reference: KmsKeyReference


class SecretsEnvelopeDocument(_StrictModel):
    format_version: Literal["hc-secret-dependencies/v1"] = "hc-secret-dependencies/v1"
    source_environment_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9._:/-]{0,126}[A-Za-z0-9])?$",
    )
    helm_render_sha256: Sha256
    provider: Literal["hashicorp_vault_kv_v2"] = "hashicorp_vault_kv_v2"
    provider_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    kv_mount: VaultMount
    kv_prefix: VaultPath
    hmac_key_reference: KmsKeyReference
    dependencies: tuple[SecretDependencyEvidence, ...] = Field(min_length=1)
    plaintext_export_allowed: Literal[False] = False

    @model_validator(mode="after")
    def require_unique_dependencies(self) -> SecretsEnvelopeDocument:
        variables = [item.environment_variable for item in self.dependencies]
        if variables != sorted(variables) or len(variables) != len(set(variables)):
            raise ValueError(
                "Secret dependencies must be sorted and unique by environment variable"
            )
        if any(item.hmac_key_reference != self.hmac_key_reference for item in self.dependencies):
            raise ValueError("Secret dependency HMAC references must match the envelope")
        if not _REQUIRED_SECRET_ENVIRONMENT.issubset(variables):
            raise ValueError("Secret envelope omits a required runtime dependency")
        return self


class PublicConfigArtifact(_StrictModel):
    path: Path
    logical_path: Literal["config/public.yaml"] = "config/public.yaml"
    media_type: Literal["application/vnd.hc.deployment-public-config.v1+yaml"] = (
        "application/vnd.hc.deployment-public-config.v1+yaml"
    )
    client_side_encryption: Literal["public_integrity_metadata"] = "public_integrity_metadata"
    size_bytes: int = Field(gt=0, le=_MAX_RENDERED_HELM_BYTES)
    sha256: Sha256
    helm_render_sha256: Sha256


class SecretsEnvelopeArtifact(_StrictModel):
    path: Path
    logical_path: ArtifactPath
    mode: Literal["snapshot", "portable"]
    media_type: Literal[
        "application/vnd.hc.secret-dependencies.v1+json",
        "application/vnd.hc.secret-dependencies.v1+json+age",
    ]
    client_side_encryption: Literal["repository_kms_only", "age_x25519_v1"]
    size_bytes: int = Field(gt=0, le=_MAX_ENVELOPE_BYTES)
    sha256: Sha256
    dependency_count: int = Field(gt=0)
    provider_reference: str = Field(min_length=1, max_length=128)
    hmac_key_reference: KmsKeyReference

    @model_validator(mode="after")
    def require_mode_contract(self) -> SecretsEnvelopeArtifact:
        if self.mode == "portable":
            expected = (
                "application/vnd.hc.secret-dependencies.v1+json+age",
                "age_x25519_v1",
                "secrets/envelope.json.age",
            )
        else:
            expected = (
                "application/vnd.hc.secret-dependencies.v1+json",
                "repository_kms_only",
                "secrets/envelope.json",
            )
        if (self.media_type, self.client_side_encryption, self.logical_path) != expected:
            raise ValueError("Secret envelope media/encryption/path does not match its mode")
        return self


class ConfigurationBackupArtifact(_StrictModel):
    format_version: Literal["hc-configuration-backup-artifact/v1"] = (
        "hc-configuration-backup-artifact/v1"
    )
    public_config: PublicConfigArtifact
    secrets_envelope: SecretsEnvelopeArtifact
    manifest_dependencies: tuple[SecretDependencyV1, ...] = Field(min_length=1)
    preflight_coordinate: str = Field(pattern=r"^secret-dependency-set/v1:sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_dependency_count(self) -> ConfigurationBackupArtifact:
        variables = [item.environment_variable for item in self.manifest_dependencies]
        if variables != sorted(variables) or len(variables) != len(set(variables)):
            raise ValueError("manifest Secret dependencies must be sorted and unique")
        if len(variables) != self.secrets_envelope.dependency_count:
            raise ValueError("Secret envelope dependency count differs from its manifest summary")
        return self


class ConfigurationVerificationReport(_StrictModel):
    public_config_sha256: Sha256
    secrets_envelope_sha256: Sha256
    dependency_count: int = Field(gt=0)
    preflight_coordinate: str = Field(pattern=r"^secret-dependency-set/v1:sha256:[0-9a-f]{64}$")


class _UniqueSafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueSafeLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[object, object]:
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_HELM_INVALID",
                "the rendered Helm manifest contains a duplicate or non-string mapping key",
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _mapping(value: object, *, code: str, message: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ConfigurationBackupError(code, message)
    return cast(Mapping[str, Any], value)


def _sequence(value: object, *, code: str, message: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ConfigurationBackupError(code, message)
    return value


def _is_secret_environment(name: str) -> bool:
    if name in _REQUIRED_SECRET_ENVIRONMENT:
        return True
    if name.endswith(_PUBLIC_KEY_SUFFIXES):
        return False
    return name.endswith(_SECRET_NAME_SUFFIXES)


def _metadata_identity(document: Mapping[str, Any]) -> tuple[str, str]:
    metadata = _mapping(
        document.get("metadata", {}),
        code="BACKUP_CONFIGURATION_HELM_INVALID",
        message="a rendered Helm object has malformed metadata",
    )
    name = metadata.get("name")
    namespace = metadata.get("namespace", "default")
    if not isinstance(name, str) or not isinstance(namespace, str):
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_HELM_INVALID",
            "a rendered Helm object has no stable name/namespace",
        )
    try:
        return (
            _KUBERNETES_NAME_ADAPTER.validate_python(namespace),
            _KUBERNETES_NAME_ADAPTER.validate_python(name),
        )
    except ValueError as exc:
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_HELM_INVALID", "a rendered Helm identity is invalid"
        ) from exc


def _pod_spec(document: Mapping[str, Any], kind: str) -> Mapping[str, Any]:
    spec = _mapping(
        document.get("spec"),
        code="BACKUP_CONFIGURATION_HELM_INVALID",
        message="a rendered workload has no spec",
    )
    if kind == "CronJob":
        job_template = _mapping(
            spec.get("jobTemplate"),
            code="BACKUP_CONFIGURATION_HELM_INVALID",
            message="a rendered CronJob has no job template",
        )
        spec = _mapping(
            job_template.get("spec"),
            code="BACKUP_CONFIGURATION_HELM_INVALID",
            message="a rendered CronJob job template has no spec",
        )
    template = _mapping(
        spec.get("template"),
        code="BACKUP_CONFIGURATION_HELM_INVALID",
        message="a rendered workload has no pod template",
    )
    return _mapping(
        template.get("spec"),
        code="BACKUP_CONFIGURATION_HELM_INVALID",
        message="a rendered workload has no pod spec",
    )


class HelmRenderedDependencyAdapter:
    """Extract exact public configuration and Secret references from Helm YAML."""

    def inspect(
        self, rendered: bytes
    ) -> tuple[DeploymentPublicConfigDocument, tuple[HelmSecretReference, ...]]:
        try:
            return self._inspect(rendered)
        except ConfigurationBackupError:
            raise
        except Exception as exc:
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_HELM_INVALID",
                "the rendered Helm manifest violates the deployment dependency contract",
            ) from exc

    def _inspect(
        self, rendered: bytes
    ) -> tuple[DeploymentPublicConfigDocument, tuple[HelmSecretReference, ...]]:
        if not rendered or len(rendered) > _MAX_RENDERED_HELM_BYTES:
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_HELM_INVALID",
                "the rendered Helm manifest is absent or exceeds its bounded size",
            )
        try:
            text = rendered.decode("utf-8")
            raw_documents = tuple(yaml.load_all(text, Loader=_UniqueSafeLoader))
        except ConfigurationBackupError:
            raise
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_HELM_INVALID",
                "the rendered Helm manifest is not strict UTF-8 YAML",
            ) from exc
        documents = tuple(
            _mapping(
                value,
                code="BACKUP_CONFIGURATION_HELM_INVALID",
                message="a rendered Helm document is not an object",
            )
            for value in raw_documents
            if value is not None
        )
        config_maps: dict[tuple[str, str], Mapping[str, Any]] = {}
        workloads: list[tuple[Mapping[str, Any], str, str, str]] = []
        workload_kinds = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}
        for document in documents:
            kind = document.get("kind")
            if not isinstance(kind, str):
                raise ConfigurationBackupError(
                    "BACKUP_CONFIGURATION_HELM_INVALID",
                    "a rendered Helm document has no kind",
                )
            namespace, name = _metadata_identity(document)
            if kind == "ConfigMap":
                identity = (namespace, name)
                if identity in config_maps:
                    raise ConfigurationBackupError(
                        "BACKUP_CONFIGURATION_HELM_INVALID",
                        "the rendered Helm manifest contains duplicate ConfigMap identity",
                    )
                config_maps[identity] = document
            elif kind in workload_kinds:
                workloads.append((document, kind, namespace, name))

        referenced_config_maps: set[tuple[str, str]] = set()
        literal_environment: list[WorkloadPublicEnvironment] = []
        runtime_fields: list[RuntimeFieldDependency] = []
        secret_bindings: dict[str, tuple[str, str, set[str]]] = {}
        for document, kind, namespace, workload_name in workloads:
            pod_spec = _pod_spec(document, kind)
            workload = f"{kind}/{namespace}/{workload_name}"
            for group_name in ("initContainers", "containers"):
                containers = _sequence(
                    pod_spec.get(group_name, ()),
                    code="BACKUP_CONFIGURATION_HELM_INVALID",
                    message="a rendered workload container list is malformed",
                )
                for raw_container in containers:
                    container = _mapping(
                        raw_container,
                        code="BACKUP_CONFIGURATION_HELM_INVALID",
                        message="a rendered workload container is malformed",
                    )
                    container_name = container.get("name")
                    if not isinstance(container_name, str):
                        raise ConfigurationBackupError(
                            "BACKUP_CONFIGURATION_HELM_INVALID",
                            "a rendered workload container has no name",
                        )
                    env_from = _sequence(
                        container.get("envFrom", ()),
                        code="BACKUP_CONFIGURATION_HELM_INVALID",
                        message="a rendered workload envFrom list is malformed",
                    )
                    for raw_source in env_from:
                        source = _mapping(
                            raw_source,
                            code="BACKUP_CONFIGURATION_HELM_INVALID",
                            message="a rendered workload envFrom source is malformed",
                        )
                        reference = source.get("configMapRef")
                        if reference is None or len(source) != 1:
                            raise ConfigurationBackupError(
                                "BACKUP_CONFIGURATION_HELM_UNSUPPORTED",
                                "only exact ConfigMap envFrom sources are supported",
                            )
                        config_ref = _mapping(
                            reference,
                            code="BACKUP_CONFIGURATION_HELM_INVALID",
                            message="a rendered ConfigMap reference is malformed",
                        )
                        ref_name = config_ref.get("name")
                        if (
                            not isinstance(ref_name, str)
                            or config_ref.get("optional", False) is not False
                            or not set(config_ref).issubset({"name", "optional"})
                        ):
                            raise ConfigurationBackupError(
                                "BACKUP_CONFIGURATION_HELM_INVALID",
                                "a rendered ConfigMap reference is absent or optional",
                            )
                        referenced_config_maps.add((namespace, ref_name))

                    environment = _sequence(
                        container.get("env", ()),
                        code="BACKUP_CONFIGURATION_HELM_INVALID",
                        message="a rendered workload environment list is malformed",
                    )
                    seen_environment: set[str] = set()
                    for raw_environment in environment:
                        item = _mapping(
                            raw_environment,
                            code="BACKUP_CONFIGURATION_HELM_INVALID",
                            message="a rendered workload environment entry is malformed",
                        )
                        env_name = item.get("name")
                        if (
                            not isinstance(env_name, str)
                            or re.fullmatch(r"^[A-Z][A-Z0-9_]{0,127}$", env_name) is None
                            or env_name in seen_environment
                        ):
                            raise ConfigurationBackupError(
                                "BACKUP_CONFIGURATION_HELM_INVALID",
                                "a rendered workload environment name is invalid",
                            )
                        seen_environment.add(env_name)
                        if "value" in item:
                            value = item.get("value")
                            if not isinstance(value, str) or "valueFrom" in item:
                                raise ConfigurationBackupError(
                                    "BACKUP_CONFIGURATION_HELM_INVALID",
                                    "a rendered literal environment entry is malformed",
                                )
                            if _is_secret_environment(env_name):
                                raise ConfigurationBackupError(
                                    "BACKUP_CONFIGURATION_PLAINTEXT_SECRET",
                                    "a Secret-shaped environment variable is rendered as plaintext",
                                )
                            literal_environment.append(
                                WorkloadPublicEnvironment(
                                    workload=workload,
                                    container=container_name,
                                    name=env_name,
                                    value=value,
                                )
                            )
                            continue
                        value_from = _mapping(
                            item.get("valueFrom"),
                            code="BACKUP_CONFIGURATION_HELM_INVALID",
                            message="a rendered environment entry has no value source",
                        )
                        if "secretKeyRef" in value_from and len(value_from) == 1:
                            secret_ref = _mapping(
                                value_from["secretKeyRef"],
                                code="BACKUP_CONFIGURATION_HELM_INVALID",
                                message="a rendered Secret key reference is malformed",
                            )
                            secret_name = secret_ref.get("name")
                            secret_key = secret_ref.get("key")
                            if (
                                not isinstance(secret_name, str)
                                or not isinstance(secret_key, str)
                                or secret_ref.get("optional", False) is not False
                                or not set(secret_ref).issubset({"name", "key", "optional"})
                            ):
                                raise ConfigurationBackupError(
                                    "BACKUP_CONFIGURATION_SECRET_REFERENCE_INVALID",
                                    "a rendered Secret key reference is absent or optional",
                                )
                            existing = secret_bindings.get(env_name)
                            if existing is None:
                                secret_bindings[env_name] = (
                                    secret_name,
                                    secret_key,
                                    {f"{workload}:{container_name}"},
                                )
                            elif existing[:2] != (secret_name, secret_key):
                                raise ConfigurationBackupError(
                                    "BACKUP_CONFIGURATION_SECRET_REFERENCE_CONFLICT",
                                    "one environment variable resolves to multiple Secret keys",
                                )
                            else:
                                existing[2].add(f"{workload}:{container_name}")
                        elif "fieldRef" in value_from and len(value_from) == 1:
                            field_ref = _mapping(
                                value_from["fieldRef"],
                                code="BACKUP_CONFIGURATION_HELM_INVALID",
                                message="a rendered field reference is malformed",
                            )
                            field_path = field_ref.get("fieldPath")
                            if (
                                not isinstance(field_path, str)
                                or not set(field_ref).issubset({"fieldPath", "apiVersion"})
                                or (
                                    "apiVersion" in field_ref
                                    and not isinstance(field_ref["apiVersion"], str)
                                )
                            ):
                                raise ConfigurationBackupError(
                                    "BACKUP_CONFIGURATION_HELM_INVALID",
                                    "a rendered field reference has no field path",
                                )
                            runtime_fields.append(
                                RuntimeFieldDependency(
                                    workload=workload,
                                    container=container_name,
                                    name=env_name,
                                    field_path=field_path,
                                )
                            )
                        else:
                            raise ConfigurationBackupError(
                                "BACKUP_CONFIGURATION_HELM_UNSUPPORTED",
                                "a rendered environment value source is unsupported",
                            )

        missing = sorted(_REQUIRED_SECRET_ENVIRONMENT - set(secret_bindings))
        if missing:
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_REQUIRED_SECRET_MISSING",
                "the rendered release omits one or more required Secret dependencies",
            )

        public_maps: list[PublicConfigMapRecord] = []
        for identity in sorted(referenced_config_maps):
            raw_config_map = config_maps.get(identity)
            if raw_config_map is None:
                raise ConfigurationBackupError(
                    "BACKUP_CONFIGURATION_CONFIGMAP_MISSING",
                    "a workload references an absent rendered ConfigMap",
                )
            raw_data = _mapping(
                raw_config_map.get("data", {}),
                code="BACKUP_CONFIGURATION_HELM_INVALID",
                message="a rendered ConfigMap data field is malformed",
            )
            entries: list[PublicConfigEntry] = []
            for name, value in sorted(raw_data.items()):
                if (
                    not isinstance(value, str)
                    or re.fullmatch(r"^[A-Z][A-Z0-9_]{0,127}$", name) is None
                ):
                    raise ConfigurationBackupError(
                        "BACKUP_CONFIGURATION_HELM_INVALID",
                        "a rendered ConfigMap entry is not a string environment value",
                    )
                if _is_secret_environment(name):
                    raise ConfigurationBackupError(
                        "BACKUP_CONFIGURATION_PLAINTEXT_SECRET",
                        "a Secret-shaped environment variable is present in a ConfigMap",
                    )
                entries.append(PublicConfigEntry(name=name, value=value))
            public_maps.append(
                PublicConfigMapRecord(
                    namespace=identity[0],
                    name=identity[1],
                    entries=tuple(entries),
                )
            )

        references = tuple(
            HelmSecretReference(
                environment_variable=environment_variable,
                kubernetes_secret_name=value[0],
                secret_key_name=value[1],
                workloads=tuple(sorted(value[2])),
            )
            for environment_variable, value in sorted(secret_bindings.items())
        )
        public_document = DeploymentPublicConfigDocument(
            helm_render_sha256=hashlib.sha256(rendered).hexdigest(),
            config_maps=tuple(public_maps),
            workload_environment=tuple(
                sorted(
                    literal_environment,
                    key=lambda item: (item.workload, item.container, item.name),
                )
            ),
            runtime_fields=tuple(
                sorted(
                    runtime_fields,
                    key=lambda item: (item.workload, item.container, item.name),
                )
            ),
        )
        findings = find_plaintext_secret_material(public_document.model_dump(mode="json"))
        if findings:
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_PLAINTEXT_SECRET",
                "the public configuration contains Secret-shaped material",
            )
        return public_document, references


VaultRequester = Callable[
    [str, str, Mapping[str, Any] | None, Mapping[str, str] | None], Mapping[str, Any]
]


class VaultKvTransitProvider:
    """Real Vault KV v2 + Transit HMAC adapter with redacted failures."""

    def __init__(
        self,
        config: VaultProviderConfig,
        *,
        requester: VaultRequester | None = None,
    ) -> None:
        self.config = config
        self._requester = requester or self._request

    def capture(
        self,
        references: Sequence[HelmSecretReference],
        *,
        source_environment_id: str,
        helm_render_sha256: str,
    ) -> SecretsEnvelopeDocument:
        self._assert_hmac_key_version()
        grouped: dict[str, list[HelmSecretReference]] = {}
        for reference in references:
            grouped.setdefault(reference.kubernetes_secret_name, []).append(reference)
        evidence: list[SecretDependencyEvidence] = []
        for secret_name in sorted(grouped):
            version, created_at, values = self._read_secret(secret_name, version=None)
            for reference in sorted(
                grouped[secret_name], key=lambda item: item.environment_variable
            ):
                value = self._required_value(values, reference.secret_key_name)
                provider_reference = self._provider_secret_reference(secret_name, version)
                fingerprint = self._fingerprint(reference, provider_reference, value)
                evidence.append(
                    SecretDependencyEvidence(
                        environment_variable=reference.environment_variable,
                        kubernetes_secret_name=reference.kubernetes_secret_name,
                        secret_key_name=reference.secret_key_name,
                        workloads=reference.workloads,
                        provider_secret_reference=provider_reference,
                        secret_version=version,
                        version_created_at=created_at,
                        fingerprint_sha256=fingerprint,
                        hmac_key_reference=self.config.hmac_key_reference,
                    )
                )
        return SecretsEnvelopeDocument(
            source_environment_id=source_environment_id,
            helm_render_sha256=helm_render_sha256,
            provider_reference=self.config.provider_reference,
            kv_mount=self.config.kv_mount,
            kv_prefix=self.config.kv_prefix,
            hmac_key_reference=self.config.hmac_key_reference,
            dependencies=tuple(sorted(evidence, key=lambda item: item.environment_variable)),
        )

    def preflight(self, envelope: SecretsEnvelopeDocument) -> str:
        if (
            envelope.provider_reference != self.config.provider_reference
            or envelope.kv_mount != self.config.kv_mount
            or envelope.kv_prefix != self.config.kv_prefix
            or envelope.hmac_key_reference != self.config.hmac_key_reference
        ):
            raise ConfigurationBackupError(
                "RESTORE_SECRET_PROVIDER_MISMATCH",
                "the restore Secret provider does not match the backup envelope",
            )
        self._assert_hmac_key_version()
        grouped: dict[tuple[str, int], list[SecretDependencyEvidence]] = {}
        for dependency in envelope.dependencies:
            grouped.setdefault(
                (dependency.kubernetes_secret_name, dependency.secret_version), []
            ).append(dependency)
        for (secret_name, version), dependencies in sorted(grouped.items()):
            observed_version, _created_at, values = self._read_secret(secret_name, version=version)
            if observed_version != version:
                raise ConfigurationBackupError(
                    "RESTORE_SECRET_VERSION_MISMATCH",
                    "the restore Secret provider returned the wrong immutable version",
                )
            expected_created_at = dependencies[0].version_created_at
            if (
                any(
                    dependency.version_created_at != expected_created_at
                    for dependency in dependencies
                )
                or _created_at != expected_created_at
            ):
                raise ConfigurationBackupError(
                    "RESTORE_SECRET_VERSION_MISMATCH",
                    "the restore Secret version identity differs from the backup envelope",
                )
            for dependency in dependencies:
                value = self._required_value(values, dependency.secret_key_name)
                expected_reference = self._provider_secret_reference(secret_name, version)
                if dependency.provider_secret_reference != expected_reference:
                    raise ConfigurationBackupError(
                        "RESTORE_SECRET_REFERENCE_MISMATCH",
                        "a Secret dependency reference does not match its immutable version",
                    )
                observed = self._fingerprint(
                    HelmSecretReference(
                        environment_variable=dependency.environment_variable,
                        kubernetes_secret_name=dependency.kubernetes_secret_name,
                        secret_key_name=dependency.secret_key_name,
                        workloads=dependency.workloads,
                    ),
                    expected_reference,
                    value,
                )
                if not hmac.compare_digest(observed, dependency.fingerprint_sha256):
                    raise ConfigurationBackupError(
                        "RESTORE_SECRET_FINGERPRINT_MISMATCH",
                        "a required Secret key does not match its backup fingerprint",
                    )
        return _preflight_coordinate(envelope.dependencies)

    def _assert_hmac_key_version(self) -> None:
        try:
            response = self._requester(
                "GET",
                self._api_path(self.config.transit_mount, "keys", self.config.hmac_key_name),
                None,
                None,
            )
        except ConfigurationBackupError as exc:
            if exc.code == "RESTORE_SECRET_VERSION_UNAVAILABLE":
                raise ConfigurationBackupError(
                    "RESTORE_KMS_KEY_VERSION_MISSING",
                    "the required immutable Vault Transit key is unavailable",
                ) from exc
            raise
        data = _mapping(
            response.get("data"),
            code="BACKUP_KMS_RESPONSE_INVALID",
            message="the Vault Transit key response is malformed",
        )
        keys = _mapping(
            data.get("keys"),
            code="BACKUP_KMS_RESPONSE_INVALID",
            message="the Vault Transit key has no version inventory",
        )
        if str(self.config.hmac_key_version) not in keys:
            raise ConfigurationBackupError(
                "RESTORE_KMS_KEY_VERSION_MISSING",
                "the required immutable Vault Transit key version is unavailable",
            )

    def _read_secret(
        self, secret_name: str, *, version: int | None
    ) -> tuple[int, str, Mapping[str, Any]]:
        query = None if version is None else {"version": str(version)}
        response = self._requester(
            "GET",
            self._api_path(
                self.config.kv_mount,
                "data",
                *self.config.kv_prefix.split("/"),
                secret_name,
            ),
            None,
            query,
        )
        outer = _mapping(
            response.get("data"),
            code="BACKUP_SECRET_RESPONSE_INVALID",
            message="the Vault KV v2 response is malformed",
        )
        values = _mapping(
            outer.get("data"),
            code="BACKUP_SECRET_RESPONSE_INVALID",
            message="the Vault KV v2 response has no Secret data",
        )
        metadata = _mapping(
            outer.get("metadata"),
            code="BACKUP_SECRET_RESPONSE_INVALID",
            message="the Vault KV v2 response has no version metadata",
        )
        observed_version = metadata.get("version")
        created_time = metadata.get("created_time")
        destroyed = metadata.get("destroyed")
        deletion_time = metadata.get("deletion_time")
        if (
            not isinstance(observed_version, int)
            or isinstance(observed_version, bool)
            or observed_version < 1
            or not isinstance(created_time, str)
            or destroyed is not False
            or deletion_time not in {"", None}
        ):
            raise ConfigurationBackupError(
                "RESTORE_SECRET_VERSION_UNAVAILABLE",
                "a required Vault Secret version is deleted, destroyed, or malformed",
            )
        if (
            re.fullmatch(
                r"[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
                r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
                r"(?:\.[0-9]{1,9})?Z",
                created_time,
            )
            is None
        ):
            raise ConfigurationBackupError(
                "BACKUP_SECRET_RESPONSE_INVALID",
                "the Vault Secret version timestamp is malformed",
            )
        try:
            datetime.strptime(created_time[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError as exc:
            raise ConfigurationBackupError(
                "BACKUP_SECRET_RESPONSE_INVALID",
                "the Vault Secret version timestamp has an invalid calendar date",
            ) from exc
        return observed_version, created_time, values

    @staticmethod
    def _required_value(values: Mapping[str, Any], key: str) -> str:
        value = values.get(key)
        if (
            not isinstance(value, str)
            or not value
            or len(value.encode("utf-8")) > _MAX_SECRET_VALUE_BYTES
        ):
            raise ConfigurationBackupError(
                "RESTORE_SECRET_KEY_MISSING",
                "a required Secret key is absent or invalid",
            )
        return value

    def _provider_secret_reference(self, secret_name: str, version: int) -> str:
        path = "/".join(
            (
                self.config.provider_reference,
                self.config.kv_mount,
                self.config.kv_prefix,
                secret_name,
            )
        )
        reference = f"vault-kv://{path}/versions/v{version}"
        if len(reference) > 255:
            raise ConfigurationBackupError(
                "BACKUP_SECRET_REFERENCE_INVALID",
                "a versioned Vault Secret reference exceeds the manifest bound",
            )
        return reference

    def _fingerprint(
        self,
        reference: HelmSecretReference,
        provider_secret_reference: str,
        value: str,
    ) -> str:
        message = canonical_json_bytes(
            {
                "environment_variable": reference.environment_variable,
                "kubernetes_secret_name": reference.kubernetes_secret_name,
                "provider_secret_reference": provider_secret_reference,
                "secret_key_name": reference.secret_key_name,
                "value_base64": base64.b64encode(value.encode("utf-8")).decode("ascii"),
            }
        )
        try:
            response = self._requester(
                "POST",
                self._api_path(
                    self.config.transit_mount,
                    "hmac",
                    self.config.hmac_key_name,
                    "sha2-256",
                ),
                {
                    "input": base64.b64encode(message).decode("ascii"),
                    "key_version": self.config.hmac_key_version,
                },
                None,
            )
        except ConfigurationBackupError as exc:
            if exc.code == "RESTORE_SECRET_VERSION_UNAVAILABLE":
                raise ConfigurationBackupError(
                    "RESTORE_KMS_KEY_VERSION_MISMATCH",
                    "the required immutable Vault Transit HMAC key version is unusable",
                ) from exc
            raise
        data = _mapping(
            response.get("data"),
            code="BACKUP_KMS_RESPONSE_INVALID",
            message="the Vault Transit HMAC response is malformed",
        )
        encoded = data.get("hmac")
        if not isinstance(encoded, str):
            raise ConfigurationBackupError(
                "BACKUP_KMS_RESPONSE_INVALID",
                "the Vault Transit HMAC response has no digest",
            )
        match = _VAULT_HMAC.fullmatch(encoded)
        if match is None or int(match.group("version")) != self.config.hmac_key_version:
            raise ConfigurationBackupError(
                "RESTORE_KMS_KEY_VERSION_MISMATCH",
                "Vault Transit used a different HMAC key version",
            )
        try:
            digest = base64.b64decode(match.group("digest"), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ConfigurationBackupError(
                "BACKUP_KMS_RESPONSE_INVALID",
                "the Vault Transit HMAC digest is malformed",
            ) from exc
        if len(digest) != 32:
            raise ConfigurationBackupError(
                "BACKUP_KMS_RESPONSE_INVALID",
                "the Vault Transit HMAC digest has the wrong length",
            )
        return digest.hex()

    @staticmethod
    def _api_path(*parts: str) -> str:
        return "/v1/" + "/".join(quote(part, safe="") for part in parts)

    def _request(
        self,
        method: str,
        api_path: str,
        body: Mapping[str, Any] | None,
        query: Mapping[str, str] | None,
    ) -> Mapping[str, Any]:
        url = f"{self.config.endpoint_url}{api_path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        payload = None if body is None else canonical_json_bytes(body)
        headers = {
            "Accept": "application/json",
            "X-Vault-Token": self.config.token.get_secret_value(),
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.config.namespace is not None:
            headers["X-Vault-Namespace"] = self.config.namespace
        request = Request(url, data=payload, headers=headers, method=method)
        try:
            with build_opener(_RejectRedirects).open(
                request, timeout=self.config.timeout_seconds
            ) as response:  # noqa: S310
                raw = response.read(_MAX_PROVIDER_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            code = (
                "RESTORE_SECRET_VERSION_UNAVAILABLE"
                if exc.code in {400, 404}
                else "BACKUP_SECRET_PROVIDER_REJECTED"
            )
            raise ConfigurationBackupError(
                code, "the Vault provider rejected a Secret/KMS request"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ConfigurationBackupError(
                "BACKUP_SECRET_PROVIDER_UNAVAILABLE",
                "the Vault provider is unavailable",
            ) from exc
        if len(raw) > _MAX_PROVIDER_RESPONSE_BYTES:
            raise ConfigurationBackupError(
                "BACKUP_SECRET_RESPONSE_INVALID",
                "the Vault provider response exceeds its bounded size",
            )
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigurationBackupError(
                "BACKUP_SECRET_RESPONSE_INVALID",
                "the Vault provider returned malformed JSON",
            ) from exc
        return _mapping(
            document,
            code="BACKUP_SECRET_RESPONSE_INVALID",
            message="the Vault provider response is not an object",
        )


def _preflight_coordinate(dependencies: Sequence[SecretDependencyEvidence]) -> str:
    evidence = [item.model_dump(mode="json") for item in dependencies]
    digest = hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()
    return f"secret-dependency-set/v1:sha256:{digest}"


def _sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_STREAM_CHUNK_BYTES):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _secure_staging(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_STAGING_UNSAFE",
            "the configuration-backup staging directory must be owner-only",
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
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_STAGING_UNSAFE",
            "a configuration artifact directory is unsafe",
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
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_ARTIFACT_EXISTS",
            "a configuration-backup artifact already exists",
        ) from exc
    temporary.unlink()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _public_yaml(document: DeploymentPublicConfigDocument) -> bytes:
    rendered = yaml.safe_dump(
        document.model_dump(mode="json"),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=True,
        width=100,
    )
    return rendered.encode("utf-8")


class ConfigurationBackupAdapter:
    """Create fenced public-config and Secret dependency artifacts."""

    def __init__(
        self,
        provider: VaultKvTransitProvider,
        *,
        helm_adapter: HelmRenderedDependencyAdapter | None = None,
    ) -> None:
        self._provider = provider
        self._helm = helm_adapter or HelmRenderedDependencyAdapter()

    def create(
        self,
        rendered_helm: bytes,
        *,
        lease: MaintenanceBackupLease,
        lease_verifier: Callable[[MaintenanceBackupLease], None],
        staging_directory: Path,
        mode: Literal["snapshot", "portable"],
        encryptor: AgeEncryptedWriter | None = None,
    ) -> ConfigurationBackupArtifact:
        if (mode == "portable") != (encryptor is not None):
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_ENCRYPTION_INVALID",
                "portable configuration backup requires exactly one age encryptor",
            )
        public_document, references = self._helm.inspect(rendered_helm)
        self._verify_lease(lease, lease_verifier)
        envelope = self._provider.capture(
            references,
            source_environment_id=lease.environment_id,
            helm_render_sha256=public_document.helm_render_sha256,
        )
        coordinate = self._provider.preflight(envelope)
        self._verify_lease(lease, lease_verifier)

        staging = _secure_staging(staging_directory)
        config_directory = _private_directory(staging, "config")
        secret_directory = _private_directory(staging, "secrets")
        public_path = config_directory / "public.yaml"
        envelope_name = "envelope.json.age" if mode == "portable" else "envelope.json"
        envelope_path = secret_directory / envelope_name
        if any(path.exists() or path.is_symlink() for path in (public_path, envelope_path)):
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_ARTIFACT_EXISTS",
                "a configuration-backup artifact already exists",
            )
        public_temporary = public_path.with_name(f".{public_path.name}.{os.getpid()}.partial")
        envelope_temporary = envelope_path.with_name(f".{envelope_path.name}.{os.getpid()}.partial")
        if any(
            path.exists() or path.is_symlink() for path in (public_temporary, envelope_temporary)
        ):
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_STAGING_CONFLICT",
                "a configuration-backup partial artifact already exists",
            )
        published: list[Path] = []
        try:
            with _private_writer(public_temporary) as stream:
                stream.write(_public_yaml(public_document))
            envelope_writer = (
                encryptor.open(envelope_temporary, cwd=envelope_temporary.parent)
                if encryptor is not None
                else _private_writer(envelope_temporary)
            )
            with envelope_writer as stream:
                stream.write(canonical_json_bytes(envelope.model_dump(mode="json")))
                stream.write(b"\n")
            _publish_no_replace(public_temporary, public_path)
            published.append(public_path)
            _publish_no_replace(envelope_temporary, envelope_path)
            published.append(envelope_path)
            _fsync_directory(config_directory)
            _fsync_directory(secret_directory)
            _fsync_directory(staging)
        except BaseException:
            for path in (public_temporary, envelope_temporary):
                with suppress(FileNotFoundError):
                    path.unlink()
            for path in reversed(published):
                with suppress(FileNotFoundError):
                    path.unlink()
            raise

        public_size, public_sha = _sha256_file(public_path)
        envelope_size, envelope_sha = _sha256_file(envelope_path)
        dependencies = tuple(
            SecretDependencyV1(
                environment_variable=item.environment_variable,
                secret_reference=item.provider_secret_reference,
                secret_key_name=item.secret_key_name,
                version=f"v{item.secret_version}",
                fingerprint_sha256=item.fingerprint_sha256,
            )
            for item in envelope.dependencies
        )
        return ConfigurationBackupArtifact(
            public_config=PublicConfigArtifact(
                path=public_path,
                size_bytes=public_size,
                sha256=public_sha,
                helm_render_sha256=public_document.helm_render_sha256,
            ),
            secrets_envelope=SecretsEnvelopeArtifact(
                path=envelope_path,
                logical_path=f"secrets/{envelope_name}",
                mode=mode,
                media_type=(
                    "application/vnd.hc.secret-dependencies.v1+json+age"
                    if mode == "portable"
                    else "application/vnd.hc.secret-dependencies.v1+json"
                ),
                client_side_encryption=(
                    "age_x25519_v1" if mode == "portable" else "repository_kms_only"
                ),
                size_bytes=envelope_size,
                sha256=envelope_sha,
                dependency_count=len(dependencies),
                provider_reference=envelope.provider_reference,
                hmac_key_reference=envelope.hmac_key_reference,
            ),
            manifest_dependencies=dependencies,
            preflight_coordinate=coordinate,
        )

    @staticmethod
    def _verify_lease(
        lease: MaintenanceBackupLease,
        verifier: Callable[[MaintenanceBackupLease], None],
    ) -> None:
        try:
            verifier(lease)
        except ConfigurationBackupError:
            raise
        except Exception as exc:
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_FENCE_REJECTED",
                "the configuration-backup Job does not own the maintenance fence",
            ) from exc


class ConfigurationBackupVerifier:
    """Verify artifact hashes, strict documents, and exact provider dependencies."""

    def verify(
        self,
        artifact: ConfigurationBackupArtifact,
        *,
        provider: VaultKvTransitProvider,
        decryptor: AgeDecryptedReader | None = None,
    ) -> ConfigurationVerificationReport:
        self._verify_file(
            artifact.public_config.path,
            artifact.public_config.size_bytes,
            artifact.public_config.sha256,
        )
        self._verify_file(
            artifact.secrets_envelope.path,
            artifact.secrets_envelope.size_bytes,
            artifact.secrets_envelope.sha256,
        )
        try:
            public_raw = artifact.public_config.path.read_bytes()
            public_value = yaml.load(public_raw.decode("utf-8"), Loader=_UniqueSafeLoader)
            public_document = DeploymentPublicConfigDocument.model_validate(public_value)
        except ConfigurationBackupError:
            raise
        except Exception as exc:
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_PUBLIC_CONFIG_INVALID",
                "the public configuration artifact violates its strict contract",
            ) from exc
        if public_document.helm_render_sha256 != artifact.public_config.helm_render_sha256:
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_PUBLIC_CONFIG_INVALID",
                "the public configuration render identity differs from its receipt",
            )
        findings = find_plaintext_secret_material(public_document.model_dump(mode="json"))
        if findings:
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_PLAINTEXT_SECRET",
                "the public configuration artifact contains Secret-shaped material",
            )

        source_context: AbstractContextManager[BinaryIO]
        if artifact.secrets_envelope.mode == "portable":
            if decryptor is None:
                raise ConfigurationBackupError(
                    "BACKUP_CONFIGURATION_AGE_IDENTITY_REQUIRED",
                    "portable Secret envelope verification requires an age identity",
                )
            source_context = decryptor.open(
                artifact.secrets_envelope.path,
                cwd=artifact.secrets_envelope.path.parent,
            )
        else:
            source_context = _binary_reader(artifact.secrets_envelope.path)
        try:
            with source_context as stream:
                envelope_raw = stream.read(_MAX_ENVELOPE_BYTES + 1)
            if len(envelope_raw) > _MAX_ENVELOPE_BYTES:
                raise ConfigurationBackupError(
                    "BACKUP_CONFIGURATION_ENVELOPE_INVALID",
                    "the Secret envelope exceeds its bounded size",
                )
            envelope_value = _strict_json(envelope_raw)
            envelope = SecretsEnvelopeDocument.model_validate(envelope_value)
        except ConfigurationBackupError:
            raise
        except Exception as exc:
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_ENVELOPE_INVALID",
                "the Secret envelope violates its strict contract",
            ) from exc
        if (
            envelope.provider_reference != artifact.secrets_envelope.provider_reference
            or envelope.hmac_key_reference != artifact.secrets_envelope.hmac_key_reference
            or len(envelope.dependencies) != artifact.secrets_envelope.dependency_count
            or envelope.helm_render_sha256 != public_document.helm_render_sha256
        ):
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_ENVELOPE_INVALID",
                "the Secret envelope differs from its receipt summary",
            )
        manifest_dependencies = tuple(
            SecretDependencyV1(
                environment_variable=item.environment_variable,
                secret_reference=item.provider_secret_reference,
                secret_key_name=item.secret_key_name,
                version=f"v{item.secret_version}",
                fingerprint_sha256=item.fingerprint_sha256,
            )
            for item in envelope.dependencies
        )
        if manifest_dependencies != artifact.manifest_dependencies:
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_ENVELOPE_INVALID",
                "the Secret envelope dependencies differ from the manifest summary",
            )
        coordinate = provider.preflight(envelope)
        if coordinate != artifact.preflight_coordinate:
            raise ConfigurationBackupError(
                "RESTORE_SECRET_PREFLIGHT_MISMATCH",
                "the Secret restore preflight coordinate differs from the backup receipt",
            )
        return ConfigurationVerificationReport(
            public_config_sha256=artifact.public_config.sha256,
            secrets_envelope_sha256=artifact.secrets_envelope.sha256,
            dependency_count=len(envelope.dependencies),
            preflight_coordinate=coordinate,
        )

    @staticmethod
    def _verify_file(path: Path, expected_size: int, expected_sha: str) -> None:
        try:
            resolved = path.resolve(strict=True)
            info = resolved.stat()
        except FileNotFoundError as exc:
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_ARTIFACT_MISSING",
                "a configuration-backup artifact is missing",
            ) from exc
        if (
            not path.is_absolute()
            or resolved != path
            or path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_ARTIFACT_INVALID",
                "a configuration-backup artifact path is unsafe",
            )
        size, digest = _sha256_file(path)
        if size != expected_size or digest != expected_sha:
            raise ConfigurationBackupError(
                "BACKUP_CONFIGURATION_ARTIFACT_HASH_MISMATCH",
                "a configuration-backup artifact differs from its receipt hash",
            )


def _strict_json(payload: bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ConfigurationBackupError(
                    "BACKUP_CONFIGURATION_ENVELOPE_INVALID",
                    "the Secret envelope contains duplicate JSON member names",
                )
            result[key] = value
        return result

    try:
        return json.loads(payload, object_pairs_hook=pairs)
    except ConfigurationBackupError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationBackupError(
            "BACKUP_CONFIGURATION_ENVELOPE_INVALID",
            "the Secret envelope is malformed JSON",
        ) from exc


@contextmanager
def _binary_reader(path: Path) -> Iterator[BinaryIO]:
    with path.open("rb") as stream:
        yield stream
