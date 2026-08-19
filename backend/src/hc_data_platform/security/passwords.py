"""Password hashing without logging or retaining plaintext credentials."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScryptParameters:
    """Technical password-storage parameters, independent from OPEN-02 abuse policy."""

    n: int = 2**14
    r: int = 8
    p: int = 1
    salt_bytes: int = 16
    derived_key_bytes: int = 32


class PasswordHasher:
    def __init__(self, parameters: ScryptParameters | None = None) -> None:
        self._parameters = parameters or ScryptParameters()

    def hash(self, password: str) -> str:
        if not password:
            raise ValueError("password must not be empty")
        salt = secrets.token_bytes(self._parameters.salt_bytes)
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=self._parameters.n,
            r=self._parameters.r,
            p=self._parameters.p,
            dklen=self._parameters.derived_key_bytes,
        )
        return "$".join(
            (
                "scrypt",
                str(self._parameters.n),
                str(self._parameters.r),
                str(self._parameters.p),
                base64.urlsafe_b64encode(salt).decode("ascii"),
                base64.urlsafe_b64encode(derived).decode("ascii"),
            )
        )

    def verify(self, password: str, encoded: str) -> bool:
        try:
            algorithm, n, r, p, encoded_salt, encoded_derived = encoded.split("$", 5)
            if algorithm != "scrypt":
                return False
            salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
            expected = base64.urlsafe_b64decode(encoded_derived.encode("ascii"))
            actual = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt,
                n=int(n),
                r=int(r),
                p=int(p),
                dklen=len(expected),
            )
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(actual, expected)
