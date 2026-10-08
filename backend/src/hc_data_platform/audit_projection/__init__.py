"""Compatibility exports. New callers use owned models/ports and adapter modules.

Resolving a contract does not initialize unrelated services, SDKs or workers.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import (
        AuditBootstrapEnvelope,
        AuditCursorEnvelope,
        AuditEventEnvelope,
        AuditFacetsEnvelope,
    )
    from .repository import InMemoryAuditProjectionRepository, PostgresAuditProjectionRepository
    from .service import AuditProjectionService

__all__ = [
    "AuditBootstrapEnvelope",
    "AuditCursorEnvelope",
    "AuditEventEnvelope",
    "AuditFacetsEnvelope",
    "AuditProjectionService",
    "InMemoryAuditProjectionRepository",
    "PostgresAuditProjectionRepository",
]

_EXPORTS = {
    "AuditBootstrapEnvelope": (".models", "AuditBootstrapEnvelope"),
    "AuditCursorEnvelope": (".models", "AuditCursorEnvelope"),
    "AuditEventEnvelope": (".models", "AuditEventEnvelope"),
    "AuditFacetsEnvelope": (".models", "AuditFacetsEnvelope"),
    "InMemoryAuditProjectionRepository": (".repository", "InMemoryAuditProjectionRepository"),
    "PostgresAuditProjectionRepository": (".repository", "PostgresAuditProjectionRepository"),
    "AuditProjectionService": (".service", "AuditProjectionService"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module, attribute = target
    value = getattr(import_module(module, __name__), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
