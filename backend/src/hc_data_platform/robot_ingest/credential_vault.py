"""Authenticated encryption for explicitly revealed robot credentials."""

import base64
import hashlib
import hmac
import json

from cryptography.fernet import Fernet, InvalidToken

from hc_data_platform.core.errors import problem


class CredentialVault:
    def __init__(self, signing_key: bytes) -> None:
        key = hmac.new(signing_key, b"hc-robot-credential-vault/v1", hashlib.sha256).digest()
        self._cipher = Fernet(base64.urlsafe_b64encode(key))

    def seal(self, organization_id: str, identity_id: str, credential_id: str, token: str) -> str:
        payload = json.dumps([organization_id, identity_id, credential_id, token]).encode()
        return self._cipher.encrypt(payload).decode("ascii")

    def reveal(
        self, organization_id: str, identity_id: str, credential_id: str, ciphertext: str
    ) -> str:
        try:
            payload = json.loads(self._cipher.decrypt(ciphertext.encode("ascii")))
            if (
                not isinstance(payload, list)
                or len(payload) != 4
                or payload[:3] != [organization_id, identity_id, credential_id]
                or not isinstance(payload[3], str)
            ):
                raise ValueError("credential binding mismatch")
            return payload[3]
        except (InvalidToken, ValueError, UnicodeError, TypeError) as exc:
            raise problem(
                status=503,
                code="ROBOT_CREDENTIAL_VAULT_UNAVAILABLE",
                title="凭据暂时无法读取",
                detail="请检查服务端凭据加密配置后重试；现有凭据未被更换。",
            ) from exc
