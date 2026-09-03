from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from hc_data_platform.backup.signing import (
    VaultSigningError,
    VaultTransitEd25519Signer,
    VaultTransitSignerConfig,
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
            raw = response.read(1024 * 1024)
    except HTTPError as exc:
        raise AssertionError(f"disposable Vault setup failed with HTTP {exc.code}") from exc
    if not raw:
        return {}
    value = json.loads(raw)
    assert isinstance(value, dict)
    return value


@pytest.mark.integration
def test_real_vault_transit_ed25519_exact_version_and_least_privilege() -> None:
    assert _required("HC_BACKUP_SIGNER_INTEGRATION_ALLOW_MUTATION") == "disposable-only"
    endpoint = _required("HC_BACKUP_SIGNER_INTEGRATION_ENDPOINT").rstrip("/")
    root_token = _required("HC_BACKUP_SIGNER_INTEGRATION_ROOT_TOKEN")
    key_name = _required("HC_BACKUP_SIGNER_INTEGRATION_KEY_NAME")
    provider_reference = "bak206-disposable-vault"
    encoded_key = quote(key_name, safe="")
    _request(
        endpoint,
        root_token,
        "POST",
        f"/v1/transit/keys/{encoded_key}",
        {
            "type": "ed25519",
            "derived": False,
            "exportable": False,
            "allow_plaintext_backup": False,
        },
    )
    policy_name = f"{key_name}-sign-only"
    policy = (
        f'path "transit/keys/{key_name}" {{ capabilities = ["read"] }}\n'
        f'path "transit/sign/{key_name}" {{ capabilities = ["update"] }}\n'
    )
    _request(
        endpoint,
        root_token,
        "PUT",
        f"/v1/sys/policies/acl/{quote(policy_name, safe='')}",
        {"policy": policy},
    )
    token_response = _request(
        endpoint,
        root_token,
        "POST",
        "/v1/auth/token/create",
        {
            "policies": [policy_name],
            "no_default_policy": True,
            "renewable": False,
            "ttl": "10m",
        },
    )
    auth = token_response.get("auth")
    assert isinstance(auth, Mapping)
    signer_token = auth.get("client_token")
    assert isinstance(signer_token, str) and signer_token
    key_reference = f"kms://vault/{provider_reference}/transit/{key_name}/versions/v1"
    signer = VaultTransitEd25519Signer(
        VaultTransitSignerConfig(
            endpoint_url=endpoint,
            token=signer_token,
            provider_reference=provider_reference,
            key_name=key_name,
            key_version=1,
            key_reference=key_reference,
        )
    )
    message = b"real Vault Ed25519 whole-backup manifest boundary"
    signature = signer.sign(message)
    signer.public_key.verify(signature, message)

    key_document = _request(
        endpoint,
        signer_token,
        "GET",
        f"/v1/transit/keys/{encoded_key}",
    )
    serialized = json.dumps(key_document, sort_keys=True)
    assert "private_key" not in serialized
    assert "private" not in serialized.lower()
    assert signer_token not in repr(signer.config)

    _request(endpoint, root_token, "POST", f"/v1/transit/keys/{encoded_key}/rotate")
    signer.public_key.verify(signer.sign(message), message)
    _request(
        endpoint,
        root_token,
        "POST",
        f"/v1/transit/keys/{encoded_key}/config",
        {"min_encryption_version": 2},
    )
    with pytest.raises(VaultSigningError) as retired:
        VaultTransitEd25519Signer(
            VaultTransitSignerConfig(
                endpoint_url=endpoint,
                token=signer_token,
                provider_reference=provider_reference,
                key_name=key_name,
                key_version=1,
                key_reference=key_reference,
            )
        )
    assert retired.value.code == "BACKUP_SIGNER_KEY_POLICY_INVALID"
