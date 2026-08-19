"""PostgreSQL access repository with row locks and repository-level visibility predicates."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.core.errors import problem

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
from .access_repository import (
    _account_principal_required,
    _fingerprint,
    _not_found,
    _transition_conflict,
    can_manage_project,
    require_project_manager,
)
from .audit import canonical_hash
from .auth import AuthContext


def _row(cursor: Any, raw: object) -> dict[str, object]:
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    if cursor.description is None:
        raise RuntimeError("database cursor did not describe its result")
    values = cast(Sequence[object], raw)
    return dict(zip((str(column[0]) for column in cursor.description), values, strict=True))


def _membership(cursor: Any, raw: object) -> MembershipRequest:
    return MembershipRequest.model_validate(_row(cursor, raw))


def _capability(cursor: Any, raw: object) -> CapabilityRequest:
    values = _row(cursor, raw)
    values["capability_keys"] = tuple(cast(Sequence[str], values["capability_keys"]))
    return CapabilityRequest.model_validate(values)


_MEMBERSHIP_COLUMNS = """
request_id::text AS request_id, project_id, requester_id::text AS requester_id,
status, reason, decided_by, decision_reason, created_at, updated_at, revision
""".strip()

_CAPABILITY_COLUMNS = """
request_id::text AS request_id, project_id, requester_id::text AS requester_id,
capability_keys, status, reason, decided_by, decision_reason,
created_at, updated_at, revision
""".strip()


def _principal_uuid(value: str) -> str | None:
    try:
        return str(UUID(value))
    except ValueError:
        return None


class PostgresAccessRepository:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(cls, dsn: str) -> PostgresAccessRepository:
        normalized = normalize_postgres_dsn(dsn)

        def connect() -> Any:
            import psycopg

            return psycopg.connect(normalized)

        return cls(connect)

    def register_account(
        self,
        *,
        canonical_username: str,
        display_username: str,
        password_hash: str,
        request_id: str,
    ) -> AccountPrincipal:
        connection = self._connection_factory()
        cursor = connection.cursor()
        principal_id = str(uuid4())
        try:
            cursor.execute(
                """
                INSERT INTO access_control.accounts (
                    principal_id, canonical_username, display_username, status,
                    password_hash, capability_revision
                ) VALUES (%s::uuid, %s, %s, 'ACTIVE', %s, 0)
                RETURNING principal_id::text AS principal_id,
                          display_username AS username, status, created_at
                """,
                (principal_id, canonical_username, display_username, password_hash),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise RuntimeError("PostgreSQL did not return the registered account")
            principal = AccountPrincipal.model_validate(_row(cursor, raw))
            self._audit(
                cursor,
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal_id,
                action="auth.registration.created",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"empty_account": True},
            )
            connection.commit()
            return principal
        except Exception as exc:
            connection.rollback()
            if getattr(exc, "sqlstate", None) == "23505":
                raise problem(
                    status=409,
                    code="ACCOUNT_REGISTRATION_CONFLICT",
                    title="Account registration conflict",
                    detail="The account could not be registered with these credentials.",
                ) from exc
            raise
        finally:
            cursor.close()
            connection.close()

    def credential_for_username(self, canonical_username: str) -> AccountCredential | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT principal_id::text AS principal_id, display_username AS username,
                       status, created_at, canonical_username, password_hash,
                       capability_revision
                FROM access_control.accounts
                WHERE canonical_username = %s
                """,
                (canonical_username,),
            )
            raw = cursor.fetchone()
            if raw is None:
                return None
            values = _row(cursor, raw)
            return AccountCredential(
                principal=AccountPrincipal(
                    principal_id=str(values["principal_id"]),
                    username=str(values["username"]),
                    status=AccountStatus(str(values["status"])),
                    created_at=cast(datetime, values["created_at"]),
                ),
                canonical_username=str(values["canonical_username"]),
                password_hash=str(values["password_hash"]),
                capability_revision=int(cast(Any, values["capability_revision"])),
            )
        finally:
            cursor.close()
            connection.close()

    def create_session(self, *, principal_id: str, token_hash: str, request_id: str) -> str:
        connection = self._connection_factory()
        cursor = connection.cursor()
        session_id = str(uuid4())
        try:
            cursor.execute(
                """
                INSERT INTO access_control.sessions (session_id, principal_id, token_hash)
                VALUES (%s::uuid, %s::uuid, %s)
                """,
                (session_id, principal_id, token_hash),
            )
            self._audit(
                cursor,
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal_id,
                action="auth.session.created",
                resource_type="session",
                resource_id=session_id,
                request_id=request_id,
                outcome="SUCCEEDED",
            )
            connection.commit()
            return session_id
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def resolve_session(self, token_hash: str) -> ResolvedSession | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT session.session_id::text AS session_id,
                       account.principal_id::text AS principal_id,
                       account.display_username AS username,
                       account.status, account.created_at, account.capability_revision
                FROM access_control.sessions session
                JOIN access_control.accounts account
                  ON account.principal_id = session.principal_id
                WHERE session.token_hash = %s
                  AND session.revoked_at IS NULL
                  AND account.status = 'ACTIVE'
                """,
                (token_hash,),
            )
            raw = cursor.fetchone()
            if raw is None:
                return None
            values = _row(cursor, raw)
            principal_id = str(values["principal_id"])
            cursor.execute(
                """
                SELECT membership.project_id,
                       COALESCE(array_agg(DISTINCT capability_grant.capability_key)
                           FILTER (WHERE capability_grant.capability_key IS NOT NULL),
                           ARRAY[]::text[])
                           AS capability_keys
                FROM access_control.memberships membership
                LEFT JOIN access_control.capability_grants capability_grant
                  ON capability_grant.principal_id = membership.principal_id
                 AND capability_grant.project_id = membership.project_id
                 AND capability_grant.active
                WHERE membership.principal_id = %s::uuid AND membership.active
                GROUP BY membership.project_id
                ORDER BY membership.project_id
                """,
                (principal_id,),
            )
            scopes = tuple(
                AvailableScope(
                    project_id=str(scope["project_id"]),
                    capabilities=tuple(sorted(cast(Sequence[str], scope["capability_keys"]))),
                )
                for scope in (_row(cursor, item) for item in cursor.fetchall())
            )
            return ResolvedSession(
                session_id=str(values["session_id"]),
                principal=AccountPrincipal(
                    principal_id=principal_id,
                    username=str(values["username"]),
                    status=AccountStatus(str(values["status"])),
                    created_at=cast(datetime, values["created_at"]),
                ),
                capability_revision=int(cast(Any, values["capability_revision"])),
                scopes=scopes,
            )
        finally:
            cursor.close()
            connection.close()

    def revoke_session(self, *, token_hash: str, request_id: str) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE access_control.sessions
                SET revoked_at = COALESCE(revoked_at, now())
                WHERE token_hash = %s
                RETURNING session_id::text AS session_id, principal_id::text AS principal_id
                """,
                (token_hash,),
            )
            raw = cursor.fetchone()
            if raw is not None:
                values = _row(cursor, raw)
                self._audit(
                    cursor,
                    scope_kind="PLATFORM",
                    project_id=None,
                    actor_id=str(values["principal_id"]),
                    action="auth.session.revoked",
                    resource_type="session",
                    resource_id=str(values["session_id"]),
                    request_id=request_id,
                    outcome="SUCCEEDED",
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def record_login(self, *, principal_id: str | None, succeeded: bool, request_id: str) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._audit(
                cursor,
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal_id,
                action="auth.login.succeeded" if succeeded else "auth.login.failed",
                resource_type="account",
                resource_id=principal_id or "anonymous-subject",
                request_id=request_id,
                outcome="SUCCEEDED" if succeeded else "DENIED",
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def create_membership_request(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest:
        principal_id = _principal_uuid(auth.subject_id)
        if principal_id is None:
            raise _account_principal_required()
        payload = {"project_id": project_id, "reason": reason}
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            repeated = self._begin_idempotency(
                cursor, auth.subject_id, "membership.create", idempotency_key, payload
            )
            if repeated is not None:
                result = self._get_membership(cursor, project_id, repeated)
                connection.commit()
                return result
            cursor.execute(
                """
                SELECT 1 FROM access_control.memberships
                WHERE principal_id = %s::uuid AND project_id = %s AND active
                """,
                (principal_id, project_id),
            )
            if cursor.fetchone() is not None:
                raise problem(
                    status=409,
                    code="MEMBERSHIP_ALREADY_ACTIVE",
                    title="Membership already active",
                    detail="The principal is already a member of this project.",
                )
            access_request_id = str(uuid4())
            cursor.execute(
                f"""
                INSERT INTO access_control.membership_requests (
                    request_id, project_id, requester_id, status, reason
                ) VALUES (%s::uuid, %s, %s::uuid, 'PENDING', %s)
                RETURNING {_MEMBERSHIP_COLUMNS}
                """,
                (access_request_id, project_id, principal_id, reason),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise RuntimeError("PostgreSQL did not return the membership request")
            item = _membership(cursor, raw)
            self._finish_idempotency(
                cursor, auth.subject_id, "membership.create", idempotency_key, item.request_id
            )
            self._audit_membership(
                cursor, item, auth.subject_id, "access.membership.requested", request_id
            )
            connection.commit()
            return item
        except Exception as exc:
            connection.rollback()
            if getattr(exc, "sqlstate", None) == "23503":
                raise _account_principal_required() from exc
            if getattr(exc, "sqlstate", None) == "23505":
                raise problem(
                    status=409,
                    code="MEMBERSHIP_REQUEST_ALREADY_PENDING",
                    title="Membership request already pending",
                    detail="A pending membership request already exists for this project.",
                ) from exc
            raise
        finally:
            cursor.close()
            connection.close()

    def list_membership_requests(
        self, *, auth: AuthContext, project_id: str
    ) -> tuple[MembershipRequest, ...]:
        manager = can_manage_project(auth, project_id)
        principal_id = _principal_uuid(auth.subject_id)
        if not manager and principal_id is None:
            return ()
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            if manager:
                cursor.execute(
                    f"""
                    SELECT {_MEMBERSHIP_COLUMNS}
                    FROM access_control.membership_requests
                    WHERE project_id = %s
                    ORDER BY created_at DESC, request_id DESC
                    """,
                    (project_id,),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT {_MEMBERSHIP_COLUMNS}
                    FROM access_control.membership_requests
                    WHERE project_id = %s AND requester_id = %s::uuid
                    ORDER BY created_at DESC, request_id DESC
                    """,
                    (project_id, principal_id),
                )
            return tuple(_membership(cursor, raw) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def get_membership_request(
        self, *, auth: AuthContext, project_id: str, access_request_id: str
    ) -> MembershipRequest:
        manager = can_manage_project(auth, project_id)
        principal_id = _principal_uuid(auth.subject_id)
        if not manager and principal_id is None:
            raise _not_found("membership_request")
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            if manager:
                cursor.execute(
                    f"""
                    SELECT {_MEMBERSHIP_COLUMNS}
                    FROM access_control.membership_requests
                    WHERE request_id = %s::uuid AND project_id = %s
                    """,
                    (access_request_id, project_id),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT {_MEMBERSHIP_COLUMNS}
                    FROM access_control.membership_requests
                    WHERE request_id = %s::uuid AND project_id = %s
                      AND requester_id = %s::uuid
                    """,
                    (access_request_id, project_id, principal_id),
                )
            raw = cursor.fetchone()
            if raw is None:
                raise _not_found("membership_request")
            return _membership(cursor, raw)
        finally:
            cursor.close()
            connection.close()

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
        require_project_manager(auth, project_id)
        payload = {"request_id": access_request_id, "status": target_status.value, "reason": reason}
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            repeated = self._begin_idempotency(
                cursor, auth.subject_id, "membership.decide", idempotency_key, payload
            )
            if repeated is not None:
                item = self._get_membership(cursor, project_id, repeated)
                connection.commit()
                return item
            item = self._get_membership(cursor, project_id, access_request_id, lock=True)
            if item.status is target_status:
                self._finish_idempotency(
                    cursor, auth.subject_id, "membership.decide", idempotency_key, item.request_id
                )
                connection.commit()
                return item
            expected = (
                AccessRequestStatus.APPROVED
                if target_status is AccessRequestStatus.REVOKED
                else AccessRequestStatus.PENDING
            )
            if item.status is not expected:
                raise _transition_conflict(item.status)
            cursor.execute(
                f"""
                UPDATE access_control.membership_requests
                SET status = %s, decided_by = %s, decision_reason = %s,
                    updated_at = now(), revision = revision + 1
                WHERE request_id = %s::uuid AND revision = %s
                RETURNING {_MEMBERSHIP_COLUMNS}
                """,
                (target_status.value, auth.subject_id, reason, item.request_id, item.revision),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise problem(
                    status=409,
                    code="ACCESS_REQUEST_CONCURRENT_UPDATE",
                    title="Access request changed concurrently",
                    detail="Reload the request before deciding it again.",
                )
            updated = _membership(cursor, raw)
            if target_status is AccessRequestStatus.APPROVED:
                cursor.execute(
                    """
                    INSERT INTO access_control.memberships (
                        principal_id, project_id, source_request_id, active,
                        activated_by, revoked_at, revoked_by
                    ) VALUES (%s::uuid, %s, %s::uuid, true, %s, NULL, NULL)
                    ON CONFLICT (principal_id, project_id) DO UPDATE SET
                        source_request_id = EXCLUDED.source_request_id,
                        active = true,
                        activated_at = now(),
                        activated_by = EXCLUDED.activated_by,
                        revoked_at = NULL,
                        revoked_by = NULL
                    """,
                    (item.requester_id, project_id, item.request_id, auth.subject_id),
                )
                self._bump_revision(cursor, item.requester_id)
            elif target_status is AccessRequestStatus.REVOKED:
                cursor.execute(
                    """
                    UPDATE access_control.memberships
                    SET active = false, revoked_at = now(), revoked_by = %s
                    WHERE principal_id = %s::uuid AND project_id = %s AND active
                    """,
                    (auth.subject_id, item.requester_id, project_id),
                )
                cursor.execute(
                    """
                    UPDATE access_control.capability_grants
                    SET active = false, revoked_at = now(), revoked_by = %s
                    WHERE principal_id = %s::uuid AND project_id = %s AND active
                    """,
                    (auth.subject_id, item.requester_id, project_id),
                )
                cursor.execute(
                    """
                    UPDATE access_control.capability_requests
                    SET status = 'REVOKED', decided_by = %s,
                        updated_at = now(), revision = revision + 1
                    WHERE requester_id = %s::uuid AND project_id = %s AND status = 'APPROVED'
                    """,
                    (auth.subject_id, item.requester_id, project_id),
                )
                self._bump_revision(cursor, item.requester_id)
            self._finish_idempotency(
                cursor, auth.subject_id, "membership.decide", idempotency_key, item.request_id
            )
            action = {
                AccessRequestStatus.APPROVED: "access.membership.approved",
                AccessRequestStatus.REJECTED: "access.membership.rejected",
                AccessRequestStatus.REVOKED: "access.membership.revoked",
            }[target_status]
            self._audit_membership(cursor, updated, auth.subject_id, action, request_id)
            connection.commit()
            return updated
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

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
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            repeated = self._begin_idempotency(
                cursor, auth.subject_id, "membership.withdraw", idempotency_key, payload
            )
            if repeated is not None:
                item = self._get_membership(cursor, project_id, repeated)
                connection.commit()
                return item
            item = self._get_membership(cursor, project_id, access_request_id, lock=True)
            if item.requester_id != auth.subject_id:
                raise _not_found("membership_request")
            if item.status is not AccessRequestStatus.PENDING:
                if item.status is AccessRequestStatus.WITHDRAWN:
                    self._finish_idempotency(
                        cursor,
                        auth.subject_id,
                        "membership.withdraw",
                        idempotency_key,
                        item.request_id,
                    )
                    connection.commit()
                    return item
                raise _transition_conflict(item.status)
            updated = self._update_membership_status(
                cursor,
                item,
                AccessRequestStatus.WITHDRAWN,
                actor_id=None,
                reason=reason,
            )
            self._finish_idempotency(
                cursor, auth.subject_id, "membership.withdraw", idempotency_key, item.request_id
            )
            self._audit_membership(
                cursor, updated, auth.subject_id, "access.membership.withdrawn", request_id
            )
            connection.commit()
            return updated
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

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
        principal_id = _principal_uuid(auth.subject_id)
        if principal_id is None:
            raise _account_principal_required()
        payload = {
            "project_id": project_id,
            "capability_keys": list(capability_keys),
            "reason": reason,
        }
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            repeated = self._begin_idempotency(
                cursor, auth.subject_id, "capability.create", idempotency_key, payload
            )
            if repeated is not None:
                item = self._get_capability(cursor, project_id, repeated)
                connection.commit()
                return item
            access_request_id = str(uuid4())
            cursor.execute(
                f"""
                INSERT INTO access_control.capability_requests (
                    request_id, project_id, requester_id, capability_keys, status, reason
                ) VALUES (%s::uuid, %s, %s::uuid, %s, 'PENDING', %s)
                RETURNING {_CAPABILITY_COLUMNS}
                """,
                (access_request_id, project_id, principal_id, list(capability_keys), reason),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise RuntimeError("PostgreSQL did not return the capability request")
            item = _capability(cursor, raw)
            self._finish_idempotency(
                cursor, auth.subject_id, "capability.create", idempotency_key, item.request_id
            )
            self._audit_capability(
                cursor, item, auth.subject_id, "access.capability.requested", request_id
            )
            connection.commit()
            return item
        except Exception as exc:
            connection.rollback()
            if getattr(exc, "sqlstate", None) == "23503":
                raise _account_principal_required() from exc
            raise
        finally:
            cursor.close()
            connection.close()

    def list_capability_requests(
        self, *, auth: AuthContext, project_id: str
    ) -> tuple[CapabilityRequest, ...]:
        manager = can_manage_project(auth, project_id)
        principal_id = _principal_uuid(auth.subject_id)
        if not manager and principal_id is None:
            return ()
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            if manager:
                cursor.execute(
                    f"""
                    SELECT {_CAPABILITY_COLUMNS}
                    FROM access_control.capability_requests
                    WHERE project_id = %s
                    ORDER BY created_at DESC, request_id DESC
                    """,
                    (project_id,),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT {_CAPABILITY_COLUMNS}
                    FROM access_control.capability_requests
                    WHERE project_id = %s AND requester_id = %s::uuid
                    ORDER BY created_at DESC, request_id DESC
                    """,
                    (project_id, principal_id),
                )
            return tuple(_capability(cursor, raw) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def get_capability_request(
        self, *, auth: AuthContext, project_id: str, access_request_id: str
    ) -> CapabilityRequest:
        manager = can_manage_project(auth, project_id)
        principal_id = _principal_uuid(auth.subject_id)
        if not manager and principal_id is None:
            raise _not_found("capability_request")
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            if manager:
                cursor.execute(
                    f"""
                    SELECT {_CAPABILITY_COLUMNS}
                    FROM access_control.capability_requests
                    WHERE request_id = %s::uuid AND project_id = %s
                    """,
                    (access_request_id, project_id),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT {_CAPABILITY_COLUMNS}
                    FROM access_control.capability_requests
                    WHERE request_id = %s::uuid AND project_id = %s
                      AND requester_id = %s::uuid
                    """,
                    (access_request_id, project_id, principal_id),
                )
            raw = cursor.fetchone()
            if raw is None:
                raise _not_found("capability_request")
            return _capability(cursor, raw)
        finally:
            cursor.close()
            connection.close()

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
        require_project_manager(auth, project_id)
        payload = {"request_id": access_request_id, "status": target_status.value, "reason": reason}
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            repeated = self._begin_idempotency(
                cursor, auth.subject_id, "capability.decide", idempotency_key, payload
            )
            if repeated is not None:
                item = self._get_capability(cursor, project_id, repeated)
                connection.commit()
                return item
            item = self._get_capability(cursor, project_id, access_request_id, lock=True)
            if item.status is target_status:
                self._finish_idempotency(
                    cursor, auth.subject_id, "capability.decide", idempotency_key, item.request_id
                )
                connection.commit()
                return item
            expected = (
                AccessRequestStatus.APPROVED
                if target_status is AccessRequestStatus.REVOKED
                else AccessRequestStatus.PENDING
            )
            if item.status is not expected:
                raise _transition_conflict(item.status)
            updated = self._update_capability_status(
                cursor,
                item,
                target_status,
                actor_id=auth.subject_id,
                reason=reason,
            )
            if target_status is AccessRequestStatus.APPROVED:
                for capability_key in item.capability_keys:
                    cursor.execute(
                        """
                        INSERT INTO access_control.capability_grants (
                            grant_id, source_request_id, principal_id, project_id,
                            capability_key, active, activated_by
                        ) VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, true, %s)
                        ON CONFLICT (source_request_id, capability_key) DO UPDATE SET
                            active = true, activated_at = now(),
                            activated_by = EXCLUDED.activated_by,
                            revoked_at = NULL, revoked_by = NULL
                        """,
                        (
                            str(uuid4()),
                            item.request_id,
                            item.requester_id,
                            project_id,
                            capability_key,
                            auth.subject_id,
                        ),
                    )
                self._bump_revision(cursor, item.requester_id)
            elif target_status is AccessRequestStatus.REVOKED:
                cursor.execute(
                    """
                    UPDATE access_control.capability_grants
                    SET active = false, revoked_at = now(), revoked_by = %s
                    WHERE source_request_id = %s::uuid AND active
                    """,
                    (auth.subject_id, item.request_id),
                )
                self._bump_revision(cursor, item.requester_id)
            self._finish_idempotency(
                cursor, auth.subject_id, "capability.decide", idempotency_key, item.request_id
            )
            action = {
                AccessRequestStatus.APPROVED: "access.capability.approved",
                AccessRequestStatus.REJECTED: "access.capability.rejected",
                AccessRequestStatus.REVOKED: "access.capability.revoked",
            }[target_status]
            self._audit_capability(cursor, updated, auth.subject_id, action, request_id)
            connection.commit()
            return updated
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

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
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            repeated = self._begin_idempotency(
                cursor, auth.subject_id, "capability.withdraw", idempotency_key, payload
            )
            if repeated is not None:
                item = self._get_capability(cursor, project_id, repeated)
                connection.commit()
                return item
            item = self._get_capability(cursor, project_id, access_request_id, lock=True)
            if item.requester_id != auth.subject_id:
                raise _not_found("capability_request")
            if item.status is not AccessRequestStatus.PENDING:
                if item.status is AccessRequestStatus.WITHDRAWN:
                    self._finish_idempotency(
                        cursor,
                        auth.subject_id,
                        "capability.withdraw",
                        idempotency_key,
                        item.request_id,
                    )
                    connection.commit()
                    return item
                raise _transition_conflict(item.status)
            updated = self._update_capability_status(
                cursor,
                item,
                AccessRequestStatus.WITHDRAWN,
                actor_id=None,
                reason=reason,
            )
            self._finish_idempotency(
                cursor, auth.subject_id, "capability.withdraw", idempotency_key, item.request_id
            )
            self._audit_capability(
                cursor, updated, auth.subject_id, "access.capability.withdrawn", request_id
            )
            connection.commit()
            return updated
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def list_audit_events(
        self, *, auth: AuthContext, project_id: str
    ) -> tuple[AccessAuditEvent, ...]:
        require_project_manager(auth, project_id)
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT event_id::text AS event_id, scope_kind, project_id, actor_id,
                       action, resource_type, resource_id, request_id, outcome,
                       safe_details, occurred_at
                FROM access_control.audit_events
                WHERE scope_kind = 'PROJECT' AND project_id = %s
                ORDER BY occurred_at DESC, event_id DESC
                """,
                (project_id,),
            )
            items: list[AccessAuditEvent] = []
            for raw in cursor.fetchall():
                values = _row(cursor, raw)
                if isinstance(values["safe_details"], str):
                    values["safe_details"] = json.loads(values["safe_details"])
                items.append(AccessAuditEvent.model_validate(values))
            return tuple(items)
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _begin_idempotency(
        cursor: Any,
        actor_id: str,
        operation: str,
        idempotency_key: str,
        payload: Mapping[str, object],
    ) -> str | None:
        fingerprint = _fingerprint(payload)
        cursor.execute(
            """
            INSERT INTO access_control.command_idempotency (
                actor_id, operation, idempotency_key, request_fingerprint
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT (actor_id, operation, idempotency_key) DO NOTHING
            """,
            (actor_id, operation, idempotency_key, fingerprint),
        )
        cursor.execute(
            """
            SELECT request_fingerprint, resource_id::text AS resource_id
            FROM access_control.command_idempotency
            WHERE actor_id = %s AND operation = %s AND idempotency_key = %s
            FOR UPDATE
            """,
            (actor_id, operation, idempotency_key),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise RuntimeError("PostgreSQL did not retain the idempotency row")
        values = _row(cursor, raw)
        if str(values["request_fingerprint"]) != fingerprint:
            raise problem(
                status=409,
                code="IDEMPOTENCY_KEY_REUSED",
                title="Idempotency key reused",
                detail="The idempotency key was already used with a different request.",
            )
        resource_id = values["resource_id"]
        return None if resource_id is None else str(resource_id)

    @staticmethod
    def _finish_idempotency(
        cursor: Any, actor_id: str, operation: str, idempotency_key: str, resource_id: str
    ) -> None:
        cursor.execute(
            """
            UPDATE access_control.command_idempotency
            SET resource_id = %s::uuid
            WHERE actor_id = %s AND operation = %s AND idempotency_key = %s
            """,
            (resource_id, actor_id, operation, idempotency_key),
        )

    @staticmethod
    def _get_membership(
        cursor: Any, project_id: str, access_request_id: str, *, lock: bool = False
    ) -> MembershipRequest:
        cursor.execute(
            f"""
            SELECT {_MEMBERSHIP_COLUMNS}
            FROM access_control.membership_requests
            WHERE request_id = %s::uuid AND project_id = %s
            {"FOR UPDATE" if lock else ""}
            """,
            (access_request_id, project_id),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise _not_found("membership_request")
        return _membership(cursor, raw)

    @staticmethod
    def _get_capability(
        cursor: Any, project_id: str, access_request_id: str, *, lock: bool = False
    ) -> CapabilityRequest:
        cursor.execute(
            f"""
            SELECT {_CAPABILITY_COLUMNS}
            FROM access_control.capability_requests
            WHERE request_id = %s::uuid AND project_id = %s
            {"FOR UPDATE" if lock else ""}
            """,
            (access_request_id, project_id),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise _not_found("capability_request")
        return _capability(cursor, raw)

    @staticmethod
    def _update_membership_status(
        cursor: Any,
        item: MembershipRequest,
        target_status: AccessRequestStatus,
        *,
        actor_id: str | None,
        reason: str | None,
    ) -> MembershipRequest:
        cursor.execute(
            f"""
            UPDATE access_control.membership_requests
            SET status = %s, decided_by = %s, decision_reason = %s,
                updated_at = now(), revision = revision + 1
            WHERE request_id = %s::uuid AND revision = %s
            RETURNING {_MEMBERSHIP_COLUMNS}
            """,
            (target_status.value, actor_id, reason, item.request_id, item.revision),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise _transition_conflict(item.status)
        return _membership(cursor, raw)

    @staticmethod
    def _update_capability_status(
        cursor: Any,
        item: CapabilityRequest,
        target_status: AccessRequestStatus,
        *,
        actor_id: str | None,
        reason: str | None,
    ) -> CapabilityRequest:
        cursor.execute(
            f"""
            UPDATE access_control.capability_requests
            SET status = %s, decided_by = %s, decision_reason = %s,
                updated_at = now(), revision = revision + 1
            WHERE request_id = %s::uuid AND revision = %s
            RETURNING {_CAPABILITY_COLUMNS}
            """,
            (target_status.value, actor_id, reason, item.request_id, item.revision),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise _transition_conflict(item.status)
        return _capability(cursor, raw)

    @staticmethod
    def _bump_revision(cursor: Any, principal_id: str) -> None:
        cursor.execute(
            """
            UPDATE access_control.accounts
            SET capability_revision = capability_revision + 1
            WHERE principal_id = %s::uuid
            """,
            (principal_id,),
        )

    def _audit_membership(
        self, cursor: Any, item: MembershipRequest, actor_id: str, action: str, request_id: str
    ) -> None:
        self._audit(
            cursor,
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
        self, cursor: Any, item: CapabilityRequest, actor_id: str, action: str, request_id: str
    ) -> None:
        self._audit(
            cursor,
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

    @staticmethod
    def _audit(
        cursor: Any,
        *,
        scope_kind: str,
        project_id: str | None,
        actor_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
        outcome: str,
        safe_details: Mapping[str, object] | None = None,
    ) -> None:
        event_id = str(uuid4())
        safe_payload = {
            "scope_kind": scope_kind,
            "outcome": outcome,
            **dict(safe_details or {}),
        }
        cursor.execute(
            """
            INSERT INTO access_control.audit_events (
                event_id, scope_kind, project_id, actor_id, action,
                resource_type, resource_id, request_id, outcome, safe_details
            ) VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                event_id,
                scope_kind,
                project_id,
                actor_id,
                action,
                resource_type,
                resource_id,
                request_id,
                outcome,
                json.dumps(dict(safe_details or {}), ensure_ascii=False, sort_keys=True),
            ),
        )
        if project_id is None:
            return
        # P20 keeps its compatibility audit API, while project-scoped facts also
        # enter the single cross-domain core audit stream in the same transaction.
        cursor.execute(
            "SELECT set_config('app.project_id', %s, true)",
            (project_id,),
        )
        cursor.execute(
            """
            INSERT INTO core.audit_events (
                audit_id, project_id, region_code, actor_id, action,
                resource_type, resource_id, request_id, before_hash,
                after_hash, details, occurred_at
            ) VALUES (
                %s::uuid, %s, NULL, %s, %s, %s, %s, %s,
                NULL, %s, %s::jsonb, now()
            ) ON CONFLICT (audit_id) DO NOTHING
            """,
            (
                event_id,
                project_id,
                actor_id or "anonymous-subject",
                action,
                resource_type,
                resource_id,
                request_id,
                canonical_hash(safe_payload),
                json.dumps(safe_payload, ensure_ascii=False, sort_keys=True),
            ),
        )
