"""Authentication, scoped persistence, idempotency, and audit contracts."""

from __future__ import annotations

from typing import Any

from .audit import (
    AuditRecord,
    AuditSink,
    InMemoryAuditSink,
    InMemoryOutboxPublisher,
    OutboxPublisher,
    canonical_hash,
)
from .auth import (
    AuthContext,
    JwtVerifier,
    OidcJwksKeyResolver,
    SigningKeyResolver,
)
from .idempotency import IdempotencyResult, IdempotencyStore, InMemoryIdempotencyStore
from .scope import InMemoryScopedRepository, ScopedResource, ScopeGuard, ScopeSelection
from .uow import InMemoryAtomicDatabase, InMemoryScopedUnitOfWork, ScopedUnitOfWork
from .versioning import ResourceVersion

__all__ = [
    "AuditRecord",
    "AuditSink",
    "AuthContext",
    "IdempotencyResult",
    "IdempotencyStore",
    "InMemoryAtomicDatabase",
    "InMemoryAuditSink",
    "InMemoryIdempotencyStore",
    "InMemoryOutboxPublisher",
    "InMemoryScopedRepository",
    "InMemoryScopedUnitOfWork",
    "JwtVerifier",
    "OidcJwksKeyResolver",
    "OutboxPublisher",
    "PostgresAuditSink",
    "PostgresIdempotencyStore",
    "PostgresOutboxPublisher",
    "PostgresScopedUnitOfWork",
    "ResourceVersion",
    "RlsSessionContext",
    "ScopeGuard",
    "ScopeSelection",
    "ScopedResource",
    "ScopedUnitOfWork",
    "SigningKeyResolver",
    "SqlAlchemyScopedRepository",
    "canonical_hash",
]


_POSTGRES_EXPORTS = frozenset(
    {
        "PostgresIdempotencyStore",
        "PostgresAuditSink",
        "PostgresOutboxPublisher",
        "PostgresScopedUnitOfWork",
        "RlsSessionContext",
        "SqlAlchemyScopedRepository",
    }
)


def __getattr__(name: str) -> Any:
    """Keep PostgreSQL dependencies optional for Fake-only unit-test installations."""

    if name in _POSTGRES_EXPORTS:
        from . import postgres

        return getattr(postgres, name)
    raise AttributeError(name)
