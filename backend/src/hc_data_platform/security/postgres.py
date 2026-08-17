from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Generic, TypeVar, cast, overload
from uuid import uuid4

from sqlalchemy import Table, and_, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from hc_data_platform.core.errors import problem

from .audit import BufferedAuditSink, BufferedOutboxPublisher
from .auth import AuthContext
from .idempotency import IdempotencyResult, idempotency_conflict, request_fingerprint
from .scope import ScopeGuard, ScopeSelection
from .versioning import ResourceVersion, etag_mismatch

T = TypeVar("T")


class PostgresAuditSink(BufferedAuditSink):
    """Audit buffer persisted by its owning PostgresScopedUnitOfWork."""


class PostgresOutboxPublisher(BufferedOutboxPublisher):
    """Outbox buffer persisted by its owning PostgresScopedUnitOfWork."""


class RlsSessionContext:
    """Install one explicit project/region scope for the current transaction."""

    @staticmethod
    async def apply(
        session: AsyncSession,
        *,
        auth: AuthContext,
        scope: ScopeSelection,
        request_id: str,
    ) -> None:
        ScopeGuard.select(auth, scope)
        await session.execute(
            text(
                """
                SELECT
                    set_config('app.project_id', :project_id, true),
                    set_config('app.region_code', :region_code, true),
                    set_config('app.project_ids', :project_id, true),
                    set_config('app.is_admin', 'false', true),
                    set_config('app.subject_id', :subject_id, true),
                    set_config('app.request_id', :request_id, true),
                    set_config('app.service_identity', :service_identity, true)
                """
            ),
            {
                "project_id": scope.project_id,
                "region_code": scope.region_code or "",
                "subject_id": auth.subject_id,
                "request_id": request_id,
                "service_identity": "true" if auth.service_identity else "false",
            },
        )


class PostgresIdempotencyStore:
    """Transaction-bound idempotency using a unique row as the concurrency lock."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        selection: ScopeSelection,
        ttl: timedelta = timedelta(hours=24),
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("idempotency TTL must be positive")
        self._session = session
        self._selection = selection
        self._ttl = ttl

    @overload
    async def execute(
        self,
        *,
        scope: str,
        key: str,
        payload: Any,
        action: Callable[[], Awaitable[T]],
    ) -> IdempotencyResult[T]: ...

    @overload
    async def execute(
        self,
        *,
        scope: str,
        key: str,
        payload: Any,
        action: Callable[[], T],
    ) -> IdempotencyResult[T]: ...

    async def execute(
        self,
        *,
        scope: str,
        key: str,
        payload: Any,
        action: Callable[[], Any],
    ) -> IdempotencyResult[Any]:
        if not scope or not key:
            raise ValueError("idempotency scope and key must not be empty")
        fingerprint = request_fingerprint(payload)
        now = datetime.now(timezone.utc)
        expires_at = now + self._ttl
        identity = {
            "project_id": self._selection.project_id,
            "region_code": self._selection.region_code or "",
            "scope_key": scope,
            "idempotency_key": key,
        }

        # A conflicting INSERT waits for the winning transaction. Once it resumes, the
        # SELECT FOR UPDATE observes either the committed response or a rolled-back insert.
        await self._session.execute(
            text(
                """
                INSERT INTO core.idempotency_records (
                    project_id, region_code, scope_key, idempotency_key,
                    request_fingerprint, response_json, created_at, expires_at
                ) VALUES (
                    :project_id, :region_code, :scope_key, :idempotency_key,
                    :request_fingerprint, NULL, :created_at, :expires_at
                )
                ON CONFLICT (project_id, region_code, scope_key, idempotency_key)
                DO NOTHING
                """
            ),
            {
                **identity,
                "request_fingerprint": fingerprint,
                "created_at": now,
                "expires_at": expires_at,
            },
        )
        result = await self._session.execute(
            text(
                """
                SELECT request_fingerprint, response_json, expires_at
                FROM core.idempotency_records
                WHERE project_id = :project_id
                  AND region_code = :region_code
                  AND scope_key = :scope_key
                  AND idempotency_key = :idempotency_key
                FOR UPDATE
                """
            ),
            identity,
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise problem(
                status=403,
                code="IDEMPOTENCY_SCOPE_DENIED",
                title="Idempotency scope denied",
                detail="The idempotency row is outside the selected database scope.",
            )

        row_expires_at = cast(datetime, row["expires_at"])
        expired = row_expires_at <= now
        if not expired:
            if row["request_fingerprint"] != fingerprint:
                raise idempotency_conflict()
            if row["response_json"] is not None:
                return IdempotencyResult(
                    value=self._decode_response(row["response_json"]),
                    replayed=True,
                )

        if expired:
            await self._session.execute(
                text(
                    """
                    UPDATE core.idempotency_records
                    SET request_fingerprint = :request_fingerprint,
                        response_json = NULL,
                        created_at = :created_at,
                        expires_at = :expires_at
                    WHERE project_id = :project_id
                      AND region_code = :region_code
                      AND scope_key = :scope_key
                      AND idempotency_key = :idempotency_key
                    """
                ),
                {
                    **identity,
                    "request_fingerprint": fingerprint,
                    "created_at": now,
                    "expires_at": expires_at,
                },
            )

        value_or_awaitable = action()
        if inspect.isawaitable(value_or_awaitable):
            value = await value_or_awaitable
        else:
            value = value_or_awaitable
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        await self._session.execute(
            text(
                """
                UPDATE core.idempotency_records
                SET response_json = CAST(:response_json AS jsonb),
                    expires_at = :expires_at
                WHERE project_id = :project_id
                  AND region_code = :region_code
                  AND scope_key = :scope_key
                  AND idempotency_key = :idempotency_key
                """
            ),
            {**identity, "response_json": encoded, "expires_at": expires_at},
        )
        return IdempotencyResult(value=value, replayed=False)

    @staticmethod
    def _decode_response(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value


class PostgresScopedUnitOfWork:
    """SQLAlchemy transaction with mandatory RLS scope and atomic side effects."""

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.auth = auth
        self.scope = ScopeSelection(project_id, region_code)
        self.request_id = request_id or str(uuid4())
        self.audit = PostgresAuditSink()
        self.outbox = PostgresOutboxPublisher()
        self._session: AsyncSession | None = None
        self._committed = False
        self._closed = False

    @classmethod
    def for_worker(
        cls,
        session_factory: Callable[[], AsyncSession],
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str | None = None,
        request_id: str | None = None,
    ) -> PostgresScopedUnitOfWork:
        if not auth.service_identity:
            raise ValueError("for_worker requires a service identity")
        # Construction itself verifies that no unscoped worker transaction can start.
        ScopeGuard.require(auth, project_id, region_code)
        return cls(
            session_factory,
            auth=auth,
            project_id=project_id,
            region_code=region_code,
            request_id=request_id,
        )

    @property
    def session(self) -> AsyncSession:
        if self._session is None or self._closed:
            raise RuntimeError("unit of work must be entered before accessing its session")
        return self._session

    @property
    def idempotency(self) -> PostgresIdempotencyStore:
        return PostgresIdempotencyStore(self.session, selection=self.scope)

    async def __aenter__(self) -> PostgresScopedUnitOfWork:
        if self._session is not None:
            raise RuntimeError("unit of work cannot be entered more than once")
        ScopeGuard.select(self.auth, self.scope)
        self._session = self._session_factory()
        await self._session.begin()
        try:
            await RlsSessionContext.apply(
                self._session,
                auth=self.auth,
                scope=self.scope,
                request_id=self.request_id,
            )
        except BaseException:
            await self._session.rollback()
            await self._session.close()
            self._closed = True
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        del exc_value, traceback
        try:
            if exc_type is not None or not self._committed:
                await self.rollback()
        finally:
            if self._session is not None:
                await self._session.close()
            self._closed = True

    async def commit(self) -> None:
        session = self.session
        if self._committed:
            raise RuntimeError("unit of work was already committed")
        self._validate_side_effect_scopes()
        try:
            await session.flush()
            await self._flush_audit(session)
            await self._flush_outbox(session)
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
        self._committed = True

    async def rollback(self) -> None:
        if self._session is not None and not self._closed:
            await self._session.rollback()
        self.audit.records.clear()
        self.outbox.events.clear()

    def _validate_side_effect_scopes(self) -> None:
        for record in self.audit.records:
            if record.project_id != self.scope.project_id:
                raise ValueError("audit record project does not match the transaction scope")
            if record.region_code is not None and record.region_code != self.scope.region_code:
                raise ValueError("audit record region does not match the transaction scope")
        for event in self.outbox.events:
            if event.project_id != self.scope.project_id:
                raise ValueError("outbox event project does not match the transaction scope")
            if event.region_code is not None and event.region_code != self.scope.region_code:
                raise ValueError("outbox event region does not match the transaction scope")

    async def _flush_audit(self, session: AsyncSession) -> None:
        statement = text(
            """
            INSERT INTO core.audit_events (
                audit_id, project_id, region_code, actor_id, action,
                resource_type, resource_id, request_id, before_hash,
                after_hash, details, occurred_at
            ) VALUES (
                CAST(:audit_id AS uuid), :project_id, :region_code, :actor_id, :action,
                :resource_type, :resource_id, :request_id, :before_hash,
                :after_hash, CAST(:details AS jsonb), :occurred_at
            )
            """
        )
        for record in self.audit.records:
            await session.execute(
                statement,
                {
                    "audit_id": record.audit_id,
                    "project_id": record.project_id,
                    "region_code": record.region_code or self.scope.region_code,
                    "actor_id": record.actor_id,
                    "action": record.action,
                    "resource_type": record.resource_type,
                    "resource_id": record.resource_id,
                    "request_id": record.request_id,
                    "before_hash": record.before_hash,
                    "after_hash": record.after_hash,
                    "details": json.dumps(record.details, sort_keys=True, default=str),
                    "occurred_at": record.occurred_at,
                },
            )

    async def _flush_outbox(self, session: AsyncSession) -> None:
        statement = text(
            """
            INSERT INTO core.outbox_events (
                event_id, project_id, region_code, event_type,
                envelope, occurred_at, published_at
            ) VALUES (
                CAST(:event_id AS uuid), :project_id, :region_code, :event_type,
                CAST(:envelope AS jsonb), :occurred_at, NULL
            )
            """
        )
        for event in self.outbox.events:
            await session.execute(
                statement,
                {
                    "event_id": event.event_id,
                    "project_id": event.project_id,
                    "region_code": event.region_code or self.scope.region_code,
                    "event_type": event.event_type,
                    "envelope": json.dumps(event.model_dump(mode="json"), sort_keys=True),
                    "occurred_at": event.occurred_at,
                },
            )


class SqlAlchemyScopedRepository(Generic[T]):
    """Small SQLAlchemy Core repository that always includes the selected scope."""

    def __init__(
        self,
        session: AsyncSession,
        table: Table,
        *,
        selection: ScopeSelection,
        resource_id_column: str,
        decoder: Callable[[Mapping[str, Any]], T],
        region_column: str | None = "region_code",
        version_column: str | None = None,
    ) -> None:
        self._session = session
        self._table = table
        self._selection = selection
        self._id_column = resource_id_column
        self._decoder = decoder
        self._region_column = region_column
        self._version_column = version_column
        for column in ("project_id", resource_id_column):
            if column not in table.c:
                raise ValueError(f"repository table is missing {column!r}")
        if region_column is not None and region_column not in table.c:
            raise ValueError(f"repository table is missing {region_column!r}")
        if version_column is not None and version_column not in table.c:
            raise ValueError(f"repository table is missing {version_column!r}")

    async def get(self, resource_id: str) -> T | None:
        statement = select(self._table).where(
            and_(self._table.c[self._id_column] == resource_id, *self._scope_conditions())
        )
        result = await self._session.execute(statement)
        row = result.mappings().one_or_none()
        return None if row is None else self._decoder(dict(row))

    async def add(self, resource_id: str, values: Mapping[str, Any]) -> None:
        normalized = self._scoped_values(values)
        normalized[self._id_column] = resource_id
        await self._session.execute(insert(self._table).values(**normalized))

    async def update_with_etag(
        self,
        resource_id: str,
        *,
        if_match: str,
        values: Mapping[str, Any],
    ) -> ResourceVersion:
        if self._version_column is None:
            raise RuntimeError("repository has no resource version column")
        expected = ResourceVersion.from_etag(if_match)
        normalized = self._scoped_values(values)
        normalized.pop(self._id_column, None)
        normalized.pop(self._version_column, None)
        next_version = expected.value + 1
        statement = (
            update(self._table)
            .where(
                and_(
                    self._table.c[self._id_column] == resource_id,
                    self._table.c[self._version_column] == expected.value,
                    *self._scope_conditions(),
                )
            )
            .values(**normalized, **{self._version_column: next_version})
            .returning(self._table.c[self._version_column])
        )
        result = await self._session.execute(statement)
        updated = result.scalar_one_or_none()
        if updated is None:
            raise etag_mismatch(expected.etag)
        return ResourceVersion(cast(int, updated))

    def _scope_conditions(self) -> list[Any]:
        conditions = [self._table.c.project_id == self._selection.project_id]
        if self._region_column is not None:
            conditions.append(self._table.c[self._region_column] == self._selection.region_code)
        return conditions

    def _scoped_values(self, values: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(values)
        supplied_project = normalized.get("project_id", self._selection.project_id)
        if supplied_project != self._selection.project_id:
            raise problem(
                status=403,
                code="PROJECT_SCOPE_DENIED",
                title="Project access denied",
                detail="A repository write cannot cross the selected project scope.",
            )
        normalized["project_id"] = self._selection.project_id
        if self._region_column is not None:
            supplied_region = normalized.get(self._region_column, self._selection.region_code)
            if supplied_region != self._selection.region_code:
                raise problem(
                    status=403,
                    code="REGION_SCOPE_DENIED",
                    title="Region access denied",
                    detail="A repository write cannot cross the selected region scope.",
                )
            normalized[self._region_column] = self._selection.region_code
        return normalized
