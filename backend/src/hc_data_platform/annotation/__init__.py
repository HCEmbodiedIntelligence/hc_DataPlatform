"""Immutable annotation revisions, non-destructive cleaning, and review.

Only dependency-free models are imported eagerly. This package is imported while
Temporal validates publishing/workflow models, so service and BE-02/JWT dependencies
must stay lazy until an annotation application boundary actually requests them.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .models import (
    AnnotationActor,
    AnnotationApprovedV1,
    AnnotationCurrent,
    AnnotationDraft,
    AnnotationHistory,
    AnnotationMutationRecord,
    AnnotationOperation,
    AnnotationReview,
    AnnotationRevision,
    AnnotationStatus,
    AnnotationTask,
    AutoAnnotationCapability,
    ExclusionRange,
    OperationKind,
    ReviewDecision,
)

if TYPE_CHECKING:
    from .ports import (
        AnnotationAggregate,
        AnnotationQueuePort,
        AnnotationReadPort,
        AnnotationRepositoryPort,
        AnnotationTaskProvisioningPort,
        AnnotationWritePort,
        AutoAnnotationProvider,
        EffectiveExclusionPort,
    )
    from .postgres import PostgresAnnotationRepository
    from .repository import (
        AnnotationRepositoryInvariantError,
        FakeAnnotationRepository,
        InMemoryAnnotationRepository,
    )
    from .service import (
        AnnotationClaimConflictError,
        AnnotationConflictError,
        AnnotationError,
        AnnotationMutationConflictError,
        AnnotationNotFoundError,
        AnnotationPermissionError,
        AnnotationService,
        DisabledAutoAnnotationProvider,
        FeatureDisabledError,
        InMemoryAnnotationService,
        InvalidAnnotationStateError,
        annotation_etag,
        normalize_operations,
    )


_LAZY_EXPORTS = {
    "AnnotationAggregate": ".ports",
    "AnnotationClaimConflictError": ".service",
    "AnnotationConflictError": ".service",
    "AnnotationError": ".service",
    "AnnotationMutationConflictError": ".service",
    "AnnotationNotFoundError": ".service",
    "AnnotationPermissionError": ".service",
    "AnnotationQueuePort": ".ports",
    "AnnotationReadPort": ".ports",
    "AnnotationRepositoryInvariantError": ".repository",
    "AnnotationRepositoryPort": ".ports",
    "AnnotationService": ".service",
    "AnnotationTaskProvisioningPort": ".ports",
    "AnnotationWritePort": ".ports",
    "AutoAnnotationProvider": ".ports",
    "DisabledAutoAnnotationProvider": ".service",
    "EffectiveExclusionPort": ".ports",
    "FakeAnnotationRepository": ".repository",
    "FeatureDisabledError": ".service",
    "InMemoryAnnotationRepository": ".repository",
    "InMemoryAnnotationService": ".service",
    "InvalidAnnotationStateError": ".service",
    "PostgresAnnotationRepository": ".postgres",
    "annotation_etag": ".service",
    "normalize_operations": ".service",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


__all__ = [
    "AnnotationActor",
    "AnnotationAggregate",
    "AnnotationApprovedV1",
    "AnnotationClaimConflictError",
    "AnnotationConflictError",
    "AnnotationCurrent",
    "AnnotationDraft",
    "AnnotationError",
    "AnnotationHistory",
    "AnnotationMutationConflictError",
    "AnnotationMutationRecord",
    "AnnotationNotFoundError",
    "AnnotationOperation",
    "AnnotationPermissionError",
    "AnnotationQueuePort",
    "AnnotationReadPort",
    "AnnotationRepositoryInvariantError",
    "AnnotationRepositoryPort",
    "AnnotationRevision",
    "AnnotationReview",
    "AnnotationService",
    "AnnotationStatus",
    "AnnotationTask",
    "AnnotationTaskProvisioningPort",
    "AnnotationWritePort",
    "AutoAnnotationCapability",
    "AutoAnnotationProvider",
    "DisabledAutoAnnotationProvider",
    "EffectiveExclusionPort",
    "ExclusionRange",
    "FakeAnnotationRepository",
    "FeatureDisabledError",
    "InMemoryAnnotationRepository",
    "InMemoryAnnotationService",
    "InvalidAnnotationStateError",
    "OperationKind",
    "PostgresAnnotationRepository",
    "ReviewDecision",
    "annotation_etag",
    "normalize_operations",
]
