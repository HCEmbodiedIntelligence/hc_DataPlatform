"""PostgreSQL access repository with row locks and repository-level visibility predicates."""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from hc_data_platform.core.dbapi import fence_connection_if_bound, normalize_postgres_dsn
from hc_data_platform.core.errors import problem

from .access_models import (
    AccessAuditEvent,
    AccessRequestStatus,
    AccountAccessOverview,
    AccountAccessRequest,
    AccountCredential,
    AccountNotification,
    AccountNotificationKind,
    AccountNotificationResourceType,
    AccountNotificationState,
    AccountOrganizationMembership,
    AccountPrincipal,
    AccountProfile,
    AccountProfileRecord,
    AccountProjectMembership,
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
from .access_repository import (
    DEFAULT_MAX_ACTIVE_SESSIONS,
    DEFAULT_SESSION_ABSOLUTE_TTL_SECONDS,
    DEFAULT_SESSION_IDLE_TTL_SECONDS,
    DEFAULT_SESSION_TOUCH_INTERVAL_SECONDS,
    _account_principal_required,
    _account_revision_conflict,
    _bounded_session_retry_after_seconds,
    _fingerprint,
    _last_platform_admin_conflict,
    _not_found,
    _session_invalid,
    _session_lifecycle_durations,
    _transition_conflict,
    can_manage_project,
    require_project_manager,
)
from .admin_accounts import (
    ManagedAccount,
    ManagedAccountPage,
    ManagedAccountState,
    PlatformAccountRole,
    recovery_email_hint,
)
from .audit import canonical_hash
from .auth import AuthContext
from .capabilities import CAPABILITY_PLATFORM_ADMIN, PLATFORM_ADMIN_CAPABILITIES
from .recovery import PasswordRecoveryCredential, PasswordRecoveryTarget


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
request_id::text AS request_id, organization_id, project_id, requester_id::text AS requester_id,
status, reason, decided_by, decision_reason, created_at, updated_at, revision
""".strip()

_CAPABILITY_COLUMNS = """
request_id::text AS request_id, organization_id, project_id, requester_id::text AS requester_id,
capability_keys, status, reason, decided_by, decision_reason,
created_at, updated_at, revision
""".strip()

_ORGANIZATION_REQUEST_COLUMNS = """
request_id::text AS request_id, 'ORGANIZATION' AS kind, organization_id,
NULL::text AS project_id, ARRAY[]::text[] AS capability_keys, status, reason,
created_at, updated_at, revision
""".strip()

_ACCOUNT_PROFILE_COLUMNS = """
principal_id::text AS principal_id, display_username AS username, display_name,
status, created_at, updated_at, password_changed_at, account_revision AS revision
""".strip()

_MANAGED_ACCOUNT_COLUMNS = """
account.principal_id::text AS principal_id,
account.display_username AS username,
account.display_name,
CASE
    WHEN account.deleted_at IS NOT NULL THEN 'DELETED'
    ELSE account.status
END AS state,
CASE
    WHEN EXISTS (
        SELECT 1
        FROM access_control.platform_capability_grants platform_grant
        WHERE platform_grant.principal_id = account.principal_id
          AND platform_grant.capability_key = 'platform.admin'
          AND platform_grant.active
    ) THEN 'PLATFORM_ADMIN'
    ELSE 'USER'
END AS platform_role,
account.recovery_email,
(
    SELECT count(*)
    FROM access_control.sessions active_session
    WHERE active_session.principal_id = account.principal_id
      AND active_session.revoked_at IS NULL
) AS active_session_count,
account.created_at,
account.updated_at,
account.password_changed_at,
account.deleted_at,
account.account_revision AS revision
""".strip()

_NOTIFICATION_COLUMNS = """
notification_id::text AS notification_id, kind, organization_id, project_id,
resource_type, resource_id, state, created_at, read_at
""".strip()


def _account_profile(cursor: Any, raw: object) -> AccountProfileRecord:
    values = _row(cursor, raw)
    return AccountProfileRecord(
        principal_id=str(values["principal_id"]),
        username=str(values["username"]),
        display_name=str(values["display_name"]),
        status=AccountStatus(str(values["status"])),
        created_at=cast(datetime, values["created_at"]),
        updated_at=cast(datetime, values["updated_at"]),
        password_changed_at=cast(datetime, values["password_changed_at"]),
        revision=int(cast(Any, values["revision"])),
    )


def _organization_request(cursor: Any, raw: object) -> AccountAccessRequest:
    values = _row(cursor, raw)
    values["capability_keys"] = tuple(cast(Sequence[str], values["capability_keys"]))
    return AccountAccessRequest.model_validate(values)


def _notification(cursor: Any, raw: object) -> AccountNotification:
    return AccountNotification.model_validate(_row(cursor, raw))


def _account_profile_from_payload(payload: Mapping[str, object]) -> AccountProfileRecord:
    profile = AccountProfile.model_validate(payload)
    return AccountProfileRecord(
        principal_id=profile.principal_id,
        username=profile.username,
        display_name=profile.display_name,
        status=profile.status,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        password_changed_at=profile.password_changed_at,
        revision=profile.revision,
    )


def _managed_account(cursor: Any, raw: object) -> ManagedAccount:
    values = _row(cursor, raw)
    recovery_email = cast(str | None, values.pop("recovery_email", None))
    revision = int(cast(Any, values["revision"]))
    values["recovery_email_configured"] = recovery_email is not None
    values["recovery_email_hint"] = recovery_email_hint(recovery_email)
    values["etag"] = f'"v{revision}"'
    return ManagedAccount.model_validate(values)


def _principal_uuid(value: str) -> str | None:
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _foreign_key_constraint_name(exc: BaseException) -> str | None:
    if getattr(exc, "sqlstate", None) != "23503":
        return None
    diagnostic = getattr(exc, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    return constraint_name if isinstance(constraint_name, str) else None


def _organization_project_not_found() -> Exception:
    return problem(
        status=404,
        code="PROJECT_NOT_FOUND",
        title="Project not found",
        detail="The requested project does not exist in the specified organization.",
    )


class PostgresAccessRepository:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        session_idle_ttl_seconds: int = DEFAULT_SESSION_IDLE_TTL_SECONDS,
        session_absolute_ttl_seconds: int = DEFAULT_SESSION_ABSOLUTE_TTL_SECONDS,
        session_touch_interval_seconds: int = DEFAULT_SESSION_TOUCH_INTERVAL_SECONDS,
        max_active_sessions: int = DEFAULT_MAX_ACTIVE_SESSIONS,
    ) -> None:
        _session_lifecycle_durations(
            session_idle_ttl_seconds=session_idle_ttl_seconds,
            session_absolute_ttl_seconds=session_absolute_ttl_seconds,
            session_touch_interval_seconds=session_touch_interval_seconds,
            max_active_sessions=max_active_sessions,
        )
        self._connection_factory: Callable[[], Any] = lambda: fence_connection_if_bound(
            connection_factory()
        )
        self._session_idle_ttl_seconds = session_idle_ttl_seconds
        self._session_absolute_ttl_seconds = session_absolute_ttl_seconds
        self._session_touch_interval_seconds = session_touch_interval_seconds
        self._max_active_sessions = max_active_sessions

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        session_idle_ttl_seconds: int = DEFAULT_SESSION_IDLE_TTL_SECONDS,
        session_absolute_ttl_seconds: int = DEFAULT_SESSION_ABSOLUTE_TTL_SECONDS,
        session_touch_interval_seconds: int = DEFAULT_SESSION_TOUCH_INTERVAL_SECONDS,
        max_active_sessions: int = DEFAULT_MAX_ACTIVE_SESSIONS,
    ) -> PostgresAccessRepository:
        normalized = normalize_postgres_dsn(dsn)

        def connect() -> Any:
            import psycopg

            return psycopg.connect(normalized)

        return cls(
            connect,
            session_idle_ttl_seconds=session_idle_ttl_seconds,
            session_absolute_ttl_seconds=session_absolute_ttl_seconds,
            session_touch_interval_seconds=session_touch_interval_seconds,
            max_active_sessions=max_active_sessions,
        )

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
                    display_name, password_hash, capability_revision,
                    account_revision, credential_revision, password_changed_at
                ) VALUES (%s::uuid, %s, %s, 'ACTIVE', %s, %s, 0, 1, 1, now())
                RETURNING principal_id::text AS principal_id,
                          display_username AS username, display_name, status, created_at
                """,
                (
                    principal_id,
                    canonical_username,
                    display_username,
                    display_username,
                    password_hash,
                ),
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
                       display_name, status, created_at, canonical_username, password_hash,
                       capability_revision, account_revision, credential_revision,
                       updated_at, password_changed_at, recovery_email
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
                    display_name=str(values["display_name"]),
                    status=AccountStatus(str(values["status"])),
                    created_at=cast(datetime, values["created_at"]),
                ),
                canonical_username=str(values["canonical_username"]),
                password_hash=str(values["password_hash"]),
                capability_revision=int(cast(Any, values["capability_revision"])),
                account_revision=int(cast(Any, values["account_revision"])),
                credential_revision=int(cast(Any, values["credential_revision"])),
                updated_at=cast(datetime, values["updated_at"]),
                password_changed_at=cast(datetime, values["password_changed_at"]),
                recovery_email=cast(str | None, values["recovery_email"]),
            )
        finally:
            cursor.close()
            connection.close()

    def credential_for_principal(self, principal_id: str) -> AccountCredential | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT principal_id::text AS principal_id, display_username AS username,
                       display_name, status, created_at, canonical_username, password_hash,
                       capability_revision, account_revision, credential_revision,
                       updated_at, password_changed_at, recovery_email
                FROM access_control.accounts
                WHERE principal_id = %s::uuid
                """,
                (principal_id,),
            )
            raw = cursor.fetchone()
            if raw is None:
                return None
            values = _row(cursor, raw)
            return AccountCredential(
                principal=AccountPrincipal(
                    principal_id=str(values["principal_id"]),
                    username=str(values["username"]),
                    display_name=str(values["display_name"]),
                    status=AccountStatus(str(values["status"])),
                    created_at=cast(datetime, values["created_at"]),
                ),
                canonical_username=str(values["canonical_username"]),
                password_hash=str(values["password_hash"]),
                capability_revision=int(cast(Any, values["capability_revision"])),
                account_revision=int(cast(Any, values["account_revision"])),
                credential_revision=int(cast(Any, values["credential_revision"])),
                updated_at=cast(datetime, values["updated_at"]),
                password_changed_at=cast(datetime, values["password_changed_at"]),
                recovery_email=cast(str | None, values["recovery_email"]),
            )
        finally:
            cursor.close()
            connection.close()

    def upgrade_password_hash(
        self,
        *,
        principal_id: str,
        expected_password_hash: str,
        upgraded_password_hash: str,
        request_id: str,
    ) -> bool:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE access_control.accounts
                SET password_hash = %s, updated_at = now()
                WHERE principal_id = %s::uuid AND password_hash = %s
                RETURNING principal_id::text AS principal_id
                """,
                (upgraded_password_hash, principal_id, expected_password_hash),
            )
            upgraded = cursor.fetchone() is not None
            if upgraded:
                self._audit(
                    cursor,
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
            connection.commit()
            return upgraded
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def get_account_profile(self, principal_id: str) -> AccountProfileRecord:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT {_ACCOUNT_PROFILE_COLUMNS}
                FROM access_control.accounts
                WHERE principal_id = %s::uuid
                """,
                (principal_id,),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise _not_found("account")
            return _account_profile(cursor, raw)
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _database_timestamp(cursor: Any) -> datetime:
        cursor.execute("SELECT clock_timestamp() AS checked_at")
        raw = cursor.fetchone()
        if raw is None:
            raise RuntimeError("PostgreSQL did not return a lifecycle timestamp")
        return cast(datetime, _row(cursor, raw)["checked_at"])

    def _session_expired_at(
        self,
        session: Mapping[str, object],
        timestamp: datetime,
    ) -> bool:
        return timestamp >= cast(datetime, session["last_seen_at"]) + timedelta(
            seconds=self._session_idle_ttl_seconds
        ) or timestamp >= cast(datetime, session["issued_at"]) + timedelta(
            seconds=self._session_absolute_ttl_seconds
        )

    def _mark_locked_session_invalid(
        self,
        cursor: Any,
        session: dict[str, object],
        *,
        reason: str,
        timestamp: datetime,
        request_id: str,
    ) -> bool:
        if session["revoked_at"] is not None:
            return False
        cursor.execute(
            """
            UPDATE access_control.sessions
            SET revoked_at = %s, revocation_reason = %s
            WHERE session_id = %s::uuid AND revoked_at IS NULL
            RETURNING session_id::text AS session_id
            """,
            (timestamp, reason, str(session["session_id"])),
        )
        if cursor.fetchone() is None:
            return False
        session["revoked_at"] = timestamp
        session["revocation_reason"] = reason
        self._audit(
            cursor,
            scope_kind="PLATFORM",
            project_id=None,
            actor_id=str(session["principal_id"]),
            action="auth.session.expired" if reason == "EXPIRED" else "auth.session.revoked",
            resource_type="session",
            resource_id=str(session["session_id"]),
            request_id=request_id,
            outcome="SUCCEEDED",
            safe_details={"reason": reason},
            occurred_at=timestamp,
        )
        return True

    def _lock_valid_mutation_session(
        self,
        cursor: Any,
        *,
        principal_id: str,
        current_token_hash: str,
        request_id: str,
        lock_all_sessions: bool = False,
    ) -> (
        tuple[
            dict[str, object],
            dict[str, object],
            datetime,
            tuple[dict[str, object], ...],
        ]
        | None
    ):
        cursor.execute(
            """
            SELECT password_hash, account_revision, credential_revision, status
            FROM access_control.accounts
            WHERE principal_id = %s::uuid
            FOR UPDATE
            """,
            (principal_id,),
        )
        raw_account = cursor.fetchone()
        if raw_account is None:
            return None
        account = _row(cursor, raw_account)
        if lock_all_sessions:
            cursor.execute(
                """
                SELECT session_id::text AS session_id,
                       principal_id::text AS principal_id,
                       token_hash, credential_revision, issued_at, last_seen_at,
                       revoked_at, revocation_reason
                FROM access_control.sessions
                WHERE principal_id = %s::uuid
                  AND (revoked_at IS NULL OR token_hash = %s)
                ORDER BY session_id
                FOR UPDATE
                """,
                (principal_id, current_token_hash),
            )
        else:
            cursor.execute(
                """
                SELECT session_id::text AS session_id,
                       principal_id::text AS principal_id,
                       token_hash, credential_revision, issued_at, last_seen_at,
                       revoked_at, revocation_reason
                FROM access_control.sessions
                WHERE principal_id = %s::uuid AND token_hash = %s
                FOR UPDATE
                """,
                (principal_id, current_token_hash),
            )
        sessions = tuple(_row(cursor, raw) for raw in cursor.fetchall())
        timestamp = self._database_timestamp(cursor)
        current = next(
            (session for session in sessions if str(session["token_hash"]) == current_token_hash),
            None,
        )
        if current is None or current["revoked_at"] is not None:
            return None

        if self._session_expired_at(current, timestamp):
            self._mark_locked_session_invalid(
                cursor,
                current,
                reason="EXPIRED",
                timestamp=timestamp,
                request_id=request_id,
            )

        if str(account["status"]) != AccountStatus.ACTIVE.value:
            self._mark_locked_session_invalid(
                cursor,
                current,
                reason="ACCOUNT_DISABLED",
                timestamp=timestamp,
                request_id=request_id,
            )
        elif int(cast(Any, current["credential_revision"])) != int(
            cast(Any, account["credential_revision"])
        ):
            self._mark_locked_session_invalid(
                cursor,
                current,
                reason="PASSWORD_CHANGED",
                timestamp=timestamp,
                request_id=request_id,
            )

        if current["revoked_at"] is not None:
            return None
        return account, current, timestamp, sessions

    def _classify_password_change_peers(
        self,
        cursor: Any,
        *,
        account: dict[str, object],
        current_token_hash: str,
        sessions: tuple[dict[str, object], ...],
        timestamp: datetime,
        request_id: str,
    ) -> None:
        credential_revision = int(cast(Any, account["credential_revision"]))
        for session in sessions:
            if (
                str(session["token_hash"]) == current_token_hash
                or session["revoked_at"] is not None
            ):
                continue
            if self._session_expired_at(session, timestamp):
                self._mark_locked_session_invalid(
                    cursor,
                    session,
                    reason="EXPIRED",
                    timestamp=timestamp,
                    request_id=request_id,
                )
            elif int(cast(Any, session["credential_revision"])) != credential_revision:
                self._mark_locked_session_invalid(
                    cursor,
                    session,
                    reason="PASSWORD_CHANGED",
                    timestamp=timestamp,
                    request_id=request_id,
                )

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
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            if (
                self._lock_valid_mutation_session(
                    cursor,
                    principal_id=principal_id,
                    current_token_hash=current_token_hash,
                    request_id=request_id,
                )
                is None
            ):
                connection.commit()
                raise _session_invalid()
            cursor.execute(
                """
                SELECT request_fingerprint, result_payload
                FROM access_control.command_idempotency
                WHERE actor_id = %s AND operation = %s AND idempotency_key = %s
                """,
                (principal_id, operation, idempotency_key),
            )
            raw = cursor.fetchone()
            if raw is None:
                connection.commit()
                return None
            values = _row(cursor, raw)
            if not secrets.compare_digest(str(values["request_fingerprint"]), request_fingerprint):
                raise problem(
                    status=409,
                    code="IDEMPOTENCY_KEY_REUSED",
                    title="Idempotency key reused",
                    detail="The idempotency key was already used with a different request.",
                )
            payload = values["result_payload"]
            if payload is None:
                connection.commit()
                return None
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, Mapping):
                raise RuntimeError("account idempotency result is not an object")
            profile = _account_profile_from_payload(cast(Mapping[str, object], payload["profile"]))
            connection.commit()
            return profile, int(cast(Any, payload.get("other_sessions_revoked", 0)))
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

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
        connection = self._connection_factory()
        cursor = connection.cursor()
        operation = "account.profile.update"
        try:
            locked = self._lock_valid_mutation_session(
                cursor,
                principal_id=principal_id,
                current_token_hash=current_token_hash,
                request_id=request_id,
            )
            if locked is None:
                connection.commit()
                raise _session_invalid()
            _, _, timestamp, _ = locked
            replay = self._begin_account_idempotency(
                cursor,
                principal_id,
                operation,
                idempotency_key,
                request_fingerprint,
            )
            if replay is not None:
                profile = _account_profile_from_payload(
                    cast(Mapping[str, object], replay["profile"])
                )
                connection.commit()
                return profile
            cursor.execute(
                f"""
                UPDATE access_control.accounts
                SET display_name = %s, updated_at = %s,
                    account_revision = account_revision + 1
                WHERE principal_id = %s::uuid AND account_revision = %s
                RETURNING {_ACCOUNT_PROFILE_COLUMNS}
                """,
                (display_name, timestamp, principal_id, expected_revision),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise _account_revision_conflict(
                    self._current_account_revision(cursor, principal_id)
                )
            profile = _account_profile(cursor, raw)
            self._audit(
                cursor,
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
            self._finish_account_idempotency(
                cursor,
                principal_id,
                operation,
                idempotency_key,
                {"profile": profile.to_profile().model_dump(mode="json")},
            )
            connection.commit()
            return profile
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

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
        connection = self._connection_factory()
        cursor = connection.cursor()
        operation = "account.password.change"
        try:
            locked = self._lock_valid_mutation_session(
                cursor,
                principal_id=principal_id,
                current_token_hash=current_token_hash,
                request_id=request_id,
                lock_all_sessions=True,
            )
            if locked is None:
                connection.commit()
                raise _session_invalid()
            account, _, timestamp, sessions = locked
            replay = self._begin_account_idempotency(
                cursor,
                principal_id,
                operation,
                idempotency_key,
                request_fingerprint,
            )
            if replay is not None:
                profile = _account_profile_from_payload(
                    cast(Mapping[str, object], replay["profile"])
                )
                connection.commit()
                return profile, int(cast(Any, replay["other_sessions_revoked"]))
            current_revision = int(cast(Any, account["account_revision"]))
            if current_revision != expected_revision:
                raise _account_revision_conflict(current_revision)
            if not secrets.compare_digest(str(account["password_hash"]), expected_password_hash):
                raise _account_revision_conflict(current_revision)
            self._classify_password_change_peers(
                cursor,
                account=account,
                current_token_hash=current_token_hash,
                sessions=sessions,
                timestamp=timestamp,
                request_id=request_id,
            )
            next_credential_revision = int(cast(Any, account["credential_revision"])) + 1
            cursor.execute(
                f"""
                UPDATE access_control.accounts
                SET password_hash = %s,
                    credential_revision = %s,
                    account_revision = account_revision + 1,
                    password_changed_at = %s, updated_at = %s
                WHERE principal_id = %s::uuid AND account_revision = %s
                RETURNING {_ACCOUNT_PROFILE_COLUMNS}
                """,
                (
                    new_password_hash,
                    next_credential_revision,
                    timestamp,
                    timestamp,
                    principal_id,
                    expected_revision,
                ),
            )
            raw_profile = cursor.fetchone()
            if raw_profile is None:
                raise _account_revision_conflict(
                    self._current_account_revision(cursor, principal_id)
                )
            profile = _account_profile(cursor, raw_profile)
            cursor.execute(
                """
                UPDATE access_control.sessions
                SET credential_revision = %s
                WHERE principal_id = %s::uuid AND token_hash = %s
                  AND revoked_at IS NULL
                """,
                (next_credential_revision, principal_id, current_token_hash),
            )
            cursor.execute(
                """
                UPDATE access_control.sessions
                SET revoked_at = %s, revocation_reason = 'PASSWORD_CHANGED'
                WHERE principal_id = %s::uuid AND token_hash <> %s
                  AND credential_revision = %s
                  AND revoked_at IS NULL
                RETURNING session_id::text AS session_id
                """,
                (
                    timestamp,
                    principal_id,
                    current_token_hash,
                    next_credential_revision - 1,
                ),
            )
            revoked_session_ids = tuple(
                str(_row(cursor, raw)["session_id"]) for raw in cursor.fetchall()
            )
            other_sessions_revoked = len(revoked_session_ids)
            for revoked_session_id in revoked_session_ids:
                self._audit(
                    cursor,
                    scope_kind="PLATFORM",
                    project_id=None,
                    actor_id=principal_id,
                    action="auth.session.revoked",
                    resource_type="session",
                    resource_id=revoked_session_id,
                    request_id=request_id,
                    outcome="SUCCEEDED",
                    safe_details={"reason": "PASSWORD_CHANGED"},
                    occurred_at=timestamp,
                )
            self._audit(
                cursor,
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
            self._finish_account_idempotency(
                cursor,
                principal_id,
                operation,
                idempotency_key,
                {
                    "profile": profile.to_profile().model_dump(mode="json"),
                    "other_sessions_revoked": other_sessions_revoked,
                },
            )
            connection.commit()
            return profile, other_sessions_revoked
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def issue_recovery_email_verification(
        self,
        *,
        principal_id: str,
        recovery_email: str,
        token_hash: str,
        expires_at: datetime,
        request_id: str,
    ) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT status, credential_revision
                FROM access_control.accounts
                WHERE principal_id = %s::uuid
                FOR UPDATE
                """,
                (principal_id,),
            )
            raw = cursor.fetchone()
            if raw is None or str(_row(cursor, raw)["status"]) != "ACTIVE":
                raise _not_found("account")
            credential_revision = int(cast(Any, _row(cursor, raw)["credential_revision"]))
            cursor.execute(
                """
                SELECT 1
                FROM access_control.accounts
                WHERE recovery_email = %s AND principal_id <> %s::uuid
                """,
                (recovery_email, principal_id),
            )
            if cursor.fetchone() is not None:
                raise problem(
                    status=409,
                    code="RECOVERY_EMAIL_CONFLICT",
                    title="Recovery email conflict",
                    detail="The recovery email cannot be assigned to this account.",
                )
            timestamp = self._database_timestamp(cursor)
            if expires_at <= timestamp:
                raise ValueError("recovery email verification expiry must be in the future")
            cursor.execute(
                """
                UPDATE access_control.account_security_challenges
                SET consumed_at = %s
                WHERE principal_id = %s::uuid
                  AND purpose = 'RECOVERY_EMAIL_VERIFY'
                  AND consumed_at IS NULL
                """,
                (timestamp, principal_id),
            )
            cursor.execute(
                """
                INSERT INTO access_control.account_security_challenges (
                    challenge_id, principal_id, purpose, target_email, token_hash,
                    credential_revision, created_at, expires_at, created_request_id
                ) VALUES (
                    %s::uuid, %s::uuid, 'RECOVERY_EMAIL_VERIFY', %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    str(uuid4()),
                    principal_id,
                    recovery_email,
                    token_hash,
                    credential_revision,
                    timestamp,
                    expires_at,
                    request_id,
                ),
            )
            self._audit(
                cursor,
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
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def confirm_recovery_email(
        self,
        *,
        principal_id: str,
        token_hash: str,
        now: datetime,
        request_id: str,
    ) -> str | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT challenge.target_email
                FROM access_control.account_security_challenges AS challenge
                JOIN access_control.accounts AS account
                  ON account.principal_id = challenge.principal_id
                WHERE challenge.principal_id = %s::uuid
                  AND challenge.token_hash = %s
                  AND challenge.purpose = 'RECOVERY_EMAIL_VERIFY'
                  AND challenge.consumed_at IS NULL
                  AND challenge.expires_at > %s
                  AND account.status = 'ACTIVE'
                  AND account.credential_revision = challenge.credential_revision
                FOR UPDATE OF challenge, account
                """,
                (principal_id, token_hash, now),
            )
            raw = cursor.fetchone()
            if raw is None:
                connection.rollback()
                return None
            recovery_email = str(_row(cursor, raw)["target_email"])
            cursor.execute(
                """
                UPDATE access_control.accounts
                SET recovery_email = %s,
                    account_revision = account_revision + 1,
                    updated_at = %s
                WHERE principal_id = %s::uuid
                """,
                (recovery_email, now, principal_id),
            )
            cursor.execute(
                """
                UPDATE access_control.account_security_challenges
                SET consumed_at = %s
                WHERE principal_id = %s::uuid
                  AND consumed_at IS NULL
                  AND (token_hash = %s OR purpose = 'PASSWORD_RECOVERY')
                """,
                (now, principal_id, token_hash),
            )
            self._audit(
                cursor,
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
            connection.commit()
            return recovery_email
        except Exception as exc:
            connection.rollback()
            if getattr(exc, "sqlstate", None) == "23505":
                return None
            raise
        finally:
            cursor.close()
            connection.close()

    def issue_password_recovery(
        self,
        *,
        canonical_identifier: str,
        token_hash: str,
        expires_at: datetime,
        request_id: str,
    ) -> PasswordRecoveryTarget | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT principal_id::text AS principal_id,
                       display_username, recovery_email, credential_revision
                FROM access_control.accounts
                WHERE status = 'ACTIVE'
                  AND recovery_email IS NOT NULL
                  AND (canonical_username = %s OR recovery_email = %s)
                FOR UPDATE
                """,
                (canonical_identifier, canonical_identifier),
            )
            raw = cursor.fetchone()
            if raw is None:
                connection.rollback()
                return None
            values = _row(cursor, raw)
            principal_id = str(values["principal_id"])
            recovery_email = str(values["recovery_email"])
            credential_revision = int(cast(Any, values["credential_revision"]))
            timestamp = self._database_timestamp(cursor)
            if expires_at <= timestamp:
                raise ValueError("password recovery expiry must be in the future")
            cursor.execute(
                """
                UPDATE access_control.account_security_challenges
                SET consumed_at = %s
                WHERE principal_id = %s::uuid
                  AND purpose = 'PASSWORD_RECOVERY'
                  AND consumed_at IS NULL
                """,
                (timestamp, principal_id),
            )
            cursor.execute(
                """
                INSERT INTO access_control.account_security_challenges (
                    challenge_id, principal_id, purpose, target_email, token_hash,
                    credential_revision, created_at, expires_at, created_request_id
                ) VALUES (
                    %s::uuid, %s::uuid, 'PASSWORD_RECOVERY', %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    str(uuid4()),
                    principal_id,
                    recovery_email,
                    token_hash,
                    credential_revision,
                    timestamp,
                    expires_at,
                    request_id,
                ),
            )
            self._audit(
                cursor,
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
            connection.commit()
            return PasswordRecoveryTarget(
                principal_id=principal_id,
                display_username=str(values["display_username"]),
                recovery_email=recovery_email,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def password_recovery_credential(
        self,
        *,
        token_hash: str,
        now: datetime,
    ) -> PasswordRecoveryCredential | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT account.principal_id::text AS principal_id,
                       account.canonical_username, account.password_hash
                FROM access_control.account_security_challenges AS challenge
                JOIN access_control.accounts AS account
                  ON account.principal_id = challenge.principal_id
                WHERE challenge.token_hash = %s
                  AND challenge.purpose = 'PASSWORD_RECOVERY'
                  AND challenge.consumed_at IS NULL
                  AND challenge.expires_at > %s
                  AND account.status = 'ACTIVE'
                  AND account.credential_revision = challenge.credential_revision
                """,
                (token_hash, now),
            )
            raw = cursor.fetchone()
            if raw is None:
                return None
            values = _row(cursor, raw)
            return PasswordRecoveryCredential(
                principal_id=str(values["principal_id"]),
                canonical_username=str(values["canonical_username"]),
                password_hash=str(values["password_hash"]),
            )
        finally:
            cursor.close()
            connection.close()

    def complete_password_recovery(
        self,
        *,
        token_hash: str,
        expected_password_hash: str,
        new_password_hash: str,
        now: datetime,
        request_id: str,
    ) -> int | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT account.principal_id::text AS principal_id,
                       account.password_hash, account.credential_revision
                FROM access_control.account_security_challenges AS challenge
                JOIN access_control.accounts AS account
                  ON account.principal_id = challenge.principal_id
                WHERE challenge.token_hash = %s
                  AND challenge.purpose = 'PASSWORD_RECOVERY'
                  AND challenge.consumed_at IS NULL
                  AND challenge.expires_at > %s
                  AND account.status = 'ACTIVE'
                  AND account.credential_revision = challenge.credential_revision
                FOR UPDATE OF challenge, account
                """,
                (token_hash, now),
            )
            raw = cursor.fetchone()
            if raw is None:
                connection.rollback()
                return None
            values = _row(cursor, raw)
            if not secrets.compare_digest(str(values["password_hash"]), expected_password_hash):
                connection.rollback()
                return None
            principal_id = str(values["principal_id"])
            cursor.execute(
                """
                SELECT session_id
                FROM access_control.sessions
                WHERE principal_id = %s::uuid AND revoked_at IS NULL
                ORDER BY session_id
                FOR UPDATE
                """,
                (principal_id,),
            )
            cursor.fetchall()
            cursor.execute(
                """
                UPDATE access_control.account_security_challenges
                SET consumed_at = %s
                WHERE principal_id = %s::uuid
                  AND purpose = 'PASSWORD_RECOVERY'
                  AND consumed_at IS NULL
                """,
                (now, principal_id),
            )
            cursor.execute(
                """
                UPDATE access_control.accounts
                SET password_hash = %s,
                    credential_revision = credential_revision + 1,
                    account_revision = account_revision + 1,
                    password_changed_at = %s,
                    updated_at = %s
                WHERE principal_id = %s::uuid AND password_hash = %s
                """,
                (new_password_hash, now, now, principal_id, expected_password_hash),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            cursor.execute(
                """
                UPDATE access_control.sessions
                SET revoked_at = %s, revocation_reason = 'PASSWORD_RECOVERED'
                WHERE principal_id = %s::uuid AND revoked_at IS NULL
                RETURNING session_id::text AS session_id
                """,
                (now, principal_id),
            )
            revoked_session_ids = tuple(
                str(_row(cursor, item)["session_id"]) for item in cursor.fetchall()
            )
            for session_id in revoked_session_ids:
                self._audit(
                    cursor,
                    scope_kind="PLATFORM",
                    project_id=None,
                    actor_id=principal_id,
                    action="auth.session.revoked",
                    resource_type="session",
                    resource_id=session_id,
                    request_id=request_id,
                    outcome="SUCCEEDED",
                    safe_details={"reason": "PASSWORD_RECOVERED"},
                    occurred_at=now,
                )
            self._audit(
                cursor,
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal_id,
                action="auth.password.recovered",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"sessions_revoked": len(revoked_session_ids)},
                occurred_at=now,
            )
            connection.commit()
            return len(revoked_session_ids)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def create_session(
        self,
        *,
        principal_id: str,
        token_hash: str,
        expected_password_hash: str,
        expected_credential_revision: int,
        request_id: str,
    ) -> SessionIssueResult:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT credential_revision
                FROM access_control.accounts
                WHERE principal_id = %s::uuid
                  AND status = 'ACTIVE'
                  AND password_hash = %s
                  AND credential_revision = %s
                FOR UPDATE
                """,
                (
                    principal_id,
                    expected_password_hash,
                    expected_credential_revision,
                ),
            )
            raw_account = cursor.fetchone()
            if raw_account is None:
                return SessionIssueStaleCredentials()
            credential_revision = int(cast(Any, _row(cursor, raw_account)["credential_revision"]))
            cursor.execute(
                """
                SELECT session_id
                FROM access_control.sessions
                WHERE principal_id = %s::uuid AND revoked_at IS NULL
                ORDER BY session_id
                FOR UPDATE
                """,
                (principal_id,),
            )
            cursor.fetchall()
            timestamp = self._database_timestamp(cursor)
            cursor.execute(
                """
                UPDATE access_control.sessions
                SET revoked_at = %s, revocation_reason = 'EXPIRED'
                WHERE principal_id = %s::uuid
                  AND revoked_at IS NULL
                  AND (
                    last_seen_at <= %s - make_interval(secs => %s)
                    OR issued_at <= %s - make_interval(secs => %s)
                  )
                RETURNING session_id::text AS session_id
                """,
                (
                    timestamp,
                    principal_id,
                    timestamp,
                    self._session_idle_ttl_seconds,
                    timestamp,
                    self._session_absolute_ttl_seconds,
                ),
            )
            expired_session_ids = tuple(
                str(_row(cursor, raw)["session_id"]) for raw in cursor.fetchall()
            )
            cursor.execute(
                """
                UPDATE access_control.sessions
                SET revoked_at = %s, revocation_reason = 'PASSWORD_CHANGED'
                WHERE principal_id = %s::uuid
                  AND credential_revision <> %s
                  AND revoked_at IS NULL
                RETURNING session_id::text AS session_id
                """,
                (timestamp, principal_id, credential_revision),
            )
            invalid_session_ids = tuple(
                str(_row(cursor, raw)["session_id"]) for raw in cursor.fetchall()
            )
            for expired_session_id in expired_session_ids:
                self._audit(
                    cursor,
                    scope_kind="PLATFORM",
                    project_id=None,
                    actor_id=principal_id,
                    action="auth.session.expired",
                    resource_type="session",
                    resource_id=expired_session_id,
                    request_id=request_id,
                    outcome="SUCCEEDED",
                    safe_details={"reason": "EXPIRED"},
                    occurred_at=timestamp,
                )
            for invalid_session_id in invalid_session_ids:
                self._audit(
                    cursor,
                    scope_kind="PLATFORM",
                    project_id=None,
                    actor_id=principal_id,
                    action="auth.session.revoked",
                    resource_type="session",
                    resource_id=invalid_session_id,
                    request_id=request_id,
                    outcome="SUCCEEDED",
                    safe_details={"reason": "PASSWORD_CHANGED"},
                    occurred_at=timestamp,
                )
            cursor.execute(
                """
                SELECT count(*) AS active_count,
                       min(
                           LEAST(
                               last_seen_at + make_interval(secs => %s),
                               issued_at + make_interval(secs => %s)
                           )
                       ) AS earliest_expires_at
                FROM access_control.sessions
                WHERE principal_id = %s::uuid
                  AND credential_revision = %s
                  AND revoked_at IS NULL
                """,
                (
                    self._session_idle_ttl_seconds,
                    self._session_absolute_ttl_seconds,
                    principal_id,
                    credential_revision,
                ),
            )
            capacity = _row(cursor, cursor.fetchone())
            active_count = int(cast(Any, capacity["active_count"]))
            if active_count >= self._max_active_sessions:
                earliest_expires_at = capacity["earliest_expires_at"]
                if not isinstance(earliest_expires_at, datetime):
                    raise RuntimeError("active sessions have no expiry timestamp")
                retry_after_seconds = _bounded_session_retry_after_seconds(
                    expires_at=earliest_expires_at,
                    timestamp=timestamp,
                )
                self._audit(
                    cursor,
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
                self._audit(
                    cursor,
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
                connection.commit()
                return SessionIssueCapacityRejected(retry_after_seconds=retry_after_seconds)

            session_id = str(uuid4())
            cursor.execute(
                """
                INSERT INTO access_control.sessions (
                    session_id, principal_id, token_hash, credential_revision,
                    issued_at, last_seen_at
                ) VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    principal_id,
                    token_hash,
                    credential_revision,
                    timestamp,
                    timestamp,
                ),
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
                occurred_at=timestamp,
            )
            self._audit(
                cursor,
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
            connection.commit()
            return SessionIssued(session_id=session_id)
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def resolve_session(
        self,
        token_hash: str,
        *,
        request_id: str | None = None,
        allow_session_mutation: bool = True,
    ) -> ResolvedSession | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        audit_request_id = request_id or str(uuid4())
        try:
            cursor.execute(
                """
                SELECT principal_id::text AS principal_id
                FROM access_control.sessions
                WHERE token_hash = %s
                """,
                (token_hash,),
            )
            raw_principal = cursor.fetchone()
            if raw_principal is None:
                return None
            principal_id = str(_row(cursor, raw_principal)["principal_id"])
            cursor.execute(
                f"""
                SELECT {_ACCOUNT_PROFILE_COLUMNS}, capability_revision,
                       credential_revision
                FROM access_control.accounts
                WHERE principal_id = %s::uuid
                FOR SHARE
                """,
                (principal_id,),
            )
            raw_account = cursor.fetchone()
            if raw_account is None:
                return None
            account = _row(cursor, raw_account)
            session_lock = "FOR UPDATE" if allow_session_mutation else "FOR SHARE"
            cursor.execute(
                f"""
                SELECT session_id::text AS session_id,
                       principal_id::text AS principal_id,
                       token_hash, credential_revision, issued_at, last_seen_at,
                       revoked_at, revocation_reason
                FROM access_control.sessions
                WHERE token_hash = %s AND principal_id = %s::uuid
                {session_lock}
                """,
                (token_hash, principal_id),
            )
            raw_session = cursor.fetchone()
            if raw_session is None:
                return None
            session = _row(cursor, raw_session)
            timestamp = self._database_timestamp(cursor)
            if session["revoked_at"] is not None:
                return None
            reason: str | None = None
            if self._session_expired_at(session, timestamp):
                reason = "EXPIRED"
            elif str(account["status"]) != AccountStatus.ACTIVE.value:
                reason = "ACCOUNT_DISABLED"
            elif int(cast(Any, session["credential_revision"])) != int(
                cast(Any, account["credential_revision"])
            ):
                reason = "PASSWORD_CHANGED"
            if reason is not None:
                if allow_session_mutation:
                    self._mark_locked_session_invalid(
                        cursor,
                        session,
                        reason=reason,
                        timestamp=timestamp,
                        request_id=audit_request_id,
                    )
                    connection.commit()
                return None
            if allow_session_mutation and timestamp >= cast(
                datetime, session["last_seen_at"]
            ) + timedelta(seconds=self._session_touch_interval_seconds):
                cursor.execute(
                    """
                    UPDATE access_control.sessions
                    SET last_seen_at = %s
                    WHERE session_id = %s::uuid AND revoked_at IS NULL
                    """,
                    (timestamp, str(session["session_id"])),
                )
            cursor.execute(
                """
                SELECT capability_key
                FROM access_control.platform_capability_grants
                WHERE principal_id = %s::uuid AND active
                ORDER BY capability_key
                """,
                (principal_id,),
            )
            platform_capabilities = tuple(
                str(_row(cursor, item)["capability_key"]) for item in cursor.fetchall()
            )
            is_platform_admin = CAPABILITY_PLATFORM_ADMIN in platform_capabilities
            cursor.execute(
                "SELECT set_config('app.platform_admin', %s, true)",
                ("true" if is_platform_admin else "false",),
            )
            if is_platform_admin:
                cursor.execute(
                    """
                    SELECT organization_id, project_id, ARRAY[]::text[] AS capability_keys
                    FROM registry.organization_projects
                    ORDER BY organization_id, project_id
                    """
                )
            else:
                cursor.execute(
                    """
                    SELECT membership.organization_id,
                           membership.project_id,
                           COALESCE(array_agg(DISTINCT capability_grant.capability_key)
                               FILTER (WHERE capability_grant.capability_key IS NOT NULL),
                               ARRAY[]::text[])
                               AS capability_keys
                    FROM access_control.memberships membership
                    LEFT JOIN access_control.capability_grants capability_grant
                      ON capability_grant.principal_id = membership.principal_id
                     AND capability_grant.organization_id = membership.organization_id
                     AND capability_grant.project_id = membership.project_id
                     AND capability_grant.active
                    WHERE membership.principal_id = %s::uuid AND membership.active
                    GROUP BY membership.organization_id, membership.project_id
                    ORDER BY membership.organization_id, membership.project_id
                    """,
                    (principal_id,),
                )
            scopes = tuple(
                AvailableScope(
                    organization_id=str(scope["organization_id"]),
                    project_id=str(scope["project_id"]),
                    region_codes=(),
                    project_wide=True,
                    capabilities=tuple(sorted(cast(Sequence[str], scope["capability_keys"]))),
                )
                for scope in (_row(cursor, item) for item in cursor.fetchall())
            )
            resolved = ResolvedSession(
                session_id=str(session["session_id"]),
                principal=AccountPrincipal(
                    principal_id=principal_id,
                    username=str(account["username"]),
                    display_name=str(account["display_name"]),
                    status=AccountStatus(str(account["status"])),
                    created_at=cast(datetime, account["created_at"]),
                ),
                capability_revision=int(cast(Any, account["capability_revision"])),
                scopes=scopes,
                platform_capabilities=platform_capabilities,
            )
            connection.commit()
            return resolved
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def revoke_session(self, *, token_hash: str, request_id: str) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT session_id::text AS session_id, principal_id::text AS principal_id,
                       revoked_at
                FROM access_control.sessions
                WHERE token_hash = %s
                FOR UPDATE
                """,
                (token_hash,),
            )
            raw = cursor.fetchone()
            if raw is not None:
                values = _row(cursor, raw)
                if values["revoked_at"] is None:
                    timestamp = self._database_timestamp(cursor)
                    cursor.execute(
                        """
                        UPDATE access_control.sessions
                        SET revoked_at = %s, revocation_reason = 'LOGOUT'
                        WHERE session_id = %s::uuid AND revoked_at IS NULL
                        """,
                        (timestamp, str(values["session_id"])),
                    )
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
                        safe_details={"reason": "LOGOUT"},
                        occurred_at=timestamp,
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def record_failed_login(self, *, principal_id: str | None, request_id: str) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._audit(
                cursor,
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=principal_id,
                action="auth.login.failed",
                resource_type="account",
                resource_id=principal_id or "anonymous-subject",
                request_id=request_id,
                outcome="DENIED",
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def count_unread_notifications(self, *, principal_id: str) -> int:
        account_id = _principal_uuid(principal_id)
        if account_id is None:
            return 0
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT set_config('app.subject_id', %s, true)", (account_id,))
            cursor.execute(
                """
                SELECT count(*)
                  FROM access_control.account_notifications
                 WHERE recipient_id = %s::uuid AND state = 'UNREAD'
                """,
                (account_id,),
            )
            raw = cursor.fetchone()
            return 0 if raw is None else int(raw[0])
        finally:
            cursor.close()
            connection.close()

    def list_notifications(
        self,
        *,
        principal_id: str,
        state: AccountNotificationState | None,
        before: tuple[datetime, str] | None,
        limit: int,
    ) -> tuple[AccountNotification, ...]:
        account_id = _principal_uuid(principal_id)
        if account_id is None:
            return ()
        clauses = ["recipient_id = %s::uuid"]
        params: list[object] = [account_id]
        if state is not None:
            clauses.append("state = %s")
            params.append(state.value)
        if before is not None:
            clauses.append("(created_at, notification_id::text) < (%s, %s)")
            params.extend(before)
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT set_config('app.subject_id', %s, true)", (account_id,))
            cursor.execute(
                f"""
                SELECT {_NOTIFICATION_COLUMNS}
                  FROM access_control.account_notifications
                 WHERE {" AND ".join(clauses)}
                 ORDER BY created_at DESC, notification_id DESC
                 LIMIT %s
                """,
                (*params, limit),
            )
            return tuple(_notification(cursor, raw) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def mark_notification_read(
        self, *, principal_id: str, notification_id: str, request_id: str
    ) -> bool:
        account_id = _principal_uuid(principal_id)
        if account_id is None:
            raise _not_found("notification")
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT set_config('app.subject_id', %s, true)", (account_id,))
            timestamp = self._database_timestamp(cursor)
            cursor.execute(
                f"""
                UPDATE access_control.account_notifications
                   SET state = 'READ', read_at = %s
                 WHERE notification_id = %s::uuid
                   AND recipient_id = %s::uuid
                   AND state = 'UNREAD'
                RETURNING {_NOTIFICATION_COLUMNS}
                """,
                (timestamp, notification_id, account_id),
            )
            raw = cursor.fetchone()
            if raw is not None:
                notification = _notification(cursor, raw)
                self._audit(
                    cursor,
                    scope_kind="PLATFORM",
                    project_id=None,
                    actor_id=account_id,
                    action="account.notification.read",
                    resource_type="account_notification",
                    resource_id=notification.notification_id,
                    request_id=request_id,
                    outcome="SUCCEEDED",
                    safe_details={"kind": notification.kind.value},
                    occurred_at=timestamp,
                )
                connection.commit()
                return True
            cursor.execute(
                """
                SELECT 1
                  FROM access_control.account_notifications
                 WHERE notification_id = %s::uuid AND recipient_id = %s::uuid
                """,
                (notification_id, account_id),
            )
            if cursor.fetchone() is None:
                raise _not_found("notification")
            connection.commit()
            return False
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def list_organization_memberships(
        self, *, principal_id: str
    ) -> tuple[AccountOrganizationMembership, ...]:
        account_id = _principal_uuid(principal_id)
        if account_id is None:
            return ()
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT membership.organization_id,
                       COALESCE(directory.display_name, membership.organization_id)
                           AS organization_name
                FROM access_control.organization_memberships membership
                LEFT JOIN access_control.organization_join_codes directory
                  ON directory.organization_id = membership.organization_id
                WHERE membership.principal_id = %s::uuid AND membership.active
                ORDER BY membership.organization_id
                """,
                (account_id,),
            )
            return tuple(
                AccountOrganizationMembership(
                    organization_id=str(values["organization_id"]),
                    organization_name=str(values["organization_name"]),
                )
                for values in (_row(cursor, raw) for raw in cursor.fetchall())
            )
        finally:
            cursor.close()
            connection.close()

    def account_access_overview(self, *, principal_id: str) -> AccountAccessOverview:
        account_id = _principal_uuid(principal_id)
        if account_id is None:
            raise _not_found("account")
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT membership.organization_id,
                       COALESCE(directory.display_name, membership.organization_id)
                           AS organization_name
                FROM access_control.organization_memberships membership
                LEFT JOIN access_control.organization_join_codes directory
                  ON directory.organization_id = membership.organization_id
                WHERE membership.principal_id = %s::uuid AND membership.active
                ORDER BY membership.organization_id
                """,
                (account_id,),
            )
            organizations = tuple(
                AccountOrganizationMembership(
                    organization_id=str(values["organization_id"]),
                    organization_name=str(values["organization_name"]),
                )
                for values in (_row(cursor, raw) for raw in cursor.fetchall())
            )
            cursor.execute(
                """
                SELECT membership.organization_id,
                       COALESCE(directory.display_name, membership.organization_id)
                           AS organization_name,
                       membership.project_id
                FROM access_control.memberships membership
                LEFT JOIN access_control.organization_join_codes directory
                  ON directory.organization_id = membership.organization_id
                WHERE membership.principal_id = %s::uuid AND membership.active
                ORDER BY membership.organization_id, membership.project_id
                """,
                (account_id,),
            )
            projects = tuple(
                AccountProjectMembership(
                    organization_id=str(values["organization_id"]),
                    organization_name=str(values["organization_name"]),
                    project_id=str(values["project_id"]),
                    project_name=str(values["project_id"]),
                )
                for values in (_row(cursor, raw) for raw in cursor.fetchall())
            )
            cursor.execute(
                f"""
                SELECT {_ORGANIZATION_REQUEST_COLUMNS}
                FROM access_control.organization_membership_requests
                WHERE requester_id = %s::uuid
                """,
                (account_id,),
            )
            requests: list[AccountAccessRequest] = [
                _organization_request(cursor, raw) for raw in cursor.fetchall()
            ]
            cursor.execute(
                """
                SELECT request_id::text AS request_id, 'PROJECT' AS kind,
                       organization_id, project_id, ARRAY[]::text[] AS capability_keys,
                       status, reason, created_at, updated_at, revision
                FROM access_control.membership_requests
                WHERE requester_id = %s::uuid
                """,
                (account_id,),
            )
            requests.extend(_organization_request(cursor, raw) for raw in cursor.fetchall())
            cursor.execute(
                """
                SELECT request_id::text AS request_id, 'CAPABILITY' AS kind,
                       organization_id, project_id, capability_keys,
                       status, reason, created_at, updated_at, revision
                FROM access_control.capability_requests
                WHERE requester_id = %s::uuid
                """,
                (account_id,),
            )
            requests.extend(_organization_request(cursor, raw) for raw in cursor.fetchall())
            ordered = tuple(
                sorted(requests, key=lambda item: (item.created_at, item.request_id), reverse=True)
            )
            return AccountAccessOverview(
                organizations=organizations,
                projects=projects,
                requests=ordered,
                pending_request_count=sum(
                    item.status is AccessRequestStatus.PENDING for item in ordered
                ),
            )
        finally:
            cursor.close()
            connection.close()

    def create_organization_membership_request(
        self,
        *,
        auth: AuthContext,
        organization_id_or_join_code: str,
        reason: str,
        idempotency_key: str,
        request_id: str,
    ) -> AccountAccessRequest:
        principal_id = _principal_uuid(auth.subject_id)
        if principal_id is None:
            raise _account_principal_required()
        payload = {
            "organization_id_or_join_code": organization_id_or_join_code,
            "reason": reason,
        }
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            repeated = self._begin_idempotency(
                cursor,
                principal_id,
                "organization-membership.create",
                idempotency_key,
                payload,
            )
            if repeated is not None:
                cursor.execute(
                    f"""
                    SELECT {_ORGANIZATION_REQUEST_COLUMNS}
                    FROM access_control.organization_membership_requests
                    WHERE request_id = %s::uuid AND requester_id = %s::uuid
                    """,
                    (repeated, principal_id),
                )
                raw = cursor.fetchone()
                if raw is None:
                    raise _not_found("organization_membership_request")
                connection.commit()
                return _organization_request(cursor, raw)
            cursor.execute(
                """
                SELECT organization_id
                FROM access_control.organization_join_codes
                WHERE organization_id = %s OR join_code = %s
                UNION
                SELECT organization_id
                FROM registry.organization_projects
                WHERE organization_id = %s
                LIMIT 1
                """,
                (
                    organization_id_or_join_code,
                    organization_id_or_join_code,
                    organization_id_or_join_code,
                ),
            )
            raw_organization = cursor.fetchone()
            if raw_organization is None:
                raise _not_found("organization")
            organization_id = str(_row(cursor, raw_organization)["organization_id"])
            cursor.execute(
                """
                SELECT 1
                FROM access_control.organization_memberships
                WHERE principal_id = %s::uuid AND organization_id = %s AND active
                """,
                (principal_id, organization_id),
            )
            if cursor.fetchone() is not None:
                raise problem(
                    status=409,
                    code="ORGANIZATION_MEMBERSHIP_ALREADY_ACTIVE",
                    title="Organization membership already active",
                    detail="The principal is already a member of this organization.",
                )
            cursor.execute(
                """
                SELECT 1
                FROM access_control.organization_membership_requests
                WHERE requester_id = %s::uuid AND organization_id = %s AND status = 'PENDING'
                """,
                (principal_id, organization_id),
            )
            if cursor.fetchone() is not None:
                raise problem(
                    status=409,
                    code="ORGANIZATION_MEMBERSHIP_REQUEST_ALREADY_PENDING",
                    title="Organization membership request already pending",
                    detail="A pending membership request already exists for this organization.",
                )
            access_request_id = str(uuid4())
            cursor.execute(
                f"""
                INSERT INTO access_control.organization_membership_requests (
                    request_id, organization_id, requester_id, status, reason
                ) VALUES (%s::uuid, %s, %s::uuid, 'PENDING', %s)
                RETURNING {_ORGANIZATION_REQUEST_COLUMNS}
                """,
                (access_request_id, organization_id, principal_id, reason),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise RuntimeError("PostgreSQL did not return the organization request")
            item = _organization_request(cursor, raw)
            self._finish_idempotency(
                cursor,
                principal_id,
                "organization-membership.create",
                idempotency_key,
                item.request_id,
            )
            self._audit(
                cursor,
                scope_kind="PLATFORM",
                organization_id=organization_id,
                project_id=None,
                actor_id=principal_id,
                action="access.organization-membership.requested",
                resource_type="organization_membership_request",
                resource_id=item.request_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"status": item.status.value, "requester_id": principal_id},
            )
            connection.commit()
            return item
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def decide_organization_membership_request(
        self,
        *,
        auth: AuthContext,
        access_request_id: str,
        target_status: AccessRequestStatus,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> AccountAccessRequest:
        auth.require_capability(CAPABILITY_PLATFORM_ADMIN)
        if target_status not in {
            AccessRequestStatus.APPROVED,
            AccessRequestStatus.REJECTED,
            AccessRequestStatus.REVOKED,
        }:
            raise ValueError("unsupported organization membership transition")
        payload = {
            "request_id": access_request_id,
            "status": target_status.value,
            "reason": reason,
        }
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            repeated = self._begin_idempotency(
                cursor,
                auth.subject_id,
                "organization-membership.decide",
                idempotency_key,
                payload,
            )
            request_key = repeated or access_request_id
            cursor.execute(
                f"""
                SELECT {_ORGANIZATION_REQUEST_COLUMNS}, requester_id::text AS requester_id
                FROM access_control.organization_membership_requests
                WHERE request_id = %s::uuid
                FOR UPDATE
                """,
                (request_key,),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise _not_found("organization_membership_request")
            values = _row(cursor, raw)
            requester_id = str(values.pop("requester_id"))
            values["capability_keys"] = tuple(cast(Sequence[str], values["capability_keys"]))
            item = AccountAccessRequest.model_validate(values)
            if repeated is not None or item.status is target_status:
                self._finish_idempotency(
                    cursor,
                    auth.subject_id,
                    "organization-membership.decide",
                    idempotency_key,
                    item.request_id,
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
                UPDATE access_control.organization_membership_requests
                SET status = %s, decided_by = %s, decision_reason = %s,
                    updated_at = now(), revision = revision + 1
                WHERE request_id = %s::uuid AND revision = %s
                RETURNING {_ORGANIZATION_REQUEST_COLUMNS}
                """,
                (
                    target_status.value,
                    auth.subject_id,
                    reason,
                    item.request_id,
                    item.revision,
                ),
            )
            raw_updated = cursor.fetchone()
            if raw_updated is None:
                raise _transition_conflict(item.status)
            updated = _organization_request(cursor, raw_updated)
            if target_status is AccessRequestStatus.APPROVED:
                cursor.execute(
                    """
                    INSERT INTO access_control.organization_memberships (
                        principal_id, organization_id, source_request_id, active, activated_by
                    ) VALUES (%s::uuid, %s, %s::uuid, true, %s)
                    ON CONFLICT (principal_id, organization_id) DO UPDATE SET
                        source_request_id = EXCLUDED.source_request_id,
                        active = true,
                        activated_at = now(),
                        activated_by = EXCLUDED.activated_by,
                        revoked_at = NULL,
                        revoked_by = NULL
                    """,
                    (requester_id, item.organization_id, item.request_id, auth.subject_id),
                )
                self._bump_revision(cursor, requester_id)
            elif target_status is AccessRequestStatus.REVOKED:
                cursor.execute(
                    """
                    UPDATE access_control.organization_memberships
                    SET active = false, revoked_at = now(), revoked_by = %s
                    WHERE principal_id = %s::uuid AND organization_id = %s AND active
                    """,
                    (auth.subject_id, requester_id, item.organization_id),
                )
                cursor.execute(
                    """
                    UPDATE access_control.memberships
                    SET active = false, revoked_at = now(), revoked_by = %s
                    WHERE principal_id = %s::uuid AND organization_id = %s AND active
                    """,
                    (auth.subject_id, requester_id, item.organization_id),
                )
                cursor.execute(
                    """
                    UPDATE access_control.capability_grants
                    SET active = false, revoked_at = now(), revoked_by = %s
                    WHERE principal_id = %s::uuid AND organization_id = %s AND active
                    """,
                    (auth.subject_id, requester_id, item.organization_id),
                )
                self._bump_revision(cursor, requester_id)
            self._finish_idempotency(
                cursor,
                auth.subject_id,
                "organization-membership.decide",
                idempotency_key,
                item.request_id,
            )
            connection.commit()
            return updated
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def withdraw_organization_membership_request(
        self,
        *,
        auth: AuthContext,
        access_request_id: str,
        idempotency_key: str,
        request_id: str,
    ) -> AccountAccessRequest:
        payload = {"request_id": access_request_id}
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            repeated = self._begin_idempotency(
                cursor,
                auth.subject_id,
                "organization-membership.withdraw",
                idempotency_key,
                payload,
            )
            request_key = repeated or access_request_id
            cursor.execute(
                f"""
                SELECT {_ORGANIZATION_REQUEST_COLUMNS}
                FROM access_control.organization_membership_requests
                WHERE request_id = %s::uuid AND requester_id = %s::uuid
                FOR UPDATE
                """,
                (request_key, auth.subject_id),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise _not_found("organization_membership_request")
            item = _organization_request(cursor, raw)
            if repeated is not None:
                connection.commit()
                return item
            if item.status is not AccessRequestStatus.PENDING:
                raise _transition_conflict(item.status)
            cursor.execute(
                f"""
                UPDATE access_control.organization_membership_requests
                SET status = 'WITHDRAWN', updated_at = now(), revision = revision + 1
                WHERE request_id = %s::uuid AND revision = %s
                RETURNING {_ORGANIZATION_REQUEST_COLUMNS}
                """,
                (item.request_id, item.revision),
            )
            raw_updated = cursor.fetchone()
            if raw_updated is None:
                raise _transition_conflict(item.status)
            updated = _organization_request(cursor, raw_updated)
            self._finish_idempotency(
                cursor,
                auth.subject_id,
                "organization-membership.withdraw",
                idempotency_key,
                item.request_id,
            )
            self._audit(
                cursor,
                scope_kind="PLATFORM",
                organization_id=item.organization_id,
                project_id=None,
                actor_id=auth.subject_id,
                action="access.organization-membership.withdrawn",
                resource_type="organization_membership_request",
                resource_id=item.request_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"status": updated.status.value},
            )
            connection.commit()
            return updated
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
        organization_id: str,
        project_id: str,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest:
        principal_id = _principal_uuid(auth.subject_id)
        if principal_id is None:
            raise _account_principal_required()
        payload = {
            "organization_id": organization_id,
            "project_id": project_id,
            "reason": reason,
        }
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            repeated = self._begin_idempotency(
                cursor, auth.subject_id, "membership.create", idempotency_key, payload
            )
            if repeated is not None:
                result = self._get_membership(cursor, organization_id, project_id, repeated)
                connection.commit()
                return result
            cursor.execute(
                """
                SELECT 1 FROM access_control.memberships
                WHERE principal_id = %s::uuid
                  AND organization_id = %s
                  AND project_id = %s
                  AND active
                """,
                (principal_id, organization_id, project_id),
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
                    request_id, organization_id, project_id, requester_id, status, reason
                ) VALUES (%s::uuid, %s, %s, %s::uuid, 'PENDING', %s)
                RETURNING {_MEMBERSHIP_COLUMNS}
                """,
                (access_request_id, organization_id, project_id, principal_id, reason),
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
            constraint_name = _foreign_key_constraint_name(exc)
            if constraint_name == "membership_requests_requester_id_fkey":
                raise _account_principal_required() from exc
            if constraint_name == "membership_requests_organization_project_fk":
                raise _organization_project_not_found() from exc
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
        self, *, auth: AuthContext, organization_id: str, project_id: str
    ) -> tuple[MembershipRequest, ...]:
        manager = can_manage_project(auth, project_id, organization_id)
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
                    WHERE organization_id = %s AND project_id = %s
                    ORDER BY created_at DESC, request_id DESC
                    """,
                    (organization_id, project_id),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT {_MEMBERSHIP_COLUMNS}
                    FROM access_control.membership_requests
                    WHERE organization_id = %s AND project_id = %s AND requester_id = %s::uuid
                    ORDER BY created_at DESC, request_id DESC
                    """,
                    (organization_id, project_id, principal_id),
                )
            return tuple(_membership(cursor, raw) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def get_membership_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
    ) -> MembershipRequest:
        manager = can_manage_project(auth, project_id, organization_id)
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
                    WHERE request_id = %s::uuid AND organization_id = %s AND project_id = %s
                    """,
                    (access_request_id, organization_id, project_id),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT {_MEMBERSHIP_COLUMNS}
                    FROM access_control.membership_requests
                    WHERE request_id = %s::uuid AND organization_id = %s AND project_id = %s
                      AND requester_id = %s::uuid
                    """,
                    (access_request_id, organization_id, project_id, principal_id),
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
        organization_id: str,
        project_id: str,
        access_request_id: str,
        target_status: AccessRequestStatus,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> MembershipRequest:
        require_project_manager(auth, project_id, organization_id)
        payload = {
            "organization_id": organization_id,
            "request_id": access_request_id,
            "status": target_status.value,
            "reason": reason,
        }
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            repeated = self._begin_idempotency(
                cursor, auth.subject_id, "membership.decide", idempotency_key, payload
            )
            if repeated is not None:
                item = self._get_membership(cursor, organization_id, project_id, repeated)
                connection.commit()
                return item
            item = self._get_membership(
                cursor, organization_id, project_id, access_request_id, lock=True
            )
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
                    INSERT INTO access_control.organization_memberships (
                        principal_id, organization_id, source_request_id, active, activated_by
                    ) VALUES (%s::uuid, %s, NULL, true, %s)
                    ON CONFLICT (principal_id, organization_id) DO UPDATE SET
                        active = true,
                        activated_at = now(),
                        activated_by = EXCLUDED.activated_by,
                        revoked_at = NULL,
                        revoked_by = NULL
                    """,
                    (item.requester_id, organization_id, auth.subject_id),
                )
                cursor.execute(
                    """
                    INSERT INTO access_control.memberships (
                        principal_id, organization_id, project_id, source_request_id, active,
                        activated_by, revoked_at, revoked_by
                    ) VALUES (%s::uuid, %s, %s, %s::uuid, true, %s, NULL, NULL)
                    ON CONFLICT (principal_id, organization_id, project_id) DO UPDATE SET
                        source_request_id = EXCLUDED.source_request_id,
                        active = true,
                        activated_at = now(),
                        activated_by = EXCLUDED.activated_by,
                        revoked_at = NULL,
                        revoked_by = NULL
                    """,
                    (
                        item.requester_id,
                        organization_id,
                        project_id,
                        item.request_id,
                        auth.subject_id,
                    ),
                )
                self._bump_revision(cursor, item.requester_id)
            elif target_status is AccessRequestStatus.REVOKED:
                cursor.execute(
                    """
                    UPDATE access_control.memberships
                    SET active = false, revoked_at = now(), revoked_by = %s
                    WHERE principal_id = %s::uuid
                      AND organization_id = %s
                      AND project_id = %s
                      AND active
                    """,
                    (auth.subject_id, item.requester_id, organization_id, project_id),
                )
                cursor.execute(
                    """
                    UPDATE access_control.capability_grants
                    SET active = false, revoked_at = now(), revoked_by = %s
                    WHERE principal_id = %s::uuid
                      AND organization_id = %s
                      AND project_id = %s
                      AND active
                    """,
                    (auth.subject_id, item.requester_id, organization_id, project_id),
                )
                cursor.execute(
                    """
                    UPDATE access_control.capability_requests
                    SET status = 'REVOKED', decided_by = %s,
                        updated_at = now(), revision = revision + 1
                    WHERE requester_id = %s::uuid
                      AND organization_id = %s
                      AND project_id = %s
                      AND status = 'APPROVED'
                    """,
                    (auth.subject_id, item.requester_id, organization_id, project_id),
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
            self._insert_notification(
                cursor,
                recipient_id=updated.requester_id,
                organization_id=updated.organization_id,
                project_id=updated.project_id,
                resource_type=AccountNotificationResourceType.ACCESS_REQUEST,
                resource_id=updated.request_id,
                event_key=f"access-request:MEMBERSHIP_{target_status.value}:{updated.request_id}",
                kind=AccountNotificationKind[f"MEMBERSHIP_{target_status.value}"],
            )
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
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            repeated = self._begin_idempotency(
                cursor, auth.subject_id, "membership.withdraw", idempotency_key, payload
            )
            if repeated is not None:
                item = self._get_membership(cursor, organization_id, project_id, repeated)
                connection.commit()
                return item
            item = self._get_membership(
                cursor, organization_id, project_id, access_request_id, lock=True
            )
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
        organization_id: str,
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
            "organization_id": organization_id,
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
                item = self._get_capability(cursor, organization_id, project_id, repeated)
                connection.commit()
                return item
            access_request_id = str(uuid4())
            cursor.execute(
                f"""
                INSERT INTO access_control.capability_requests (
                    request_id, organization_id, project_id, requester_id,
                    capability_keys, status, reason
                ) VALUES (%s::uuid, %s, %s, %s::uuid, %s, 'PENDING', %s)
                RETURNING {_CAPABILITY_COLUMNS}
                """,
                (
                    access_request_id,
                    organization_id,
                    project_id,
                    principal_id,
                    list(capability_keys),
                    reason,
                ),
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
            constraint_name = _foreign_key_constraint_name(exc)
            if constraint_name == "capability_requests_requester_id_fkey":
                raise _account_principal_required() from exc
            if constraint_name == "capability_requests_organization_project_fk":
                raise _organization_project_not_found() from exc
            raise
        finally:
            cursor.close()
            connection.close()

    def list_capability_requests(
        self, *, auth: AuthContext, organization_id: str, project_id: str
    ) -> tuple[CapabilityRequest, ...]:
        manager = can_manage_project(auth, project_id, organization_id)
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
                    WHERE organization_id = %s AND project_id = %s
                    ORDER BY created_at DESC, request_id DESC
                    """,
                    (organization_id, project_id),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT {_CAPABILITY_COLUMNS}
                    FROM access_control.capability_requests
                    WHERE organization_id = %s AND project_id = %s AND requester_id = %s::uuid
                    ORDER BY created_at DESC, request_id DESC
                    """,
                    (organization_id, project_id, principal_id),
                )
            return tuple(_capability(cursor, raw) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def get_capability_request(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        access_request_id: str,
    ) -> CapabilityRequest:
        manager = can_manage_project(auth, project_id, organization_id)
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
                    WHERE request_id = %s::uuid AND organization_id = %s AND project_id = %s
                    """,
                    (access_request_id, organization_id, project_id),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT {_CAPABILITY_COLUMNS}
                    FROM access_control.capability_requests
                    WHERE request_id = %s::uuid AND organization_id = %s AND project_id = %s
                      AND requester_id = %s::uuid
                    """,
                    (access_request_id, organization_id, project_id, principal_id),
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
        organization_id: str,
        project_id: str,
        access_request_id: str,
        target_status: AccessRequestStatus,
        reason: str | None,
        idempotency_key: str,
        request_id: str,
    ) -> CapabilityRequest:
        require_project_manager(auth, project_id, organization_id)
        payload = {
            "organization_id": organization_id,
            "request_id": access_request_id,
            "status": target_status.value,
            "reason": reason,
        }
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            repeated = self._begin_idempotency(
                cursor, auth.subject_id, "capability.decide", idempotency_key, payload
            )
            if repeated is not None:
                item = self._get_capability(cursor, organization_id, project_id, repeated)
                connection.commit()
                return item
            item = self._get_capability(
                cursor, organization_id, project_id, access_request_id, lock=True
            )
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
                            grant_id, source_request_id, principal_id, organization_id, project_id,
                            capability_key, active, activated_by
                        ) VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, true, %s)
                        ON CONFLICT (source_request_id, capability_key) DO UPDATE SET
                            active = true, activated_at = now(),
                            activated_by = EXCLUDED.activated_by,
                            revoked_at = NULL, revoked_by = NULL
                        """,
                        (
                            str(uuid4()),
                            item.request_id,
                            item.requester_id,
                            organization_id,
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
            self._insert_notification(
                cursor,
                recipient_id=updated.requester_id,
                organization_id=updated.organization_id,
                project_id=updated.project_id,
                resource_type=AccountNotificationResourceType.ACCESS_REQUEST,
                resource_id=updated.request_id,
                event_key=f"access-request:CAPABILITY_{target_status.value}:{updated.request_id}",
                kind=AccountNotificationKind[f"CAPABILITY_{target_status.value}"],
            )
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
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            repeated = self._begin_idempotency(
                cursor, auth.subject_id, "capability.withdraw", idempotency_key, payload
            )
            if repeated is not None:
                item = self._get_capability(cursor, organization_id, project_id, repeated)
                connection.commit()
                return item
            item = self._get_capability(
                cursor, organization_id, project_id, access_request_id, lock=True
            )
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

    def list_managed_accounts(
        self,
        *,
        query: str | None,
        state: ManagedAccountState | None,
        role: PlatformAccountRole | None,
        page: int,
        page_size: int,
    ) -> ManagedAccountPage:
        conditions: list[str] = []
        parameters: list[object] = []
        if query is not None:
            conditions.append(
                """(
                    account.principal_id::text = %s
                    OR account.canonical_username ILIKE %s
                    OR account.display_username ILIKE %s
                    OR account.display_name ILIKE %s
                    OR account.recovery_email ILIKE %s
                )"""
            )
            pattern = f"%{query}%"
            parameters.extend((query, pattern, pattern, pattern, pattern))
        if state is ManagedAccountState.DELETED:
            conditions.append("account.deleted_at IS NOT NULL")
        elif state is not None:
            conditions.extend(("account.deleted_at IS NULL", "account.status = %s"))
            parameters.append(state.value)
        else:
            conditions.append("account.deleted_at IS NULL")
        if role is not None:
            role_operator = "EXISTS" if role is PlatformAccountRole.PLATFORM_ADMIN else "NOT EXISTS"
            conditions.append(
                f"""{role_operator} (
                    SELECT 1
                    FROM access_control.platform_capability_grants role_grant
                    WHERE role_grant.principal_id = account.principal_id
                      AND role_grant.capability_key = 'platform.admin'
                      AND role_grant.active
                )"""
            )
        where = " AND ".join(conditions)
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"SELECT count(*) AS total FROM access_control.accounts account WHERE {where}",
                tuple(parameters),
            )
            raw_total = cursor.fetchone()
            total = 0 if raw_total is None else int(cast(Any, _row(cursor, raw_total)["total"]))
            cursor.execute(
                f"""
                SELECT {_MANAGED_ACCOUNT_COLUMNS}
                FROM access_control.accounts account
                WHERE {where}
                ORDER BY account.updated_at DESC, account.principal_id DESC
                LIMIT %s OFFSET %s
                """,
                (*parameters, page_size, (page - 1) * page_size),
            )
            items = tuple(_managed_account(cursor, raw) for raw in cursor.fetchall())
            return ManagedAccountPage(
                items=items,
                page=page,
                page_size=page_size,
                total=total,
            )
        finally:
            cursor.close()
            connection.close()

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
        connection = self._connection_factory()
        cursor = connection.cursor()
        principal_id = str(uuid4())
        try:
            cursor.execute(
                """
                INSERT INTO access_control.accounts (
                    principal_id, canonical_username, display_username, display_name,
                    status, password_hash, capability_revision, account_revision,
                    credential_revision, password_changed_at, recovery_email
                ) VALUES (
                    %s::uuid, %s, %s, %s, 'ACTIVE', %s, %s, 1, 1, now(), %s
                )
                """,
                (
                    principal_id,
                    canonical_username,
                    display_username,
                    display_name,
                    password_hash,
                    1 if platform_role is PlatformAccountRole.PLATFORM_ADMIN else 0,
                    recovery_email,
                ),
            )
            if platform_role is PlatformAccountRole.PLATFORM_ADMIN:
                self._set_platform_admin_grants(
                    cursor, principal_id=principal_id, actor_id=actor_id, enabled=True
                )
            self._audit(
                cursor,
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=actor_id,
                action="platform.account.created",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"platform_role": platform_role.value},
            )
            account = self._get_managed_account(cursor, principal_id)
            connection.commit()
            return account
        except Exception as exc:
            connection.rollback()
            if getattr(exc, "sqlstate", None) == "23505":
                raise problem(
                    status=409,
                    code="ACCOUNT_CREATE_CONFLICT",
                    title="Account could not be created",
                    detail="The username or recovery email is already in use.",
                ) from exc
            raise
        finally:
            cursor.close()
            connection.close()

    def set_managed_account_status(
        self,
        *,
        principal_id: str,
        target_status: AccountStatus,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> ManagedAccount:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._lock_platform_admin_set(cursor)
            current = self._get_managed_account(cursor, principal_id, lock=True)
            self._require_managed_mutation(current, expected_revision)
            if current.state is ManagedAccountState(target_status.value):
                connection.commit()
                return current
            if (
                target_status is AccountStatus.DISABLED
                and current.platform_role is PlatformAccountRole.PLATFORM_ADMIN
            ):
                self._require_another_active_platform_admin(cursor, principal_id)
            cursor.execute(
                """
                UPDATE access_control.accounts
                SET status = %s,
                    disabled_at = CASE WHEN %s = 'DISABLED' THEN now() ELSE NULL END,
                    account_revision = account_revision + 1,
                    capability_revision = capability_revision + 1,
                    updated_at = now()
                WHERE principal_id = %s::uuid
                """,
                (target_status.value, target_status.value, principal_id),
            )
            if target_status is AccountStatus.DISABLED:
                self._revoke_account_sessions(cursor, principal_id)
            self._audit(
                cursor,
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
            )
            account = self._get_managed_account(cursor, principal_id)
            connection.commit()
            return account
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def set_managed_account_role(
        self,
        *,
        principal_id: str,
        platform_role: PlatformAccountRole,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> ManagedAccount:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._lock_platform_admin_set(cursor)
            current = self._get_managed_account(cursor, principal_id, lock=True)
            self._require_managed_mutation(current, expected_revision)
            if current.platform_role is platform_role:
                connection.commit()
                return current
            if current.platform_role is PlatformAccountRole.PLATFORM_ADMIN:
                self._require_another_active_platform_admin(cursor, principal_id)
            self._set_platform_admin_grants(
                cursor,
                principal_id=principal_id,
                actor_id=actor_id,
                enabled=platform_role is PlatformAccountRole.PLATFORM_ADMIN,
            )
            cursor.execute(
                """
                UPDATE access_control.accounts
                SET account_revision = account_revision + 1,
                    capability_revision = capability_revision + 1,
                    updated_at = now()
                WHERE principal_id = %s::uuid
                """,
                (principal_id,),
            )
            self._revoke_account_sessions(cursor, principal_id)
            self._audit(
                cursor,
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=actor_id,
                action="platform.account.role_changed",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"platform_role": platform_role.value},
            )
            account = self._get_managed_account(cursor, principal_id)
            connection.commit()
            return account
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def reset_managed_account_password(
        self,
        *,
        principal_id: str,
        new_password_hash: str,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> tuple[ManagedAccount, int]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            current = self._get_managed_account(cursor, principal_id, lock=True)
            self._require_managed_mutation(current, expected_revision)
            revoked = self._revoke_account_sessions(cursor, principal_id)
            cursor.execute(
                """
                UPDATE access_control.accounts
                SET password_hash = %s,
                    credential_revision = credential_revision + 1,
                    capability_revision = capability_revision + 1,
                    account_revision = account_revision + 1,
                    password_changed_at = now(),
                    updated_at = now()
                WHERE principal_id = %s::uuid
                """,
                (new_password_hash, principal_id),
            )
            self._audit(
                cursor,
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=actor_id,
                action="platform.account.password_reset",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"sessions_revoked": revoked},
            )
            account = self._get_managed_account(cursor, principal_id)
            connection.commit()
            return account, revoked
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def delete_managed_account(
        self,
        *,
        principal_id: str,
        expected_revision: int,
        actor_id: str,
        request_id: str,
    ) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._lock_platform_admin_set(cursor)
            current = self._get_managed_account(cursor, principal_id, lock=True)
            self._require_managed_mutation(current, expected_revision)
            if current.platform_role is PlatformAccountRole.PLATFORM_ADMIN:
                self._require_another_active_platform_admin(cursor, principal_id)
            self._revoke_account_sessions(cursor, principal_id)
            self._set_platform_admin_grants(
                cursor, principal_id=principal_id, actor_id=actor_id, enabled=False
            )
            cursor.execute(
                """
                UPDATE access_control.accounts
                SET status = 'DISABLED', disabled_at = COALESCE(disabled_at, now()),
                    deleted_at = now(), account_revision = account_revision + 1,
                    capability_revision = capability_revision + 1, updated_at = now()
                WHERE principal_id = %s::uuid
                """,
                (principal_id,),
            )
            self._audit(
                cursor,
                scope_kind="PLATFORM",
                project_id=None,
                actor_id=actor_id,
                action="platform.account.deleted",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"deletion_mode": "SOFT"},
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def list_audit_events(
        self, *, auth: AuthContext, organization_id: str, project_id: str
    ) -> tuple[AccessAuditEvent, ...]:
        require_project_manager(auth, project_id, organization_id)
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT event_id::text AS event_id, scope_kind, organization_id,
                       project_id, actor_id,
                       action, resource_type, resource_id, request_id, outcome,
                       safe_details, occurred_at
                FROM access_control.audit_events
                WHERE scope_kind = 'PROJECT' AND organization_id = %s AND project_id = %s
                ORDER BY occurred_at DESC, event_id DESC
                """,
                (organization_id, project_id),
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
    def _get_managed_account(
        cursor: Any, principal_id: str, *, lock: bool = False
    ) -> ManagedAccount:
        cursor.execute(
            f"""
            SELECT {_MANAGED_ACCOUNT_COLUMNS}
            FROM access_control.accounts account
            WHERE account.principal_id = %s::uuid AND account.deleted_at IS NULL
            {"FOR UPDATE OF account" if lock else ""}
            """,
            (principal_id,),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise _not_found("account")
        return _managed_account(cursor, raw)

    @staticmethod
    def _require_managed_mutation(account: ManagedAccount, expected_revision: int) -> None:
        if account.revision != expected_revision:
            raise _account_revision_conflict(account.revision)

    @staticmethod
    def _set_platform_admin_grants(
        cursor: Any,
        *,
        principal_id: str,
        actor_id: str,
        enabled: bool,
    ) -> None:
        if enabled:
            for capability in sorted(PLATFORM_ADMIN_CAPABILITIES):
                cursor.execute(
                    """
                    INSERT INTO access_control.platform_capability_grants (
                        principal_id, capability_key, active, granted_by,
                        granted_at, revoked_at, revoked_by
                    ) VALUES (%s::uuid, %s, true, %s, now(), NULL, NULL)
                    ON CONFLICT (principal_id, capability_key) DO UPDATE
                    SET active = true, granted_by = EXCLUDED.granted_by,
                        granted_at = now(), revoked_at = NULL, revoked_by = NULL
                    """,
                    (principal_id, capability, actor_id),
                )
            return
        cursor.execute(
            """
            UPDATE access_control.platform_capability_grants
            SET active = false, revoked_at = now(), revoked_by = %s
            WHERE principal_id = %s::uuid AND active
            """,
            (actor_id, principal_id),
        )

    @staticmethod
    def _require_another_active_platform_admin(cursor: Any, principal_id: str) -> None:
        cursor.execute(
            """
            SELECT count(DISTINCT account.principal_id) AS total
            FROM access_control.accounts account
            JOIN access_control.platform_capability_grants platform_grant
              ON platform_grant.principal_id = account.principal_id
             AND platform_grant.capability_key = 'platform.admin'
             AND platform_grant.active
            WHERE account.principal_id <> %s::uuid
              AND account.status = 'ACTIVE'
              AND account.deleted_at IS NULL
            """,
            (principal_id,),
        )
        raw = cursor.fetchone()
        total = 0 if raw is None else int(cast(Any, _row(cursor, raw)["total"]))
        if total < 1:
            raise _last_platform_admin_conflict()

    @staticmethod
    def _lock_platform_admin_set(cursor: Any) -> None:
        """Serialize removals from the active-admin set before last-admin checks."""

        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ("hc-data-platform:platform-admin-set",),
        )

    @staticmethod
    def _revoke_account_sessions(cursor: Any, principal_id: str) -> int:
        cursor.execute(
            """
            UPDATE access_control.sessions
            SET revoked_at = now(), revocation_reason = 'ADMIN_REVOKED'
            WHERE principal_id = %s::uuid AND revoked_at IS NULL
            RETURNING session_id
            """,
            (principal_id,),
        )
        return len(cursor.fetchall())

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
    def _begin_account_idempotency(
        cursor: Any,
        principal_id: str,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Mapping[str, object] | None:
        cursor.execute(
            """
            INSERT INTO access_control.command_idempotency (
                actor_id, operation, idempotency_key, request_fingerprint
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT (actor_id, operation, idempotency_key) DO NOTHING
            """,
            (principal_id, operation, idempotency_key, request_fingerprint),
        )
        cursor.execute(
            """
            SELECT request_fingerprint, result_payload
            FROM access_control.command_idempotency
            WHERE actor_id = %s AND operation = %s AND idempotency_key = %s
            FOR UPDATE
            """,
            (principal_id, operation, idempotency_key),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise RuntimeError("PostgreSQL did not retain the account idempotency row")
        values = _row(cursor, raw)
        if not secrets.compare_digest(str(values["request_fingerprint"]), request_fingerprint):
            raise problem(
                status=409,
                code="IDEMPOTENCY_KEY_REUSED",
                title="Idempotency key reused",
                detail="The idempotency key was already used with a different request.",
            )
        payload = values["result_payload"]
        if payload is None:
            return None
        if isinstance(payload, str):
            decoded = json.loads(payload)
            if not isinstance(decoded, dict):
                raise RuntimeError("account idempotency result is not an object")
            return cast(Mapping[str, object], decoded)
        return cast(Mapping[str, object], payload)

    @staticmethod
    def _finish_account_idempotency(
        cursor: Any,
        principal_id: str,
        operation: str,
        idempotency_key: str,
        result: Mapping[str, object],
    ) -> None:
        cursor.execute(
            """
            UPDATE access_control.command_idempotency
            SET resource_id = %s::uuid, result_payload = %s::jsonb
            WHERE actor_id = %s AND operation = %s AND idempotency_key = %s
            """,
            (
                principal_id,
                json.dumps(dict(result), ensure_ascii=False, sort_keys=True),
                principal_id,
                operation,
                idempotency_key,
            ),
        )

    @staticmethod
    def _current_account_revision(cursor: Any, principal_id: str) -> int:
        cursor.execute(
            """
            SELECT account_revision
            FROM access_control.accounts
            WHERE principal_id = %s::uuid
            """,
            (principal_id,),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise _not_found("account")
        return int(cast(Any, _row(cursor, raw)["account_revision"]))

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
        cursor: Any,
        organization_id: str,
        project_id: str,
        access_request_id: str,
        *,
        lock: bool = False,
    ) -> MembershipRequest:
        cursor.execute(
            f"""
            SELECT {_MEMBERSHIP_COLUMNS}
            FROM access_control.membership_requests
            WHERE request_id = %s::uuid AND organization_id = %s AND project_id = %s
            {"FOR UPDATE" if lock else ""}
            """,
            (access_request_id, organization_id, project_id),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise _not_found("membership_request")
        return _membership(cursor, raw)

    @staticmethod
    def _get_capability(
        cursor: Any,
        organization_id: str,
        project_id: str,
        access_request_id: str,
        *,
        lock: bool = False,
    ) -> CapabilityRequest:
        cursor.execute(
            f"""
            SELECT {_CAPABILITY_COLUMNS}
            FROM access_control.capability_requests
            WHERE request_id = %s::uuid AND organization_id = %s AND project_id = %s
            {"FOR UPDATE" if lock else ""}
            """,
            (access_request_id, organization_id, project_id),
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

    @staticmethod
    def _insert_notification(
        cursor: Any,
        *,
        recipient_id: str,
        organization_id: str,
        project_id: str,
        resource_type: AccountNotificationResourceType,
        resource_id: str,
        event_key: str,
        kind: AccountNotificationKind,
    ) -> None:
        cursor.execute("SELECT set_config('app.subject_id', %s, true)", (recipient_id,))
        cursor.execute(
            """
            INSERT INTO access_control.account_notifications (
                notification_id, recipient_id, kind, organization_id, project_id,
                access_request_id, resource_type, resource_id, event_key, state
            ) VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s::uuid, %s, %s, %s, 'UNREAD')
            ON CONFLICT (recipient_id, event_key) DO NOTHING
            """,
            (
                str(uuid4()),
                recipient_id,
                kind.value,
                organization_id,
                project_id,
                resource_id,
                resource_type.value,
                resource_id,
                event_key,
            ),
        )

    def _audit_capability(
        self, cursor: Any, item: CapabilityRequest, actor_id: str, action: str, request_id: str
    ) -> None:
        self._audit(
            cursor,
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

    @staticmethod
    def _audit(
        cursor: Any,
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
        safe_details: Mapping[str, object] | None = None,
        occurred_at: datetime | None = None,
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
                event_id, scope_kind, organization_id, project_id, actor_id, action,
                resource_type, resource_id, request_id, outcome, safe_details,
                occurred_at
            ) VALUES (
                %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                COALESCE(%s, now())
            )
            """,
            (
                event_id,
                scope_kind,
                organization_id,
                project_id,
                actor_id,
                action,
                resource_type,
                resource_id,
                request_id,
                outcome,
                json.dumps(dict(safe_details or {}), ensure_ascii=False, sort_keys=True),
                occurred_at,
            ),
        )
        if project_id is None:
            return
        # P20 keeps its compatibility audit API, while project-scoped facts also
        # enter the single cross-domain core audit stream in the same transaction.
        cursor.execute(
            "SELECT set_config('app.organization_id', %s, true)",
            (organization_id or "",),
        )
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
