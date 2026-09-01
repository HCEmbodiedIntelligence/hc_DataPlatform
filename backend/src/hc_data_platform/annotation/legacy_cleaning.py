"""One-way P11 cleaning compatibility adapter into annotation revisions.

This module intentionally exposes no HTTP router. Legacy records are imported as
annotation revision history and remain readable through annotation history APIs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import (
    AnnotationActor,
    AnnotationOperation,
    LegacyAuditReference,
    OperationKind,
    RevisionOrigin,
)
from .service import AnnotationService


class LegacyCleaningOperation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    operation_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    start_step: int = Field(ge=0)
    end_step: int = Field(gt=0)
    reason: str = Field(default="", max_length=2000)


class LegacyCleaningRevision(BaseModel):
    model_config = ConfigDict(frozen=True)

    revision: int = Field(ge=0)
    actor_id: str = Field(min_length=1)
    created_at: datetime
    audit_event_id: str | None = None
    operations: tuple[LegacyCleaningOperation, ...]


class LegacyCleaningDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    draft_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: int = Field(ge=1)
    base_lance_version: int = Field(ge=1)
    base_step_count: int = Field(gt=0)
    rollout_id: str = Field(min_length=1)
    revisions: tuple[LegacyCleaningRevision, ...]

    @model_validator(mode="after")
    def unique_revision_numbers(self) -> LegacyCleaningDraft:
        revisions = [item.revision for item in self.revisions]
        if len(revisions) != len(set(revisions)):
            raise ValueError("legacy cleaning revision numbers must be unique")
        return self


class LegacyCleaningMigrationItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_revision: int
    annotation_revision: int
    mode: Literal["MIGRATED", "ALREADY_MIGRATED"]
    unsupported_operation_kinds: tuple[str, ...] = ()


class LegacyCleaningMigrationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    draft_id: str
    task_id: str
    items: tuple[LegacyCleaningMigrationItem, ...]


class LegacyCleaningAnnotationAdapter:
    """Idempotently imports legacy P11 audit records without a cleaning API."""

    _EXCLUDE_KINDS = frozenset({"EXCLUDE", "EXCLUDE_RANGE", "INVALID_MASK"})
    _RESTORE_KINDS = frozenset({"RESTORE", "RESTORE_RANGE"})

    def __init__(self, service: AnnotationService) -> None:
        self._service = service

    def migrate(self, draft: LegacyCleaningDraft) -> LegacyCleaningMigrationResult:
        task_id = f"legacy-cleaning:{draft.draft_id}"
        task = self._service.create_task(
            task_id=task_id,
            project_id=draft.project_id,
            dataset_id=draft.dataset_id,
            dataset_version=draft.dataset_version,
            rollout_id=draft.rollout_id,
            base_lance_version=draft.base_lance_version,
            base_step_count=draft.base_step_count,
            tag_schema_id="legacy-flat",
            tag_schema_version=1,
        )
        migration_actor = AnnotationActor(
            actor_id="legacy-cleaning-migration",
            capabilities=frozenset(
                {"annotation_task.claim", "annotation_task.assign", "annotation.save"}
            ),
            project_ids=frozenset({draft.project_id}),
        )
        task = self._service.claim(task_id, migration_actor)
        items: list[LegacyCleaningMigrationItem] = []
        for source in sorted(draft.revisions, key=lambda item: item.revision):
            existing = next(
                (
                    revision
                    for revision in self._service.list_revisions(task_id)
                    if revision.legacy_audit is not None
                    and revision.legacy_audit.draft_id == draft.draft_id
                    and revision.legacy_audit.source_revision == source.revision
                ),
                None,
            )
            unsupported = tuple(
                sorted(
                    {
                        operation.kind
                        for operation in source.operations
                        if operation.kind not in self._EXCLUDE_KINDS | self._RESTORE_KINDS
                    }
                )
            )
            if existing is not None:
                items.append(
                    LegacyCleaningMigrationItem(
                        source_revision=source.revision,
                        annotation_revision=existing.revision,
                        mode="ALREADY_MIGRATED",
                        unsupported_operation_kinds=unsupported,
                    )
                )
                continue
            operations = tuple(
                AnnotationOperation(
                    operation_id=(
                        f"legacy:{draft.draft_id}:{source.revision}:{operation.operation_id}"
                    ),
                    kind=(
                        OperationKind.EXCLUDE
                        if operation.kind in self._EXCLUDE_KINDS
                        else OperationKind.RESTORE
                    ),
                    start_step=operation.start_step,
                    end_step=operation.end_step,
                    reason=operation.reason,
                )
                for operation in source.operations
                if operation.kind in self._EXCLUDE_KINDS | self._RESTORE_KINDS
            )
            audit = LegacyAuditReference(
                draft_id=draft.draft_id,
                source_revision=source.revision,
                source_actor_id=source.actor_id,
                source_created_at=source.created_at,
                source_audit_event_id=source.audit_event_id,
                source_payload=source.model_dump(mode="json"),
            )
            migrated = self._service.save_draft(
                task_id,
                migration_actor,
                operations,
                expected_revision=task.current_revision,
                if_match=task.etag,
                client_mutation_id=f"legacy-cleaning:{draft.draft_id}:{source.revision}",
                origin=RevisionOrigin.LEGACY_CLEANING,
                legacy_audit=audit,
                imported_author_id=source.actor_id,
                imported_created_at=source.created_at,
            )
            task = self._service.get_task(task_id)
            items.append(
                LegacyCleaningMigrationItem(
                    source_revision=source.revision,
                    annotation_revision=migrated.revision,
                    mode="MIGRATED",
                    unsupported_operation_kinds=unsupported,
                )
            )
        return LegacyCleaningMigrationResult(
            draft_id=draft.draft_id,
            task_id=task_id,
            items=tuple(items),
        )
