"""Contracts for direct registration and project-scoped access requests.

The public models deliberately contain no account-approval state.  Registration creates an
ACTIVE, empty principal.  Project membership and capability grants have independent review
lifecycles and are the only objects in this module that can be pending approval.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

# Technical resource ceilings for endpoints exposed to the public internet. Password strength
# is enforced by the centrally configured PasswordPolicy after request-shape validation.
MAX_USERNAME_CHARS = 128
MAX_PASSWORD_CHARS = 1024
MAX_NEW_PASSWORD_CHARS = 128
MAX_DISPLAY_NAME_CHARS = 128
MAX_ACCESS_REASON_CHARS = 2_000
MAX_CAPABILITY_KEYS = 64
MAX_CAPABILITY_KEY_CHARS = 128
MAX_ACCESS_TOKEN_CHARS = 4_096
ACCESS_TOKEN_PATTERN = r"^[!-~]+$"
NonEmptyScopeValue = Annotated[str, Field(min_length=1)]


def _validate_unicode_scalar_text(value: str, *, field_name: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} must contain only Unicode scalar values") from exc
    return value


def _validate_username_text(value: str) -> str:
    _validate_unicode_scalar_text(value, field_name="username")
    if value != value.strip():
        raise ValueError("username must not contain leading or trailing whitespace")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError("username must not contain control characters")
    return value


def _validate_password_text(value: SecretStr, *, field_name: str) -> SecretStr:
    raw = value.get_secret_value()
    if not raw:
        raise ValueError(f"{field_name} must not be empty")
    _validate_unicode_scalar_text(raw, field_name=field_name)
    return value


class AccountStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class AccessRequestStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    REVOKED = "REVOKED"


class AccessRequestKind(str, Enum):
    ORGANIZATION = "ORGANIZATION"
    MEMBERSHIP = "MEMBERSHIP"
    CAPABILITY = "CAPABILITY"


class AccountNotificationKind(str, Enum):
    MEMBERSHIP_APPROVED = "MEMBERSHIP_APPROVED"
    MEMBERSHIP_REJECTED = "MEMBERSHIP_REJECTED"
    MEMBERSHIP_REVOKED = "MEMBERSHIP_REVOKED"
    CAPABILITY_APPROVED = "CAPABILITY_APPROVED"
    CAPABILITY_REJECTED = "CAPABILITY_REJECTED"
    CAPABILITY_REVOKED = "CAPABILITY_REVOKED"
    COLLECTION_TASK_CLOSED = "COLLECTION_TASK_CLOSED"
    COLLECTION_TASK_CANCELLED = "COLLECTION_TASK_CANCELLED"
    COLLECTION_TASK_REOPENED = "COLLECTION_TASK_REOPENED"
    DATASET_VERSION_PUBLISHED = "DATASET_VERSION_PUBLISHED"


class AccountNotificationResourceType(str, Enum):
    """The stable object identity behind a compact account-inbox fact."""

    ACCESS_REQUEST = "ACCESS_REQUEST"
    COLLECTION_TASK = "COLLECTION_TASK"
    DATASET_VERSION = "DATASET_VERSION"


class AccountNotificationState(str, Enum):
    UNREAD = "UNREAD"
    READ = "READ"


class AccountPrincipal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    principal_id: str = Field(min_length=1)
    username: str = Field(min_length=1, max_length=MAX_USERNAME_CHARS)
    display_name: str = Field(min_length=1, max_length=MAX_DISPLAY_NAME_CHARS)
    status: AccountStatus
    created_at: datetime


class PasswordPolicyView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_length: int = Field(ge=1, le=MAX_NEW_PASSWORD_CHARS)
    max_length: int = Field(ge=1, le=MAX_NEW_PASSWORD_CHARS)
    disallow_username: bool
    blocked_password_count: int = Field(ge=0)


class PublicAuthChallengeProvider(str, Enum):
    TURNSTILE = "TURNSTILE"


class PublicAuthChallengeConfiguration(BaseModel):
    """Browser-safe provider metadata; secrets never belong in this contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: PublicAuthChallengeProvider
    site_key: str = Field(min_length=1, max_length=256)


class PublicAuthConfiguration(BaseModel):
    """Unauthenticated registration policy discovery with a stable challenge seam."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    password_policy: PasswordPolicyView
    challenge: PublicAuthChallengeConfiguration | None


class AccountProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    principal_id: str = Field(min_length=1)
    username: str = Field(min_length=1, max_length=MAX_USERNAME_CHARS)
    display_name: str = Field(min_length=1, max_length=MAX_DISPLAY_NAME_CHARS)
    status: AccountStatus
    created_at: datetime
    updated_at: datetime
    password_changed_at: datetime
    revision: int = Field(ge=1)
    etag: str = Field(pattern=r'^"v[1-9][0-9]*"$')


class AccountSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: AccountProfile
    password_policy: PasswordPolicyView


class AccountProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=MAX_DISPLAY_NAME_CHARS)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        _validate_unicode_scalar_text(value, field_name="display_name")
        if value != value.strip():
            raise ValueError("display_name must not contain surrounding whitespace")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("display_name must not contain control characters")
        return value


class PasswordChangeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: SecretStr = Field(min_length=1, max_length=MAX_PASSWORD_CHARS)
    new_password: SecretStr = Field(min_length=1, max_length=MAX_NEW_PASSWORD_CHARS)

    @field_validator("current_password", "new_password")
    @classmethod
    def reject_non_scalar_passwords(cls, value: SecretStr) -> SecretStr:
        return _validate_password_text(value, field_name="password")


class PasswordChangeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account: AccountSettings
    other_sessions_revoked: int = Field(ge=0)


class RegistrationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=MAX_USERNAME_CHARS)
    password: SecretStr = Field(min_length=1, max_length=MAX_PASSWORD_CHARS)

    @field_validator("username")
    @classmethod
    def reject_surrounding_whitespace(cls, value: str) -> str:
        return _validate_username_text(value)

    @field_validator("password")
    @classmethod
    def reject_empty_password(cls, value: SecretStr) -> SecretStr:
        return _validate_password_text(value, field_name="password")


class RegistrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    principal: AccountPrincipal


class LoginCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=MAX_USERNAME_CHARS)
    password: SecretStr = Field(min_length=1, max_length=MAX_PASSWORD_CHARS)

    @field_validator("username")
    @classmethod
    def reject_invalid_username(cls, value: str) -> str:
        return _validate_username_text(value)

    @field_validator("password")
    @classmethod
    def reject_empty_password(cls, value: SecretStr) -> SecretStr:
        return _validate_password_text(value, field_name="password")


class SessionCreated(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str = Field(
        min_length=1,
        max_length=MAX_ACCESS_TOKEN_CHARS,
        pattern=ACCESS_TOKEN_PATTERN,
        repr=False,
    )
    token_type: Literal["Bearer"]
    principal: AccountPrincipal
    capability_revision: int = Field(ge=0)


class AvailableScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: str = Field(min_length=1)
    organization_name: str | None = Field(default=None, min_length=1, max_length=256)
    project_id: str = Field(min_length=1)
    project_name: str | None = Field(default=None, min_length=1, max_length=256)
    region_codes: tuple[NonEmptyScopeValue, ...] = Field(json_schema_extra={"uniqueItems": True})
    project_wide: bool
    capabilities: tuple[NonEmptyScopeValue, ...] = Field(json_schema_extra={"uniqueItems": True})

    @field_validator("region_codes", "capabilities")
    @classmethod
    def reject_duplicate_scope_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("scope values must be unique")
        return value


class AvailableOrganization(BaseModel):
    """An active organization relationship that does not imply project access."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: str = Field(min_length=1, max_length=256)
    organization_name: str = Field(min_length=1, max_length=256)
    member_status: Literal["ACTIVE"] = "ACTIVE"


class SessionBootstrap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    principal: AccountPrincipal
    available_organizations: tuple[AvailableOrganization, ...] = ()
    available_scopes: tuple[AvailableScope, ...]
    platform_capabilities: tuple[NonEmptyScopeValue, ...] = Field(
        default=(), json_schema_extra={"uniqueItems": True}
    )
    capability_revision: int = Field(ge=0)

    @field_validator("platform_capabilities")
    @classmethod
    def reject_duplicate_platform_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("platform capabilities must be unique")
        return value


class MembershipRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=MAX_ACCESS_REASON_CHARS)


class OrganizationMembershipRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id_or_join_code: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=MAX_ACCESS_REASON_CHARS)

    @field_validator("organization_id_or_join_code", "reason")
    @classmethod
    def reject_surrounding_whitespace(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("value must not contain surrounding whitespace")
        return value


class CapabilityRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_keys: tuple[str, ...] = Field(min_length=1, max_length=MAX_CAPABILITY_KEYS)
    reason: str | None = Field(default=None, max_length=MAX_ACCESS_REASON_CHARS)

    @field_validator("capability_keys")
    @classmethod
    def validate_capability_keys(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(value.strip() for value in values))
        if any(not value for value in normalized):
            raise ValueError("capability keys must not be empty")
        if any(len(value) > MAX_CAPABILITY_KEY_CHARS for value in normalized):
            raise ValueError(
                f"capability keys must not exceed {MAX_CAPABILITY_KEY_CHARS} characters"
            )
        if any(value.startswith("platform.") for value in normalized):
            raise ValueError(
                "platform capabilities can only be granted through the global identity plane"
            )
        return normalized


class AccessDecisionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=MAX_ACCESS_REASON_CHARS)


class MembershipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    organization_id: str
    project_id: str
    requester_id: str
    status: AccessRequestStatus
    reason: str | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    revision: int = Field(ge=1)


class CapabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    organization_id: str
    project_id: str
    requester_id: str
    capability_keys: tuple[str, ...]
    status: AccessRequestStatus
    reason: str | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    revision: int = Field(ge=1)


class MembershipRequestList(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[MembershipRequest, ...]


class CapabilityRequestList(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[CapabilityRequest, ...]


class AccountOrganizationMembership(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: str = Field(min_length=1, max_length=256)
    organization_name: str = Field(min_length=1, max_length=256)
    member_status: Literal["ACTIVE"] = "ACTIVE"


class AccountProjectMembership(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: str = Field(min_length=1, max_length=256)
    organization_name: str = Field(min_length=1, max_length=256)
    project_id: str = Field(min_length=1, max_length=256)
    project_name: str = Field(min_length=1, max_length=256)
    member_status: Literal["ACTIVE"] = "ACTIVE"


class AccountAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1)
    kind: Literal["ORGANIZATION", "PROJECT", "CAPABILITY"]
    organization_id: str = Field(min_length=1, max_length=256)
    project_id: str | None = Field(default=None, max_length=256)
    capability_keys: tuple[str, ...] = ()
    status: AccessRequestStatus
    reason: str | None = None
    created_at: datetime
    updated_at: datetime
    revision: int = Field(ge=1)


class AccountAccessOverview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organizations: tuple[AccountOrganizationMembership, ...]
    projects: tuple[AccountProjectMembership, ...]
    requests: tuple[AccountAccessRequest, ...]
    pending_request_count: int = Field(ge=0)


class AccessAuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    scope_kind: str
    organization_id: str | None = None
    project_id: str | None = None
    actor_id: str | None = None
    action: str
    resource_type: str
    resource_id: str
    request_id: str
    outcome: str
    safe_details: dict[str, object]
    occurred_at: datetime


class AccessAuditEventList(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[AccessAuditEvent, ...]


class AccountNotification(BaseModel):
    """Recipient-scoped, deliberately metadata-only account notification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    notification_id: str = Field(min_length=1, json_schema_extra={"format": "uuid"})
    kind: AccountNotificationKind
    organization_id: str = Field(min_length=1, max_length=256)
    project_id: str = Field(min_length=1, max_length=256)
    resource_type: AccountNotificationResourceType
    resource_id: str = Field(min_length=1, max_length=512)
    state: AccountNotificationState
    created_at: datetime
    read_at: datetime | None = None


class AccountNotificationPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[AccountNotification, ...]
    next_cursor: str | None = Field(default=None, min_length=16, max_length=16_384)


class AccountNotificationUnreadCount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    unread_count: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class AccountCredential:
    principal: AccountPrincipal
    canonical_username: str
    password_hash: str
    capability_revision: int
    account_revision: int
    credential_revision: int
    updated_at: datetime
    password_changed_at: datetime
    recovery_email: str | None = None


@dataclass(frozen=True, slots=True)
class AccountProfileRecord:
    principal_id: str
    username: str
    display_name: str
    status: AccountStatus
    created_at: datetime
    updated_at: datetime
    password_changed_at: datetime
    revision: int

    def to_profile(self) -> AccountProfile:
        return AccountProfile(
            principal_id=self.principal_id,
            username=self.username,
            display_name=self.display_name,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            password_changed_at=self.password_changed_at,
            revision=self.revision,
            etag=f'"v{self.revision}"',
        )


@dataclass(frozen=True, slots=True)
class SessionIssued:
    session_id: str


@dataclass(frozen=True, slots=True)
class SessionIssueStaleCredentials:
    pass


@dataclass(frozen=True, slots=True)
class SessionIssueCapacityRejected:
    retry_after_seconds: int

    def __post_init__(self) -> None:
        if not 1 <= self.retry_after_seconds <= 86_400:
            raise ValueError("session admission retry delay must be between 1 and 86400 seconds")


SessionIssueResult = SessionIssued | SessionIssueStaleCredentials | SessionIssueCapacityRejected


@dataclass(frozen=True, slots=True)
class ResolvedSession:
    session_id: str
    principal: AccountPrincipal
    capability_revision: int
    scopes: tuple[AvailableScope, ...]
    platform_capabilities: tuple[str, ...] = ()
