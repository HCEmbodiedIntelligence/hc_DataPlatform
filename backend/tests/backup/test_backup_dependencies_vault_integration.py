from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from hc_data_platform.backup.dependencies import (
    ConfigurationBackupError,
    HelmRenderedDependencyAdapter,
    VaultKvTransitProvider,
    VaultProviderConfig,
)


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required")
    return value


def _request(
    endpoint: str,
    token: str,
    method: str,
    path: str,
    body: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    payload = None
    headers = {"Accept": "application/json", "X-Vault-Token": token}
    if body is not None:
        payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    request = Request(f"{endpoint}{path}", data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310
            raw = response.read(8 * 1024 * 1024)
    except HTTPError as exc:
        raise AssertionError(f"disposable Vault setup request failed with HTTP {exc.code}") from exc
    if not raw:
        return {}
    value = json.loads(raw)
    assert isinstance(value, dict)
    return value


def _secret_values(marker: str) -> dict[str, dict[str, str]]:
    return {
        "hc-data-application": {
            "auth-abuse-hmac-secret": f"{marker}-abuse-hmac",
            "auth-turnstile-secret": f"{marker}-turnstile",
            "cursor-secret": f"{marker}-cursor",
            "data-source-credential-key": f"{marker}-credential-key",
        },
        "hc-data-object-store": {
            "access-key": f"{marker}-object-access",
            "secret-key": f"{marker}-object-secret",
        },
        "hc-data-postgres": {
            "dsn": f"postgresql://hc_backup:{marker}-pg@postgres.invalid/hc_data",
        },
    }


@pytest.mark.integration
def test_real_vault_exact_secret_and_transit_versions_fail_closed() -> None:
    assert _required("HC_BACKUP_VAULT_INTEGRATION_ALLOW_MUTATION") == "disposable-only"
    endpoint = _required("HC_BACKUP_VAULT_INTEGRATION_ENDPOINT").rstrip("/")
    token = _required("HC_BACKUP_VAULT_INTEGRATION_TOKEN")
    prefix = _required("HC_BACKUP_VAULT_INTEGRATION_PREFIX")
    key_name = _required("HC_BACKUP_VAULT_INTEGRATION_HMAC_KEY")
    chart = Path(_required("HC_BACKUP_VAULT_INTEGRATION_HELM_CHART"))
    values_file = Path(_required("HC_BACKUP_VAULT_INTEGRATION_HELM_VALUES"))
    rendered = subprocess.run(
        [
            "helm",
            "template",
            "hc-bak204-integration",
            str(chart),
            "--values",
            str(values_file),
        ],
        capture_output=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout
    public, references = HelmRenderedDependencyAdapter().inspect(rendered)
    assert len(references) == 7

    _request(
        endpoint,
        token,
        "POST",
        f"/v1/transit/keys/{quote(key_name, safe='')}",
        {
            "type": "aes256-gcm96",
            "derived": False,
            "exportable": False,
            "allow_plaintext_backup": False,
        },
    )
    values = _secret_values("bak204-real-secret-must-not-be-exported")
    for secret_name, data in values.items():
        _request(
            endpoint,
            token,
            "POST",
            f"/v1/secret/data/{prefix}/{secret_name}",
            {"data": data},
        )

    provider = VaultKvTransitProvider(
        VaultProviderConfig.model_validate(
            {
                "endpoint_url": endpoint,
                "token": token,
                "provider_reference": "bak204-disposable-vault",
                "kv_mount": "secret",
                "kv_prefix": prefix,
                "transit_mount": "transit",
                "hmac_key_name": key_name,
                "hmac_key_version": 1,
                "hmac_key_reference": (
                    f"kms://vault/bak204-disposable-vault/transit/{key_name}/versions/v1"
                ),
            }
        )
    )
    original = provider.capture(
        references,
        source_environment_id="bak204-integration",
        helm_render_sha256=public.helm_render_sha256,
    )
    original_coordinate = provider.preflight(original)

    changed_application = dict(values["hc-data-application"])
    changed_application["cursor-secret"] = "new-latest-version-must-not-replace-old"
    _request(
        endpoint,
        token,
        "POST",
        f"/v1/secret/data/{prefix}/hc-data-application",
        {"data": changed_application},
    )
    assert provider.preflight(original) == original_coordinate

    postgres_dependency = next(
        item for item in original.dependencies if item.environment_variable == "HC_POSTGRES_DSN"
    )
    _request(
        endpoint,
        token,
        "POST",
        f"/v1/secret/delete/{prefix}/hc-data-postgres",
        {"versions": [postgres_dependency.secret_version]},
    )
    with pytest.raises(ConfigurationBackupError) as deleted:
        provider.preflight(original)
    assert deleted.value.code == "RESTORE_SECRET_VERSION_UNAVAILABLE"

    _request(
        endpoint,
        token,
        "POST",
        f"/v1/secret/undelete/{prefix}/hc-data-postgres",
        {"versions": [postgres_dependency.secret_version]},
    )
    assert provider.preflight(original) == original_coordinate

    _request(
        endpoint,
        token,
        "POST",
        f"/v1/secret/destroy/{prefix}/hc-data-postgres",
        {"versions": [postgres_dependency.secret_version]},
    )
    with pytest.raises(ConfigurationBackupError) as destroyed:
        provider.preflight(original)
    assert destroyed.value.code == "RESTORE_SECRET_VERSION_UNAVAILABLE"

    _request(
        endpoint,
        token,
        "POST",
        f"/v1/secret/data/{prefix}/hc-data-postgres",
        {"data": values["hc-data-postgres"]},
    )
    current = provider.capture(
        references,
        source_environment_id="bak204-integration",
        helm_render_sha256=public.helm_render_sha256,
    )
    _request(
        endpoint,
        token,
        "POST",
        f"/v1/transit/keys/{quote(key_name, safe='')}/rotate",
    )
    provider.preflight(current)
    _request(
        endpoint,
        token,
        "POST",
        f"/v1/transit/keys/{quote(key_name, safe='')}/config",
        {"min_encryption_version": 2},
    )
    with pytest.raises(ConfigurationBackupError) as kms_version:
        provider.preflight(current)
    assert kms_version.value.code == "RESTORE_KMS_KEY_VERSION_MISMATCH"

    serialized = original.model_dump_json() + current.model_dump_json()
    for secret in (
        *values["hc-data-application"].values(),
        *values["hc-data-object-store"].values(),
        *values["hc-data-postgres"].values(),
    ):
        assert secret not in serialized
    assert token not in serialized
    assert endpoint not in serialized
