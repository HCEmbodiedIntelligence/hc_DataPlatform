"""Repository contract and scope-enforcing in-memory reference implementation."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from math import ceil
from threading import RLock
from typing import Protocol
from uuid import uuid4

from hc_data_platform.core.errors import ProblemException, problem

from .access_models import (
    AccessAuditEvent,
    AccessRequestStatus,
    AccountCredential,
    AccountNotification,
    AccountNotificationKind,
    AccountNotificationResourceType,
    AccountNotificationState,
    AccountPrincipal,
    AccountProfileRecord,
    AccountStatus,
    AvailableScope,
    CapabilityRequest,
    MembershipRequest,
    ResolvedSession,
    SessionIssueCapacityRejected,
    SessionIssued,
    SessionIssueResult,
    SessionIssueStaleCredentials,
)
from .admin_accounts import (
    AdminAccountRepository,
    ManagedAccount,
    ManagedAccountPage,
    ManagedAccountState,
    PlatformAccountRole,
    recovery_email_hint,
)
from .auth import AuthContext, Role
from .capabilities import CAPABILITY_PLATFORM_ADMIN, PLATFORM_ADMIN_CAPABILITIES
from .recovery import (
    AccountRecoveryRepository,
    PasswordRecoveryCredential,
    PasswordRecoveryTarget,
)
from .scope import ScopeGuard


def _now() -> datetime:
    return datetime.now(timezone.utc)


DEFAULT_SESSION_IDLE_TTL_SECONDS = 1_800
DEFAULT_SESSION_ABSOLUTE_TTL_SECONDS = 86_400
DEFAULT_SESSION_TOUCH_INTERVAL_SECONDS = 60
DEFAULT_MAX_ACTIVE_SESSIONS = 5
MAX_SESSION_RETRY_AFTER_SECONDS = 86_400


def _session_lifecycle_durations(
    *,
    session_idle_ttl_seconds: int,
    session_absolute_ttl_seconds: int,
    session_touch_interval_seconds: int,
    max_active_sessions: int,
) -> tuple[timedelta, timedelta, timedelta]:
    if session_touch_interval_seconds < 1:
        raise ValueError("session_touch_interval_seconds must be positive")
    if session_idle_ttl_seconds <= session_touch_interval_seconds:
        raise ValueError("session_idle_ttl_seconds must be greater than the touch interval")
    if session_absolute_ttl_seconds < session_idle_ttl_seconds:
        raise ValueError("session_absolute_ttl_seconds must be at least the idle TTL")
    if max_active_sessions < 1:
        raise ValueError("max_active_sessions must be positive")
    return (
        timedelta(seconds=session_idle_ttl_seconds),
        timedelta(seconds=session_absolute_ttl_seconds),
        timedelta(seconds=session_touch_interval_seconds),
    )


def _utc_timestamp(clock: Callable[[], datetime]) -> datetime:
    timestamp = clock()
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("session lifecycle clock must return a timezone-aware timestamp")
    return timestamp.astimezone(timezone.utc)


def _bounded_session_retry_after_seconds(*, expires_at: datetime, timestamp: datetime) -> int:
    seconds = ceil((expires_at - timestamp).total_seconds())
    return min(max(seconds, 1), MAX_SESSION_RETRY_AFTER_SECONDS)


@dataclass(slots=True)
class _OpaqueSessionRecord:
    session_id: str
    principal_id: str
    credential_revision: int
    issued_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None = None
    revocation_reason: str | None = None


@dataclass(slots=True)
class _AccountSecurityChallengeRecord:
    principal_id: str
    purpose: str
    target_email: str
    credential_revision: int
    token_hash: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None


def _fingerprint(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _not_found(resource: str) -> ProblemException:
    return problem(
        status=404,
        code=f"{resource.upper()}_NOT_FOUND",
        title=f"{resource.replace('_', ' ').title()} not found",
        detail="The requested access object does not exist in this visible scope.",
    )


def _transition_conflict(status: AccessRequestStatus) -> ProblemException:
    return problem(
        status=409,
        code="ACCESS_REQUEST_STATE_CONFLICT",
        title="Access request state conflict",
        detail=f"The requested transition is not valid from {status.value}.",
        details={"current_status": status.value},
    )


def _account_principal_required() -> ProblemException:
    return problem(
        status=403,
        code="ACCOUNT_PRINCIPAL_REQUIRED",
        title="Platform account required",
        detail="Access requests can only be created by a registered platform account.",
    )


def _session_invalid() -> ProblemException:
    return problem(
        status=401,
        code="SESSION_INVALID",
        title="Session invalid",
        detail="The session is revoked or no longer valid.",
    )


def _account_revision_conflict(current_revision: int) -> ProblemException:
    return problem(
        status=412,
        code="ETAG_MISMATCH",
        title="Account version changed",
        detail="The account changed after it was read.",
        details={"current_etag": f'"v{current_revision}"'},
    )


def _last_platform_admin_conflict() -> ProblemException:
    return problem(
        status=409,
        code="LAST_PLATFORM_ADMIN_REQUIRED",
        title="Last platform administrator required",
        detail="Promote another active platform administrator before changing this account.",
    )


def can_manage_project(
    auth: AuthContext, project_id: str, organization_id: str | None = None
) -> bool:
    """Management is capability based and never grants an implicit tenant bypass."""

    try:
        ScopeGuard.require(auth, project_id, organization_id=organization_id)
    except ProblemException:
        return False
    return Role.ADMIN.value in auth.roles or auth.has_capability(
        "project.access.manage", project_id, organization_id
    )


def require_project_manager(
    auth: AuthContext, project_id: str, organization_id: str | None = None
) -> None:
    ScopeGuard.require(auth, project_id, organization_id=organization_id)
    if Role.ADMIN.value not in auth.roles and not auth.has_capability(
        "project.access.manage", project_id, organization_id
    ):
        raise problem(
            status=403,
            code="ACCESS_MANAGEMENT_REQUIRED",
            title="Access management permission required",
            detail="This project scope does not grant access-request management.",
        )


class AccessRepository(AccountRecoveryRepository, AdminAccountRepository, Protocol):
    def register_account(
        self,
        *,
        canonical_username: str,
        display_username: str,
        password_hash: str,
        request_id: str,
    ) -> AccountPrincipal: ...

    def credential_for_username(self, canonical_username: str) -> AccountCredential | None: ...

    def credential_for_principal(self, principal_id: str) -> AccountCredential | None: ...

    def upgrade_password_hash(
        self,
        *,
        principal_id: str,
        expected_password_hash: str,
        upgraded_password_hash: str,
        request_id: str,
    ) -> bool: ...

    def get_account_profile(self, principal_id: str) -> AccountProfileRecord: ...

    def replay_account_command(
        self,
        *,
        principal_id: str,
        current_token_hash: str,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
    ) -> tuple[AccountProfileRecord, int] | None: ...

    def update_account_profile(
        self,
        *,
        principal_id: str,
        current_token_hash: str,
        display_name: str,
        expected_revision: int,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
    ) -> AccountProfileRecord: ...

    def change_password(
        self,
        *,
        principal_id: str,
        current_token_hash: str,
        expected_password_hash: str,
        new_password_hash: str,
        expected_revision: int,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
    ) -> tuple[AccountProfileRecord, int]: ...

    def create_session(
        self,
        *,
        principal_id: str,
        token_hash: str,
        expected_password_hash: str,
        expected_credential_revision: int,
        request_id: str,
    ) -> SessionIssueResult: ...

    def resolve_session(
        self, token_hash: str, *, request_id: str | None = None
    ) -> ResolvedSession | None: ...

    def revoke_session(self, *, token_hash: str, request_id: str) -> None: ...

    def record_failed_login(self, *, principal_id: str | None, request_id: str) -> None: ...

    def count_unread_notifications(self, *, principal_id: str) -> int: ...

    def list_notifications(
        self,
        *,
        principal_id: str,
        state: AccountNotificationState | None,
        before: tuple[datetime, str] | None,
        limit: int,
    ) -> tuple[AccountNotification, ...]: ...

    def mark_notification_read(
        self, *, principal_id: str, notification_id: str, request_id: str
    ) -> bool: ...

    def create_membership_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest: ...

    def list_membership_requests(
        self, *, auth: AuthContext, organization_id: str, project_id: str
    ) -> tuple[MembershipRequest, ...]: ...

    def get_membership_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
    ) -> MembershipRequest: ...

    def decide_membership_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
        target_status: AccessRequestStatus,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest: ...

    def withdraw_membership_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest: ...

    def create_capability_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        capability_keys: tuple[str, ...],
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> CapabilityRequest: ...

    def list_capability_requests(
        self, *, auth: AuthContext, organization_id: str, project_id: str
    ) -> tuple[CapabilityRequest, ...]: ...

    def get_capability_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
    ) -> CapabilityRequest: ...

    def decide_capability_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
        target_status: AccessRequestStatus,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> CapabilityRequest: ...

    def withdraw_capability_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> CapabilityRequest: ...

    def list_audit_events(
        self, *, auth: AuthContext, organization_id: str, project_id: str
    ) -> tuple[AccessAuditEvent, ...]: ...


class InMemoryAccessRepository:
    """Thread-safe executable specification for the PostgreSQL adapter."""

    def __init__(
        self,
        *,
        session_idle_ttl_seconds: int = DEFAULT_SESSION_IDLE_TTL_SECONDS,
        session_absolute_ttl_seconds: int = DEFAULT_SESSION_ABSOLUTE_TTL_SECONDS,
        session_touch_interval_seconds: int = DEFAULT_SESSION_TOUCH_INTERVAL_SECONDS,
        max_active_sessions: int = DEFAULT_MAX_ACTIVE_SESSIONS,
        clock: Callable[[], datetime] = _now,
        organization_projects: tuple[tuple[str, str], ...] = (),
    ) -> None:
        (
            self._session_idle_ttl,
            self._session_absolute_ttl,
            self._session_touch_interval,
        ) = _session_lifecycle_durations(
            session_idle_ttl_seconds=session_idle_ttl_seconds,
            session_absolute_ttl_seconds=session_absolute_ttl_seconds,
            session_touch_interval_seconds=session_touch_interval_seconds,
            max_active_sessions=max_active_sessions,
        )
        self._max_active_sessions = max_active_sessions
        self._clock = clock
        self._accounts: dict[str, AccountCredential] = {}
        self._username_index: dict[str, str] = {}
        self._sessions: dict[str, _OpaqueSessionRecord] = {}
        self._account_security_challenges: dict[str, _AccountSecurityChallengeRecord] = {}
        self._platform_capabilities: dict[str, set[str]] = {}
        self._deleted_accounts: dict[str, datetime] = {}
        self._memberships: set[tuple[str, str, str]] = set()
        self._organization_projects = set(organization_projects)
        self._membership_requests: dict[str, MembershipRequest] = {}
        self._capability_requests: dict[str, CapabilityRequest] = {}
        self._notifications: dict[str, AccountNotification] = {}
        self._notification_recipients: dict[str, str] = {}
        self._notification_event_keys: set[tuple[str, str]] = set()
        self._idempotency: dict[tuple[str, str, str], tuple[str, str]] = {}
        self._account_idempotency: dict[
            tuple[str, str, str], tuple[str, AccountProfileRecord, int]
        ] = {}
        self._audit: list[AccessAuditEvent] = []
        self._lock = RLock()

    def register_account(
        self,
        *,
        canonical_username: str,
        display_username: str,
        password_hash: str,
        request_id: str,
    ) -> AccountPrincipal:
        with self._lock:
            if canonical_username in self._username_index:
                raise problem(
                    status=409,
                    code="ACCOUNT_REGISTRATION_CONFLICT",
                    title="Account registration conflict",
                    detail="The account could not be registered with these credentials.",
                )
            timestamp = _now()
            principal = AccountPrincipal(
                principal_id=str(uuid4()),
                username=display_username,
                display_name=display_username,
                status=AccountStatus.ACTIVE,
                created_at=timestamp,
            )
            self._accounts[principal.principal_id] = AccountCredential(
                principal=principal,
                canonical_username=canonical_username,
                password_hash=password_hash,
                capability_revision=0,
                account_revision=1,
                credential_revision=1,
                updated_at=timestamp,
                password_changed_at=timestamp,
                recovery_email=None,
            )
            self._username_index[canonical_username] = principal.principal_id
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal.principal_id,
                action="auth.registration.created",
                resource_type="account",
                resource_id=principal.principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"empty_account": True},
                occurred_at=timestamp,
            )
            return principal

    def credential_for_username(self, canonical_username: str) -> AccountCredential | None:
        with self._lock:
            principal_id = self._username_index.get(canonical_username)
            return None if principal_id is None else self._accounts[principal_id]

    def credential_for_principal(self, principal_id: str) -> AccountCredential | None:
        with self._lock:
            return self._accounts.get(principal_id)

    def upgrade_password_hash(
        self,
        *,
        principal_id: str,
        expected_password_hash: str,
        upgraded_password_hash: str,
        request_id: str,
    ) -> bool:
        with self._lock:
            account = self._accounts.get(principal_id)
            if account is None or not secrets.compare_digest(
                account.password_hash, expected_password_hash
            ):
                return False
            self._accounts[principal_id] = AccountCredential(
                principal=account.principal,
                canonical_username=account.canonical_username,
                password_hash=upgraded_password_hash,
                capability_revision=account.capability_revision,
                account_revision=account.account_revision,
                credential_revision=account.credential_revision,
                updated_at=_now(),
                password_changed_at=account.password_changed_at,
                recovery_email=account.recovery_email,
            )
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal_id,
                action="auth.password.rehashed",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"algorithm": "scrypt"},
            )
            return True

    def get_account_profile(self, principal_id: str) -> AccountProfileRecord:
        with self._lock:
            account = self._accounts.get(principal_id)
            if account is None:
                raise _not_found("account")
            return self._profile_record(account)

    def replay_account_command(
        self,
        *,
        principal_id: str,
        current_token_hash: str,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
    ) -> tuple[AccountProfileRecord, int] | None:
        with self._lock:
            self._require_current_session(
                principal_id=principal_id,
                current_token_hash=current_token_hash,
                request_id=request_id,
            )
            return self._repeat_account_command(
                principal_id,
                operation,
                idempotency_key,
                request_fingerprint,
            )

    def update_account_profile(
        self,
        *,
        principal_id: str,
        current_token_hash: str,
        display_name: str,
        expected_revision: int,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
    ) -> AccountProfileRecord:
        operation = "account.profile.update"
        with self._lock:
            account, _, timestamp = self._require_current_session(
                principal_id=principal_id,
                current_token_hash=current_token_hash,
                request_id=request_id,
            )
            replay = self._repeat_account_command(
                principal_id, operation, idempotency_key, request_fingerprint
            )
            if replay is not None:
                return replay[0]
            if account.account_revision != expected_revision:
                raise _account_revision_conflict(account.account_revision)
            principal = account.principal.model_copy(update={"display_name": display_name})
            updated = AccountCredential(
                principal=principal,
                canonical_username=account.canonical_username,
                password_hash=account.password_hash,
                capability_revision=account.capability_revision,
                account_revision=account.account_revision + 1,
                credential_revision=account.credential_revision,
                updated_at=timestamp,
                password_changed_at=account.password_changed_at,
                recovery_email=account.recovery_email,
            )
            self._accounts[principal_id] = updated
            profile = self._profile_record(updated)
            self._remember_account_command(
                principal_id,
                operation,
                idempotency_key,
                request_fingerprint,
                profile,
                0,
            )
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal_id,
                action="account.profile.updated",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"changed_fields": ["display_name"]},
                occurred_at=timestamp,
            )
            return profile

    def change_password(
        self,
        *,
        principal_id: str,
        current_token_hash: str,
        expected_password_hash: str,
        new_password_hash: str,
        expected_revision: int,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
    ) -> tuple[AccountProfileRecord, int]:
        operation = "account.password.change"
        with self._lock:
            account, current_session, timestamp = self._require_current_session(
                principal_id=principal_id,
                current_token_hash=current_token_hash,
                request_id=request_id,
            )
            replay = self._repeat_account_command(
                principal_id, operation, idempotency_key, request_fingerprint
            )
            if replay is not None:
                return replay
            if account.account_revision != expected_revision:
                raise _account_revision_conflict(account.account_revision)
            if not secrets.compare_digest(account.password_hash, expected_password_hash):
                raise _account_revision_conflict(account.account_revision)
            self._expire_sessions(
                principal_id=principal_id,
                timestamp=timestamp,
                request_id=request_id,
            )
            self._revoke_invalid_sessions(
                account=account,
                timestamp=timestamp,
                request_id=request_id,
            )
            if current_session.revoked_at is not None:
                raise _session_invalid()
            next_credential_revision = account.credential_revision + 1
            updated = AccountCredential(
                principal=account.principal,
                canonical_username=account.canonical_username,
                password_hash=new_password_hash,
                capability_revision=account.capability_revision,
                account_revision=account.account_revision + 1,
                credential_revision=next_credential_revision,
                updated_at=timestamp,
                password_changed_at=timestamp,
                recovery_email=account.recovery_email,
            )
            self._accounts[principal_id] = updated
            other_sessions_revoked = 0
            for token_hash, session in self._sessions.items():
                if session.principal_id != principal_id or session.revoked_at is not None:
                    continue
                if token_hash == current_token_hash:
                    session.credential_revision = next_credential_revision
                    continue
                session.revoked_at = timestamp
                session.revocation_reason = "PASSWORD_CHANGED"
                other_sessions_revoked += 1
                self._append_audit(
                    scope_kind="PLATFORM",
                    project_id=None,
                    actor_id=principal_id,
                    action="auth.session.revoked",
                    resource_type="session",
                    resource_id=session.session_id,
                    request_id=request_id,
                    outcome="SUCCEEDED",
                    safe_details={"reason": "PASSWORD_CHANGED"},
                    occurred_at=timestamp,
                )
            profile = self._profile_record(updated)
            self._remember_account_command(
                principal_id,
                operation,
                idempotency_key,
                request_fingerprint,
                profile,
                other_sessions_revoked,
            )
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal_id,
                action="auth.password.changed",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"other_sessions_revoked": other_sessions_revoked},
                occurred_at=timestamp,
            )
            return profile, other_sessions_revoked

    def issue_recovery_email_verification(
        self,
        *,
        principal_id: str,
        recovery_email: str,
        token_hash: str,
        expires_at: datetime,
        request_id: str,
    ) -> None:
        with self._lock:
            account = self._accounts.get(principal_id)
            if account is None or account.principal.status is not AccountStatus.ACTIVE:
                raise _not_found("account")
            if any(
                other.principal.principal_id != principal_id
                and other.recovery_email == recovery_email
                for other in self._accounts.values()
            ):
                raise problem(
                    status=409,
                    code="RECOVERY_EMAIL_CONFLICT",
                    title="Recovery email conflict",
                    detail="The recovery email cannot be assigned to this account.",
                )
            timestamp = _utc_timestamp(self._clock)
            if expires_at <= timestamp:
                raise ValueError("recovery email verification expiry must be in the future")
            self._consume_active_security_challenges(
                principal_id=principal_id,
                purpose="RECOVERY_EMAIL_VERIFY",
                timestamp=timestamp,
            )
            self._account_security_challenges[token_hash] = _AccountSecurityChallengeRecord(
                principal_id=principal_id,
                purpose="RECOVERY_EMAIL_VERIFY",
                target_email=recovery_email,
                credential_revision=account.credential_revision,
                token_hash=token_hash,
                created_at=timestamp,
                expires_at=expires_at,
            )
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal_id,
                action="auth.recovery_email.verification_requested",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={},
                occurred_at=timestamp,
            )

    def confirm_recovery_email(
        self,
        *,
        principal_id: str,
        token_hash: str,
        now: datetime,
        request_id: str,
    ) -> str | None:
        with self._lock:
            challenge = self._account_security_challenges.get(token_hash)
            account = self._accounts.get(principal_id)
            if (
                challenge is None
                or account is None
                or challenge.principal_id != principal_id
                or challenge.purpose != "RECOVERY_EMAIL_VERIFY"
                or challenge.consumed_at is not None
                or challenge.expires_at <= now
                or account.principal.status is not AccountStatus.ACTIVE
                or challenge.credential_revision != account.credential_revision
            ):
                return None
            if any(
                other.principal.principal_id != principal_id
                and other.recovery_email == challenge.target_email
                for other in self._accounts.values()
            ):
                return None
            challenge.consumed_at = now
            self._consume_active_security_challenges(
                principal_id=principal_id,
                purpose="PASSWORD_RECOVERY",
                timestamp=now,
            )
            self._accounts[principal_id] = AccountCredential(
                principal=account.principal,
                canonical_username=account.canonical_username,
                password_hash=account.password_hash,
                capability_revision=account.capability_revision,
                account_revision=account.account_revision + 1,
                credential_revision=account.credential_revision,
                updated_at=now,
                password_changed_at=account.password_changed_at,
                recovery_email=challenge.target_email,
            )
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal_id,
                action="auth.recovery_email.configured",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={},
                occurred_at=now,
            )
            return challenge.target_email

    def issue_password_recovery(
        self,
        *,
        canonical_identifier: str,
        token_hash: str,
        expires_at: datetime,
        request_id: str,
    ) -> PasswordRecoveryTarget | None:
        with self._lock:
            account = next(
                (
                    candidate
                    for candidate in self._accounts.values()
                    if candidate.canonical_username == canonical_identifier
                    or candidate.recovery_email == canonical_identifier
                ),
                None,
            )
            if (
                account is None
                or account.principal.status is not AccountStatus.ACTIVE
                or account.recovery_email is None
            ):
                return None
            timestamp = _utc_timestamp(self._clock)
            if expires_at <= timestamp:
                raise ValueError("password recovery expiry must be in the future")
            principal_id = account.principal.principal_id
            self._consume_active_security_challenges(
                principal_id=principal_id,
                purpose="PASSWORD_RECOVERY",
                timestamp=timestamp,
            )
            self._account_security_challenges[token_hash] = _AccountSecurityChallengeRecord(
                principal_id=principal_id,
                purpose="PASSWORD_RECOVERY",
                target_email=account.recovery_email,
                credential_revision=account.credential_revision,
                token_hash=token_hash,
                created_at=timestamp,
                expires_at=expires_at,
            )
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal_id,
                action="auth.password_recovery.requested",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={},
                occurred_at=timestamp,
            )
            return PasswordRecoveryTarget(
                principal_id=principal_id,
                display_username=account.principal.username,
                recovery_email=account.recovery_email,
            )

    def password_recovery_credential(
        self,
        *,
        token_hash: str,
        now: datetime,
    ) -> PasswordRecoveryCredential | None:
        with self._lock:
            challenge = self._account_security_challenges.get(token_hash)
            if (
                challenge is None
                or challenge.purpose != "PASSWORD_RECOVERY"
                or challenge.consumed_at is not None
                or challenge.expires_at <= now
            ):
                return None
            account = self._accounts.get(challenge.principal_id)
            if (
                account is None
                or account.principal.status is not AccountStatus.ACTIVE
                or challenge.credential_revision != account.credential_revision
            ):
                return None
            return PasswordRecoveryCredential(
                principal_id=account.principal.principal_id,
                canonical_username=account.canonical_username,
                password_hash=account.password_hash,
            )

    def complete_password_recovery(
        self,
        *,
        token_hash: str,
        expected_password_hash: str,
        new_password_hash: str,
        now: datetime,
        request_id: str,
    ) -> int | None:
        with self._lock:
            challenge = self._account_security_challenges.get(token_hash)
            if (
                challenge is None
                or challenge.purpose != "PASSWORD_RECOVERY"
                or challenge.consumed_at is not None
                or challenge.expires_at <= now
            ):
                return None
            account = self._accounts.get(challenge.principal_id)
            if (
                account is None
                or account.principal.status is not AccountStatus.ACTIVE
                or challenge.credential_revision != account.credential_revision
                or not secrets.compare_digest(account.password_hash, expected_password_hash)
            ):
                return None
            challenge.consumed_at = now
            principal_id = account.principal.principal_id
            self._accounts[principal_id] = AccountCredential(
                principal=account.principal,
                canonical_username=account.canonical_username,
                password_hash=new_password_hash,
                capability_revision=account.capability_revision,
                account_revision=account.account_revision + 1,
                credential_revision=account.credential_revision + 1,
                updated_at=now,
                password_changed_at=now,
                recovery_email=account.recovery_email,
            )
            sessions_revoked = 0
            for session in self._sessions.values():
                if session.principal_id != principal_id or session.revoked_at is not None:
                    continue
                session.revoked_at = now
                session.revocation_reason = "PASSWORD_RECOVERED"
                sessions_revoked += 1
                self._append_audit(
                    scope_kind="PLATFORM",
                    project_id=None,
                    actor_id=principal_id,
                    action="auth.session.revoked",
                    resource_type="session",
                    resource_id=session.session_id,
                    request_id=request_id,
                    outcome="SUCCEEDED",
                    safe_details={"reason": "PASSWORD_RECOVERED"},
                    occurred_at=now,
                )
            self._consume_active_security_challenges(
                principal_id=principal_id,
                purpose="PASSWORD_RECOVERY",
                timestamp=now,
            )
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal_id,
                action="auth.password.recovered",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"sessions_revoked": sessions_revoked},
                occurred_at=now,
            )
            return sessions_revoked

    def create_session(
        self,
        *,
        principal_id: str,
        token_hash: str,
        expected_password_hash: str,
        expected_credential_revision: int,
        request_id: str,
    ) -> SessionIssueResult:
        with self._lock:
            account = self._accounts.get(principal_id)
            if (
                account is None
                or account.principal.status is not AccountStatus.ACTIVE
                or account.credential_revision != expected_credential_revision
                or not secrets.compare_digest(account.password_hash, expected_password_hash)
            ):
                return SessionIssueStaleCredentials()
            timestamp = _utc_timestamp(self._clock)
            self._expire_sessions(
                principal_id=principal_id,
                timestamp=timestamp,
                request_id=request_id,
            )
            self._revoke_invalid_sessions(
                account=account,
                timestamp=timestamp,
                request_id=request_id,
            )
            active = tuple(
                session
                for session in self._sessions.values()
                if session.principal_id == principal_id
                and session.revoked_at is None
                and session.credential_revision == account.credential_revision
            )
            if len(active) >= self._max_active_sessions:
                earliest_expiry = min(
                    min(
                        session.last_seen_at + self._session_idle_ttl,
                        session.issued_at + self._session_absolute_ttl,
                    )
                    for session in active
                )
                retry_after_seconds = _bounded_session_retry_after_seconds(
                    expires_at=earliest_expiry,
                    timestamp=timestamp,
                )
                audit_checkpoint = len(self._audit)
                try:
                    self._append_audit(
                        scope_kind="PLATFORM",
                        project_id=None,
                        actor_id=principal_id,
                        action="auth.login.succeeded",
                        resource_type="account",
                        resource_id=principal_id,
                        request_id=request_id,
                        outcome="SUCCEEDED",
                        occurred_at=timestamp,
                    )
                    self._append_audit(
                        scope_kind="PLATFORM",
                        project_id=None,
                        actor_id=principal_id,
                        action="auth.session.admission.denied",
                        resource_type="account",
                        resource_id=principal_id,
                        request_id=request_id,
                        outcome="DENIED",
                        safe_details={"reason": "SESSION_LIMIT_REACHED"},
                        occurred_at=timestamp,
                    )
                except Exception:
                    del self._audit[audit_checkpoint:]
                    raise
                return SessionIssueCapacityRejected(retry_after_seconds=retry_after_seconds)

            if token_hash in self._sessions:
                raise RuntimeError("opaque session token hash collision")
            session_id = str(uuid4())
            session = _OpaqueSessionRecord(
                session_id=session_id,
                principal_id=principal_id,
                credential_revision=account.credential_revision,
                issued_at=timestamp,
                last_seen_at=timestamp,
            )
            audit_checkpoint = len(self._audit)
            try:
                self._append_audit(
                    scope_kind="PLATFORM",
                    project_id=None,
                    actor_id=principal_id,
                    action="auth.session.created",
                    resource_type="session",
                    resource_id=session_id,
                    request_id=request_id,
                    outcome="SUCCEEDED",
                    occurred_at=timestamp,
                )
                self._append_audit(
                    scope_kind="PLATFORM",
                    project_id=None,
                    actor_id=principal_id,
                    action="auth.login.succeeded",
                    resource_type="account",
                    resource_id=principal_id,
                    request_id=request_id,
                    outcome="SUCCEEDED",
                    occurred_at=timestamp,
                )
            except Exception:
                del self._audit[audit_checkpoint:]
                raise
            self._sessions[token_hash] = session
            return SessionIssued(session_id=session_id)

    def resolve_session(
        self, token_hash: str, *, request_id: str | None = None
    ) -> ResolvedSession | None:
        with self._lock:
            session = self._sessions.get(token_hash)
            if session is None or session.revoked_at is not None:
                return None
            audit_request_id = request_id or str(uuid4())
            timestamp = _utc_timestamp(self._clock)
            if self._session_expired(session, timestamp):
                self._expire_session(
                    session,
                    timestamp=timestamp,
                    request_id=audit_request_id,
                )
                return None
            principal_id = session.principal_id
            account = self._accounts.get(principal_id)
            if account is None or account.principal.status is not AccountStatus.ACTIVE:
                self._revoke_invalid_session(
                    session,
                    reason="ACCOUNT_DISABLED",
                    timestamp=timestamp,
                    request_id=audit_request_id,
                )
                return None
            if session.credential_revision != account.credential_revision:
                self._revoke_invalid_session(
                    session,
                    reason="PASSWORD_CHANGED",
                    timestamp=timestamp,
                    request_id=audit_request_id,
                )
                return None
            if timestamp >= session.last_seen_at + self._session_touch_interval:
                session.last_seen_at = timestamp
            platform_capabilities = self._platform_capabilities.get(principal_id, set())
            projects = (
                sorted(self._organization_projects)
                if CAPABILITY_PLATFORM_ADMIN in platform_capabilities
                else sorted(
                    (organization_id, project_id)
                    for member_id, organization_id, project_id in self._memberships
                    if member_id == principal_id
                )
            )
            scopes = tuple(
                AvailableScope(
                    organization_id=organization_id,
                    project_id=project_id,
                    region_codes=(),
                    project_wide=True,
                    capabilities=tuple(
                        sorted(
                            set()
                            if CAPABILITY_PLATFORM_ADMIN in platform_capabilities
                            else {
                                capability
                                for request in self._capability_requests.values()
                                if request.requester_id == principal_id
                                and request.organization_id == organization_id
                                and request.project_id == project_id
                                and request.status is AccessRequestStatus.APPROVED
                                for capability in request.capability_keys
                            }
                        )
                    ),
                )
                for organization_id, project_id in projects
            )
            return ResolvedSession(
                session_id=session.session_id,
                principal=account.principal,
                capability_revision=account.capability_revision,
                scopes=scopes,
                platform_capabilities=tuple(sorted(platform_capabilities)),
            )

    def revoke_session(self, *, token_hash: str, request_id: str) -> None:
        with self._lock:
            session = self._sessions.get(token_hash)
            if session is None:
                return
            if session.revoked_at is not None:
                return
            timestamp = _utc_timestamp(self._clock)
            session.revoked_at = timestamp
            session.revocation_reason = "LOGOUT"
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=session.principal_id,
                action="auth.session.revoked",
                resource_type="session",
                resource_id=session.session_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                occurred_at=timestamp,
            )

    def _expire_sessions(
        self,
        *,
        principal_id: str,
        timestamp: datetime,
        request_id: str | None,
    ) -> None:
        for session in self._sessions.values():
            if (
                session.principal_id == principal_id
                and session.revoked_at is None
                and self._session_expired(session, timestamp)
            ):
                self._expire_session(session, timestamp=timestamp, request_id=request_id)

    def _require_current_session(
        self,
        *,
        principal_id: str,
        current_token_hash: str,
        request_id: str,
    ) -> tuple[AccountCredential, _OpaqueSessionRecord, datetime]:
        account = self._accounts.get(principal_id)
        session = self._sessions.get(current_token_hash)
        if (
            account is None
            or session is None
            or session.principal_id != principal_id
            or session.revoked_at is not None
        ):
            raise _session_invalid()
        timestamp = _utc_timestamp(self._clock)
        if self._session_expired(session, timestamp):
            self._expire_session(session, timestamp=timestamp, request_id=request_id)
            raise _session_invalid()
        if account.principal.status is not AccountStatus.ACTIVE:
            self._revoke_invalid_session(
                session,
                reason="ACCOUNT_DISABLED",
                timestamp=timestamp,
                request_id=request_id,
            )
            raise _session_invalid()
        if session.credential_revision != account.credential_revision:
            self._revoke_invalid_session(
                session,
                reason="PASSWORD_CHANGED",
                timestamp=timestamp,
                request_id=request_id,
            )
            raise _session_invalid()
        return account, session, timestamp

    def _expire_session(
        self,
        session: _OpaqueSessionRecord,
        *,
        timestamp: datetime,
        request_id: str | None,
    ) -> None:
        session.revoked_at = timestamp
        session.revocation_reason = "EXPIRED"
        self._append_audit(
            scope_kind="PLATFORM",
            project_id=None,
            actor_id=session.principal_id,
            action="auth.session.expired",
            resource_type="session",
            resource_id=session.session_id,
            request_id=request_id or str(uuid4()),
            outcome="SUCCEEDED",
            safe_details={"reason": "EXPIRED"},
            occurred_at=timestamp,
        )

    def _revoke_invalid_sessions(
        self,
        *,
        account: AccountCredential,
        timestamp: datetime,
        request_id: str,
    ) -> None:
        for session in self._sessions.values():
            if (
                session.principal_id == account.principal.principal_id
                and session.revoked_at is None
                and session.credential_revision != account.credential_revision
            ):
                self._revoke_invalid_session(
                    session,
                    reason="PASSWORD_CHANGED",
                    timestamp=timestamp,
                    request_id=request_id,
                )

    def _revoke_invalid_session(
        self,
        session: _OpaqueSessionRecord,
        *,
        reason: str,
        timestamp: datetime,
        request_id: str,
    ) -> None:
        session.revoked_at = timestamp
        session.revocation_reason = reason
        self._append_audit(
            scope_kind="PLATFORM",
            project_id=None,
            actor_id=session.principal_id,
            action="auth.session.revoked",
            resource_type="session",
            resource_id=session.session_id,
            request_id=request_id,
            outcome="SUCCEEDED",
            safe_details={"reason": reason},
            occurred_at=timestamp,
        )

    def _session_expired(self, session: _OpaqueSessionRecord, timestamp: datetime) -> bool:
        return (
            timestamp >= session.last_seen_at + self._session_idle_ttl
            or timestamp >= session.issued_at + self._session_absolute_ttl
        )

    def record_failed_login(self, *, principal_id: str | None, request_id: str) -> None:
        with self._lock:
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal_id,
                action="auth.login.failed",
                resource_type="account",
                resource_id=principal_id or "anonymous-subject",
                request_id=request_id,
                outcome="DENIED",
            )

    def count_unread_notifications(self, *, principal_id: str) -> int:
        with self._lock:
            return sum(
                notification.state is AccountNotificationState.UNREAD
                for notification in self._notifications.values()
                if self._notification_recipients.get(notification.notification_id) == principal_id
            )

    def list_notifications(
        self,
        *,
        principal_id: str,
        state: AccountNotificationState | None,
        before: tuple[datetime, str] | None,
        limit: int,
    ) -> tuple[AccountNotification, ...]:
        with self._lock:
            owned = tuple(
                notification
                for notification in self._notifications.values()
                if self._notification_recipients.get(notification.notification_id) == principal_id
                and (state is None or notification.state is state)
                and (
                    before is None
                    or (notification.created_at, notification.notification_id) < before
                )
            )
        return tuple(
            sorted(owned, key=lambda item: (item.created_at, item.notification_id), reverse=True)[
                :limit
            ]
        )

    def mark_notification_read(
        self, *, principal_id: str, notification_id: str, request_id: str
    ) -> bool:
        with self._lock:
            notification = self._notifications.get(notification_id)
            if (
                notification is None
                or self._notification_recipients.get(notification_id) != principal_id
            ):
                raise _not_found("notification")
            if notification.state is AccountNotificationState.READ:
                return False
            timestamp = _now()
            self._notifications[notification_id] = notification.model_copy(
                update={"state": AccountNotificationState.READ, "read_at": timestamp}
            )
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal_id,
                action="account.notification.read",
                resource_type="account_notification",
                resource_id=notification_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"kind": notification.kind.value},
                occurred_at=timestamp,
            )
            return True

    def create_membership_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest:
        payload = {
            "organization_id": organization_id,
            "project_id": project_id,
            "reason": reason,
        }
        with self._lock:
            if auth.subject_id not in self._accounts:
                raise _account_principal_required()
            repeated = self._repeat(auth.subject_id, "membership.create", idempotency_key, payload)
            if repeated is not None:
                return self._membership_requests[repeated]
            if (auth.subject_id, organization_id, project_id) in self._memberships:
                raise problem(
                    status=409,
                    code="MEMBERSHIP_ALREADY_ACTIVE",
                    title="Membership already active",
                    detail="The principal is already a member of this project.",
                )
            if any(
                item.organization_id == organization_id
                and item.project_id == project_id
                and item.requester_id == auth.subject_id
                and item.status is AccessRequestStatus.PENDING
                for item in self._membership_requests.values()
            ):
                raise problem(
                    status=409,
                    code="MEMBERSHIP_REQUEST_ALREADY_PENDING",
                    title="Membership request already pending",
                    detail="A pending membership request already exists for this project.",
                )
            timestamp = _now()
            item = MembershipRequest(
                request_id=str(uuid4()),
                organization_id=organization_id,
                project_id=project_id,
                requester_id=auth.subject_id,
                status=AccessRequestStatus.PENDING,
                reason=reason,
                created_at=timestamp,
                updated_at=timestamp,
                revision=1,
            )
            self._membership_requests[item.request_id] = item
            self._remember(
                auth.subject_id, "membership.create", idempotency_key, payload, item.request_id
            )
            self._audit_request(item, auth.subject_id, "access.membership.requested", request_id)
            return item

    def list_membership_requests(
        self, *, auth: AuthContext, organization_id: str, project_id: str
    ) -> tuple[MembershipRequest, ...]:
        with self._lock:
            manager = can_manage_project(auth, project_id, organization_id)
            return tuple(
                sorted(
                    (
                        item
                        for item in self._membership_requests.values()
                        if item.organization_id == organization_id
                        and item.project_id == project_id
                        and (manager or item.requester_id == auth.subject_id)
                    ),
                    key=lambda item: (item.created_at, item.request_id),
                    reverse=True,
                )
            )

    def get_membership_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
    ) -> MembershipRequest:
        with self._lock:
            item = self._membership_requests.get(access_request_id)
            if (
                item is None
                or item.organization_id != organization_id
                or item.project_id != project_id
            ):
                raise _not_found("membership_request")
            if item.requester_id != auth.subject_id and not can_manage_project(
                auth, project_id, organization_id
            ):
                raise _not_found("membership_request")
            return item

    def decide_membership_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
        target_status: AccessRequestStatus,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest:
        if target_status not in {
            AccessRequestStatus.APPROVED,
            AccessRequestStatus.REJECTED,
            AccessRequestStatus.REVOKED,
        }:
            raise ValueError("unsupported manager transition")
        require_project_manager(auth, project_id, organization_id)
        payload = {
            "organization_id": organization_id,
            "request_id": access_request_id,
            "status": target_status.value,
            "reason": reason,
        }
        with self._lock:
            repeated = self._repeat(auth.subject_id, "membership.decide", idempotency_key, payload)
            if repeated is not None:
                return self._membership_requests[repeated]
            item = self._membership_requests.get(access_request_id)
            if (
                item is None
                or item.organization_id != organization_id
                or item.project_id != project_id
            ):
                raise _not_found("membership_request")
            if item.status is target_status:
                self._remember(
                    auth.subject_id,
                    "membership.decide",
                    idempotency_key,
                    payload,
                    item.request_id,
                )
                return item
            expected = (
                AccessRequestStatus.APPROVED
                if target_status is AccessRequestStatus.REVOKED
                else AccessRequestStatus.PENDING
            )
            if item.status is not expected:
                raise _transition_conflict(item.status)
            updated = item.model_copy(
                update={
                    "status": target_status,
                    "decided_by": auth.subject_id,
                    "decision_reason": reason,
                    "updated_at": _now(),
                    "revision": item.revision + 1,
                }
            )
            self._membership_requests[item.request_id] = updated
            if target_status is AccessRequestStatus.APPROVED:
                self._memberships.add((item.requester_id, organization_id, project_id))
                self._bump_revision(item.requester_id)
            elif target_status is AccessRequestStatus.REVOKED:
                self._memberships.discard((item.requester_id, organization_id, project_id))
                self._revoke_capability_requests(
                    item.requester_id, organization_id, project_id, auth.subject_id
                )
                self._bump_revision(item.requester_id)
            self._remember(
                auth.subject_id,
                "membership.decide",
                idempotency_key,
                payload,
                item.request_id,
            )
            action = {
                AccessRequestStatus.APPROVED: "access.membership.approved",
                AccessRequestStatus.REJECTED: "access.membership.rejected",
                AccessRequestStatus.REVOKED: "access.membership.revoked",
            }[target_status]
            self._audit_request(updated, auth.subject_id, action, request_id)
            self._create_notification(
                recipient_id=updated.requester_id,
                organization_id=updated.organization_id,
                project_id=updated.project_id,
                resource_type=AccountNotificationResourceType.ACCESS_REQUEST,
                resource_id=updated.request_id,
                event_key=f"access-request:MEMBERSHIP_{target_status.value}:{updated.request_id}",
                kind=AccountNotificationKind[f"MEMBERSHIP_{target_status.value}"],
            )
            return updated

    def withdraw_membership_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest:
        payload = {
            "organization_id": organization_id,
            "request_id": access_request_id,
            "reason": reason,
        }
        with self._lock:
            repeated = self._repeat(
                auth.subject_id, "membership.withdraw", idempotency_key, payload
            )
            if repeated is not None:
                return self._membership_requests[repeated]
            item = self._membership_requests.get(access_request_id)
            if (
                item is None
                or item.organization_id != organization_id
                or item.project_id != project_id
                or item.requester_id != auth.subject_id
            ):
                raise _not_found("membership_request")
            if item.status is AccessRequestStatus.WITHDRAWN:
                return item
            if item.status is not AccessRequestStatus.PENDING:
                raise _transition_conflict(item.status)
            updated = item.model_copy(
                update={
                    "status": AccessRequestStatus.WITHDRAWN,
                    "decision_reason": reason,
                    "updated_at": _now(),
                    "revision": item.revision + 1,
                }
            )
            self._membership_requests[item.request_id] = updated
            self._remember(
                auth.subject_id,
                "membership.withdraw",
                idempotency_key,
                payload,
                item.request_id,
            )
            self._audit_request(updated, auth.subject_id, "access.membership.withdrawn", request_id)
            return updated

    def create_capability_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        capability_keys: tuple[str, ...],
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> CapabilityRequest:
        payload = {
            "organization_id": organization_id,
            "project_id": project_id,
            "capability_keys": list(capability_keys),
            "reason": reason,
        }
        with self._lock:
            if auth.subject_id not in self._accounts:
                raise _account_principal_required()
            repeated = self._repeat(auth.subject_id, "capability.create", idempotency_key, payload)
            if repeated is not None:
                return self._capability_requests[repeated]
            timestamp = _now()
            item = CapabilityRequest(
                request_id=str(uuid4()),
                organization_id=organization_id,
                project_id=project_id,
                requester_id=auth.subject_id,
                capability_keys=capability_keys,
                status=AccessRequestStatus.PENDING,
                reason=reason,
                created_at=timestamp,
                updated_at=timestamp,
                revision=1,
            )
            self._capability_requests[item.request_id] = item
            self._remember(
                auth.subject_id, "capability.create", idempotency_key, payload, item.request_id
            )
            self._audit_capability(item, auth.subject_id, "access.capability.requested", request_id)
            return item

    def list_capability_requests(
        self, *, auth: AuthContext, organization_id: str, project_id: str
    ) -> tuple[CapabilityRequest, ...]:
        with self._lock:
            manager = can_manage_project(auth, project_id, organization_id)
            return tuple(
                sorted(
                    (
                        item
                        for item in self._capability_requests.values()
                        if item.organization_id == organization_id
                        and item.project_id == project_id
                        and (manager or item.requester_id == auth.subject_id)
                    ),
                    key=lambda item: (item.created_at, item.request_id),
                    reverse=True,
                )
            )

    def get_capability_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
    ) -> CapabilityRequest:
        with self._lock:
            item = self._capability_requests.get(access_request_id)
            if (
                item is None
                or item.organization_id != organization_id
                or item.project_id != project_id
            ):
                raise _not_found("capability_request")
            if item.requester_id != auth.subject_id and not can_manage_project(
                auth, project_id, organization_id
            ):
                raise _not_found("capability_request")
            return item

    def decide_capability_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
        target_status: AccessRequestStatus,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> CapabilityRequest:
        if target_status not in {
            AccessRequestStatus.APPROVED,
            AccessRequestStatus.REJECTED,
            AccessRequestStatus.REVOKED,
        }:
            raise ValueError("unsupported manager transition")
        require_project_manager(auth, project_id, organization_id)
        payload = {
            "organization_id": organization_id,
            "request_id": access_request_id,
            "status": target_status.value,
            "reason": reason,
        }
        with self._lock:
            repeated = self._repeat(auth.subject_id, "capability.decide", idempotency_key, payload)
            if repeated is not None:
                return self._capability_requests[repeated]
            item = self._capability_requests.get(access_request_id)
            if (
                item is None
                or item.organization_id != organization_id
                or item.project_id != project_id
            ):
                raise _not_found("capability_request")
            if item.status is target_status:
                self._remember(
                    auth.subject_id,
                    "capability.decide",
                    idempotency_key,
                    payload,
                    item.request_id,
                )
                return item
            expected = (
                AccessRequestStatus.APPROVED
                if target_status is AccessRequestStatus.REVOKED
                else AccessRequestStatus.PENDING
            )
            if item.status is not expected:
                raise _transition_conflict(item.status)
            updated = item.model_copy(
                update={
                    "status": target_status,
                    "decided_by": auth.subject_id,
                    "decision_reason": reason,
                    "updated_at": _now(),
                    "revision": item.revision + 1,
                }
            )
            self._capability_requests[item.request_id] = updated
            if target_status in {
                AccessRequestStatus.APPROVED,
                AccessRequestStatus.REVOKED,
            }:
                self._bump_revision(item.requester_id)
            self._remember(
                auth.subject_id,
                "capability.decide",
                idempotency_key,
                payload,
                item.request_id,
            )
            action = {
                AccessRequestStatus.APPROVED: "access.capability.approved",
                AccessRequestStatus.REJECTED: "access.capability.rejected",
                AccessRequestStatus.REVOKED: "access.capability.revoked",
            }[target_status]
            self._audit_capability(updated, auth.subject_id, action, request_id)
            self._create_notification(
                recipient_id=updated.requester_id,
                organization_id=updated.organization_id,
                project_id=updated.project_id,
                resource_type=AccountNotificationResourceType.ACCESS_REQUEST,
                resource_id=updated.request_id,
                event_key=f"access-request:CAPABILITY_{target_status.value}:{updated.request_id}",
                kind=AccountNotificationKind[f"CAPABILITY_{target_status.value}"],
            )
            return updated

    def withdraw_capability_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> CapabilityRequest:
        payload = {
            "organization_id": organization_id,
            "request_id": access_request_id,
            "reason": reason,
        }
        with self._lock:
            repeated = self._repeat(
                auth.subject_id, "capability.withdraw", idempotency_key, payload
            )
            if repeated is not None:
                return self._capability_requests[repeated]
            item = self._capability_requests.get(access_request_id)
            if (
                item is None
                or item.organization_id != organization_id
                or item.project_id != project_id
                or item.requester_id != auth.subject_id
            ):
                raise _not_found("capability_request")
            if item.status is AccessRequestStatus.WITHDRAWN:
                return item
            if item.status is not AccessRequestStatus.PENDING:
                raise _transition_conflict(item.status)
            updated = item.model_copy(
                update={
                    "status": AccessRequestStatus.WITHDRAWN,
                    "decision_reason": reason,
                    "updated_at": _now(),
                    "revision": item.revision + 1,
                }
            )
            self._capability_requests[item.request_id] = updated
            self._remember(
                auth.subject_id,
                "capability.withdraw",
                idempotency_key,
                payload,
                item.request_id,
            )
            self._audit_capability(
                updated, auth.subject_id, "access.capability.withdrawn", request_id
            )
            return updated

    def list_audit_events(
        self, *, auth: AuthContext, organization_id: str, project_id: str
    ) -> tuple[AccessAuditEvent, ...]:
        require_project_manager(auth, project_id, organization_id)
        with self._lock:
            return tuple(
                sorted(
                    (
                        event
                        for event in self._audit
                        if event.organization_id == organization_id
                        and event.project_id == project_id
                    ),
                    key=lambda event: (event.occurred_at, event.event_id),
                    reverse=True,
                )
            )

    def list_managed_accounts(
        self,
        *,
        query: str | None,
        state: ManagedAccountState | None,
        role: PlatformAccountRole | None,
        page: int,
        page_size: int,
    ) -> ManagedAccountPage:
        with self._lock:
            normalized_query = query.casefold() if query is not None else None
            items = [
                self._managed_account(account)
                for account in self._accounts.values()
                if self._managed_account_matches(
                    account,
                    query=normalized_query,
                    state=state,
                    role=role,
                )
            ]
            items.sort(key=lambda item: (item.updated_at, item.principal_id), reverse=True)
            start = (page - 1) * page_size
            return ManagedAccountPage(
                items=tuple(items[start : start + page_size]),
                page=page,
                page_size=page_size,
                total=len(items),
            )

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
    ) -> ManagedAccount:
        with self._lock:
            if canonical_username in self._username_index or (
                recovery_email is not None
                and any(item.recovery_email == recovery_email for item in self._accounts.values())
            ):
                raise problem(
                    status=409,
                    code="ACCOUNT_CREATE_CONFLICT",
                    title="Account could not be created",
                    detail="The username or recovery email is already in use.",
                )
            timestamp = _utc_timestamp(self._clock)
            principal_id = str(uuid4())
            principal = AccountPrincipal(
                principal_id=principal_id,
                username=display_username,
                display_name=display_name,
                status=AccountStatus.ACTIVE,
                created_at=timestamp,
            )
            account = AccountCredential(
                principal=principal,
                canonical_username=canonical_username,
                password_hash=password_hash,
                capability_revision=1 if platform_role is PlatformAccountRole.PLATFORM_ADMIN else 0,
                account_revision=1,
                credential_revision=1,
                updated_at=timestamp,
                password_changed_at=timestamp,
                recovery_email=recovery_email,
            )
            self._accounts[principal_id] = account
            self._username_index[canonical_username] = principal_id
            if platform_role is PlatformAccountRole.PLATFORM_ADMIN:
                self._platform_capabilities[principal_id] = set(PLATFORM_ADMIN_CAPABILITIES)
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=actor_id,
                action="platform.account.created",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"platform_role": platform_role.value},
                occurred_at=timestamp,
            )
            return self._managed_account(account)

    def set_managed_account_status(
        self,
        *,
        principal_id: str,
        target_status: AccountStatus,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> ManagedAccount:
        with self._lock:
            account = self._active_managed_credential(principal_id)
            self._require_expected_account_revision(account, expected_revision)
            if account.principal.status is target_status:
                return self._managed_account(account)
            if (
                target_status is AccountStatus.DISABLED
                and self._is_platform_admin(principal_id)
                and self._active_platform_admin_count() <= 1
            ):
                raise _last_platform_admin_conflict()
            timestamp = _utc_timestamp(self._clock)
            updated = replace(
                account,
                principal=account.principal.model_copy(update={"status": target_status}),
                capability_revision=account.capability_revision + 1,
                account_revision=account.account_revision + 1,
                updated_at=timestamp,
            )
            self._accounts[principal_id] = updated
            if target_status is AccountStatus.DISABLED:
                self._revoke_managed_sessions(principal_id, timestamp)
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=actor_id,
                action=(
                    "platform.account.disabled"
                    if target_status is AccountStatus.DISABLED
                    else "platform.account.enabled"
                ),
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                occurred_at=timestamp,
            )
            return self._managed_account(updated)

    def set_managed_account_role(
        self,
        *,
        principal_id: str,
        platform_role: PlatformAccountRole,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> ManagedAccount:
        with self._lock:
            account = self._active_managed_credential(principal_id)
            self._require_expected_account_revision(account, expected_revision)
            current_role = self._platform_role(principal_id)
            if current_role is platform_role:
                return self._managed_account(account)
            if (
                current_role is PlatformAccountRole.PLATFORM_ADMIN
                and self._active_platform_admin_count() <= 1
            ):
                raise _last_platform_admin_conflict()
            if platform_role is PlatformAccountRole.PLATFORM_ADMIN:
                self._platform_capabilities[principal_id] = set(PLATFORM_ADMIN_CAPABILITIES)
            else:
                self._platform_capabilities.pop(principal_id, None)
            timestamp = _utc_timestamp(self._clock)
            updated = replace(
                account,
                capability_revision=account.capability_revision + 1,
                account_revision=account.account_revision + 1,
                updated_at=timestamp,
            )
            self._accounts[principal_id] = updated
            self._revoke_managed_sessions(principal_id, timestamp)
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=actor_id,
                action="platform.account.role_changed",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"platform_role": platform_role.value},
                occurred_at=timestamp,
            )
            return self._managed_account(updated)

    def reset_managed_account_password(
        self,
        *,
        principal_id: str,
        new_password_hash: str,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> tuple[ManagedAccount, int]:
        with self._lock:
            account = self._active_managed_credential(principal_id)
            self._require_expected_account_revision(account, expected_revision)
            timestamp = _utc_timestamp(self._clock)
            revoked = self._revoke_managed_sessions(principal_id, timestamp)
            updated = replace(
                account,
                password_hash=new_password_hash,
                capability_revision=account.capability_revision + 1,
                account_revision=account.account_revision + 1,
                credential_revision=account.credential_revision + 1,
                updated_at=timestamp,
                password_changed_at=timestamp,
            )
            self._accounts[principal_id] = updated
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=actor_id,
                action="platform.account.password_reset",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"sessions_revoked": revoked},
                occurred_at=timestamp,
            )
            return self._managed_account(updated), revoked

    def delete_managed_account(
        self,
        *,
        principal_id: str,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> None:
        with self._lock:
            account = self._active_managed_credential(principal_id)
            self._require_expected_account_revision(account, expected_revision)
            if self._is_platform_admin(principal_id) and self._active_platform_admin_count() <= 1:
                raise _last_platform_admin_conflict()
            timestamp = _utc_timestamp(self._clock)
            self._revoke_managed_sessions(principal_id, timestamp)
            self._platform_capabilities.pop(principal_id, None)
            updated = replace(
                account,
                principal=account.principal.model_copy(update={"status": AccountStatus.DISABLED}),
                capability_revision=account.capability_revision + 1,
                account_revision=account.account_revision + 1,
                updated_at=timestamp,
            )
            self._accounts[principal_id] = updated
            self._deleted_accounts[principal_id] = timestamp
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=actor_id,
                action="platform.account.deleted",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"deletion_mode": "SOFT"},
                occurred_at=timestamp,
            )

    def grant_platform_admin_for_test(self, principal_id: str) -> None:
        """Testing seam for issuing the global platform-admin bundle."""

        with self._lock:
            account = self._active_managed_credential(principal_id)
            self._platform_capabilities[principal_id] = set(PLATFORM_ADMIN_CAPABILITIES)
            self._accounts[principal_id] = replace(
                account, capability_revision=account.capability_revision + 1
            )

    def _managed_account_matches(
        self,
        account: AccountCredential,
        *,
        query: str | None,
        state: ManagedAccountState | None,
        role: PlatformAccountRole | None,
    ) -> bool:
        item = self._managed_account(account)
        if state is not None and item.state is not state:
            return False
        if state is None and item.state is ManagedAccountState.DELETED:
            return False
        if role is not None and item.platform_role is not role:
            return False
        if query is None:
            return True
        fields = (
            item.principal_id,
            item.username,
            item.display_name,
            account.recovery_email or "",
        )
        return any(query in field.casefold() for field in fields)

    def _managed_account(self, account: AccountCredential) -> ManagedAccount:
        principal_id = account.principal.principal_id
        deleted_at = self._deleted_accounts.get(principal_id)
        if deleted_at is not None:
            state = ManagedAccountState.DELETED
        else:
            state = ManagedAccountState(account.principal.status.value)
        active_session_count = sum(
            1
            for session in self._sessions.values()
            if session.principal_id == principal_id and session.revoked_at is None
        )
        return ManagedAccount(
            principal_id=principal_id,
            username=account.principal.username,
            display_name=account.principal.display_name,
            state=state,
            platform_role=self._platform_role(principal_id),
            recovery_email_configured=account.recovery_email is not None,
            recovery_email_hint=recovery_email_hint(account.recovery_email),
            active_session_count=active_session_count,
            created_at=account.principal.created_at,
            updated_at=account.updated_at,
            password_changed_at=account.password_changed_at,
            deleted_at=deleted_at,
            revision=account.account_revision,
            etag=f'"v{account.account_revision}"',
        )

    def _active_managed_credential(self, principal_id: str) -> AccountCredential:
        account = self._accounts.get(principal_id)
        if account is None or principal_id in self._deleted_accounts:
            raise _not_found("account")
        return account

    @staticmethod
    def _require_expected_account_revision(
        account: AccountCredential, expected_revision: int
    ) -> None:
        if account.account_revision != expected_revision:
            raise _account_revision_conflict(account.account_revision)

    def _platform_role(self, principal_id: str) -> PlatformAccountRole:
        return (
            PlatformAccountRole.PLATFORM_ADMIN
            if PLATFORM_ADMIN_CAPABILITIES.issubset(
                self._platform_capabilities.get(principal_id, set())
            )
            else PlatformAccountRole.USER
        )

    def _is_platform_admin(self, principal_id: str) -> bool:
        return self._platform_role(principal_id) is PlatformAccountRole.PLATFORM_ADMIN

    def _active_platform_admin_count(self) -> int:
        return sum(
            1
            for principal_id, account in self._accounts.items()
            if principal_id not in self._deleted_accounts
            and account.principal.status is AccountStatus.ACTIVE
            and self._is_platform_admin(principal_id)
        )

    def _revoke_managed_sessions(self, principal_id: str, timestamp: datetime) -> int:
        revoked = 0
        for session in self._sessions.values():
            if session.principal_id == principal_id and session.revoked_at is None:
                session.revoked_at = timestamp
                session.revocation_reason = "ADMIN_REVOKED"
                revoked += 1
        return revoked

    @staticmethod
    def _profile_record(account: AccountCredential) -> AccountProfileRecord:
        return AccountProfileRecord(
            principal_id=account.principal.principal_id,
            username=account.principal.username,
            display_name=account.principal.display_name,
            status=account.principal.status,
            created_at=account.principal.created_at,
            updated_at=account.updated_at,
            password_changed_at=account.password_changed_at,
            revision=account.account_revision,
        )

    def _repeat_account_command(
        self,
        principal_id: str,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[AccountProfileRecord, int] | None:
        previous = self._account_idempotency.get((principal_id, operation, idempotency_key))
        if previous is None:
            return None
        previous_fingerprint, profile, count = previous
        if not secrets.compare_digest(previous_fingerprint, request_fingerprint):
            raise problem(
                status=409,
                code="IDEMPOTENCY_KEY_REUSED",
                title="Idempotency key reused",
                detail="The idempotency key was already used with a different request.",
            )
        return profile, count

    def _remember_account_command(
        self,
        principal_id: str,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
        profile: AccountProfileRecord,
        other_sessions_revoked: int,
    ) -> None:
        self._account_idempotency[(principal_id, operation, idempotency_key)] = (
            request_fingerprint,
            profile,
            other_sessions_revoked,
        )

    def _repeat(
        self,
        actor_id: str,
        operation: str,
        idempotency_key: str,
        payload: Mapping[str, object],
    ) -> str | None:
        key = (actor_id, operation, idempotency_key)
        previous = self._idempotency.get(key)
        if previous is None:
            return None
        fingerprint, resource_id = previous
        if fingerprint != _fingerprint(payload):
            raise problem(
                status=409,
                code="IDEMPOTENCY_KEY_REUSED",
                title="Idempotency key reused",
                detail="The idempotency key was already used with a different request.",
            )
        return resource_id

    def _remember(
        self,
        actor_id: str,
        operation: str,
        idempotency_key: str,
        payload: Mapping[str, object],
        resource_id: str,
    ) -> None:
        self._idempotency[(actor_id, operation, idempotency_key)] = (
            _fingerprint(payload),
            resource_id,
        )

    def _bump_revision(self, principal_id: str) -> None:
        account = self._accounts.get(principal_id)
        if account is None:
            return
        self._accounts[principal_id] = AccountCredential(
            principal=account.principal,
            canonical_username=account.canonical_username,
            password_hash=account.password_hash,
            capability_revision=account.capability_revision + 1,
            account_revision=account.account_revision,
            credential_revision=account.credential_revision,
            updated_at=account.updated_at,
            password_changed_at=account.password_changed_at,
            recovery_email=account.recovery_email,
        )

    def _consume_active_security_challenges(
        self,
        *,
        principal_id: str,
        purpose: str,
        timestamp: datetime,
    ) -> None:
        for challenge in self._account_security_challenges.values():
            if (
                challenge.principal_id == principal_id
                and challenge.purpose == purpose
                and challenge.consumed_at is None
            ):
                challenge.consumed_at = timestamp

    def _revoke_capability_requests(
        self,
        principal_id: str,
        organization_id: str,
        project_id: str,
        actor_id: str,
    ) -> None:
        timestamp = _now()
        for request_id, item in tuple(self._capability_requests.items()):
            if (
                item.requester_id == principal_id
                and item.organization_id == organization_id
                and item.project_id == project_id
                and item.status is AccessRequestStatus.APPROVED
            ):
                self._capability_requests[request_id] = item.model_copy(
                    update={
                        "status": AccessRequestStatus.REVOKED,
                        "decided_by": actor_id,
                        "updated_at": timestamp,
                        "revision": item.revision + 1,
                    }
                )

    def _audit_request(
        self, item: MembershipRequest, actor_id: str, action: str, request_id: str
    ) -> None:
        self._append_audit(
            scope_kind="PROJECT",
            organization_id=item.organization_id,
            project_id=item.project_id,
            actor_id=actor_id,
            action=action,
            resource_type="membership_request",
            resource_id=item.request_id,
            request_id=request_id,
            outcome="SUCCEEDED",
            safe_details={"status": item.status.value, "requester_id": item.requester_id},
        )

    def _create_notification(
        self,
        *,
        recipient_id: str,
        organization_id: str,
        project_id: str,
        resource_type: AccountNotificationResourceType,
        resource_id: str,
        event_key: str,
        kind: AccountNotificationKind,
    ) -> None:
        # Decisions are serialized by the same request lock; this id is only
        # created after a real transition, so idempotent repeats cannot emit a
        # second recipient event.
        if (recipient_id, event_key) in self._notification_event_keys:
            return
        notification_id = str(uuid4())
        created = _now()
        self._notifications[notification_id] = AccountNotification(
            notification_id=notification_id,
            kind=kind,
            organization_id=organization_id,
            project_id=project_id,
            resource_type=resource_type,
            resource_id=resource_id,
            state=AccountNotificationState.UNREAD,
            created_at=created,
        )
        self._notification_recipients[notification_id] = recipient_id
        self._notification_event_keys.add((recipient_id, event_key))

    def _audit_capability(
        self, item: CapabilityRequest, actor_id: str, action: str, request_id: str
    ) -> None:
        self._append_audit(
            scope_kind="PROJECT",
            organization_id=item.organization_id,
            project_id=item.project_id,
            actor_id=actor_id,
            action=action,
            resource_type="capability_request",
            resource_id=item.request_id,
            request_id=request_id,
            outcome="SUCCEEDED",
            safe_details={
                "status": item.status.value,
                "requester_id": item.requester_id,
                "capability_keys": list(item.capability_keys),
            },
        )

    def _append_audit(
        self,
        *,
        scope_kind: str,
        organization_id: str | None = None,
        project_id: str | None,
        actor_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
        outcome: str,
        safe_details: dict[str, object] | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        self._audit.append(
            AccessAuditEvent(
                event_id=str(uuid4()),
                scope_kind=scope_kind,
                organization_id=organization_id,
                project_id=project_id,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=request_id,
                outcome=outcome,
                safe_details=safe_details or {},
                occurred_at=occurred_at or _now(),
            )
        )
