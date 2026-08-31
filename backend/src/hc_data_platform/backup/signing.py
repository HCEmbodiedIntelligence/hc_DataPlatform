"""External Ed25519 signing through an exact-version Vault Transit key."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from hc_data_platform.backup.contracts import KmsKeyReference, canonical_json_bytes

_MAX_RESPONSE_BYTES = 1024 * 1024
_VAULT_SIGNATURE = re.compile(
    r"^vault:v(?P<version>[1-9][0-9]*):(?P<signature>[A-Za-z0-9+/]+={0,2})$"
)


class VaultSigningError(RuntimeError):
    """Stable, redacted Vault Transit signing failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class VaultTransitSignerConfig(BaseModel):
    """Public signer identity plus the process-local Vault access token."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    endpoint_url: str = Field(min_length=1, max_length=2048)
    token: SecretStr = Field(repr=False)
    provider_reference: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    transit_mount: str = Field(default="transit", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    key_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    key_version: int = Field(gt=0)
    key_reference: KmsKeyReference
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
    def bind_exact_key_reference(self) -> VaultTransitSignerConfig:
        expected = (
            f"kms://vault/{self.provider_reference}/{self.transit_mount}/"
            f"{self.key_name}/versions/v{self.key_version}"
        )
        if self.key_reference != expected:
            raise ValueError("Vault Transit signing key reference does not match its exact version")
        return self


VaultSignerRequester = Callable[[str, str, Mapping[str, Any] | None], Mapping[str, Any]]


class FunctionalFileEd25519Signer:
    """Run-ID-bound ephemeral signer for the disposable Compose exercise only."""

    def __init__(self, path: Path, *, key_reference: str, run_id: str) -> None:
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9-]{5,63}", run_id)
            or f"hc-migration-{run_id}" not in path.name
        ):
            raise VaultSigningError(
                "BACKUP_FUNCTIONAL_SIGNER_UNSAFE",
                "the functional signer is not bound to its exact migration run",
            )
        try:
            resolved = path.resolve(strict=True)
            info = resolved.stat()
            if (
                path.is_symlink()
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_size != 32
            ):
                raise ValueError("key file policy differs")
            raw = resolved.read_bytes()
            self._private_key = Ed25519PrivateKey.from_private_bytes(raw)
        except Exception as exc:
            raise VaultSigningError(
                "BACKUP_FUNCTIONAL_SIGNER_UNSAFE",
                "the functional signing key is unavailable or unsafe",
            ) from exc
        if (
            re.fullmatch(
                r"kms://[a-zA-Z0-9._:-]+(?:/[a-zA-Z0-9._:-]+)*/versions/v[1-9][0-9]*",
                key_reference,
            )
            is None
        ):
            raise VaultSigningError(
                "BACKUP_FUNCTIONAL_SIGNER_UNSAFE",
                "the functional signing key reference is invalid",
            )
        self.key_reference: KmsKeyReference = key_reference

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._private_key.public_key()

    def sign(self, message: bytes) -> bytes:
        return self._private_key.sign(message)


class VaultTransitEd25519Signer:
    """Manifest signing port whose private key remains inside Vault Transit."""

    def __init__(
        self,
        config: VaultTransitSignerConfig,
        *,
        requester: VaultSignerRequester | None = None,
    ) -> None:
        self.config = config
        self._requester = requester or self._request
        self._public_key = self._load_exact_public_key()

    @property
    def key_reference(self) -> str:
        return self.config.key_reference

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._public_key

    def sign(self, message: bytes) -> bytes:
        try:
            response = self._requester(
                "POST",
                self._api_path(self.config.transit_mount, "sign", self.config.key_name),
                {
                    "input": base64.b64encode(message).decode("ascii"),
                    "key_version": self.config.key_version,
                    "prehashed": False,
                },
            )
        except VaultSigningError:
            raise
        except Exception as exc:
            raise VaultSigningError(
                "BACKUP_SIGNER_UNAVAILABLE", "the external manifest signer is unavailable"
            ) from exc
        data = _mapping(response.get("data"))
        encoded = data.get("signature")
        if not isinstance(encoded, str):
            raise VaultSigningError(
                "BACKUP_SIGNER_RESPONSE_INVALID", "the external signer response is malformed"
            )
        matched = _VAULT_SIGNATURE.fullmatch(encoded)
        if matched is None or int(matched.group("version")) != self.config.key_version:
            raise VaultSigningError(
                "BACKUP_SIGNER_KEY_VERSION_MISMATCH",
                "the external signer used a different key version",
            )
        signature = _decode_base64(
            matched.group("signature"),
            expected_length=64,
            label="signature",
        )
        try:
            self._public_key.verify(signature, message)
        except InvalidSignature as exc:
            raise VaultSigningError(
                "BACKUP_SIGNER_RESPONSE_INVALID",
                "the external signature does not match its pinned public key",
            ) from exc
        return signature

    def _load_exact_public_key(self) -> Ed25519PublicKey:
        try:
            response = self._requester(
                "GET",
                self._api_path(self.config.transit_mount, "keys", self.config.key_name),
                None,
            )
        except VaultSigningError:
            raise
        except Exception as exc:
            raise VaultSigningError(
                "BACKUP_SIGNER_UNAVAILABLE", "the external signing key is unavailable"
            ) from exc
        data = _mapping(response.get("data"))
        keys = _mapping(data.get("keys"))
        latest = data.get("latest_version")
        minimum_available = data.get("min_available_version")
        minimum_encryption = data.get("min_encryption_version")
        if (
            data.get("type") != "ed25519"
            or data.get("supports_signing") is not True
            or data.get("exportable") is not False
            or data.get("allow_plaintext_backup") is not False
            or not isinstance(latest, int)
            or isinstance(latest, bool)
            or not isinstance(minimum_available, int)
            or isinstance(minimum_available, bool)
            or not isinstance(minimum_encryption, int)
            or isinstance(minimum_encryption, bool)
            or latest < self.config.key_version
            or self.config.key_version < max(1, minimum_available, minimum_encryption)
        ):
            raise VaultSigningError(
                "BACKUP_SIGNER_KEY_POLICY_INVALID",
                "the external key is not an available Ed25519 signing key",
            )
        version = _mapping(keys.get(str(self.config.key_version)))
        encoded = version.get("public_key")
        if not isinstance(encoded, str):
            raise VaultSigningError(
                "BACKUP_SIGNER_KEY_VERSION_MISSING",
                "the exact external signing key version is unavailable",
            )
        public_bytes = _decode_base64(encoded, expected_length=32, label="public key")
        try:
            return Ed25519PublicKey.from_public_bytes(public_bytes)
        except ValueError as exc:
            raise VaultSigningError(
                "BACKUP_SIGNER_RESPONSE_INVALID", "the external public key is malformed"
            ) from exc

    @staticmethod
    def _api_path(*parts: str) -> str:
        return "/v1/" + "/".join(quote(part, safe="") for part in parts)

    def _request(
        self,
        method: str,
        api_path: str,
        body: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        payload = None if body is None else canonical_json_bytes(body)
        headers = {
            "Accept": "application/json",
            "X-Vault-Token": self.config.token.get_secret_value(),
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.config.namespace is not None:
            headers["X-Vault-Namespace"] = self.config.namespace
        request = Request(
            f"{self.config.endpoint_url}{api_path}",
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            with build_opener(_RejectRedirects).open(
                request, timeout=self.config.timeout_seconds
            ) as response:  # noqa: S310
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            code = (
                "BACKUP_SIGNER_KEY_VERSION_MISSING"
                if method == "GET" and exc.code in {400, 404}
                else "BACKUP_SIGNER_REJECTED"
            )
            raise VaultSigningError(code, "the external signer rejected the request") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise VaultSigningError(
                "BACKUP_SIGNER_UNAVAILABLE", "the external manifest signer is unavailable"
            ) from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise VaultSigningError(
                "BACKUP_SIGNER_RESPONSE_INVALID", "the external signer response is too large"
            )
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VaultSigningError(
                "BACKUP_SIGNER_RESPONSE_INVALID", "the external signer returned malformed JSON"
            ) from exc
        return _mapping(document)


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


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise VaultSigningError(
            "BACKUP_SIGNER_RESPONSE_INVALID", "the external signer response is malformed"
        )
    return value


def _decode_base64(
    value: str,
    *,
    expected_length: int,
    label: Literal["public key", "signature"],
) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise VaultSigningError(
            "BACKUP_SIGNER_RESPONSE_INVALID", f"the external {label} is malformed"
        ) from exc
    if len(decoded) != expected_length:
        raise VaultSigningError(
            "BACKUP_SIGNER_RESPONSE_INVALID", f"the external {label} has the wrong length"
        )
    return decoded
