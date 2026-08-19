"""Application service for accounts, sessions, memberships, and capability requests."""

from __future__ import annotations

import hashlib
import secrets
import unicodedata
from typing import Protocol

from hc_data_platform.core.errors import problem

from .access_models import (
    AccessAuditEventList,
    AccessDecisionCommand,
    AccessRequestStatus,
    CapabilityRequest,
    CapabilityRequestCreate,
    CapabilityRequestList,
    LoginCommand,
    MembershipRequest,
    MembershipRequestCreate,
    MembershipRequestList,
    RegistrationCommand,
    RegistrationResult,
    SessionBootstrap,
    SessionCreated,
)
from .access_repository import AccessRepository
from .auth import AuthContext
from .passwords import PasswordHasher


class AbuseProtection(Protocol):
    """OPEN-02 extension point; production thresholds are intentionally not defined here."""

    def check(self, *, operation: str, subject_hint: str | None) -> None: ...


class UnconfiguredAbuseProtection:
    """No implicit numeric policy while OPEN-02 remains unconfirmed."""

    def check(self, *, operation: str, subject_hint: str | None) -> None:
        del operation, subject_hint


class CapabilityRequestAdmissionPolicy(Protocol):
    """OPEN-03 extension point for deciding whether membership is a submit prerequisite."""

    def check(self, *, auth: AuthContext, project_id: str) -> None: ...


class UnconfiguredCapabilityRequestAdmissionPolicy:
    def check(self, *, auth: AuthContext, project_id: str) -> None:
        del auth, project_id


class AccessService:
    TOKEN_PREFIX = "hcs_"

    def __init__(
        self,
        repository: AccessRepository,
        *,
        password_hasher: PasswordHasher | None = None,
        abuse_protection: AbuseProtection | None = None,
        capability_request_admission: CapabilityRequestAdmissionPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._password_hasher = password_hasher or PasswordHasher()
        self._abuse_protection = abuse_protection or UnconfiguredAbuseProtection()
        self._capability_request_admission = (
            capability_request_admission or UnconfiguredCapabilityRequestAdmissionPolicy()
        )
        # A real hash keeps the unknown-user path on the same password-verification primitive.
        self._dummy_password_hash = self._password_hasher.hash(secrets.token_urlsafe(24))

    @staticmethod
    def canonical_username(username: str) -> str:
        return unicodedata.normalize("NFKC", username).casefold()

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def register(self, command: RegistrationCommand, *, request_id: str) -> RegistrationResult:
        canonical = self.canonical_username(command.username)
        self._abuse_protection.check(operation="register", subject_hint=canonical)
        password_hash = self._password_hasher.hash(command.password.get_secret_value())
        principal = self._repository.register_account(
            canonical_username=canonical,
            display_username=command.username,
            password_hash=password_hash,
            request_id=request_id,
        )
        return RegistrationResult(principal=principal)

    def login(self, command: LoginCommand, *, request_id: str) -> SessionCreated:
        canonical = self.canonical_username(command.username)
        self._abuse_protection.check(operation="login", subject_hint=canonical)
        credential = self._repository.credential_for_username(canonical)
        encoded = self._dummy_password_hash if credential is None else credential.password_hash
        valid = self._password_hasher.verify(command.password.get_secret_value(), encoded)
        if credential is None or not valid or credential.principal.status.value != "ACTIVE":
            self._repository.record_login(
                principal_id=None if credential is None else credential.principal.principal_id,
                succeeded=False,
                request_id=request_id,
            )
            raise problem(
                status=401,
                code="INVALID_CREDENTIALS",
                title="Authentication failed",
                detail="The username or password is incorrect.",
            )
        token = self.TOKEN_PREFIX + secrets.token_urlsafe(32)
        self._repository.create_session(
            principal_id=credential.principal.principal_id,
            token_hash=self.token_hash(token),
            request_id=request_id,
        )
        self._repository.record_login(
            principal_id=credential.principal.principal_id,
            succeeded=True,
            request_id=request_id,
        )
        return SessionCreated(
            access_token=token,
            principal=credential.principal,
            capability_revision=credential.capability_revision,
        )

    def authenticate_access_token(self, token: str) -> AuthContext | None:
        if not token.startswith(self.TOKEN_PREFIX):
            return None
        resolved = self._repository.resolve_session(self.token_hash(token))
        if resolved is None:
            return None
        project_ids = frozenset(scope.project_id for scope in resolved.scopes)
        region_codes = frozenset(
            region for scope in resolved.scopes for region in scope.region_codes
        )
        scope_pairs = frozenset(
            (scope.project_id, None) if scope.project_wide else (scope.project_id, region)
            for scope in resolved.scopes
            for region in ((None,) if scope.project_wide else scope.region_codes)
        )
        scoped_capabilities = frozenset(
            (scope.project_id, capability)
            for scope in resolved.scopes
            for capability in scope.capabilities
        )
        return AuthContext(
            subject_id=resolved.principal.principal_id,
            project_ids=project_ids,
            region_codes=region_codes,
            roles=frozenset(),
            capabilities=frozenset(),
            capability_revision=resolved.capability_revision,
            scope_pairs=scope_pairs,
            scoped_capabilities=scoped_capabilities,
        )

    def bootstrap(self, token: str) -> SessionBootstrap:
        if not token.startswith(self.TOKEN_PREFIX):
            raise problem(
                status=401,
                code="AUTHENTICATION_REQUIRED",
                title="Authentication required",
                detail="A current platform session is required.",
            )
        resolved = self._repository.resolve_session(self.token_hash(token))
        if resolved is None:
            raise problem(
                status=401,
                code="SESSION_INVALID",
                title="Session invalid",
                detail="The session is revoked or no longer valid.",
            )
        return SessionBootstrap(
            principal=resolved.principal,
            available_scopes=resolved.scopes,
            capability_revision=resolved.capability_revision,
        )

    def logout(self, token: str, *, request_id: str) -> None:
        if token.startswith(self.TOKEN_PREFIX):
            self._repository.revoke_session(
                token_hash=self.token_hash(token),
                request_id=request_id,
            )

    def create_membership_request(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        command: MembershipRequestCreate,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest:
        self._abuse_protection.check(operation="membership.create", subject_hint=auth.subject_id)
        return self._repository.create_membership_request(
            auth=auth,
            project_id=project_id,
            reason=command.reason,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )

    def list_membership_requests(
        self, *, auth: AuthContext, project_id: str
    ) -> MembershipRequestList:
        return MembershipRequestList(
            items=self._repository.list_membership_requests(auth=auth, project_id=project_id)
        )

    def get_membership_request(
        self, *, auth: AuthContext, project_id: str, access_request_id: str
    ) -> MembershipRequest:
        return self._repository.get_membership_request(
            auth=auth,
            project_id=project_id,
            access_request_id=access_request_id,
        )

    def decide_membership_request(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        access_request_id: str,
        target_status: AccessRequestStatus,
        command: AccessDecisionCommand,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest:
        self._abuse_protection.check(
            operation=f"membership.{target_status.value.lower()}",
            subject_hint=auth.subject_id,
        )
        return self._repository.decide_membership_request(
            auth=auth,
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
        project_id: str,
        access_request_id: str,
        command: AccessDecisionCommand,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest:
        self._abuse_protection.check(operation="membership.withdraw", subject_hint=auth.subject_id)
        return self._repository.withdraw_membership_request(
            auth=auth,
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
        project_id: str,
        command: CapabilityRequestCreate,
        idempotency_key: str,
        request_id: str,
    ) -> CapabilityRequest:
        self._abuse_protection.check(operation="capability.create", subject_hint=auth.subject_id)
        # Effective capabilities are always intersected with active membership by
        # resolve_session. Whether membership is also a submission prerequisite remains an
        # OPEN-03 policy decision and is therefore injected rather than hard-coded.
        self._capability_request_admission.check(auth=auth, project_id=project_id)
        return self._repository.create_capability_request(
            auth=auth,
            project_id=project_id,
            capability_keys=command.capability_keys,
            reason=command.reason,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )

    def list_capability_requests(
        self, *, auth: AuthContext, project_id: str
    ) -> CapabilityRequestList:
        return CapabilityRequestList(
            items=self._repository.list_capability_requests(auth=auth, project_id=project_id)
        )

    def get_capability_request(
        self, *, auth: AuthContext, project_id: str, access_request_id: str
    ) -> CapabilityRequest:
        return self._repository.get_capability_request(
            auth=auth,
            project_id=project_id,
            access_request_id=access_request_id,
        )

    def decide_capability_request(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        access_request_id: str,
        target_status: AccessRequestStatus,
        command: AccessDecisionCommand,
        idempotency_key: str,
        request_id: str,
    ) -> CapabilityRequest:
        self._abuse_protection.check(
            operation=f"capability.{target_status.value.lower()}",
            subject_hint=auth.subject_id,
        )
        return self._repository.decide_capability_request(
            auth=auth,
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
        project_id: str,
        access_request_id: str,
        command: AccessDecisionCommand,
        idempotency_key: str,
        request_id: str,
    ) -> CapabilityRequest:
        self._abuse_protection.check(operation="capability.withdraw", subject_hint=auth.subject_id)
        return self._repository.withdraw_capability_request(
            auth=auth,
            project_id=project_id,
            access_request_id=access_request_id,
            reason=command.reason,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )

    def list_audit_events(self, *, auth: AuthContext, project_id: str) -> AccessAuditEventList:
        return AccessAuditEventList(
            items=self._repository.list_audit_events(auth=auth, project_id=project_id)
        )
