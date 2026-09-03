"""Account recovery flows with durable, single-use token persistence.

Forgot-password confirmation deliberately never asks for the previous password.  The previous
password remains a requirement only for the authenticated password-change flow.
"""

from __future__ import annotations

import hashlib
import secrets
import smtplib
import ssl
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Literal, Protocol
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException, problem

from .abuse import AbuseProtection, PublicAuthAttempt, UnconfiguredAbuseProtection
from .auth import AuthContext
from .challenge import DisabledPublicAuthChallengeVerifier, PublicAuthChallengeVerifier
from .passwords import PasswordHasher, PasswordPolicy

MAX_EMAIL_CHARS = 254
MAX_RECOVERY_IDENTIFIER_CHARS = 254
MAX_RECOVERY_TOKEN_CHARS = 512


def normalize_email(value: str) -> str:
    """Return the deliberately narrow canonical email form accepted by account recovery."""

    normalized = value.strip().casefold()
    if len(normalized) > MAX_EMAIL_CHARS or normalized.count("@") != 1:
        raise ValueError("email address is invalid")
    local, domain = normalized.split("@", 1)
    if (
        not local
        or not domain
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or domain.startswith(".")
        or domain.endswith(".")
        or ".." in domain
        or any(character.isspace() or ord(character) < 32 for character in normalized)
    ):
        raise ValueError("email address is invalid")
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("email address is invalid") from exc
    if len(ascii_domain) > 253 or any(
        not label or len(label) > 63 for label in ascii_domain.split(".")
    ):
        raise ValueError("email address is invalid")
    return f"{local}@{ascii_domain}"


def _validate_token(value: SecretStr) -> SecretStr:
    raw = value.get_secret_value()
    if raw != raw.strip() or any(ord(character) < 33 or ord(character) == 127 for character in raw):
        raise ValueError("token must not contain whitespace or control characters")
    return value


class RecoveryEmailVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recovery_email: str = Field(min_length=3, max_length=MAX_EMAIL_CHARS)

    @field_validator("recovery_email")
    @classmethod
    def validate_recovery_email(cls, value: str) -> str:
        return normalize_email(value)


class RecoveryEmailConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: SecretStr = Field(min_length=16, max_length=MAX_RECOVERY_TOKEN_CHARS)

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: SecretStr) -> SecretStr:
        return _validate_token(value)


class RecoveryEmailConfigured(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recovery_email_hint: str = Field(min_length=3, max_length=MAX_EMAIL_CHARS)


class PasswordRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str = Field(min_length=1, max_length=MAX_RECOVERY_IDENTIFIER_CHARS)

    @field_validator("identifier")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("identifier is invalid")
        return normalized


class PasswordRecoveryAccepted(BaseModel):
    """The response is intentionally identical for known and unknown identities."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: Literal[True] = True


class PasswordRecoveryConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: SecretStr = Field(min_length=16, max_length=MAX_RECOVERY_TOKEN_CHARS)
    new_password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: SecretStr) -> SecretStr:
        return _validate_token(value)


class PasswordRecoveryCompleted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sessions_revoked: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class PasswordRecoveryTarget:
    principal_id: str
    display_username: str
    recovery_email: str


@dataclass(frozen=True, slots=True)
class PasswordRecoveryCredential:
    principal_id: str
    canonical_username: str
    password_hash: str


class AccountRecoveryRepository(Protocol):
    def issue_recovery_email_verification(
        self,
        *,
        principal_id: str,
        recovery_email: str,
        token_hash: str,
        expires_at: datetime,
        request_id: str,
    ) -> None: ...

    def confirm_recovery_email(
        self,
        *,
        principal_id: str,
        token_hash: str,
        now: datetime,
        request_id: str,
    ) -> str | None: ...

    def issue_password_recovery(
        self,
        *,
        canonical_identifier: str,
        token_hash: str,
        expires_at: datetime,
        request_id: str,
    ) -> PasswordRecoveryTarget | None: ...

    def password_recovery_credential(
        self,
        *,
        token_hash: str,
        now: datetime,
    ) -> PasswordRecoveryCredential | None: ...

    def complete_password_recovery(
        self,
        *,
        token_hash: str,
        expected_password_hash: str,
        new_password_hash: str,
        now: datetime,
        request_id: str,
    ) -> int | None: ...


class AccountRecoveryDelivery(Protocol):
    @property
    def enabled(self) -> bool: ...

    def send_recovery_email_verification(self, *, email: str, token: str) -> None: ...

    def send_password_recovery(self, *, email: str, token: str) -> None: ...


class RecoveryDeliveryError(RuntimeError):
    """Stable boundary error that never includes an address, token, or SMTP response."""


class DisabledAccountRecoveryDelivery:
    @property
    def enabled(self) -> bool:
        return False

    def send_recovery_email_verification(self, *, email: str, token: str) -> None:
        del email, token
        raise RecoveryDeliveryError("account recovery delivery is disabled")

    def send_password_recovery(self, *, email: str, token: str) -> None:
        del email, token
        raise RecoveryDeliveryError("account recovery delivery is disabled")


class SmtpAccountRecoveryDelivery:
    """Small SMTP adapter; public responses never expose transport failures or recipients."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        from_address: str,
        public_base_url: str,
        username: str | None = None,
        password: str | None = None,
        starttls: bool = True,
        timeout_seconds: float = 10,
    ) -> None:
        if not host or not from_address or not public_base_url:
            raise ValueError("SMTP account recovery settings are incomplete")
        self._host = host
        self._port = port
        self._from_address = normalize_email(from_address)
        self._public_base_url = public_base_url.rstrip("/")
        self._username = username
        self._password = password
        self._starttls = starttls
        self._timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        return True

    def send_recovery_email_verification(self, *, email: str, token: str) -> None:
        self._send(
            email=email,
            subject="验证账号恢复邮箱",
            body=(
                "你正在为 HC Data Platform 验证账号恢复邮箱。\n\n"
                f"验证码：{token}\n\n"
                "如果不是你本人操作，请忽略本邮件。"
            ),
        )

    def send_password_recovery(self, *, email: str, token: str) -> None:
        query = urlencode({"token": token})
        self._send(
            email=email,
            subject="重置账号密码",
            body=(
                "你正在重置 HC Data Platform 账号密码。\n\n"
                f"重置链接：{self._public_base_url}/auth/reset-password#{query}\n\n"
                "此链接只能使用一次。如果不是你本人操作，请忽略本邮件。"
            ),
        )

    def _send(self, *, email: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self._from_address
        message["To"] = normalize_email(email)
        message["Subject"] = subject
        message.set_content(body)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout_seconds) as client:
                client.ehlo()
                if self._starttls:
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                if self._username is not None:
                    client.login(self._username, self._password or "")
                client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise RecoveryDeliveryError("account recovery email delivery failed") from exc


class AccountRecoveryService:
    EMAIL_VERIFICATION_PREFIX = "hcev_"
    PASSWORD_RECOVERY_PREFIX = "hcpr_"

    def __init__(
        self,
        repository: AccountRecoveryRepository,
        *,
        delivery: AccountRecoveryDelivery | None = None,
        password_hasher: PasswordHasher | None = None,
        password_policy: PasswordPolicy | None = None,
        abuse_protection: AbuseProtection | None = None,
        challenge_verifier: PublicAuthChallengeVerifier | None = None,
        email_verification_ttl_seconds: int = 600,
        password_recovery_ttl_seconds: int = 900,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if email_verification_ttl_seconds < 60 or password_recovery_ttl_seconds < 60:
            raise ValueError("account recovery token TTL must be at least 60 seconds")
        self._repository = repository
        self._delivery = delivery or DisabledAccountRecoveryDelivery()
        self._password_hasher = password_hasher or PasswordHasher()
        self._password_policy = password_policy or PasswordPolicy()
        self._abuse_protection = abuse_protection or UnconfiguredAbuseProtection()
        self._challenge_verifier = challenge_verifier or DisabledPublicAuthChallengeVerifier()
        self._email_verification_ttl = timedelta(seconds=email_verification_ttl_seconds)
        self._password_recovery_ttl = timedelta(seconds=password_recovery_ttl_seconds)
        self._clock = clock

    def request_recovery_email_verification(
        self,
        command: RecoveryEmailVerificationRequest,
        *,
        auth: AuthContext,
        request_id: str,
    ) -> PasswordRecoveryAccepted:
        if not self._delivery.enabled:
            raise _delivery_unavailable()
        now = self._now()
        raw_token = self.EMAIL_VERIFICATION_PREFIX + secrets.token_urlsafe(24)
        self._repository.issue_recovery_email_verification(
            principal_id=auth.subject_id,
            recovery_email=command.recovery_email,
            token_hash=self.token_hash(raw_token),
            expires_at=now + self._email_verification_ttl,
            request_id=request_id,
        )
        try:
            self._delivery.send_recovery_email_verification(
                email=command.recovery_email,
                token=raw_token,
            )
        except RecoveryDeliveryError:
            raise _delivery_unavailable() from None
        return PasswordRecoveryAccepted()

    def confirm_recovery_email(
        self,
        command: RecoveryEmailConfirmation,
        *,
        auth: AuthContext,
        request_id: str,
    ) -> RecoveryEmailConfigured:
        email = self._repository.confirm_recovery_email(
            principal_id=auth.subject_id,
            token_hash=self.token_hash(command.token.get_secret_value()),
            now=self._now(),
            request_id=request_id,
        )
        if email is None:
            raise _token_invalid()
        return RecoveryEmailConfigured(recovery_email_hint=mask_email(email))

    def request_password_recovery(
        self,
        command: PasswordRecoveryRequest,
        *,
        source_network: str | None,
        challenge_response: str | None,
        request_id: str,
    ) -> PasswordRecoveryAccepted:
        identifier = canonical_recovery_identifier(command.identifier)
        attempt = PublicAuthAttempt(
            operation="PASSWORD_RECOVERY",
            source_network=source_network or "unattributed",
            subject_hint=identifier,
        )
        challenge_required = self._abuse_protection.check(attempt, request_id=request_id)
        if self._challenge_verifier.configuration is not None:
            challenge_required = True
        self._verify_challenge(
            attempt,
            required=challenge_required,
            response_token=challenge_response,
            request_id=request_id,
        )
        now = self._now()
        raw_token = self.PASSWORD_RECOVERY_PREFIX + secrets.token_urlsafe(32)
        target = self._repository.issue_password_recovery(
            canonical_identifier=identifier,
            token_hash=self.token_hash(raw_token),
            expires_at=now + self._password_recovery_ttl,
            request_id=request_id,
        )
        if target is not None and self._delivery.enabled:
            # Public responses stay indistinguishable for unknown users and delivery errors.
            with suppress(RecoveryDeliveryError):
                self._delivery.send_password_recovery(
                    email=target.recovery_email,
                    token=raw_token,
                )
        return PasswordRecoveryAccepted()

    def confirm_password_recovery(
        self,
        command: PasswordRecoveryConfirmation,
        *,
        request_id: str,
    ) -> PasswordRecoveryCompleted:
        token_hash = self.token_hash(command.token.get_secret_value())
        now = self._now()
        credential = self._repository.password_recovery_credential(
            token_hash=token_hash,
            now=now,
        )
        if credential is None:
            raise _token_invalid()
        new_password = command.new_password.get_secret_value()
        self._password_policy.require(
            new_password,
            canonical_username=credential.canonical_username,
        )
        if self._password_hasher.verify(new_password, credential.password_hash):
            raise problem(
                status=422,
                code="PASSWORD_REUSE_FORBIDDEN",
                title="Choose a new password",
                detail="The new password must differ from the current password.",
            )
        sessions_revoked = self._repository.complete_password_recovery(
            token_hash=token_hash,
            expected_password_hash=credential.password_hash,
            new_password_hash=self._password_hasher.hash(new_password),
            now=now,
            request_id=request_id,
        )
        if sessions_revoked is None:
            raise _token_invalid()
        return PasswordRecoveryCompleted(sessions_revoked=sessions_revoked)

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _verify_challenge(
        self,
        attempt: PublicAuthAttempt,
        *,
        required: bool,
        response_token: str | None,
        request_id: str,
    ) -> None:
        if not required:
            return
        try:
            self._challenge_verifier.verify(
                response_token=response_token,
                operation=attempt.operation,
            )
        except ProblemException as exc:
            self._abuse_protection.record_challenge_denied(
                attempt,
                reason=exc.problem.code,
                request_id=request_id,
            )
            raise

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("account recovery clock must return a timezone-aware timestamp")
        return value.astimezone(timezone.utc)


def canonical_recovery_identifier(identifier: str) -> str:
    normalized = identifier.strip()
    if "@" in normalized:
        return normalize_email(normalized)
    return normalized.casefold()


def mask_email(email: str) -> str:
    local, domain = normalize_email(email).split("@", 1)
    visible = local[:1]
    return f"{visible}{'*' * max(2, len(local) - 1)}@{domain}"


def recovery_delivery_from_settings(settings: Settings) -> AccountRecoveryDelivery:
    if not settings.auth_recovery_enabled:
        return DisabledAccountRecoveryDelivery()
    password = settings.auth_smtp_password
    return SmtpAccountRecoveryDelivery(
        host=settings.auth_smtp_host or "",
        port=settings.auth_smtp_port,
        from_address=settings.auth_recovery_email_from or "",
        public_base_url=settings.auth_recovery_public_base_url or "",
        username=settings.auth_smtp_username,
        password=None if password is None else password.get_secret_value(),
        starttls=settings.auth_smtp_starttls,
        timeout_seconds=settings.auth_smtp_timeout_seconds,
    )


def _token_invalid() -> ProblemException:
    return problem(
        status=422,
        code="ACCOUNT_RECOVERY_TOKEN_INVALID",
        title="Account recovery token is invalid",
        detail="Request a new account recovery message before trying again.",
    )


def _delivery_unavailable() -> ProblemException:
    return problem(
        status=503,
        code="ACCOUNT_RECOVERY_DELIVERY_UNAVAILABLE",
        title="Account recovery delivery is unavailable",
        detail="Account recovery email delivery is not currently configured.",
        retryable=True,
    )
