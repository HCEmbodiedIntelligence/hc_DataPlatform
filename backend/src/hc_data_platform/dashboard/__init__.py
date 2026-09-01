"""P01 dashboard query contracts and scope-safe repository adapters."""

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
