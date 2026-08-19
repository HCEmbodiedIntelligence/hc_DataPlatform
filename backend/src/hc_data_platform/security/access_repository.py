"""Repository contract and scope-enforcing in-memory reference implementation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from threading import RLock
from typing import Protocol
from uuid import uuid4

from hc_data_platform.core.errors import ProblemException, problem

from .access_models import (
    AccessAuditEvent,
    AccessRequestStatus,
    AccountCredential,
    AccountPrincipal,
    AccountStatus,
    AvailableScope,
    CapabilityRequest,
    MembershipRequest,
    ResolvedSession,
)
from .auth import AuthContext, Role
from .scope import ScopeGuard


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def can_manage_project(auth: AuthContext, project_id: str) -> bool:
    """Management is capability based and never grants an implicit tenant bypass."""

    try:
        ScopeGuard.require(auth, project_id)
    except ProblemException:
        return False
    return Role.ADMIN.value in auth.roles or auth.has_capability(
        "project.access.manage", project_id
    )


def require_project_manager(auth: AuthContext, project_id: str) -> None:
    ScopeGuard.require(auth, project_id)
    if Role.ADMIN.value not in auth.roles and not auth.has_capability(
        "project.access.manage", project_id
    ):
        raise problem(
            status=403,
            code="ACCESS_MANAGEMENT_REQUIRED",
            title="Access management permission required",
            detail="This project scope does not grant access-request management.",
        )


class AccessRepository(Protocol):
    def register_account(
        self,
        *,
        canonical_username: str,
        display_username: str,
        password_hash: str,
        request_id: str,
    ) -> AccountPrincipal: ...

    def credential_for_username(self, canonical_username: str) -> AccountCredential | None: ...

    def create_session(self, *, principal_id: str, token_hash: str, request_id: str) -> str: ...

    def resolve_session(self, token_hash: str) -> ResolvedSession | None: ...

    def revoke_session(self, *, token_hash: str, request_id: str) -> None: ...

    def record_login(
        self, *, principal_id: str | None, succeeded: bool, request_id: str
    ) -> None: ...

    def create_membership_request(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest: ...

    def list_membership_requests(
        self, *, auth: AuthContext, project_id: str
    ) -> tuple[MembershipRequest, ...]: ...

    def get_membership_request(
        self, *, auth: AuthContext, project_id: str, access_request_id: str
    ) -> MembershipRequest: ...

    def decide_membership_request(
        self,
        *,
        auth: AuthContext,
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
        project_id: str,
        capability_keys: tuple[str, ...],
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> CapabilityRequest: ...

    def list_capability_requests(
        self, *, auth: AuthContext, project_id: str
    ) -> tuple[CapabilityRequest, ...]: ...

    def get_capability_request(
        self, *, auth: AuthContext, project_id: str, access_request_id: str
    ) -> CapabilityRequest: ...

    def decide_capability_request(
        self,
        *,
        auth: AuthContext,
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
        project_id: str,
        access_request_id: str,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> CapabilityRequest: ...

    def list_audit_events(
        self, *, auth: AuthContext, project_id: str
    ) -> tuple[AccessAuditEvent, ...]: ...


class InMemoryAccessRepository:
    """Thread-safe executable specification for the PostgreSQL adapter."""

    def __init__(self) -> None:
        self._accounts: dict[str, AccountCredential] = {}
        self._username_index: dict[str, str] = {}
        self._sessions: dict[str, tuple[str, str, bool]] = {}
        self._memberships: set[tuple[str, str]] = set()
        self._membership_requests: dict[str, MembershipRequest] = {}
        self._capability_requests: dict[str, CapabilityRequest] = {}
        self._idempotency: dict[tuple[str, str, str], tuple[str, str]] = {}
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
            principal = AccountPrincipal(
                principal_id=str(uuid4()),
                username=display_username,
                status=AccountStatus.ACTIVE,
                created_at=_now(),
            )
            self._accounts[principal.principal_id] = AccountCredential(
                principal=principal,
                canonical_username=canonical_username,
                password_hash=password_hash,
                capability_revision=0,
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
            )
            return principal

    def credential_for_username(self, canonical_username: str) -> AccountCredential | None:
        with self._lock:
            principal_id = self._username_index.get(canonical_username)
            return None if principal_id is None else self._accounts[principal_id]

    def create_session(self, *, principal_id: str, token_hash: str, request_id: str) -> str:
        with self._lock:
            if principal_id not in self._accounts:
                raise _not_found("account")
            session_id = str(uuid4())
            self._sessions[token_hash] = (session_id, principal_id, False)
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal_id,
                action="auth.session.created",
                resource_type="session",
                resource_id=session_id,
                request_id=request_id,
                outcome="SUCCEEDED",
            )
            return session_id

    def resolve_session(self, token_hash: str) -> ResolvedSession | None:
        with self._lock:
            session = self._sessions.get(token_hash)
            if session is None or session[2]:
                return None
            session_id, principal_id, _ = session
            account = self._accounts.get(principal_id)
            if account is None or account.principal.status is not AccountStatus.ACTIVE:
                return None
            projects = sorted(
                project_id
                for member_id, project_id in self._memberships
                if member_id == principal_id
            )
            scopes = tuple(
                AvailableScope(
                    project_id=project_id,
                    capabilities=tuple(
                        sorted(
                            {
                                capability
                                for request in self._capability_requests.values()
                                if request.requester_id == principal_id
                                and request.project_id == project_id
                                and request.status is AccessRequestStatus.APPROVED
                                for capability in request.capability_keys
                            }
                        )
                    ),
                )
                for project_id in projects
            )
            return ResolvedSession(
                session_id=session_id,
                principal=account.principal,
                capability_revision=account.capability_revision,
                scopes=scopes,
            )

    def revoke_session(self, *, token_hash: str, request_id: str) -> None:
        with self._lock:
            session = self._sessions.get(token_hash)
            if session is None:
                return
            session_id, principal_id, _ = session
            self._sessions[token_hash] = (session_id, principal_id, True)
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal_id,
                action="auth.session.revoked",
                resource_type="session",
                resource_id=session_id,
                request_id=request_id,
                outcome="SUCCEEDED",
            )

    def record_login(self, *, principal_id: str | None, succeeded: bool, request_id: str) -> None:
        with self._lock:
            self._append_audit(
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal_id,
                action="auth.login.succeeded" if succeeded else "auth.login.failed",
                resource_type="account",
                resource_id=principal_id or "anonymous-subject",
                request_id=request_id,
                outcome="SUCCEEDED" if succeeded else "DENIED",
            )

    def create_membership_request(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest:
        payload = {"project_id": project_id, "reason": reason}
        with self._lock:
            if auth.subject_id not in self._accounts:
                raise _account_principal_required()
            repeated = self._repeat(auth.subject_id, "membership.create", idempotency_key, payload)
            if repeated is not None:
                return self._membership_requests[repeated]
            if (auth.subject_id, project_id) in self._memberships:
                raise problem(
                    status=409,
                    code="MEMBERSHIP_ALREADY_ACTIVE",
                    title="Membership already active",
                    detail="The principal is already a member of this project.",
                )
            if any(
                item.project_id == project_id
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
        self, *, auth: AuthContext, project_id: str
    ) -> tuple[MembershipRequest, ...]:
        with self._lock:
            manager = can_manage_project(auth, project_id)
            return tuple(
                sorted(
                    (
                        item
                        for item in self._membership_requests.values()
                        if item.project_id == project_id
                        and (manager or item.requester_id == auth.subject_id)
                    ),
                    key=lambda item: (item.created_at, item.request_id),
                    reverse=True,
                )
            )

    def get_membership_request(
        self, *, auth: AuthContext, project_id: str, access_request_id: str
    ) -> MembershipRequest:
        with self._lock:
            item = self._membership_requests.get(access_request_id)
            if item is None or item.project_id != project_id:
                raise _not_found("membership_request")
            if item.requester_id != auth.subject_id and not can_manage_project(auth, project_id):
                raise _not_found("membership_request")
            return item

    def decide_membership_request(
        self,
        *,
        auth: AuthContext,
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
        require_project_manager(auth, project_id)
        payload = {"request_id": access_request_id, "status": target_status.value, "reason": reason}
        with self._lock:
            repeated = self._repeat(auth.subject_id, "membership.decide", idempotency_key, payload)
            if repeated is not None:
                return self._membership_requests[repeated]
            item = self._membership_requests.get(access_request_id)
            if item is None or item.project_id != project_id:
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
                self._memberships.add((item.requester_id, project_id))
                self._bump_revision(item.requester_id)
            elif target_status is AccessRequestStatus.REVOKED:
                self._memberships.discard((item.requester_id, project_id))
                self._revoke_capability_requests(item.requester_id, project_id, auth.subject_id)
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
            return updated

    def withdraw_membership_request(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        access_request_id: str,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest:
        payload = {"request_id": access_request_id, "reason": reason}
        with self._lock:
            repeated = self._repeat(
                auth.subject_id, "membership.withdraw", idempotency_key, payload
            )
            if repeated is not None:
                return self._membership_requests[repeated]
            item = self._membership_requests.get(access_request_id)
            if (
                item is None
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
        project_id: str,
        capability_keys: tuple[str, ...],
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> CapabilityRequest:
        payload = {
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
        self, *, auth: AuthContext, project_id: str
    ) -> tuple[CapabilityRequest, ...]:
        with self._lock:
            manager = can_manage_project(auth, project_id)
            return tuple(
                sorted(
                    (
                        item
                        for item in self._capability_requests.values()
                        if item.project_id == project_id
                        and (manager or item.requester_id == auth.subject_id)
                    ),
                    key=lambda item: (item.created_at, item.request_id),
                    reverse=True,
                )
            )

    def get_capability_request(
        self, *, auth: AuthContext, project_id: str, access_request_id: str
    ) -> CapabilityRequest:
        with self._lock:
            item = self._capability_requests.get(access_request_id)
            if item is None or item.project_id != project_id:
                raise _not_found("capability_request")
            if item.requester_id != auth.subject_id and not can_manage_project(auth, project_id):
                raise _not_found("capability_request")
            return item

    def decide_capability_request(
        self,
        *,
        auth: AuthContext,
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
        require_project_manager(auth, project_id)
        payload = {"request_id": access_request_id, "status": target_status.value, "reason": reason}
        with self._lock:
            repeated = self._repeat(auth.subject_id, "capability.decide", idempotency_key, payload)
            if repeated is not None:
                return self._capability_requests[repeated]
            item = self._capability_requests.get(access_request_id)
            if item is None or item.project_id != project_id:
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
            return updated

    def withdraw_capability_request(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        access_request_id: str,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> CapabilityRequest:
        payload = {"request_id": access_request_id, "reason": reason}
        with self._lock:
            repeated = self._repeat(
                auth.subject_id, "capability.withdraw", idempotency_key, payload
            )
            if repeated is not None:
                return self._capability_requests[repeated]
            item = self._capability_requests.get(access_request_id)
            if (
                item is None
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
        self, *, auth: AuthContext, project_id: str
    ) -> tuple[AccessAuditEvent, ...]:
        require_project_manager(auth, project_id)
        with self._lock:
            return tuple(
                sorted(
                    (event for event in self._audit if event.project_id == project_id),
                    key=lambda event: (event.occurred_at, event.event_id),
                    reverse=True,
                )
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
        )

    def _revoke_capability_requests(
        self, principal_id: str, project_id: str, actor_id: str
    ) -> None:
        timestamp = _now()
        for request_id, item in tuple(self._capability_requests.items()):
            if (
                item.requester_id == principal_id
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
            project_id=item.project_id,
            actor_id=actor_id,
            action=action,
            resource_type="membership_request",
            resource_id=item.request_id,
            request_id=request_id,
            outcome="SUCCEEDED",
            safe_details={"status": item.status.value, "requester_id": item.requester_id},
        )

    def _audit_capability(
        self, item: CapabilityRequest, actor_id: str, action: str, request_id: str
    ) -> None:
        self._append_audit(
            scope_kind="PROJECT",
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
        project_id: str | None,
        actor_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
        outcome: str,
        safe_details: dict[str, object] | None = None,
    ) -> None:
        self._audit.append(
            AccessAuditEvent(
                event_id=str(uuid4()),
                scope_kind=scope_kind,
                project_id=project_id,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=request_id,
                outcome=outcome,
                safe_details=safe_details or {},
                occurred_at=_now(),
            )
        )
