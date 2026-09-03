from __future__ import annotations

import base64
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from hc_data_platform.backup.signing import (
    FunctionalFileEd25519Signer,
    VaultSigningError,
    VaultTransitEd25519Signer,
    VaultTransitSignerConfig,
)


def test_functional_file_signer_is_private_and_bound_to_exact_run(tmp_path: Path) -> None:
    path = tmp_path / "hc-migration-unit001-signing.key"
    path.write_bytes(bytes(range(32)))
    path.chmod(0o600)
    signer = FunctionalFileEd25519Signer(
        path,
        key_reference="kms://compose/unit001/signing/versions/v1",
        run_id="unit001",
    )

    signature = signer.sign(b"functional compose backup")

    signer.public_key.verify(signature, b"functional compose backup")
    assert signer.key_reference.endswith("/versions/v1")


def test_functional_file_signer_rejects_cross_run_or_public_key_file(tmp_path: Path) -> None:
    path = tmp_path / "hc-migration-unit001-signing.key"
    path.write_bytes(bytes(range(32)))
    path.chmod(0o644)

    with pytest.raises(VaultSigningError) as public:
        FunctionalFileEd25519Signer(
            path,
            key_reference="kms://compose/unit001/signing/versions/v1",
            run_id="unit001",
        )
    assert public.value.code == "BACKUP_FUNCTIONAL_SIGNER_UNSAFE"

    path.chmod(0o600)
    with pytest.raises(VaultSigningError) as cross_run:
        FunctionalFileEd25519Signer(
            path,
            key_reference="kms://compose/unit002/signing/versions/v1",
            run_id="unit002",
        )
    assert cross_run.value.code == "BACKUP_FUNCTIONAL_SIGNER_UNSAFE"


class _VaultFixture:
    def __init__(self) -> None:
        self.private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
        self.returned_version = 4
        self.tamper_signature = False
        self.calls: list[tuple[str, str, Mapping[str, Any] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        self.calls.append((method, path, body))
        if method == "GET":
            public_bytes = self.private_key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
            return {
                "data": {
                    "type": "ed25519",
                    "supports_signing": True,
                    "exportable": False,
                    "allow_plaintext_backup": False,
                    "latest_version": 4,
                    "min_available_version": 0,
                    "min_encryption_version": 2,
                    "keys": {"4": {"public_key": base64.b64encode(public_bytes).decode("ascii")}},
                }
            }
        assert body is not None
        message = base64.b64decode(str(body["input"]), validate=True)
        signature = self.private_key.sign(message)
        if self.tamper_signature:
            signature = bytes([signature[0] ^ 1, *signature[1:]])
        return {
            "data": {
                "signature": (
                    f"vault:v{self.returned_version}:{base64.b64encode(signature).decode('ascii')}"
                )
            }
        }


def _config() -> VaultTransitSignerConfig:
    return VaultTransitSignerConfig(
        endpoint_url="https://vault.example.invalid",
        token="vault-token-that-must-never-be-exported",
        provider_reference="production-vault",
        transit_mount="transit",
        key_name="backup-manifest-ed25519",
        key_version=4,
        key_reference=("kms://vault/production-vault/transit/backup-manifest-ed25519/versions/v4"),
    )


def test_vault_transit_signer_uses_exact_ed25519_version_and_verifies_response() -> None:
    fixture = _VaultFixture()
    signer = VaultTransitEd25519Signer(_config(), requester=fixture.request)

    signature = signer.sign(b"canonical whole backup manifest")

    signer.public_key.verify(signature, b"canonical whole backup manifest")
    assert signer.key_reference.endswith("/versions/v4")
    assert fixture.calls[0] == (
        "GET",
        "/v1/transit/keys/backup-manifest-ed25519",
        None,
    )
    assert fixture.calls[1][0:2] == (
        "POST",
        "/v1/transit/sign/backup-manifest-ed25519",
    )
    assert fixture.calls[1][2] is not None
    assert fixture.calls[1][2]["key_version"] == 4
    assert fixture.calls[1][2]["prehashed"] is False
    assert "vault-token-that-must-never-be-exported" not in repr(signer.config)


@pytest.mark.parametrize(
    ("returned_version", "tamper", "expected_code"),
    [
        (3, False, "BACKUP_SIGNER_KEY_VERSION_MISMATCH"),
        (4, True, "BACKUP_SIGNER_RESPONSE_INVALID"),
    ],
)
def test_vault_transit_signer_rejects_wrong_version_or_signature(
    returned_version: int,
    tamper: bool,
    expected_code: str,
) -> None:
    fixture = _VaultFixture()
    fixture.returned_version = returned_version
    fixture.tamper_signature = tamper
    signer = VaultTransitEd25519Signer(_config(), requester=fixture.request)

    with pytest.raises(VaultSigningError) as captured:
        signer.sign(b"canonical whole backup manifest")
    assert captured.value.code == expected_code


def test_vault_signer_config_rejects_unversioned_reference_and_plain_http() -> None:
    document = _config().model_dump(mode="python")
    document["key_reference"] = "kms://vault/production-vault/transit/key/versions/v4"
    with pytest.raises(ValidationError):
        VaultTransitSignerConfig.model_validate(document)

    document = _config().model_dump(mode="python")
    document["endpoint_url"] = "http://vault.example.invalid"
    with pytest.raises(ValidationError):
        VaultTransitSignerConfig.model_validate(document)
