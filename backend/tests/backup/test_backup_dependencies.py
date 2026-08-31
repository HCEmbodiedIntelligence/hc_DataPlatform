from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml

from hc_data_platform.backup.dependencies import (
    ConfigurationBackupAdapter,
    ConfigurationBackupError,
    ConfigurationBackupVerifier,
    HelmRenderedDependencyAdapter,
    SecretsEnvelopeDocument,
    VaultKvTransitProvider,
    VaultProviderConfig,
)
from hc_data_platform.backup.postgresql import MaintenanceBackupLease

_SECRET_KEYS = {
    "HC_AUTH_ABUSE_HMAC_SECRET": "auth-abuse-hmac-secret",
    "HC_AUTH_TURNSTILE_SECRET": "auth-turnstile-secret",
    "HC_CURSOR_SECRET": "cursor-secret",
    "HC_DATA_SOURCE_CREDENTIAL_KEY": "data-source-credential-key",
    "HC_OBJECT_STORE_ACCESS_KEY": "access-key",
    "HC_OBJECT_STORE_SECRET_KEY": "secret-key",
    "HC_POSTGRES_DSN": "dsn",
}


def _rendered(
    *,
    omit: str | None = None,
    literal_secret: bool = False,
    optional_secret: bool = False,
) -> bytes:
    config_map = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "hc-public", "namespace": "hc-test"},
        "data": {
            "HC_ENVIRONMENT": "production",
            "HC_PASSWORD_MIN_LENGTH": "12",
            "HC_PUBLIC_ENDPOINT": "https://public.example.invalid",
        },
    }
    environment: list[dict[str, object]] = []
    for name, key in _SECRET_KEYS.items():
        if name == omit:
            continue
        if literal_secret and name == "HC_CURSOR_SECRET":
            environment.append({"name": name, "value": "plaintext-cursor-value"})
            continue
        reference: dict[str, object] = {
            "name": "runtime-secrets",
            "key": key,
        }
        if optional_secret and name == "HC_CURSOR_SECRET":
            reference["optional"] = True
        environment.append(
            {
                "name": name,
                "valueFrom": {"secretKeyRef": reference},
            }
        )
    environment.extend(
        (
            {"name": "HC_IMAGE_DIGEST", "value": "sha256:" + "a" * 64},
            {
                "name": "HC_POD_NAME",
                "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}},
            },
        )
    )
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "hc-api", "namespace": "hc-test"},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "api",
                            "envFrom": [{"configMapRef": {"name": "hc-public"}}],
                            "env": environment,
                        }
                    ]
                }
            }
        },
    }
    return yaml.safe_dump_all(
        (config_map, deployment),
        explicit_start=True,
        sort_keys=False,
    ).encode()


class _VaultRequester:
    def __init__(self) -> None:
        self.values: dict[str, str] = {
            key: f"provider-value-for-{key}" for key in _SECRET_KEYS.values()
        }
        self.version = 3
        self.created_at = "2026-08-28T01:02:03.123456Z"
        self.transit_versions: dict[str, object] = {"1": 1_777_000_000}
        self.hmac_version = 1

    def __call__(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None,
        query: Mapping[str, str] | None,
    ) -> Mapping[str, Any]:
        if method == "GET" and path == "/v1/transit/keys/config-hmac":
            return {"data": {"keys": self.transit_versions}}
        if method == "GET" and path == "/v1/secret/data/hc-data-platform/runtime-secrets":
            if query is not None and query != {"version": str(self.version)}:
                raise ConfigurationBackupError(
                    "RESTORE_SECRET_VERSION_UNAVAILABLE",
                    "the requested immutable Secret version is unavailable",
                )
            return {
                "data": {
                    "data": dict(self.values),
                    "metadata": {
                        "created_time": self.created_at,
                        "deletion_time": "",
                        "destroyed": False,
                        "version": self.version,
                    },
                }
            }
        if method == "POST" and path == "/v1/transit/hmac/config-hmac/sha2-256":
            assert body is not None
            message = base64.b64decode(str(body["input"]), validate=True)
            digest = hmac.new(b"external-transit-key", message, hashlib.sha256).digest()
            encoded = base64.b64encode(digest).decode("ascii")
            return {"data": {"hmac": f"vault:v{self.hmac_version}:{encoded}"}}
        raise AssertionError((method, path, body, query))


def _provider(requester: _VaultRequester | None = None) -> VaultKvTransitProvider:
    return VaultKvTransitProvider(
        VaultProviderConfig.model_validate(
            {
                "endpoint_url": "http://127.0.0.1:8200",
                "token": "unit-vault-token-must-never-be-exported",
                "provider_reference": "unit-vault",
                "kv_mount": "secret",
                "kv_prefix": "hc-data-platform",
                "transit_mount": "transit",
                "hmac_key_name": "config-hmac",
                "hmac_key_version": 1,
                "hmac_key_reference": ("kms://vault/unit-vault/transit/config-hmac/versions/v1"),
            }
        ),
        requester=requester or _VaultRequester(),
    )


def _lease() -> MaintenanceBackupLease:
    return MaintenanceBackupLease(
        environment_id="unit-environment",
        operation_id="backup-operation",
        owner_instance_id=UUID("11111111-2222-4333-8444-555555555555"),
        fencing_token=7,
    )


def test_helm_adapter_exports_public_config_and_exact_secret_references() -> None:
    public, references = HelmRenderedDependencyAdapter().inspect(_rendered())

    assert len(public.config_maps) == 1
    assert [entry.name for entry in public.config_maps[0].entries] == [
        "HC_ENVIRONMENT",
        "HC_PASSWORD_MIN_LENGTH",
        "HC_PUBLIC_ENDPOINT",
    ]
    assert [(item.name, item.field_path) for item in public.runtime_fields] == [
        ("HC_POD_NAME", "metadata.name")
    ]
    assert [item.environment_variable for item in references] == sorted(_SECRET_KEYS)
    assert all(item.kubernetes_secret_name == "runtime-secrets" for item in references)
    exported = public.model_dump_json()
    assert "provider-value" not in exported
    assert "runtime-secrets" not in exported


@pytest.mark.parametrize(
    ("rendered", "code"),
    (
        (_rendered(literal_secret=True), "BACKUP_CONFIGURATION_PLAINTEXT_SECRET"),
        (
            _rendered(omit="HC_POSTGRES_DSN"),
            "BACKUP_CONFIGURATION_REQUIRED_SECRET_MISSING",
        ),
        (
            _rendered(optional_secret=True),
            "BACKUP_CONFIGURATION_SECRET_REFERENCE_INVALID",
        ),
    ),
)
def test_helm_adapter_rejects_unsafe_secret_contracts(rendered: bytes, code: str) -> None:
    with pytest.raises(ConfigurationBackupError) as captured:
        HelmRenderedDependencyAdapter().inspect(rendered)
    assert captured.value.code == code


def test_helm_adapter_rejects_duplicate_yaml_members() -> None:
    rendered = (
        _rendered()
        + b"---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: dup\n  name: dup2\n"
    )
    with pytest.raises(ConfigurationBackupError) as captured:
        HelmRenderedDependencyAdapter().inspect(rendered)
    assert captured.value.code == "BACKUP_CONFIGURATION_HELM_INVALID"


def test_vault_capture_exports_only_exact_versions_and_opaque_hmacs() -> None:
    requester = _VaultRequester()
    provider = _provider(requester)
    public, references = HelmRenderedDependencyAdapter().inspect(_rendered())

    envelope = provider.capture(
        references,
        source_environment_id="unit-environment",
        helm_render_sha256=public.helm_render_sha256,
    )
    coordinate = provider.preflight(envelope)

    assert coordinate.startswith("secret-dependency-set/v1:sha256:")
    assert {item.secret_version for item in envelope.dependencies} == {3}
    assert {item.version_created_at for item in envelope.dependencies} == {
        "2026-08-28T01:02:03.123456Z"
    }
    serialized = envelope.model_dump_json()
    for secret in requester.values.values():
        assert secret not in serialized
    assert "unit-vault-token-must-never-be-exported" not in serialized
    assert "127.0.0.1" not in serialized
    assert "vault-kv://unit-vault/secret/hc-data-platform/runtime-secrets/versions/v3" in serialized


def test_vault_preflight_rejects_changed_or_missing_required_key() -> None:
    requester = _VaultRequester()
    provider = _provider(requester)
    public, references = HelmRenderedDependencyAdapter().inspect(_rendered())
    envelope = provider.capture(
        references,
        source_environment_id="unit-environment",
        helm_render_sha256=public.helm_render_sha256,
    )

    requester.values["cursor-secret"] = "changed-after-backup"
    with pytest.raises(ConfigurationBackupError) as changed:
        provider.preflight(envelope)
    assert changed.value.code == "RESTORE_SECRET_FINGERPRINT_MISMATCH"

    requester.values["cursor-secret"] = "provider-value-for-cursor-secret"
    del requester.values["dsn"]
    with pytest.raises(ConfigurationBackupError) as missing:
        provider.preflight(envelope)
    assert missing.value.code == "RESTORE_SECRET_KEY_MISSING"


def test_vault_preflight_rejects_missing_or_wrong_transit_key_version() -> None:
    requester = _VaultRequester()
    provider = _provider(requester)
    public, references = HelmRenderedDependencyAdapter().inspect(_rendered())
    envelope = provider.capture(
        references,
        source_environment_id="unit-environment",
        helm_render_sha256=public.helm_render_sha256,
    )

    requester.transit_versions.clear()
    with pytest.raises(ConfigurationBackupError) as missing:
        provider.preflight(envelope)
    assert missing.value.code == "RESTORE_KMS_KEY_VERSION_MISSING"

    requester.transit_versions["1"] = 1_777_000_000
    requester.hmac_version = 2
    with pytest.raises(ConfigurationBackupError) as mismatch:
        provider.preflight(envelope)
    assert mismatch.value.code == "RESTORE_KMS_KEY_VERSION_MISMATCH"


def test_secret_envelope_contract_cannot_omit_a_required_dependency() -> None:
    requester = _VaultRequester()
    public, references = HelmRenderedDependencyAdapter().inspect(_rendered())
    envelope = _provider(requester).capture(
        references,
        source_environment_id="unit-environment",
        helm_render_sha256=public.helm_render_sha256,
    )
    value = envelope.model_dump(mode="json")
    value["dependencies"] = [
        item for item in value["dependencies"] if item["environment_variable"] != "HC_POSTGRES_DSN"
    ]

    with pytest.raises(ValueError, match="omits a required"):
        SecretsEnvelopeDocument.model_validate(value)


def test_configuration_adapter_creates_private_pair_and_verifies(tmp_path: Path) -> None:
    requester = _VaultRequester()
    provider = _provider(requester)
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    lease_checks: list[MaintenanceBackupLease] = []

    artifact = ConfigurationBackupAdapter(provider).create(
        _rendered(),
        lease=_lease(),
        lease_verifier=lease_checks.append,
        staging_directory=staging,
        mode="snapshot",
    )
    report = ConfigurationBackupVerifier().verify(artifact, provider=provider)

    assert lease_checks == [_lease(), _lease()]
    assert report.dependency_count == 7
    assert artifact.public_config.path.stat().st_mode & 0o077 == 0
    assert artifact.secrets_envelope.path.stat().st_mode & 0o077 == 0
    combined = (
        artifact.public_config.path.read_bytes() + artifact.secrets_envelope.path.read_bytes()
    )
    for secret in requester.values.values():
        assert secret.encode() not in combined
    assert b"unit-vault-token" not in combined


def test_configuration_adapter_fence_failure_precedes_artifact_writes(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)

    def reject(_lease: MaintenanceBackupLease) -> None:
        raise RuntimeError("stale owner details must not be exposed")

    with pytest.raises(ConfigurationBackupError) as captured:
        ConfigurationBackupAdapter(_provider()).create(
            _rendered(),
            lease=_lease(),
            lease_verifier=reject,
            staging_directory=staging,
            mode="snapshot",
        )

    assert captured.value.code == "BACKUP_CONFIGURATION_FENCE_REJECTED"
    assert list(staging.iterdir()) == []
    assert "stale owner" not in str(captured.value)


def test_configuration_verifier_binds_public_and_secret_helm_identity(tmp_path: Path) -> None:
    provider = _provider()
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    artifact = ConfigurationBackupAdapter(provider).create(
        _rendered(),
        lease=_lease(),
        lease_verifier=lambda _lease: None,
        staging_directory=staging,
        mode="snapshot",
    )
    envelope_path = artifact.secrets_envelope.path
    value = json.loads(envelope_path.read_bytes())
    value["helm_render_sha256"] = "f" * 64
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    envelope_path.write_bytes(payload)
    envelope_path.chmod(0o600)
    envelope_artifact = artifact.secrets_envelope.model_copy(
        update={
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    )
    modified = artifact.model_copy(update={"secrets_envelope": envelope_artifact})

    with pytest.raises(ConfigurationBackupError) as captured:
        ConfigurationBackupVerifier().verify(modified, provider=provider)
    assert captured.value.code == "BACKUP_CONFIGURATION_ENVELOPE_INVALID"
