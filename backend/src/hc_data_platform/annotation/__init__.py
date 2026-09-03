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
    AnnotationObjectRelation,
    AnnotationOperation,
    AnnotationReview,
    AnnotationReviewCheck,
    AnnotationRevision,
    AnnotationRevisionThread,
    AnnotationRevisionThreadPage,
    AnnotationRevisionThreadRevision,
    AnnotationStatus,
    AnnotationSubmission,
    AnnotationSubmissionMutationRecord,
    AnnotationTag,
    AnnotationTask,
    AutoAnnotationCapability,
    ExclusionRange,
    MutualExclusionConstraint,
    ObjectRelationConstraint,
    OperationKind,
    ReviewCheckKind,
    ReviewCheckStatus,
    ReviewDecision,
    RevisionOrigin,
    SelfReviewPolicy,
    TagAttributeDefinition,
    TagAttributeType,
    TagNodeDefinition,
    TagObjectRef,
    TagSchemaDocument,
    TagSchemaStatus,
    TagSchemaTarget,
    TagSchemaVersion,
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
        AnnotationIdempotencyConflictError,
        AnnotationMutationConflictError,
        AnnotationNotFoundError,
        AnnotationPermissionError,
        AnnotationPolicyUnconfirmedError,
        AnnotationRestoreTargetError,
        AnnotationService,
        AnnotationValidationError,
        DisabledAutoAnnotationProvider,
        FeatureDisabledError,
        InMemoryAnnotationService,
        InvalidAnnotationStateError,
        TagSchemaImmutableError,
        annotation_etag,
        normalize_operations,
    )


_LAZY_EXPORTS = {
    "AnnotationAggregate": ".ports",
    "AnnotationClaimConflictError": ".service",
    "AnnotationConflictError": ".service",
    "AnnotationError": ".service",
    "AnnotationIdempotencyConflictError": ".service",
    "AnnotationMutationConflictError": ".service",
    "AnnotationNotFoundError": ".service",
    "AnnotationPolicyUnconfirmedError": ".service",
    "AnnotationPermissionError": ".service",
    "AnnotationRestoreTargetError": ".service",
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
    "AnnotationValidationError": ".service",
    "PostgresAnnotationRepository": ".postgres",
    "TagSchemaImmutableError": ".service",
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
    "AnnotationIdempotencyConflictError",
    "AnnotationHistory",
    "AnnotationMutationConflictError",
    "AnnotationMutationRecord",
    "AnnotationNotFoundError",
    "AnnotationOperation",
    "AnnotationObjectRelation",
    "AnnotationPolicyUnconfirmedError",
    "AnnotationPermissionError",
    "AnnotationRestoreTargetError",
    "AnnotationQueuePort",
    "AnnotationReadPort",
    "AnnotationRepositoryInvariantError",
    "AnnotationRepositoryPort",
    "AnnotationRevision",
    "AnnotationRevisionThread",
    "AnnotationRevisionThreadPage",
    "AnnotationRevisionThreadRevision",
    "AnnotationReview",
    "AnnotationReviewCheck",
    "AnnotationService",
    "AnnotationStatus",
    "AnnotationSubmission",
    "AnnotationSubmissionMutationRecord",
    "AnnotationTag",
    "AnnotationTask",
    "AnnotationTaskProvisioningPort",
    "AnnotationWritePort",
    "AnnotationValidationError",
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
    "MutualExclusionConstraint",
    "ObjectRelationConstraint",
    "OperationKind",
    "PostgresAnnotationRepository",
    "ReviewDecision",
    "RevisionOrigin",
    "ReviewCheckKind",
    "ReviewCheckStatus",
    "SelfReviewPolicy",
    "TagAttributeDefinition",
    "TagAttributeType",
    "TagNodeDefinition",
    "TagObjectRef",
    "TagSchemaDocument",
    "TagSchemaImmutableError",
    "TagSchemaStatus",
    "TagSchemaTarget",
    "TagSchemaVersion",
    "annotation_etag",
    "normalize_operations",
]
