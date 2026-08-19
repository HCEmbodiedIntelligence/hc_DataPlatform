"""Contracts for direct registration and project-scoped access requests.

The public models deliberately contain no account-approval state.  Registration creates an
ACTIVE, empty principal.  Project membership and capability grants have independent review
lifecycles and are the only objects in this module that can be pending approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

# Technical resource ceilings for endpoints exposed to the public internet.  They deliberately
# do not define the still-unconfirmed password-strength or abuse-rate policy (OPEN-02).
MAX_USERNAME_CHARS = 128
MAX_PASSWORD_CHARS = 1024
MAX_ACCESS_REASON_CHARS = 2_000
MAX_CAPABILITY_KEYS = 64
MAX_CAPABILITY_KEY_CHARS = 128


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
    MEMBERSHIP = "MEMBERSHIP"
    CAPABILITY = "CAPABILITY"


class AccountPrincipal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    principal_id: str = Field(min_length=1)
    username: str = Field(min_length=1, max_length=MAX_USERNAME_CHARS)
    status: AccountStatus
    created_at: datetime


class RegistrationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=MAX_USERNAME_CHARS)
    password: SecretStr = Field(min_length=1, max_length=MAX_PASSWORD_CHARS)

    @field_validator("username")
    @classmethod
    def reject_surrounding_whitespace(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("username must not contain leading or trailing whitespace")
        return value

    @field_validator("password")
    @classmethod
    def reject_empty_password(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("password must not be empty")
        return value


class RegistrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    principal: AccountPrincipal


class LoginCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=MAX_USERNAME_CHARS)
    password: SecretStr = Field(min_length=1, max_length=MAX_PASSWORD_CHARS)

    @field_validator("password")
    @classmethod
    def reject_empty_password(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("password must not be empty")
        return value


class SessionCreated(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str = Field(min_length=1, repr=False)
    token_type: str = "Bearer"
    principal: AccountPrincipal
    capability_revision: int = Field(ge=0)


class AvailableScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str = Field(min_length=1)
    region_codes: tuple[str, ...] = ()
    project_wide: bool = True
    capabilities: tuple[str, ...] = ()


class SessionBootstrap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    principal: AccountPrincipal
    available_scopes: tuple[AvailableScope, ...]
    capability_revision: int = Field(ge=0)


class MembershipRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=MAX_ACCESS_REASON_CHARS)


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
        return normalized


class AccessDecisionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=MAX_ACCESS_REASON_CHARS)


class MembershipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
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


class AccessAuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    scope_kind: str
    project_id: str | None = None
    actor_id: str | None = None
    action: str
    resource_type: str
    resource_id: str
    request_id: str
    outcome: str
    safe_details: dict[str, object] = Field(default_factory=dict)
    occurred_at: datetime


class AccessAuditEventList(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[AccessAuditEvent, ...]


@dataclass(frozen=True, slots=True)
class AccountCredential:
    principal: AccountPrincipal
    canonical_username: str
    password_hash: str
    capability_revision: int


@dataclass(frozen=True, slots=True)
class ResolvedSession:
    session_id: str
    principal: AccountPrincipal
    capability_revision: int
    scopes: tuple[AvailableScope, ...]
