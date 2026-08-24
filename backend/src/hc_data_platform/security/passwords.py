"""Password hashing without logging or retaining plaintext credentials."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import unicodedata
from dataclasses import dataclass

from hc_data_platform.core.errors import problem

from .access_models import MAX_NEW_PASSWORD_CHARS, PasswordPolicyView

_BUILT_IN_BLOCKLIST = frozenset(
    {
        "123456789012345",
        "letmeinletmeinletmein",
        "passwordpassword",
        "qwertyqwertyqwerty",
        "adminadminadminadmin",
    }
)
_MAX_VERIFICATION_MEMORY_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ScryptParameters:
    """Technical password-storage parameters, independent from OPEN-02 abuse policy."""

    n: int = 2**15
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
            maxmem=1024 * 1024 * 1024,
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
            parsed_n = int(n)
            parsed_r = int(r)
            parsed_p = int(p)
            if (
                parsed_n < 2**10
                or parsed_n > 2**18
                or parsed_n & (parsed_n - 1)
                or parsed_r < 1
                or parsed_r > 32
                or parsed_p < 1
                or parsed_p > 8
                or 128 * parsed_n * parsed_r > _MAX_VERIFICATION_MEMORY_BYTES
            ):
                return False
            salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
            expected = base64.urlsafe_b64decode(encoded_derived.encode("ascii"))
            if not 8 <= len(salt) <= 64 or not 16 <= len(expected) <= 64:
                return False
            actual = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt,
                n=parsed_n,
                r=parsed_r,
                p=parsed_p,
                dklen=len(expected),
                maxmem=1024 * 1024 * 1024,
            )
        except (ValueError, TypeError, MemoryError):
            return False
        return hmac.compare_digest(actual, expected)

    def needs_rehash(self, encoded: str) -> bool:
        try:
            algorithm, n, r, p, encoded_salt, encoded_derived = encoded.split("$", 5)
            return (
                algorithm != "scrypt"
                or int(n) != self._parameters.n
                or int(r) != self._parameters.r
                or int(p) != self._parameters.p
                or len(base64.urlsafe_b64decode(encoded_salt.encode("ascii")))
                != self._parameters.salt_bytes
                or len(base64.urlsafe_b64decode(encoded_derived.encode("ascii")))
                != self._parameters.derived_key_bytes
            )
        except (ValueError, TypeError):
            return True

    def verify_and_rehash(self, password: str, encoded: str) -> tuple[bool, str | None]:
        if not self.verify(password, encoded):
            return False, None
        return True, self.hash(password) if self.needs_rehash(encoded) else None


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    min_length: int = 6
    max_length: int = MAX_NEW_PASSWORD_CHARS
    disallow_username: bool = True
    blocked_passwords: frozenset[str] = _BUILT_IN_BLOCKLIST

    def __post_init__(self) -> None:
        if self.min_length < 6 or self.max_length < self.min_length:
            raise ValueError("password policy length bounds are invalid")
        if self.max_length > MAX_NEW_PASSWORD_CHARS:
            raise ValueError("password policy exceeds the request resource ceiling")

    @property
    def view(self) -> PasswordPolicyView:
        return PasswordPolicyView(
            min_length=self.min_length,
            max_length=self.max_length,
            disallow_username=self.disallow_username,
            blocked_password_count=len(self.blocked_passwords),
        )

    def require(self, password: str, *, canonical_username: str) -> None:
        normalized_password = unicodedata.normalize("NFKC", password)
        folded_password = normalized_password.casefold()
        violations: list[str] = []
        if len(normalized_password) < self.min_length:
            violations.append("TOO_SHORT")
        if len(normalized_password) > self.max_length:
            violations.append("TOO_LONG")
        if folded_password in self.blocked_passwords:
            violations.append("BLOCKED_PASSWORD")
        if (
            self.disallow_username
            and len(canonical_username) >= 3
            and canonical_username in folded_password
        ):
            violations.append("CONTAINS_USERNAME")
        if violations:
            raise problem(
                status=422,
                code="PASSWORD_POLICY_VIOLATION",
                title="Password does not meet policy",
                detail="Choose a password that satisfies the current account security policy.",
                details={"violations": violations},
            )
