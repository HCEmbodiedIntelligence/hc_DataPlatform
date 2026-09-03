from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from typing import Any, cast

from pydantic import BaseModel, ConfigDict

from .errors import problem


class PageInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    has_next_page: bool
    has_previous_page: bool
    start_cursor: str | None = None
    end_cursor: str | None = None


class CursorCodec:
    """Encode opaque, canonical JSON cursors protected by HMAC-SHA256."""

    _SIGNATURE_BYTES = hashlib.sha256().digest_size
    _MAX_ENCODED_LENGTH = 16_384

    def __init__(self, secret: str | bytes) -> None:
        self._secret = secret.encode() if isinstance(secret, str) else secret
        if not self._secret:
            raise ValueError("cursor secret must not be empty")

    def encode(self, payload: dict[str, Any]) -> str:
        body = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        signature = hmac.new(self._secret, body, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(signature + body).decode("ascii").rstrip("=")

    def decode(self, cursor: str) -> dict[str, Any]:
        try:
            if not cursor or len(cursor) > self._MAX_ENCODED_LENGTH:
                raise ValueError("cursor length is invalid")
            padded = cursor + "=" * (-len(cursor) % 4)
            raw = base64.b64decode(padded, altchars=b"-_", validate=True)
            canonical = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
            if not hmac.compare_digest(canonical, cursor):
                raise ValueError("cursor encoding is not canonical")
            if len(raw) <= self._SIGNATURE_BYTES:
                raise ValueError("cursor body is missing")
            signature, body = raw[: self._SIGNATURE_BYTES], raw[self._SIGNATURE_BYTES :]
            expected = hmac.new(self._secret, body, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature mismatch")
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError("cursor payload must be an object")
            return cast(dict[str, Any], value)
        except (
            ValueError,
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            binascii.Error,
        ) as exc:
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid pagination cursor",
                detail="The cursor is malformed or was issued for a different environment.",
            ) from exc
