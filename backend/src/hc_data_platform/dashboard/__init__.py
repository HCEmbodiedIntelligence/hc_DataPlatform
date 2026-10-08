"""Compatibility exports. New callers use owned models/ports and adapter modules.

Resolving a contract does not initialize unrelated services, SDKs or workers.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import (
        DashboardActivityEvent,
        DashboardActivityEventType,
        DashboardActivityResponse,
        DashboardCoverageResponse,
        DashboardPendingItem,
        DashboardPendingItemsResponse,
        DashboardPendingItemType,
        DashboardPendingSeverity,
        DashboardSectionStatus,
        DashboardTaskStatusResponse,
    )
    from .service import DashboardService

__all__ = [
    "DashboardActivityEvent",
    "DashboardActivityEventType",
    "DashboardActivityResponse",
    "DashboardCoverageResponse",
    "DashboardPendingItem",
    "DashboardPendingItemType",
    "DashboardPendingItemsResponse",
    "DashboardPendingSeverity",
    "DashboardSectionStatus",
    "DashboardService",
    "DashboardTaskStatusResponse",
]

_EXPORTS = {
    "DashboardActivityEvent": (".models", "DashboardActivityEvent"),
    "DashboardActivityEventType": (".models", "DashboardActivityEventType"),
    "DashboardActivityResponse": (".models", "DashboardActivityResponse"),
    "DashboardCoverageResponse": (".models", "DashboardCoverageResponse"),
    "DashboardPendingItem": (".models", "DashboardPendingItem"),
    "DashboardPendingItemsResponse": (".models", "DashboardPendingItemsResponse"),
    "DashboardPendingItemType": (".models", "DashboardPendingItemType"),
    "DashboardPendingSeverity": (".models", "DashboardPendingSeverity"),
    "DashboardSectionStatus": (".models", "DashboardSectionStatus"),
    "DashboardTaskStatusResponse": (".models", "DashboardTaskStatusResponse"),
    "DashboardService": (".service", "DashboardService"),
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
