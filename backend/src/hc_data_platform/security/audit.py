from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from hc_data_platform.core.events import DomainEventEnvelope


def canonical_hash(value: Any) -> str:
    """Return the stable SHA-256 used in before/after audit records."""

    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class AuditRecord:
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    project_id: str
    request_id: str
    region_code: str | None = None
    before_hash: str | None = None
    after_hash: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    audit_id: str = field(default_factory=lambda: str(uuid4()))


class AuditSink(Protocol):
    def append(self, record: AuditRecord) -> None: ...


class OutboxPublisher(Protocol):
    def stage(self, event: DomainEventEnvelope) -> None: ...


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []
        self._lock = RLock()

    def append(self, record: AuditRecord) -> None:
        with self._lock:
            self.records.append(record)


class InMemoryOutboxPublisher:
    def __init__(self) -> None:
        self.events: list[DomainEventEnvelope] = []
        self._lock = RLock()

    def stage(self, event: DomainEventEnvelope) -> None:
        with self._lock:
            self.events.append(event)


class BufferedAuditSink:
    """Transaction-local sink flushed by a ScopedUnitOfWork."""

    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def append(self, record: AuditRecord) -> None:
        self.records.append(record)


class BufferedOutboxPublisher:
    """Transaction-local outbox publisher flushed by a ScopedUnitOfWork."""

    def __init__(self) -> None:
        self.events: list[DomainEventEnvelope] = []

    def stage(self, event: DomainEventEnvelope) -> None:
        self.events.append(event)
