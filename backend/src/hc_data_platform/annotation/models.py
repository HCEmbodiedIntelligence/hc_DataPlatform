"""Versioned public models owned by the annotation module."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from hc_data_platform.security import AuthContext


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AnnotationStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    NEEDS_REVISION = "NEEDS_REVISION"
    REJECTED = "REJECTED"


class OperationKind(str, Enum):
    EXCLUDE = "EXCLUDE"
    RESTORE = "RESTORE"


class RevisionOrigin(str, Enum):
    ANNOTATION = "ANNOTATION"
    LEGACY_CLEANING = "LEGACY_CLEANING"


class TagSchemaStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class AnnotationTaskKind(str, Enum):
    TAGGING = "TAGGING"


class AnnotationTaskCreationSource(str, Enum):
    LEGACY = "LEGACY"
    SYSTEM_LANCE = "SYSTEM_LANCE"


class TagAttributeType(str, Enum):
    STRING = "STRING"
    INTEGER = "INTEGER"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    ENUM = "ENUM"


class ReviewCheckKind(str, Enum):
    HIERARCHY = "HIERARCHY"
    BOUNDARY = "BOUNDARY"
    REQUIRED_ATTRIBUTES = "REQUIRED_ATTRIBUTES"
    MUTUAL_EXCLUSION = "MUTUAL_EXCLUSION"
    OBJECT_RELATIONS = "OBJECT_RELATIONS"
    SCHEMA_VERSION = "SCHEMA_VERSION"


class ReviewCheckStatus(str, Enum):
    PASS = "PASS"


class SelfReviewPolicy(str, Enum):
    """Product-owned switch kept unresolved until OPEN-08 is decided."""

    UNCONFIRMED = "UNCONFIRMED"
    ALLOW = "ALLOW"
    DENY = "DENY"


class ReviewDecision(str, Enum):
    APPROVE = "APPROVE"
    NEEDS_REVISION = "NEEDS_REVISION"
    REJECT = "REJECT"


TagAttributeValue = str | int | float | bool


class TagAttributeDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    display_name: str = Field(min_length=1, max_length=256)
    value_type: TagAttributeType
    required: bool = False
    enum_values: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_enum_values(self) -> TagAttributeDefinition:
        if len(self.enum_values) != len(set(self.enum_values)):
            raise ValueError("enum_values must be unique")
        if self.value_type is TagAttributeType.ENUM and not self.enum_values:
            raise ValueError("ENUM attributes require at least one enum value")
        if self.value_type is not TagAttributeType.ENUM and self.enum_values:
            raise ValueError("enum_values are only valid for ENUM attributes")
        return self


class TagNodeDefinition(BaseModel):
    """One node in an unbounded-depth, single-parent Tag hierarchy."""

    model_config = ConfigDict(frozen=True)

    tag_id: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=256)
    parent_tag_id: str | None = None
    attributes: tuple[TagAttributeDefinition, ...] = ()

    @model_validator(mode="after")
    def validate_attribute_keys(self) -> TagNodeDefinition:
        keys = [attribute.key for attribute in self.attributes]
        if len(keys) != len(set(keys)):
            raise ValueError("attribute keys must be unique within a Tag node")
        return self


class MutualExclusionConstraint(BaseModel):
    model_config = ConfigDict(frozen=True)

    constraint_id: str = Field(min_length=1, max_length=256)
    tag_ids: tuple[str, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_tag_ids(self) -> MutualExclusionConstraint:
        if len(self.tag_ids) != len(set(self.tag_ids)):
            raise ValueError("mutual exclusion tag_ids must be unique")
        return self


class ObjectRelationConstraint(BaseModel):
    model_config = ConfigDict(frozen=True)

    relation_type: str = Field(min_length=1, max_length=128)
    source_tag_ids: tuple[str, ...] = Field(min_length=1)
    target_object_types: tuple[str, ...] = Field(min_length=1)
    required: bool = False

    @model_validator(mode="after")
    def validate_unique_values(self) -> ObjectRelationConstraint:
        if len(self.source_tag_ids) != len(set(self.source_tag_ids)):
            raise ValueError("source_tag_ids must be unique")
        if len(self.target_object_types) != len(set(self.target_object_types)):
            raise ValueError("target_object_types must be unique")
        return self


class TagSchemaDocument(BaseModel):
    """Immutable schema content; no product maximum hierarchy depth is assumed."""

    model_config = ConfigDict(frozen=True)

    nodes: tuple[TagNodeDefinition, ...]
    mutual_exclusions: tuple[MutualExclusionConstraint, ...] = ()
    object_relations: tuple[ObjectRelationConstraint, ...] = ()

    @model_validator(mode="after")
    def validate_graph_and_constraints(self) -> TagSchemaDocument:
        node_by_id = {node.tag_id: node for node in self.nodes}
        if len(node_by_id) != len(self.nodes):
            raise ValueError("tag_id must be unique within a schema version")
        codes = [node.code for node in self.nodes]
        if len(codes) != len(set(codes)):
            raise ValueError("Tag codes must be unique within a schema version")
        for node in self.nodes:
            if node.parent_tag_id is not None and node.parent_tag_id not in node_by_id:
                raise ValueError(f"parent Tag {node.parent_tag_id!r} does not exist")

        for tag_id in node_by_id:
            seen: set[str] = set()
            current: str | None = tag_id
            while current is not None:
                if current in seen:
                    raise ValueError("Tag hierarchy contains a cycle")
                seen.add(current)
                current = node_by_id[current].parent_tag_id

        constraint_ids = [item.constraint_id for item in self.mutual_exclusions]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("mutual exclusion constraint_id must be unique")
        for exclusion_constraint in self.mutual_exclusions:
            missing = set(exclusion_constraint.tag_ids).difference(node_by_id)
            if missing:
                raise ValueError(f"mutual exclusion references unknown Tags: {sorted(missing)}")

        relation_types = [item.relation_type for item in self.object_relations]
        if len(relation_types) != len(set(relation_types)):
            raise ValueError("object relation_type must be unique")
        for relation_constraint in self.object_relations:
            missing = set(relation_constraint.source_tag_ids).difference(node_by_id)
            if missing:
                raise ValueError(f"object relation references unknown Tags: {sorted(missing)}")
        return self

    def path_for(self, tag_id: str) -> tuple[str, ...]:
        node_by_id = {node.tag_id: node for node in self.nodes}
        if tag_id not in node_by_id:
            raise KeyError(tag_id)
        reverse_path: list[str] = []
        current: str | None = tag_id
        while current is not None:
            reverse_path.append(current)
            current = node_by_id[current].parent_tag_id
        return tuple(reversed(reverse_path))


class TagSchemaTarget(BaseModel):
    """Explicit compatibility link; automatic task creation never chooses a latest schema."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    region_code: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=256)
    dataset_schema_snapshot_id: str = Field(min_length=1, max_length=256)
    task_kind: AnnotationTaskKind = AnnotationTaskKind.TAGGING


class TagSchemaVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_id: str = Field(min_length=1, max_length=256)
    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=256)
    version: int = Field(ge=1)
    status: TagSchemaStatus = TagSchemaStatus.DRAFT
    document: TagSchemaDocument
    compatible_targets: tuple[TagSchemaTarget, ...] = ()
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_by: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    published_by: str | None = None
    published_at: datetime | None = None

    @model_validator(mode="after")
    def validate_publication(self) -> TagSchemaVersion:
        published = self.status is TagSchemaStatus.PUBLISHED
        if published != (self.published_by is not None and self.published_at is not None):
            raise ValueError("published schema versions require publisher and timestamp")
        identities = [
            (
                target.region_code,
                target.dataset_id,
                target.dataset_schema_snapshot_id,
                target.task_kind,
            )
            for target in self.compatible_targets
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("Tag Schema compatible targets must be unique")
        return self


class TagObjectRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    object_id: str = Field(min_length=1, max_length=512)
    object_type: str = Field(min_length=1, max_length=128)


class AnnotationObjectRelation(BaseModel):
    model_config = ConfigDict(frozen=True)

    relation_type: str = Field(min_length=1, max_length=128)
    target: TagObjectRef


class AnnotationTag(BaseModel):
    """A Tag instance pinned to an exact schema path and half-open Lance range."""

    model_config = ConfigDict(frozen=True)

    annotation_id: str = Field(min_length=1, max_length=256)
    tag_id: str = Field(min_length=1, max_length=256)
    path: tuple[str, ...] = Field(min_length=1)
    start_step: int = Field(ge=0)
    end_step: int = Field(gt=0)
    attributes: dict[str, TagAttributeValue] = Field(default_factory=dict)
    subject: TagObjectRef | None = None
    relations: tuple[AnnotationObjectRelation, ...] = ()

    @model_validator(mode="after")
    def validate_non_empty(self) -> AnnotationTag:
        if self.start_step >= self.end_step:
            raise ValueError("end_step must be greater than start_step")
        return self


class LegacyAuditReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_system: Literal["P11_CLEANING"] = "P11_CLEANING"
    draft_id: str = Field(min_length=1)
    source_revision: int = Field(ge=0)
    source_actor_id: str = Field(min_length=1)
    source_created_at: datetime
    source_audit_event_id: str | None = None
    source_payload: dict[str, object] = Field(default_factory=dict)


class AnnotationReviewCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ReviewCheckKind
    status: ReviewCheckStatus = ReviewCheckStatus.PASS
    evidence: str = Field(min_length=1, max_length=2000)


class AnnotationSubmission(BaseModel):
    """Immutable, reviewable snapshot produced from one exact draft revision."""

    model_config = ConfigDict(frozen=True)

    submission_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    submitted_by: str = Field(min_length=1)
    base_lance_version: int = Field(ge=1)
    tag_schema_id: str = Field(min_length=1)
    tag_schema_version: int = Field(ge=1)
    tag_schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    checks: tuple[AnnotationReviewCheck, ...]
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_complete_checklist(self) -> AnnotationSubmission:
        kinds = [check.kind for check in self.checks]
        if len(kinds) != len(set(kinds)):
            raise ValueError("review checks must be unique")
        if set(kinds) != set(ReviewCheckKind):
            raise ValueError("reviewable submissions require the complete Tag checklist")
        return self


class AnnotationActor(BaseModel):
    """Compatibility identity accepted by the domain service and unit-test fake.

    HTTP adapters use BE-02's :class:`AuthContext` and convert it at the module
    boundary. Keeping this small value object makes the persistence fake useful
    without requiring a JWT in domain tests.
    """

    model_config = ConfigDict(frozen=True)

    actor_id: str = Field(min_length=1)
    roles: frozenset[str]
    project_ids: frozenset[str]

    @classmethod
    def from_auth(cls, auth: AuthContext, project_id: str | None = None) -> AnnotationActor:
        return cls(
            actor_id=auth.subject_id,
            roles=auth.legacy_roles(project_id),
            project_ids=auth.project_ids,
        )


class ExclusionRange(BaseModel):
    """Normalized half-open range that applies to every synchronized modality."""

    model_config = ConfigDict(frozen=True)

    start_step: int = Field(ge=0)
    end_step: int = Field(gt=0)
    modality_scope: Literal["ALL_MODALITIES"] = "ALL_MODALITIES"

    @model_validator(mode="after")
    def validate_non_empty(self) -> ExclusionRange:
        if self.start_step >= self.end_step:
            raise ValueError("end_step must be greater than start_step")
        return self


class AnnotationOperation(BaseModel):
    """An append-only operation over the half-open interval ``[start, end)``."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    operation_id: str = Field(min_length=1)
    kind: OperationKind
    start_step: int = Field(ge=0)
    end_step: int = Field(gt=0)
    reason: str = Field(default="", max_length=2000)
    modality_scope: Literal["ALL_MODALITIES"] = "ALL_MODALITIES"

    @model_validator(mode="after")
    def validate_non_empty(self) -> AnnotationOperation:
        if self.start_step >= self.end_step:
            raise ValueError("end_step must be greater than start_step")
        return self


class AnnotationRevision(BaseModel):
    """An immutable revision containing the operations added by one draft save."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    schema_version: Literal["1"] = "1"
    task_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    parent_revision: int | None
    author_id: str = Field(min_length=1)
    client_mutation_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("client_mutation_id", "mutation_id"),
    )
    base_lance_version: int = Field(default=1, ge=1)
    tag_schema_id: str = Field(default="legacy-flat", min_length=1)
    tag_schema_version: int = Field(default=1, ge=1)
    tags: tuple[AnnotationTag, ...] = ()
    operations: tuple[AnnotationOperation, ...]
    origin: RevisionOrigin = RevisionOrigin.ANNOTATION
    legacy_audit: LegacyAuditReference | None = None
    content_hash: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def mutation_id(self) -> str:
        """Backward-compatible spelling; serialized contracts use client_mutation_id."""

        return self.client_mutation_id

    @model_validator(mode="after")
    def validate_parent(self) -> AnnotationRevision:
        expected_parent = None if self.revision == 0 else self.revision - 1
        if self.parent_revision != expected_parent:
            raise ValueError("parent_revision must identify the immediately preceding revision")
        if (self.origin is RevisionOrigin.LEGACY_CLEANING) != (self.legacy_audit is not None):
            raise ValueError("legacy cleaning revisions require their source audit reference")
        return self


class AnnotationReview(BaseModel):
    """An immutable reviewer decision bound to one exact revision."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    review_id: str = Field(min_length=1)
    submission_id: str = Field(default="legacy-submission", min_length=1)
    task_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    reviewer_id: str = Field(min_length=1)
    decision: ReviewDecision
    checked_kinds: tuple[ReviewCheckKind, ...] = tuple(ReviewCheckKind)
    comment: str = Field(default="", max_length=10000)
    created_at: datetime = Field(default_factory=utc_now)


class AnnotationTask(BaseModel):
    """Task identity plus its current workflow pointers.

    ``state_version`` changes for claim, save, submit, and review. ``current_revision``
    changes only when an immutable draft revision is appended.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    task_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    region_code: str | None = Field(default=None, min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: int = Field(ge=1)
    base_lance_version: int = Field(default=1, ge=1)
    base_step_count: int | None = Field(default=None, gt=0)
    tag_schema_id: str = Field(default="legacy-flat", min_length=1)
    tag_schema_version: int = Field(default=1, ge=1)
    rollout_id: str = Field(min_length=1)
    task_kind: AnnotationTaskKind = AnnotationTaskKind.TAGGING
    creation_source: AnnotationTaskCreationSource = AnnotationTaskCreationSource.LEGACY
    source_workflow_id: str | None = Field(default=None, min_length=1)
    assignee_id: str | None = None
    current_revision: int = Field(ge=0)
    state_version: int = Field(default=0, ge=0)
    status: AnnotationStatus
    submitted_revision: int | None = Field(default=None, ge=0)
    submitted_by: str | None = None
    current_submission_id: str | None = None
    approved_revision: int | None = Field(default=None, ge=0)
    approved_review_id: str | None = None
    etag: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_state_pointers(self) -> AnnotationTask:
        if self.submitted_revision is not None and self.submitted_revision > self.current_revision:
            raise ValueError("submitted_revision cannot be newer than current_revision")
        if self.approved_revision is not None and self.approved_revision > self.current_revision:
            raise ValueError("approved_revision cannot be newer than current_revision")
        if (self.approved_revision is None) != (self.approved_review_id is None):
            raise ValueError("approved revision and review pointers must be set together")
        if self.status is AnnotationStatus.APPROVED and self.approved_revision is None:
            raise ValueError("APPROVED state must identify an exact approved revision")
        if self.status is not AnnotationStatus.APPROVED and self.approved_revision is not None:
            raise ValueError("only APPROVED state may expose an approved revision")
        # Pre-0002 tasks can retain a submitted_revision without a generated
        # reviewable submission. New transitions always set both pointers, while
        # this one-way compatibility permits their append-only history to load.
        if self.submitted_revision is None and self.current_submission_id is not None:
            raise ValueError("a submission pointer requires a submitted revision")
        return self


class AnnotationDraft(BaseModel):
    """Current editable read model backed by an immutable revision."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    task_id: str
    revision: int = Field(ge=0)
    author_id: str
    client_mutation_id: str
    base_lance_version: int = Field(ge=1)
    base_step_count: int | None = Field(default=None, gt=0)
    tag_schema_id: str
    tag_schema_version: int = Field(ge=1)
    tags: tuple[AnnotationTag, ...]
    operations: tuple[AnnotationOperation, ...]
    effective_exclusions: tuple[ExclusionRange, ...]
    etag: str
    updated_at: datetime


class AnnotationCurrent(BaseModel):
    """Stable projection of the mutable pointers separated from task identity."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    task_id: str
    current_revision: int = Field(ge=0)
    state_version: int = Field(ge=0)
    status: AnnotationStatus
    submitted_revision: int | None = Field(default=None, ge=0)
    submitted_by: str | None = None
    current_submission_id: str | None = None
    approved_revision: int | None = Field(default=None, ge=0)
    approved_review_id: str | None = None
    etag: str
    updated_at: datetime


class AnnotationApprovedV1(BaseModel):
    """Immutable publishing snapshot of one approved revision."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    project_id: str
    dataset_id: str
    dataset_version: int = Field(ge=1)
    rollout_id: str
    task_id: str
    annotation_revision: int = Field(ge=0)
    submission_id: str = Field(default="legacy-submission", min_length=1)
    base_lance_version: int = Field(default=1, ge=1)
    tag_schema_id: str = Field(default="legacy-flat", min_length=1)
    tag_schema_version: int = Field(default=1, ge=1)
    revision_content_hash: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    review_id: str
    reviewer_id: str
    excluded_ranges: tuple[ExclusionRange, ...]
    approved_at: datetime


class AnnotationHistory(BaseModel):
    """Append-only history plus the current task projection."""

    model_config = ConfigDict(frozen=True)

    task: AnnotationTask
    revisions: tuple[AnnotationRevision, ...]
    submissions: tuple[AnnotationSubmission, ...] = ()
    reviews: tuple[AnnotationReview, ...]


class AnnotationMutationRecord(BaseModel):
    """Durable idempotency record for a draft-save response."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    client_mutation_id: str
    actor_id: str
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: int = Field(ge=0)
    request_etag: str
    result_revision: int = Field(ge=1)
    result_etag: str
    created_at: datetime = Field(default_factory=utc_now)


class AnnotationSubmissionMutationRecord(BaseModel):
    """Durable Idempotency-Key result for submit-for-review."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    idempotency_key: str = Field(min_length=1, max_length=256)
    actor_id: str
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    submission_id: str
    result_etag: str
    created_at: datetime = Field(default_factory=utc_now)


class AutoAnnotationCapability(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: Literal[False] = False
    code: Literal["FEATURE_DISABLED"] = "FEATURE_DISABLED"
