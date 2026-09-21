"""Application service for accounts, sessions, memberships, and capability requests."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import unicodedata
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable
from datetime import datetime, timezone
from typing import NoReturn, Protocol

from hc_data_platform.core.context import session_mutations_allowed
from hc_data_platform.core.errors import ProblemException, problem

from .abuse import AbuseProtection, PublicAuthAttempt, UnconfiguredAbuseProtection
from .access_models import (
    AccessAuditEventList,
    AccessDecisionCommand,
    AccessRequestStatus,
    AccountAccessOverview,
    AccountAccessRequest,
    AccountNotificationPage,
    AccountNotificationState,
    AccountNotificationUnreadCount,
    AccountProfileRecord,
    AccountProfileUpdate,
    AccountSettings,
    AvailableOrganization,
    AvailableScope,
    CapabilityRequest,
    CapabilityRequestCreate,
    CapabilityRequestList,
    LoginCommand,
    MembershipRequest,
    MembershipRequestCreate,
    MembershipRequestList,
    OrganizationMembershipRequestCreate,
    PasswordChangeCommand,
    PasswordChangeResult,
    PublicAuthConfiguration,
    RegistrationCommand,
    RegistrationResult,
    ResolvedSession,
    SessionBootstrap,
    SessionCreated,
    SessionIssueCapacityRejected,
    SessionIssued,
    SessionIssueStaleCredentials,
)
from .access_repository import AccessRepository
from .auth import AuthContext
from .capabilities import (
    CAPABILITY_PLATFORM_ACCOUNT_SECURITY_MANAGE,
    expand_data_workflow_capabilities,
)
from .challenge import (
    DisabledPublicAuthChallengeVerifier,
    PublicAuthChallengeVerifier,
)
from .passwords import PasswordHasher, PasswordPolicy
from .versioning import ResourceVersion


class CapabilityRequestAdmissionPolicy(Protocol):
    """OPEN-03 extension point for deciding whether membership is a submit prerequisite."""

    def check(self, *, auth: AuthContext, organization_id: str, project_id: str) -> None: ...


class UnconfiguredCapabilityRequestAdmissionPolicy:
    def check(self, *, auth: AuthContext, organization_id: str, project_id: str) -> None:
        del auth, organization_id, project_id


class AccessService:
    TOKEN_PREFIX = "hcs_"

    def __init__(
        self,
        repository: AccessRepository,
        *,
        password_hasher: PasswordHasher | None = None,
        password_policy: PasswordPolicy | None = None,
        abuse_protection: AbuseProtection | None = None,
        challenge_verifier: PublicAuthChallengeVerifier | None = None,
        capability_request_admission: CapabilityRequestAdmissionPolicy | None = None,
        scope_region_resolver: Callable[[AvailableScope], tuple[str, ...]] | None = None,
    ) -> None:
        self._repository = repository
        self._scope_region_resolver = scope_region_resolver
        self._password_hasher = password_hasher or PasswordHasher()
        self._password_policy = password_policy or PasswordPolicy()
        self._abuse_protection = abuse_protection or UnconfiguredAbuseProtection()
        self._challenge_verifier = challenge_verifier or DisabledPublicAuthChallengeVerifier()
        self._capability_request_admission = (
            capability_request_admission or UnconfiguredCapabilityRequestAdmissionPolicy()
        )
        # A real hash keeps the unknown-user path on the same password-verification primitive.
        self._dummy_password_hash = self._password_hasher.hash(secrets.token_urlsafe(24))

    @property
    def repository(self) -> AccessRepository:
        """Expose the composed repository to adjacent account-security services."""

        return self._repository

    @staticmethod
    def canonical_username(username: str) -> str:
        return unicodedata.normalize("NFKC", username).casefold()

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def register(
        self,
        command: RegistrationCommand,
        *,
        request_id: str,
        source_network: str | None = None,
        challenge_response: str | None = None,
    ) -> RegistrationResult:
        canonical = self.canonical_username(command.username)
        attempt = PublicAuthAttempt(
            operation="REGISTER",
            source_network=source_network or "unattributed",
            subject_hint=canonical,
        )
        challenge_required = self._abuse_protection.check(attempt, request_id=request_id)
        self._verify_challenge_if_required(
            attempt,
            challenge_required=challenge_required,
            response_token=challenge_response,
            request_id=request_id,
        )
        password = command.password.get_secret_value()
        self._password_policy.require(password, canonical_username=canonical)
        password_hash = self._password_hasher.hash(password)
        principal = self._repository.register_account(
            canonical_username=canonical,
            display_username=command.username,
            password_hash=password_hash,
            request_id=request_id,
        )
        return RegistrationResult(principal=principal)

    def login(
        self,
        command: LoginCommand,
        *,
        request_id: str,
        source_network: str | None = None,
        challenge_response: str | None = None,
    ) -> SessionCreated:
        canonical = self.canonical_username(command.username)
        attempt = PublicAuthAttempt(
            operation="LOGIN",
            source_network=source_network or "unattributed",
            subject_hint=canonical,
        )
        challenge_required = self._abuse_protection.check(attempt, request_id=request_id)
        self._verify_challenge_if_required(
            attempt,
            challenge_required=challenge_required,
            response_token=challenge_response,
            request_id=request_id,
        )
        credential = self._repository.credential_for_username(canonical)
        password = command.password.get_secret_value()
        encoded = self._dummy_password_hash if credential is None else credential.password_hash
        valid, upgraded_hash = self._password_hasher.verify_and_rehash(password, encoded)
        if credential is None or not valid or credential.principal.status.value != "ACTIVE":
            self._reject_login(
                principal_id=None if credential is None else credential.principal.principal_id,
                attempt=attempt,
                request_id=request_id,
            )
        if upgraded_hash is not None:
            self._repository.upgrade_password_hash(
                principal_id=credential.principal.principal_id,
                expected_password_hash=credential.password_hash,
                upgraded_password_hash=upgraded_hash,
                request_id=request_id,
            )
            current = self._repository.credential_for_principal(credential.principal.principal_id)
            if (
                current is None
                or current.principal.status.value != "ACTIVE"
                or current.credential_revision != credential.credential_revision
                or not self._password_hasher.verify(password, current.password_hash)
            ):
                self._reject_login(
                    principal_id=credential.principal.principal_id,
                    attempt=attempt,
                    request_id=request_id,
                )
            credential = current
        # A valid credential resets the preceding failure window before a session is issued.
        # This keeps a later infrastructure failure from consuming a session slot while also
        # making a capacity rejection an authentication success for lockout purposes.
        self._abuse_protection.record_login_success(attempt, request_id=request_id)
        token = self.TOKEN_PREFIX + secrets.token_urlsafe(32)
        issue = self._repository.create_session(
            principal_id=credential.principal.principal_id,
            token_hash=self.token_hash(token),
            expected_password_hash=credential.password_hash,
            expected_credential_revision=credential.credential_revision,
            request_id=request_id,
        )
        if isinstance(issue, SessionIssueStaleCredentials):
            self._reject_login(
                principal_id=credential.principal.principal_id,
                attempt=attempt,
                request_id=request_id,
            )
        if isinstance(issue, SessionIssueCapacityRejected):
            raise problem(
                status=429,
                code="SESSION_LIMIT_REACHED",
                title="Active session limit reached",
                detail="This account already has the maximum number of active sessions.",
                retryable=True,
                retry_after_seconds=issue.retry_after_seconds,
            )
        if not isinstance(issue, SessionIssued):
            raise RuntimeError("repository returned an unknown session issue result")
        return SessionCreated(
            access_token=token,
            token_type="Bearer",
            principal=credential.principal,
            capability_revision=credential.capability_revision,
        )

    def _reject_login(
        self,
        *,
        principal_id: str | None,
        attempt: PublicAuthAttempt,
        request_id: str,
    ) -> NoReturn:
        self._abuse_protection.record_login_failure(
            attempt,
            principal_id=principal_id,
            request_id=request_id,
        )
        self._repository.record_failed_login(
            principal_id=principal_id,
            request_id=request_id,
        )
        raise problem(
            status=401,
            code="INVALID_CREDENTIALS",
            title="Authentication failed",
            detail="The username or password is incorrect.",
        )

    def _verify_challenge_if_required(
        self,
        attempt: PublicAuthAttempt,
        *,
        challenge_required: bool,
        response_token: str | None,
        request_id: str,
    ) -> None:
        if not challenge_required:
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
        except Exception:
            self._abuse_protection.record_challenge_denied(
                attempt,
                reason="AUTH_CHALLENGE_UNAVAILABLE",
                request_id=request_id,
            )
            raise problem(
                status=503,
                code="AUTH_CHALLENGE_UNAVAILABLE",
                title="Authentication challenge unavailable",
                detail="Authentication challenge verification is temporarily unavailable.",
                retryable=True,
            ) from None

    def authenticate_access_token(
        self, token: str, *, request_id: str | None = None
    ) -> AuthContext | None:
        if not token.startswith(self.TOKEN_PREFIX):
            return None
        resolved = self._repository.resolve_session(
            self.token_hash(token),
            request_id=request_id,
            allow_session_mutation=session_mutations_allowed(),
        )
        if resolved is None:
            return None
        project_ids = frozenset(scope.project_id for scope in resolved.scopes)
        organization_ids = frozenset(scope.organization_id for scope in resolved.scopes)
        region_codes = frozenset(
            region for scope in resolved.scopes for region in scope.region_codes
        )
        scope_pairs = frozenset(
            (scope.project_id, None) if scope.project_wide else (scope.project_id, region)
            for scope in resolved.scopes
            for region in ((None,) if scope.project_wide else scope.region_codes)
        )
        organization_scope_triples = frozenset(
            (
                scope.organization_id,
                scope.project_id,
                None if scope.project_wide else region,
            )
            for scope in resolved.scopes
            for region in ((None,) if scope.project_wide else scope.region_codes)
        )
        scoped_capabilities = frozenset(
            (scope.project_id, capability)
            for scope in resolved.scopes
            for capability in scope.capabilities
        )
        organization_scoped_capabilities = frozenset(
            (scope.organization_id, scope.project_id, capability)
            for scope in resolved.scopes
            for capability in scope.capabilities
        )
        return AuthContext(
            subject_id=resolved.principal.principal_id,
            project_ids=project_ids,
            region_codes=region_codes,
            capabilities=frozenset(resolved.platform_capabilities),
            capability_revision=resolved.capability_revision,
            scope_pairs=scope_pairs,
            scoped_capabilities=scoped_capabilities,
            organization_ids=organization_ids,
            organization_scope_triples=organization_scope_triples,
            organization_scoped_capabilities=organization_scoped_capabilities,
        )

    def bootstrap(self, token: str, *, request_id: str | None = None) -> SessionBootstrap:
        if not token.startswith(self.TOKEN_PREFIX):
            raise problem(
                status=401,
                code="AUTHENTICATION_REQUIRED",
                title="Authentication required",
                detail="A current platform session is required.",
            )
        resolved = self._repository.resolve_session(
            self.token_hash(token),
            request_id=request_id,
            allow_session_mutation=session_mutations_allowed(),
        )
        if resolved is None:
            raise problem(
                status=401,
                code="SESSION_INVALID",
                title="Session invalid",
                detail="The session is revoked or no longer valid.",
            )
        return SessionBootstrap(
            principal=resolved.principal,
            available_organizations=tuple(
                AvailableOrganization(
                    organization_id=item.organization_id,
                    organization_name=item.organization_name,
                    member_status=item.member_status,
                )
                for item in self._repository.list_organization_memberships(
                    principal_id=resolved.principal.principal_id
                )
            ),
            available_scopes=tuple(
                scope.model_copy(
                    update={
                        "region_codes": (
                            self._scope_region_resolver(scope)
                            if self._scope_region_resolver is not None and scope.project_wide
                            else scope.region_codes
                        ),
                        "capabilities": tuple(
                            sorted(expand_data_workflow_capabilities(scope.capabilities))
                        ),
                    }
                )
                for scope in resolved.scopes
            ),
            platform_capabilities=resolved.platform_capabilities,
            capability_revision=resolved.capability_revision,
        )

    def public_auth_configuration(self) -> PublicAuthConfiguration:
        return PublicAuthConfiguration(
            password_policy=self._password_policy.view,
            challenge=self._challenge_verifier.configuration,
        )

    def unlock_account(
        self,
        *,
        auth: AuthContext,
        principal_id: str,
        request_id: str,
    ) -> None:
        """Administratively clear an active temporary login lock.

        The required capability is global: a project administrator must never be able to
        affect a direct account merely because that account belongs to a project elsewhere.
        The adapter returns ``False`` for an already-unlocked or unknown account so retries
        remain non-enumerating and do not create duplicate audit facts.
        """

        auth.require_capability(CAPABILITY_PLATFORM_ACCOUNT_SECURITY_MANAGE)
        self._abuse_protection.unlock_account(
            principal_id=principal_id,
            actor_id=auth.subject_id,
            request_id=request_id,
        )

    def logout(self, token: str, *, request_id: str) -> None:
        if token.startswith(self.TOKEN_PREFIX):
            self._repository.revoke_session(
                token_hash=self.token_hash(token),
                request_id=request_id,
            )

    def get_account_settings(self, token: str, *, request_id: str | None = None) -> AccountSettings:
        resolved = self._require_platform_session(token, request_id=request_id)
        return self._settings_for_record(
            self._repository.get_account_profile(resolved.principal.principal_id)
        )

    def update_account_profile(
        self,
        token: str,
        command: AccountProfileUpdate,
        *,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> AccountSettings:
        resolved = self._require_platform_session(token, request_id=request_id)
        expected_revision = ResourceVersion.from_etag(if_match).value
        fingerprint = self._session_fingerprint(
            token,
            {
                "display_name": command.display_name,
                "expected_revision": expected_revision,
            },
        )
        record = self._repository.update_account_profile(
            principal_id=resolved.principal.principal_id,
            current_token_hash=self.token_hash(token),
            display_name=command.display_name,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            request_id=request_id,
        )
        return self._settings_for_record(record)

    def change_password(
        self,
        token: str,
        command: PasswordChangeCommand,
        *,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> PasswordChangeResult:
        resolved = self._require_platform_session(token, request_id=request_id)
        principal_id = resolved.principal.principal_id
        current_password = command.current_password.get_secret_value()
        new_password = command.new_password.get_secret_value()
        expected_revision = ResourceVersion.from_etag(if_match).value
        fingerprint = self._session_fingerprint(
            token,
            {
                "current_password": current_password,
                "new_password": new_password,
                "expected_revision": expected_revision,
            },
        )
        replay = self._repository.replay_account_command(
            principal_id=principal_id,
            current_token_hash=self.token_hash(token),
            operation="account.password.change",
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            request_id=request_id,
        )
        if replay is not None:
            record, other_sessions_revoked = replay
            return PasswordChangeResult(
                account=self._settings_for_record(record),
                other_sessions_revoked=other_sessions_revoked,
            )
        credential = self._repository.credential_for_principal(principal_id)
        if credential is None:
            raise problem(
                status=401,
                code="PLATFORM_SESSION_REQUIRED",
                title="Platform session required",
                detail="A current opaque platform session is required for this operation.",
            )
        if not self._password_hasher.verify(current_password, credential.password_hash):
            raise problem(
                status=422,
                code="CURRENT_PASSWORD_INVALID",
                title="Current password is incorrect",
                detail="The current password could not be verified.",
            )
        if hmac.compare_digest(current_password, new_password):
            raise problem(
                status=422,
                code="PASSWORD_REUSE_FORBIDDEN",
                title="Choose a new password",
                detail="The new password must differ from the current password.",
            )
        self._password_policy.require(
            new_password,
            canonical_username=credential.canonical_username,
        )
        new_password_hash = self._password_hasher.hash(new_password)
        record, other_sessions_revoked = self._repository.change_password(
            principal_id=principal_id,
            current_token_hash=self.token_hash(token),
            expected_password_hash=credential.password_hash,
            new_password_hash=new_password_hash,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            request_id=request_id,
        )
        return PasswordChangeResult(
            account=self._settings_for_record(record),
            other_sessions_revoked=other_sessions_revoked,
        )

    def _require_platform_session(
        self, token: str, *, request_id: str | None = None
    ) -> ResolvedSession:
        if not token.startswith(self.TOKEN_PREFIX):
            raise problem(
                status=401,
                code="PLATFORM_SESSION_REQUIRED",
                title="Platform session required",
                detail="A current opaque platform session is required for this operation.",
            )
        resolved = self._repository.resolve_session(
            self.token_hash(token),
            request_id=request_id,
            allow_session_mutation=session_mutations_allowed(),
        )
        if resolved is None:
            raise problem(
                status=401,
                code="SESSION_INVALID",
                title="Session invalid",
                detail="The session is revoked or no longer valid.",
            )
        return resolved

    def _settings_for_record(self, record: AccountProfileRecord) -> AccountSettings:
        return AccountSettings(
            profile=record.to_profile(),
            password_policy=self._password_policy.view,
        )

    def notification_unread_count(self, *, auth: AuthContext) -> AccountNotificationUnreadCount:
        return AccountNotificationUnreadCount(
            unread_count=self._repository.count_unread_notifications(principal_id=auth.subject_id)
        )

    def account_access_overview(self, *, auth: AuthContext) -> AccountAccessOverview:
        return self._repository.account_access_overview(principal_id=auth.subject_id)

    def create_organization_membership_request(
        self,
        *,
        auth: AuthContext,
        command: OrganizationMembershipRequestCreate,
        idempotency_key: str,
        request_id: str,
    ) -> AccountAccessRequest:
        self._abuse_protection.check_authenticated(
            operation="organization-membership.create", subject_hint=auth.subject_id
        )
        return self._repository.create_organization_membership_request(
            auth=auth,
            organization_id_or_join_code=command.organization_id_or_join_code,
            reason=command.reason,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )

    def decide_organization_membership_request(
        self,
        *,
        auth: AuthContext,
        access_request_id: str,
        target_status: AccessRequestStatus,
        command: AccessDecisionCommand,
        idempotency_key: str,
        request_id: str,
    ) -> AccountAccessRequest:
        return self._repository.decide_organization_membership_request(
            auth=auth,
            access_request_id=access_request_id,
            target_status=target_status,
            reason=command.reason,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )

    def withdraw_organization_membership_request(
        self,
        *,
        auth: AuthContext,
        access_request_id: str,
        idempotency_key: str,
        request_id: str,
    ) -> AccountAccessRequest:
        return self._repository.withdraw_organization_membership_request(
            auth=auth,
            access_request_id=access_request_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )

    def notifications(
        self,
        *,
        auth: AuthContext,
        state: AccountNotificationState | None,
        cursor: str | None,
        limit: int,
    ) -> AccountNotificationPage:
        if limit not in {20, 50}:
            raise problem(
                status=422,
                code="NOTIFICATION_PAGE_LIMIT_INVALID",
                title="Invalid notification page size",
                detail="Notification page size must be 20 or 50.",
            )
        before = self._decode_notification_cursor(cursor, auth.subject_id, state)
        rows = self._repository.list_notifications(
            principal_id=auth.subject_id,
            state=state,
            before=before,
            limit=limit + 1,
        )
        items = rows[:limit]
        next_cursor = (
            self._encode_notification_cursor(
                items[-1].created_at, items[-1].notification_id, auth.subject_id, state
            )
            if len(rows) > limit and items
            else None
        )
        return AccountNotificationPage(items=items, next_cursor=next_cursor)

    def mark_notification_read(
        self, *, auth: AuthContext, notification_id: str, request_id: str
    ) -> None:
        self._repository.mark_notification_read(
            principal_id=auth.subject_id,
            notification_id=notification_id,
            request_id=request_id,
        )

    @staticmethod
    def _encode_notification_cursor(
        created_at: datetime,
        notification_id: str,
        subject_id: str,
        state: AccountNotificationState | None,
    ) -> str:
        payload = json.dumps(
            {
                "created_at": created_at.astimezone(timezone.utc).isoformat(),
                "notification_id": notification_id,
                "subject_id": subject_id,
                "state": None if state is None else state.value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_notification_cursor(
        value: str | None,
        subject_id: str,
        state: AccountNotificationState | None,
    ) -> tuple[datetime, str] | None:
        if value is None:
            return None
        try:
            decoded = value + "=" * (-len(value) % 4)
            raw = json.loads(urlsafe_b64decode(decoded.encode("ascii")))
            if (
                not isinstance(raw, dict)
                or raw.get("subject_id") != subject_id
                or raw.get("state") != (None if state is None else state.value)
                or not isinstance(raw.get("notification_id"), str)
                or not raw["notification_id"]
                or not isinstance(raw.get("created_at"), str)
            ):
                raise ValueError
            created_at = datetime.fromisoformat(raw["created_at"].replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                raise ValueError
            return created_at.astimezone(timezone.utc), raw["notification_id"]
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise problem(
                status=400,
                code="INVALID_NOTIFICATION_CURSOR",
                title="Invalid notification cursor",
                detail="Refresh the notification list before loading more items.",
            ) from exc

    @staticmethod
    def _session_fingerprint(token: str, payload: dict[str, object]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(token.encode("utf-8"), encoded, hashlib.sha256).hexdigest()

    def create_membership_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        command: MembershipRequestCreate,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest:
        self._abuse_protection.check_authenticated(
            operation="membership.create", subject_hint=auth.subject_id
        )
        return self._repository.create_membership_request(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            reason=command.reason,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )

    def list_membership_requests(
        self, *, auth: AuthContext, organization_id: str, project_id: str
    ) -> MembershipRequestList:
        return MembershipRequestList(
            items=self._repository.list_membership_requests(
                auth=auth, organization_id=organization_id, project_id=project_id
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
        return self._repository.get_membership_request(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            access_request_id=access_request_id,
        )

    def decide_membership_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
        target_status: AccessRequestStatus,
        command: AccessDecisionCommand,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest:
        self._abuse_protection.check_authenticated(
            operation=f"membership.{target_status.value.lower()}",
            subject_hint=auth.subject_id,
        )
        return self._repository.decide_membership_request(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            access_request_id=access_request_id,
            target_status=target_status,
            reason=command.reason,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )

    def withdraw_membership_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
        command: AccessDecisionCommand,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest:
        self._abuse_protection.check_authenticated(
            operation="membership.withdraw", subject_hint=auth.subject_id
        )
        return self._repository.withdraw_membership_request(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            access_request_id=access_request_id,
            reason=command.reason,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )

    def create_capability_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        command: CapabilityRequestCreate,
        idempotency_key: str,
        request_id: str,
    ) -> CapabilityRequest:
        self._abuse_protection.check_authenticated(
            operation="capability.create", subject_hint=auth.subject_id
        )
        # Effective capabilities are always intersected with active membership by
        # resolve_session. Whether membership is also a submission prerequisite remains an
        # OPEN-03 policy decision and is therefore injected rather than hard-coded.
        self._capability_request_admission.check(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        return self._repository.create_capability_request(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            capability_keys=command.capability_keys,
            reason=command.reason,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )

    def list_capability_requests(
        self, *, auth: AuthContext, organization_id: str, project_id: str
    ) -> CapabilityRequestList:
        return CapabilityRequestList(
            items=self._repository.list_capability_requests(
                auth=auth, organization_id=organization_id, project_id=project_id
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
        return self._repository.get_capability_request(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            access_request_id=access_request_id,
        )

    def decide_capability_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
        target_status: AccessRequestStatus,
        command: AccessDecisionCommand,
        idempotency_key: str,
        request_id: str,
    ) -> CapabilityRequest:
        self._abuse_protection.check_authenticated(
            operation=f"capability.{target_status.value.lower()}",
            subject_hint=auth.subject_id,
        )
        return self._repository.decide_capability_request(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            access_request_id=access_request_id,
            target_status=target_status,
            reason=command.reason,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )

    def withdraw_capability_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
        command: AccessDecisionCommand,
        idempotency_key: str,
        request_id: str,
    ) -> CapabilityRequest:
        self._abuse_protection.check_authenticated(
            operation="capability.withdraw", subject_hint=auth.subject_id
        )
        return self._repository.withdraw_capability_request(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            access_request_id=access_request_id,
            reason=command.reason,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )

    def list_audit_events(
        self, *, auth: AuthContext, organization_id: str, project_id: str
    ) -> AccessAuditEventList:
        return AccessAuditEventList(
            items=self._repository.list_audit_events(
                auth=auth, organization_id=organization_id, project_id=project_id
            )
        )
