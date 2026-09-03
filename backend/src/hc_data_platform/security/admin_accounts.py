"""Platform-scoped account administration contracts and application service."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from enum import Enum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from hc_data_platform.core.errors import ProblemException, problem

from .access_models import (
    MAX_DISPLAY_NAME_CHARS,
    MAX_NEW_PASSWORD_CHARS,
    MAX_PASSWORD_CHARS,
    MAX_USERNAME_CHARS,
    AccountStatus,
)
from .auth import AuthContext
from .capabilities import (
    CAPABILITY_PLATFORM_ACCOUNT_MANAGE,
    CAPABILITY_PLATFORM_ACCOUNT_READ,
    CAPABILITY_PLATFORM_ACCOUNT_SECURITY_MANAGE,
)
from .passwords import PasswordHasher, PasswordPolicy
from .recovery import MAX_EMAIL_CHARS, normalize_email
from .versioning import ResourceVersion


class ManagedAccountState(str, Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    DELETED = "DELETED"


class PlatformAccountRole(str, Enum):
    USER = "USER"
    PLATFORM_ADMIN = "PLATFORM_ADMIN"


def recovery_email_hint(email: str | None) -> str | None:
    if email is None:
        return None
    local, domain = email.split("@", 1)
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}{'*' * max(3, len(local) - len(visible))}@{domain}"


def _validate_plain_text(value: str, *, field_name: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} must contain only Unicode scalar values") from exc
    if value != value.strip() or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ValueError(f"{field_name} must not contain surrounding whitespace or controls")
    return value


def _validate_password(value: SecretStr) -> SecretStr:
    raw = value.get_secret_value()
    if not raw:
        raise ValueError("password must not be empty")
    try:
        raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("password must contain only Unicode scalar values") from exc
    return value


class ManagedAccount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    principal_id: str = Field(min_length=1)
    username: str = Field(min_length=1, max_length=MAX_USERNAME_CHARS)
    display_name: str = Field(min_length=1, max_length=MAX_DISPLAY_NAME_CHARS)
    state: ManagedAccountState
    platform_role: PlatformAccountRole
    recovery_email_configured: bool
    recovery_email_hint: str | None = Field(default=None, max_length=MAX_EMAIL_CHARS)
    active_session_count: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    password_changed_at: datetime
    deleted_at: datetime | None = None
    revision: int = Field(ge=1)
    etag: str = Field(pattern=r'^"v[1-9][0-9]*"$')


class ManagedAccountPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ManagedAccount, ...]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class ManagedAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=MAX_USERNAME_CHARS)
    display_name: str | None = Field(default=None, min_length=1, max_length=MAX_DISPLAY_NAME_CHARS)
    password: SecretStr = Field(min_length=1, max_length=MAX_PASSWORD_CHARS)
    recovery_email: str | None = Field(default=None, min_length=3, max_length=MAX_EMAIL_CHARS)
    platform_role: PlatformAccountRole = PlatformAccountRole.USER

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return _validate_plain_text(value, field_name="username")

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        return None if value is None else _validate_plain_text(value, field_name="display_name")

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        return _validate_password(value)

    @field_validator("recovery_email")
    @classmethod
    def validate_recovery_email(cls, value: str | None) -> str | None:
        return None if value is None else normalize_email(value)


class ManagedAccountRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform_role: PlatformAccountRole


class ManagedAccountPasswordReset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: SecretStr = Field(min_length=1, max_length=MAX_NEW_PASSWORD_CHARS)
    confirm_password: SecretStr = Field(min_length=1, max_length=MAX_NEW_PASSWORD_CHARS)

    @field_validator("new_password", "confirm_password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        return _validate_password(value)

    @model_validator(mode="after")
    def passwords_match(self) -> ManagedAccountPasswordReset:
        if self.new_password.get_secret_value() != self.confirm_password.get_secret_value():
            raise ValueError("password confirmation does not match")
        return self


class ManagedAccountPasswordResetResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account: ManagedAccount
    sessions_revoked: int = Field(ge=0)


class AdminAccountRepository(Protocol):
    def list_managed_accounts(
        self,
        *,
        query: str | None,
        state: ManagedAccountState | None,
        role: PlatformAccountRole | None,
        page: int,
        page_size: int,
    ) -> ManagedAccountPage: ...

    def create_managed_account(
        self,
        *,
        canonical_username: str,
        display_username: str,
        display_name: str,
        password_hash: str,
        recovery_email: str | None,
        platform_role: PlatformAccountRole,
        actor_id: str,
        request_id: str,
    ) -> ManagedAccount: ...

    def set_managed_account_status(
        self,
        *,
        principal_id: str,
        target_status: AccountStatus,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> ManagedAccount: ...

    def set_managed_account_role(
        self,
        *,
        principal_id: str,
        platform_role: PlatformAccountRole,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> ManagedAccount: ...

    def reset_managed_account_password(
        self,
        *,
        principal_id: str,
        new_password_hash: str,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> tuple[ManagedAccount, int]: ...

    def delete_managed_account(
        self,
        *,
        principal_id: str,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> None: ...


class AdminAccountService:
    """Global account lifecycle operations guarded independently of project roles."""

    def __init__(
        self,
        repository: AdminAccountRepository,
        *,
        password_hasher: PasswordHasher | None = None,
        password_policy: PasswordPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._password_hasher = password_hasher or PasswordHasher()
        self._password_policy = password_policy or PasswordPolicy()

    @staticmethod
    def canonical_username(username: str) -> str:
        return unicodedata.normalize("NFKC", username).casefold()

    @staticmethod
    def _require_read(auth: AuthContext) -> None:
        if not auth.effective_capabilities().intersection(
            {CAPABILITY_PLATFORM_ACCOUNT_READ, CAPABILITY_PLATFORM_ACCOUNT_MANAGE}
        ):
            raise problem(
                status=403,
                code="PLATFORM_ACCOUNT_READ_REQUIRED",
                title="Platform account access required",
                detail="A global platform account capability is required.",
            )

    @staticmethod
    def _require_manage(auth: AuthContext) -> None:
        auth.require_capability(CAPABILITY_PLATFORM_ACCOUNT_MANAGE)

    def list_accounts(
        self,
        *,
        auth: AuthContext,
        query: str | None,
        state: ManagedAccountState | None,
        role: PlatformAccountRole | None,
        page: int,
        page_size: int,
    ) -> ManagedAccountPage:
        self._require_read(auth)
        normalized_query = query.strip() if query is not None else None
        return self._repository.list_managed_accounts(
            query=normalized_query or None,
            state=state,
            role=role,
            page=page,
            page_size=page_size,
        )

    def create_account(
        self,
        *,
        auth: AuthContext,
        command: ManagedAccountCreate,
        request_id: str,
    ) -> ManagedAccount:
        self._require_manage(auth)
        canonical = self.canonical_username(command.username)
        password = command.password.get_secret_value()
        self._password_policy.require(password, canonical_username=canonical)
        return self._repository.create_managed_account(
            canonical_username=canonical,
            display_username=command.username,
            display_name=command.display_name or command.username,
            password_hash=self._password_hasher.hash(password),
            recovery_email=command.recovery_email,
            platform_role=command.platform_role,
            actor_id=auth.subject_id,
            request_id=request_id,
        )

    def set_status(
        self,
        *,
        auth: AuthContext,
        principal_id: str,
        target_status: AccountStatus,
        if_match: str,
        request_id: str,
    ) -> ManagedAccount:
        self._require_manage(auth)
        if principal_id == auth.subject_id and target_status is AccountStatus.DISABLED:
            raise _self_management_denied("disable")
        return self._repository.set_managed_account_status(
            principal_id=principal_id,
            target_status=target_status,
            expected_revision=ResourceVersion.from_etag(if_match).value,
            actor_id=auth.subject_id,
            request_id=request_id,
        )

    def set_role(
        self,
        *,
        auth: AuthContext,
        principal_id: str,
        command: ManagedAccountRoleUpdate,
        if_match: str,
        request_id: str,
    ) -> ManagedAccount:
        self._require_manage(auth)
        if principal_id == auth.subject_id and command.platform_role is PlatformAccountRole.USER:
            raise _self_management_denied("demote")
        return self._repository.set_managed_account_role(
            principal_id=principal_id,
            platform_role=command.platform_role,
            expected_revision=ResourceVersion.from_etag(if_match).value,
            actor_id=auth.subject_id,
            request_id=request_id,
        )

    def reset_password(
        self,
        *,
        auth: AuthContext,
        principal_id: str,
        command: ManagedAccountPasswordReset,
        if_match: str,
        request_id: str,
    ) -> ManagedAccountPasswordResetResult:
        auth.require_capability(CAPABILITY_PLATFORM_ACCOUNT_SECURITY_MANAGE)
        if principal_id == auth.subject_id:
            raise _self_management_denied("reset the password for")
        canonical = self._repository.list_managed_accounts(
            query=principal_id,
            state=None,
            role=None,
            page=1,
            page_size=2,
        )
        exact = next((item for item in canonical.items if item.principal_id == principal_id), None)
        if exact is None:
            raise problem(
                status=404,
                code="ACCOUNT_NOT_FOUND",
                title="Account not found",
                detail="The requested platform account does not exist.",
            )
        password = command.new_password.get_secret_value()
        self._password_policy.require(
            password,
            canonical_username=self.canonical_username(exact.username),
        )
        account, revoked = self._repository.reset_managed_account_password(
            principal_id=principal_id,
            new_password_hash=self._password_hasher.hash(password),
            expected_revision=ResourceVersion.from_etag(if_match).value,
            actor_id=auth.subject_id,
            request_id=request_id,
        )
        return ManagedAccountPasswordResetResult(account=account, sessions_revoked=revoked)

    def delete_account(
        self,
        *,
        auth: AuthContext,
        principal_id: str,
        if_match: str,
        request_id: str,
    ) -> None:
        self._require_manage(auth)
        if principal_id == auth.subject_id:
            raise _self_management_denied("delete")
        self._repository.delete_managed_account(
            principal_id=principal_id,
            expected_revision=ResourceVersion.from_etag(if_match).value,
            actor_id=auth.subject_id,
            request_id=request_id,
        )


def _self_management_denied(action: str) -> ProblemException:
    return problem(
        status=409,
        code="PLATFORM_ADMIN_SELF_MANAGEMENT_DENIED",
        title="Self-management denied",
        detail=f"A platform administrator cannot {action} the current account here.",
    )
