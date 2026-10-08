"""Public authorized frozen annotation selection; no concrete file or queue service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .delivery_models import AnnotationDeliveryPlan, RequestAnnotationDelivery
from .models import AnnotationTask
from .revision_models import RevisionFileDownload

if TYPE_CHECKING:
    from hc_data_platform.security import AuthContext


@dataclass(frozen=True)
class FrozenAnnotationSelection:
    plan: AnnotationDeliveryPlan
    snapshot: RevisionFileDownload


class AnnotationSelectionPort(Protocol):
    def authorize(self, task_id: str, auth: AuthContext) -> AnnotationTask: ...

    def freeze_selection(
        self, task_id: str, body: RequestAnnotationDelivery, auth: AuthContext
    ) -> FrozenAnnotationSelection: ...

    def snapshot_for_plan(
        self, plan: AnnotationDeliveryPlan, auth: AuthContext
    ) -> RevisionFileDownload: ...
