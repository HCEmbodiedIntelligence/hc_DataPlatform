from __future__ import annotations

from collections.abc import Mapping
from threading import RLock
from typing import Any, Protocol

from hc_data_platform.core.events import DomainEventEnvelope

from .audit import (
    AuditRecord,
    AuditSink,
    BufferedAuditSink,
    BufferedOutboxPublisher,
    OutboxPublisher,
)
from .auth import AuthContext
from .scope import ScopeGuard, ScopeSelection


class ScopedUnitOfWork(Protocol):
    auth: AuthContext
    scope: ScopeSelection
    audit: AuditSink
    outbox: OutboxPublisher

    async def __aenter__(self) -> ScopedUnitOfWork: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class InMemoryAtomicDatabase:
    """Shared state for transaction-capable fakes used without PostgreSQL."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, dict[str, Any]]] = {}
        self.audit_records: list[AuditRecord] = []
        self.outbox_events: list[DomainEventEnvelope] = []
        self._lock = RLock()


class InMemoryUnitOfWorkRepository:
    def __init__(self, uow: InMemoryScopedUnitOfWork, table_name: str) -> None:
        self._uow = uow
        self._table_name = table_name

    def get(self, resource_id: str) -> Mapping[str, Any] | None:
        self._uow._require_active()
        staged = self._uow._staged_rows.get(self._table_name, {})
        value = staged.get(resource_id)
        if value is None:
            with self._uow.database._lock:
                value = self._uow.database.tables.get(self._table_name, {}).get(resource_id)
        if value is None:
            return None
        ScopeGuard.require(
            self._uow.auth,
            str(value["project_id"]),
            str(value["region_code"]) if value.get("region_code") is not None else None,
        )
        return dict(value)

    def add(self, resource_id: str, values: Mapping[str, Any]) -> None:
        self._uow._require_active()
        normalized = dict(values)
        project_id = str(normalized.get("project_id", self._uow.scope.project_id))
        raw_region = normalized.get("region_code", self._uow.scope.region_code)
        region_code = str(raw_region) if raw_region is not None else None
        ScopeGuard.require(self._uow.auth, project_id, region_code)
        if project_id != self._uow.scope.project_id or region_code != self._uow.scope.region_code:
            raise ValueError("resource scope does not match the unit of work scope")
        normalized["project_id"] = project_id
        if raw_region is not None or self._uow.scope.region_code is not None:
            normalized["region_code"] = region_code
        self._uow._staged_rows.setdefault(self._table_name, {})[resource_id] = normalized


class InMemoryScopedUnitOfWork:
    """Atomic fake for business rows, audit records, and outbox events."""

    def __init__(
        self,
        database: InMemoryAtomicDatabase,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str | None = None,
    ) -> None:
        self.database = database
        self.auth = auth
        self.scope = ScopeSelection(project_id, region_code)
        self.audit = BufferedAuditSink()
        self.outbox = BufferedOutboxPublisher()
        self._staged_rows: dict[str, dict[str, dict[str, Any]]] = {}
        self._active = False
        self._committed = False

    async def __aenter__(self) -> InMemoryScopedUnitOfWork:
        ScopeGuard.select(self.auth, self.scope)
        if self._active:
            raise RuntimeError("unit of work is already active")
        self._active = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        del exc_value, traceback
        if exc_type is not None or not self._committed:
            await self.rollback()
        self._active = False

    def repository(self, table_name: str) -> InMemoryUnitOfWorkRepository:
        self._require_active()
        if not table_name:
            raise ValueError("table_name must not be empty")
        return InMemoryUnitOfWorkRepository(self, table_name)

    async def commit(self) -> None:
        self._require_active()
        if self._committed:
            raise RuntimeError("unit of work was already committed")
        self._validate_side_effect_scopes()
        with self.database._lock:
            for table_name, staged in self._staged_rows.items():
                self.database.tables.setdefault(table_name, {}).update(
                    {resource_id: dict(value) for resource_id, value in staged.items()}
                )
            self.database.audit_records.extend(self.audit.records)
            self.database.outbox_events.extend(self.outbox.events)
        self._committed = True

    async def rollback(self) -> None:
        self._staged_rows.clear()
        self.audit.records.clear()
        self.outbox.events.clear()

    def _validate_side_effect_scopes(self) -> None:
        for record in self.audit.records:
            if record.project_id != self.scope.project_id:
                raise ValueError("audit record project does not match the unit of work scope")
            if record.region_code is not None and record.region_code != self.scope.region_code:
                raise ValueError("audit record region does not match the unit of work scope")
        for event in self.outbox.events:
            if event.project_id != self.scope.project_id:
                raise ValueError("outbox event project does not match the unit of work scope")
            if event.region_code is not None and event.region_code != self.scope.region_code:
                raise ValueError("outbox event region does not match the unit of work scope")

    def _require_active(self) -> None:
        if not self._active:
            raise RuntimeError("unit of work must be entered before use")
