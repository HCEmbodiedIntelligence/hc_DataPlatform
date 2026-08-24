"""Durable P11 workbench persistence.

The repository owns SQL predicates and transactions only.  Operation semantics
live in :mod:`calculation`, which keeps every preview/commit decision replayable
from the persisted EDL revision and immutable base revision.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Protocol, TypeAlias, cast
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from hc_data_platform.security.audit import canonical_hash

from .calculation import calculate_edl, draft_etag
from .models import (
    CleaningAsyncJob,
    CleaningCommit,
    CleaningDraft,
    CleaningDraftBase,
    CleaningEdl,
    CleaningOperation,
    CleaningOutputVersion,
    CleaningPreview,
    CleaningStream,
    CleaningWorkbenchAuditEvent,
    CleaningWorkbenchScope,
    CleaningWorkbenchState,
    CommitSucceeded,
    ImmutableReviewDecision,
    IssueDerivedContext,
    IssueDerivedOrigin,
    PreviewExpiredOrStale,
    ReviewFindingProjection,
    ReviewReturnFeedback,
    ReviewReturnLineage,
    ReviewReturnOrigin,
    ReviewReturnSummary,
    ReviewSuccessorComposition,
    ReviewSuccessorCompositionMember,
)

_OPERATIONS: TypeAdapter[tuple[CleaningOperation, ...]] = TypeAdapter(tuple[CleaningOperation, ...])
_PREVIEW: TypeAdapter[CleaningPreview] = TypeAdapter(CleaningPreview)
_COMMIT: TypeAdapter[CleaningCommit] = TypeAdapter(CleaningCommit)


class CleaningWorkbenchIntegrityError(RuntimeError):
    """A durable row cannot be represented by the strict browser contract."""


class CleaningWorkbenchPreconditionError(RuntimeError):
    """A compare-and-swap / EDL identity precondition did not hold."""


class CleaningWorkbenchMutationConflict(RuntimeError):
    """A client mutation identifier was reused for different content."""


class CleaningWorkbenchSourceIncomplete(RuntimeError):
    """The immutable source lacks P07 projections needed for a safe commit."""


@dataclass(frozen=True, slots=True)
class WorkbenchSaveOutcome:
    state: CleaningWorkbenchState
    replayed: bool


@dataclass(frozen=True, slots=True)
class WorkbenchOutputMember:
    revision_id: str
    source_revision_id: str
    episode_id: str
    ordinal: int
    member_mode: str
    episode_stream_ids: tuple[str, ...]


class CleaningWorkbenchRepository(Protocol):
    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool: ...

    def get_state(
        self,
        *,
        scope: CleaningWorkbenchScope,
        draft_id: str,
        now: datetime | None = None,
    ) -> CleaningWorkbenchState | None: ...

    def save_edl(
        self,
        *,
        scope: CleaningWorkbenchScope,
        draft_id: str,
        expected_etag: str,
        expected_edl_revision: int,
        expected_operation_hash: str | None,
        client_mutation_id: str,
        edl_operations: tuple[CleaningOperation, ...],
        actor_id: str,
        occurred_at: datetime,
        audit_event: CleaningWorkbenchAuditEvent,
    ) -> WorkbenchSaveOutcome: ...

    def create_preview(
        self,
        *,
        scope: CleaningWorkbenchScope,
        draft_id: str,
        expected_etag: str,
        preview: CleaningPreview,
        job: CleaningAsyncJob,
        audit_event: CleaningWorkbenchAuditEvent,
    ) -> CleaningWorkbenchState: ...

    def output_members(
        self,
        *,
        state: CleaningWorkbenchState,
        commit_id: str,
    ) -> tuple[WorkbenchOutputMember, ...]: ...

    def commit(
        self,
        *,
        scope: CleaningWorkbenchScope,
        draft_id: str,
        expected_etag: str,
        commit: CommitSucceeded,
        job: CleaningAsyncJob,
        output_members: tuple[WorkbenchOutputMember, ...],
        audit_event: CleaningWorkbenchAuditEvent,
    ) -> CleaningWorkbenchState: ...

    def append_audit(self, event: CleaningWorkbenchAuditEvent) -> None: ...


def _scope_key(scope: CleaningWorkbenchScope) -> tuple[str, str, str]:
    return scope.organization_id, scope.project_id, scope.region_code


def _draft_key(scope: CleaningWorkbenchScope, draft_id: str) -> tuple[str, str, str, str]:
    return (*_scope_key(scope), draft_id)


class InMemoryCleaningWorkbenchRepository:
    """Thread-safe reference persistence used by service and API tests."""

    def __init__(
        self,
        *,
        organization_projects: tuple[tuple[str, str], ...] = (),
        states: tuple[CleaningWorkbenchState, ...] = (),
    ) -> None:
        self._organization_projects = set(organization_projects)
        self._states: dict[tuple[str, str, str, str], CleaningWorkbenchState] = {}
        self._mutations: dict[tuple[str, str, str, str, str], tuple[CleaningOperation, ...]] = {}
        self.audit_events: list[CleaningWorkbenchAuditEvent] = []
        self._lock = RLock()
        for state in states:
            self._organization_projects.add((state.scope.organization_id, state.scope.project_id))
            self._states[_draft_key(state.scope, state.draft.draft_id)] = state

    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool:
        with self._lock:
            return (organization_id, project_id) in self._organization_projects

    def get_state(
        self,
        *,
        scope: CleaningWorkbenchScope,
        draft_id: str,
        now: datetime | None = None,
    ) -> CleaningWorkbenchState | None:
        del now
        with self._lock:
            return self._states.get(_draft_key(scope, draft_id))

    def save_edl(
        self,
        *,
        scope: CleaningWorkbenchScope,
        draft_id: str,
        expected_etag: str,
        expected_edl_revision: int,
        expected_operation_hash: str | None,
        client_mutation_id: str,
        edl_operations: tuple[CleaningOperation, ...],
        actor_id: str,
        occurred_at: datetime,
        audit_event: CleaningWorkbenchAuditEvent,
    ) -> WorkbenchSaveOutcome:
        del actor_id
        with self._lock:
            key = _draft_key(scope, draft_id)
            current = self._required_state(key)
            mutation_key = (*key, client_mutation_id)
            prior = self._mutations.get(mutation_key)
            if prior is not None:
                if tuple(item.model_dump(mode="json") for item in prior) != tuple(
                    item.model_dump(mode="json") for item in edl_operations
                ):
                    raise CleaningWorkbenchMutationConflict
                return WorkbenchSaveOutcome(state=current, replayed=True)
            self._assert_editable(
                state=current,
                expected_etag=expected_etag,
                expected_edl_revision=expected_edl_revision,
                expected_operation_hash=expected_operation_hash,
            )
            next_state = _state_with_edl(
                state=current,
                operations=edl_operations,
                workbench_version=current.workbench_version + 1,
                updated_at=occurred_at,
            )
            self._states[key] = next_state
            self._mutations[mutation_key] = edl_operations
            self.audit_events.append(audit_event)
            return WorkbenchSaveOutcome(state=next_state, replayed=False)

    def create_preview(
        self,
        *,
        scope: CleaningWorkbenchScope,
        draft_id: str,
        expected_etag: str,
        preview: CleaningPreview,
        job: CleaningAsyncJob,
        audit_event: CleaningWorkbenchAuditEvent,
    ) -> CleaningWorkbenchState:
        del job
        with self._lock:
            key = _draft_key(scope, draft_id)
            current = self._required_state(key)
            if current.draft.etag != expected_etag or current.draft.status != "EDITING":
                raise CleaningWorkbenchPreconditionError
            if (
                preview.base_revision_id != current.base.revision_id
                or preview.edl_revision != current.edl.edl_revision
                or preview.operation_hash != current.edl.operation_hash
            ):
                raise CleaningWorkbenchPreconditionError
            next_state = current.model_copy(update={"active_preview": preview})
            self._states[key] = next_state
            self.audit_events.append(audit_event)
            return next_state

    def output_members(
        self, *, state: CleaningWorkbenchState, commit_id: str
    ) -> tuple[WorkbenchOutputMember, ...]:
        composition = state.successor_composition
        if composition is not None and len(composition.members) != 1:
            raise CleaningWorkbenchSourceIncomplete(
                "the in-memory repository needs source episode projections for "
                "carry-forward members"
            )
        return (
            WorkbenchOutputMember(
                revision_id=f"revision_{canonical_hash({'commit_id': commit_id})[:32]}",
                source_revision_id=state.base.revision_id,
                episode_id=state.base.episode_id,
                ordinal=0,
                member_mode="EDIT_RESULT",
                episode_stream_ids=tuple(stream.stream_id for stream in state.streams),
            ),
        )

    def commit(
        self,
        *,
        scope: CleaningWorkbenchScope,
        draft_id: str,
        expected_etag: str,
        commit: CommitSucceeded,
        job: CleaningAsyncJob,
        output_members: tuple[WorkbenchOutputMember, ...],
        audit_event: CleaningWorkbenchAuditEvent,
    ) -> CleaningWorkbenchState:
        del job, output_members
        with self._lock:
            key = _draft_key(scope, draft_id)
            current = self._required_state(key)
            if current.draft.etag != expected_etag or current.draft.status != "EDITING":
                raise CleaningWorkbenchPreconditionError
            next_draft = current.draft.model_copy(
                update={
                    "status": "COMMITTED",
                    "commit_status": "SUCCEEDED",
                    "output_version_status": "REVIEWING",
                    "etag": draft_etag(
                        draft_id=draft_id, workbench_version=current.workbench_version + 1
                    ),
                    "updated_at": commit.completed_at,
                }
            )
            next_state = current.model_copy(
                update={
                    "workbench_version": current.workbench_version + 1,
                    "draft": next_draft,
                    "active_commit": commit,
                }
            )
            self._states[key] = next_state
            self.audit_events.append(audit_event)
            return next_state

    def append_audit(self, event: CleaningWorkbenchAuditEvent) -> None:
        with self._lock:
            self.audit_events.append(event)

    def _required_state(self, key: tuple[str, str, str, str]) -> CleaningWorkbenchState:
        state = self._states.get(key)
        if state is None:
            raise KeyError(key)
        return state

    @staticmethod
    def _assert_editable(
        *,
        state: CleaningWorkbenchState,
        expected_etag: str,
        expected_edl_revision: int,
        expected_operation_hash: str | None,
    ) -> None:
        if (
            state.draft.status != "EDITING"
            or state.draft.etag != expected_etag
            or int(state.edl.edl_revision) != expected_edl_revision
            or (
                expected_operation_hash is not None
                and state.edl.operation_hash != expected_operation_hash
            )
        ):
            raise CleaningWorkbenchPreconditionError


def _state_with_edl(
    *,
    state: CleaningWorkbenchState,
    operations: tuple[CleaningOperation, ...],
    workbench_version: int,
    updated_at: datetime,
) -> CleaningWorkbenchState:
    from .calculation import operation_hash
    from .models import CleaningEdl

    revision = int(state.edl.edl_revision) + 1
    signature = operation_hash(operations)
    validation, summary, _mapping = calculate_edl(
        operations=operations,
        streams=state.streams,
        edl_revision=revision,
        operation_signature=signature,
        calculated_at=updated_at,
    )
    next_draft = state.draft.model_copy(
        update={
            "etag": draft_etag(draft_id=state.draft.draft_id, workbench_version=workbench_version),
            "preview_status": "STALE" if state.active_preview is not None else "NONE",
            "updated_at": updated_at,
        }
    )
    return state.model_copy(
        update={
            "workbench_version": workbench_version,
            "draft": next_draft,
            "edl": CleaningEdl(
                edl_revision=str(revision),
                etag=next_draft.etag,
                operation_hash=signature,
                operations=operations,
                validation=validation,
                summary=summary,
                updated_at=updated_at,
            ),
        }
    )


class DbApiCursor(Protocol):
    description: Sequence[Sequence[object] | object] | None

    def execute(self, query: str, params: Sequence[object] | None = None) -> object: ...

    def fetchone(self) -> object | None: ...

    def fetchall(self) -> list[object]: ...

    def close(self) -> None: ...


class DbApiConnection(Protocol):
    def cursor(self) -> DbApiCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


ConnectionFactory: TypeAlias = Callable[[], DbApiConnection]


def _row(cursor: DbApiCursor, raw: object) -> dict[str, object]:
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    if cursor.description is None:
        raise RuntimeError("database cursor did not describe result columns")
    names = tuple(
        str(column[0]) if isinstance(column, Sequence) else str(column)
        for column in cursor.description
    )
    return dict(zip(names, cast(Sequence[object], raw), strict=True))


def _decode_json(value: object) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _document(value: object) -> str:
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


class PostgresCleaningWorkbenchRepository:
    """Production P11 repository with exact scope predicates and atomic commits."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT 1 FROM registry.organization_projects
                 WHERE organization_id = %s AND project_id = %s
                """,
                (organization_id, project_id),
            )
            return cursor.fetchone() is not None
        finally:
            cursor.close()
            connection.close()

    def get_state(
        self,
        *,
        scope: CleaningWorkbenchScope,
        draft_id: str,
        now: datetime | None = None,
    ) -> CleaningWorkbenchState | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            return self._state(
                cursor,
                scope=scope,
                draft_id=draft_id,
                now=datetime.now(timezone.utc) if now is None else now,
            )
        finally:
            cursor.close()
            connection.close()

    def save_edl(
        self,
        *,
        scope: CleaningWorkbenchScope,
        draft_id: str,
        expected_etag: str,
        expected_edl_revision: int,
        expected_operation_hash: str | None,
        client_mutation_id: str,
        edl_operations: tuple[CleaningOperation, ...],
        actor_id: str,
        occurred_at: datetime,
        audit_event: CleaningWorkbenchAuditEvent,
    ) -> WorkbenchSaveOutcome:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT operations_document
                  FROM manual_cleaning.cleaning_draft_edl_revisions
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND draft_id = %s AND client_mutation_id = %s
                """,
                (*_scope_key(scope), draft_id, client_mutation_id),
            )
            existing = cursor.fetchone()
            if existing is not None:
                previous = _OPERATIONS.validate_python(
                    _decode_json(_row(cursor, existing)["operations_document"])
                )
                if _operations_document(previous) != _operations_document(edl_operations):
                    raise CleaningWorkbenchMutationConflict
                state = self._required_state(
                    cursor, scope=scope, draft_id=draft_id, now=occurred_at, lock=False
                )
                connection.commit()
                return WorkbenchSaveOutcome(state=state, replayed=True)

            state = self._required_state(
                cursor, scope=scope, draft_id=draft_id, now=occurred_at, lock=True
            )
            _assert_editable_state(
                state=state,
                expected_etag=expected_etag,
                expected_edl_revision=expected_edl_revision,
                expected_operation_hash=expected_operation_hash,
            )
            next_revision = int(state.edl.edl_revision) + 1
            from .calculation import operation_hash

            signature = operation_hash(edl_operations)
            cursor.execute(
                """
                INSERT INTO manual_cleaning.cleaning_draft_edl_revisions (
                    organization_id, project_id, region_code, draft_id, edl_revision,
                    client_mutation_id, operation_hash, operations_document, actor_id, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                """,
                (
                    *_scope_key(scope),
                    draft_id,
                    next_revision,
                    client_mutation_id,
                    signature,
                    _document(_operations_document(edl_operations)),
                    actor_id,
                    occurred_at,
                ),
            )
            cursor.execute(
                """
                UPDATE manual_cleaning.cleaning_workbench_drafts
                   SET workbench_version = workbench_version + 1, updated_at = %s
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND draft_id = %s AND workbench_version = %s AND status = 'EDITING'
                RETURNING draft_id
                """,
                (occurred_at, *_scope_key(scope), draft_id, state.workbench_version),
            )
            if cursor.fetchone() is None:
                raise CleaningWorkbenchPreconditionError
            self._insert_audit(cursor, audit_event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
        saved_state = self.get_state(scope=scope, draft_id=draft_id, now=occurred_at)
        if saved_state is None:
            raise CleaningWorkbenchIntegrityError("saved draft disappeared")
        return WorkbenchSaveOutcome(state=saved_state, replayed=False)

    def create_preview(
        self,
        *,
        scope: CleaningWorkbenchScope,
        draft_id: str,
        expected_etag: str,
        preview: CleaningPreview,
        job: CleaningAsyncJob,
        audit_event: CleaningWorkbenchAuditEvent,
    ) -> CleaningWorkbenchState:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            state = self._required_state(
                cursor, scope=scope, draft_id=draft_id, now=preview.created_at, lock=True
            )
            if state.draft.status != "EDITING" or state.draft.etag != expected_etag:
                raise CleaningWorkbenchPreconditionError
            if (
                preview.base_revision_id != state.base.revision_id
                or preview.edl_revision != state.edl.edl_revision
                or preview.operation_hash != state.edl.operation_hash
                or state.edl.validation.status != "PASSED"
            ):
                raise CleaningWorkbenchPreconditionError
            self._insert_job(cursor, job)
            cursor.execute(
                """
                INSERT INTO manual_cleaning.cleaning_draft_previews (
                    organization_id, project_id, region_code, preview_id, draft_id,
                    base_revision_id, edl_revision, operation_hash, preview_document,
                    created_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (organization_id, project_id, region_code, preview_id)
                DO NOTHING
                """,
                (
                    *_scope_key(scope),
                    preview.preview_id,
                    draft_id,
                    preview.base_revision_id,
                    int(preview.edl_revision),
                    preview.operation_hash,
                    _document(preview),
                    preview.created_at,
                    getattr(preview, "expires_at", None),
                ),
            )
            self._insert_audit(cursor, audit_event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
        preview_state = self.get_state(scope=scope, draft_id=draft_id, now=preview.created_at)
        if preview_state is None:
            raise CleaningWorkbenchIntegrityError("preview draft disappeared")
        return preview_state

    def output_members(
        self, *, state: CleaningWorkbenchState, commit_id: str
    ) -> tuple[WorkbenchOutputMember, ...]:
        composition = state.successor_composition
        source_ids = (
            (state.base.revision_id,)
            if composition is None
            else tuple(member.source_revision_id for member in composition.members)
        )
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT revision_id, episode_id, ordinal, revision_document
                  FROM dataset_registry.dataset_version_episode_revisions
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND dataset_id = %s AND version_id = %s
                   AND revision_id = ANY(%s::text[])
                """,
                (
                    *_scope_key(state.scope),
                    state.base.dataset_id,
                    state.base.version_id,
                    list(source_ids),
                ),
            )
            rows = {
                str(item["revision_id"]): item
                for raw in cursor.fetchall()
                for item in (_row(cursor, raw),)
            }
            if set(rows) != set(source_ids):
                raise CleaningWorkbenchSourceIncomplete("a source composition member is missing")
            members: list[WorkbenchOutputMember] = []
            for source_id in source_ids:
                row = rows[source_id]
                revision = _json_object(row["revision_document"])
                streams = _streams_from_revision(revision)
                editable = source_id == state.base.revision_id
                members.append(
                    WorkbenchOutputMember(
                        revision_id=(
                            f"revision_{canonical_hash({'commit_id': commit_id})[:32]}"
                            if editable
                            else source_id
                        ),
                        source_revision_id=source_id,
                        episode_id=str(row["episode_id"]),
                        ordinal=_integer(row["ordinal"], field="output member ordinal"),
                        member_mode="EDIT_RESULT" if editable else "CARRY_FORWARD",
                        episode_stream_ids=tuple(stream.stream_id for stream in streams),
                    )
                )
            return tuple(sorted(members, key=lambda member: (member.ordinal, member.episode_id)))
        finally:
            cursor.close()
            connection.close()

    def commit(
        self,
        *,
        scope: CleaningWorkbenchScope,
        draft_id: str,
        expected_etag: str,
        commit: CommitSucceeded,
        job: CleaningAsyncJob,
        output_members: tuple[WorkbenchOutputMember, ...],
        audit_event: CleaningWorkbenchAuditEvent,
    ) -> CleaningWorkbenchState:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            state = self._required_state(
                cursor, scope=scope, draft_id=draft_id, now=commit.completed_at, lock=True
            )
            if state.draft.status != "EDITING" or state.draft.etag != expected_etag:
                raise CleaningWorkbenchPreconditionError
            if state.edl.validation.status != "PASSED":
                raise CleaningWorkbenchPreconditionError
            preview = state.active_preview
            if (
                preview is None
                or preview.status != "READY"
                or preview.preview_id != commit.preview_id
                or preview.base_revision_id != state.base.revision_id
                or preview.edl_revision != state.edl.edl_revision
                or preview.operation_hash != state.edl.operation_hash
                or preview.expires_at <= commit.completed_at
            ):
                raise CleaningWorkbenchPreconditionError
            if commit.operation_hash != state.edl.operation_hash:
                raise CleaningWorkbenchPreconditionError

            self._create_output_version(
                cursor,
                state=state,
                commit=commit,
                output_members=output_members,
            )
            self._insert_job(cursor, job)
            cursor.execute(
                """
                INSERT INTO manual_cleaning.cleaning_workbench_commits (
                    organization_id, project_id, region_code, commit_id, draft_id,
                    preview_id, job_id, output_version_id, operation_hash, status,
                    materialization_status, commit_document, created_at, completed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'SUCCEEDED', %s,
                          %s::jsonb, %s, %s)
                """,
                (
                    *_scope_key(scope),
                    commit.commit_id,
                    draft_id,
                    commit.preview_id,
                    commit.job_id,
                    commit.output_version.version_id,
                    commit.operation_hash,
                    commit.materialization_status,
                    _document(commit),
                    commit.created_at,
                    commit.completed_at,
                ),
            )
            for member in output_members:
                cursor.execute(
                    """
                    INSERT INTO manual_cleaning.cleaning_commit_output_revisions (
                        organization_id, project_id, region_code, commit_id, revision_id,
                        source_revision_id, episode_id, ordinal, member_mode, episode_stream_ids
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        *_scope_key(scope),
                        commit.commit_id,
                        member.revision_id,
                        member.source_revision_id,
                        member.episode_id,
                        member.ordinal,
                        member.member_mode,
                        _document(member.episode_stream_ids),
                    ),
                )
            cursor.execute(
                """
                UPDATE manual_cleaning.cleaning_workbench_drafts
                   SET status = 'COMMITTED', workbench_version = workbench_version + 1,
                       updated_at = %s
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND draft_id = %s AND workbench_version = %s AND status = 'EDITING'
                RETURNING draft_id
                """,
                (commit.completed_at, *_scope_key(scope), draft_id, state.workbench_version),
            )
            if cursor.fetchone() is None:
                raise CleaningWorkbenchPreconditionError
            self._update_legacy_issue_handoff(cursor, state=state, commit=commit)
            self._insert_audit(cursor, audit_event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
        next_state = self.get_state(scope=scope, draft_id=draft_id, now=commit.completed_at)
        if next_state is None:
            raise CleaningWorkbenchIntegrityError("committed draft disappeared")
        return next_state

    def append_audit(self, event: CleaningWorkbenchAuditEvent) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._insert_audit(cursor, event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def _required_state(
        self,
        cursor: DbApiCursor,
        *,
        scope: CleaningWorkbenchScope,
        draft_id: str,
        now: datetime,
        lock: bool,
    ) -> CleaningWorkbenchState:
        state = self._state(cursor, scope=scope, draft_id=draft_id, now=now, lock=lock)
        if state is None:
            raise KeyError((*_scope_key(scope), draft_id))
        return state

    def _state(
        self,
        cursor: DbApiCursor,
        *,
        scope: CleaningWorkbenchScope,
        draft_id: str,
        now: datetime,
        lock: bool = False,
    ) -> CleaningWorkbenchState | None:
        cursor.execute(
            f"""
            SELECT draft.*, edl.edl_revision, edl.operation_hash, edl.operations_document,
                   edl.created_at AS edl_updated_at, revision.revision_document
              FROM manual_cleaning.cleaning_workbench_drafts AS draft
              JOIN LATERAL (
                    SELECT edl_revision, operation_hash, operations_document, created_at
                      FROM manual_cleaning.cleaning_draft_edl_revisions
                     WHERE organization_id = draft.organization_id
                       AND project_id = draft.project_id
                       AND region_code = draft.region_code
                       AND draft_id = draft.draft_id
                     ORDER BY edl_revision DESC
                     LIMIT 1
              ) AS edl ON TRUE
              JOIN dataset_registry.dataset_version_episode_revisions AS revision
                ON revision.organization_id = draft.organization_id
               AND revision.project_id = draft.project_id
               AND revision.region_code = draft.region_code
               AND revision.dataset_id = draft.dataset_id
               AND revision.version_id = draft.base_version_id
               AND revision.revision_id = draft.base_revision_id
             WHERE draft.organization_id = %s
               AND draft.project_id = %s
               AND draft.region_code = %s
               AND draft.draft_id = %s
            {"FOR UPDATE OF draft" if lock else ""}
            """,
            (*_scope_key(scope), draft_id),
        )
        raw = cursor.fetchone()
        if raw is None:
            return None
        row = _row(cursor, raw)
        try:
            operations = _OPERATIONS.validate_python(_decode_json(row["operations_document"]))
            revision_document = _decode_json(row["revision_document"])
            if not isinstance(revision_document, Mapping):
                raise CleaningWorkbenchIntegrityError("base revision document is not an object")
            streams = _streams_from_revision(revision_document)
            base = CleaningDraftBase(
                dataset_id=str(row["dataset_id"]),
                version_id=str(row["base_version_id"]),
                episode_id=str(row["episode_id"]),
                revision_id=str(row["base_revision_id"]),
                schema_snapshot_id=str(row["schema_snapshot_id"]),
                robot_model_version_id=_optional_text(row.get("robot_model_version_id")),
                calibration_set_id=_optional_text(row.get("calibration_set_id")),
            )
            origin = _origin_from_row(row)
            workbench_version = _integer(row["workbench_version"], field="workbench version")
            draft_identity = str(row["draft_id"])
            signature = str(row["operation_hash"])
            revision = _integer(row["edl_revision"], field="EDL revision")
            edl_updated_at = _as_datetime(row["edl_updated_at"])
            validation, summary, _mapping = calculate_edl(
                operations=operations,
                streams=streams,
                edl_revision=revision,
                operation_signature=signature,
                calculated_at=edl_updated_at,
            )
            active_preview = self._active_preview(
                cursor,
                scope=scope,
                draft_id=draft_identity,
                edl_revision=revision,
                operation_hash=signature,
                now=now,
            )
            active_commit = self._active_commit(cursor, scope=scope, draft_id=draft_identity)
            output_status, review_feedback = self._output_status_and_feedback(
                cursor,
                scope=scope,
                row=row,
                active_commit=active_commit,
            )
            if output_status == "RETURNED" and review_feedback is None:
                raise CleaningWorkbenchIntegrityError("returned output has no review feedback")
            returned_feedback = review_feedback if output_status == "RETURNED" else None
            review_summary = (
                _review_summary(returned_feedback) if returned_feedback is not None else None
            )
            draft = CleaningDraft(
                draft_id=draft_identity,
                etag=draft_etag(draft_id=draft_identity, workbench_version=workbench_version),
                status=str(row["status"]),
                origin=origin,
                base_version_id=base.version_id,
                base_revision_id=base.revision_id,
                episode_id=base.episode_id,
                manual_issue_count="1" if isinstance(origin, IssueDerivedOrigin) else "0",
                preview_status="NONE" if active_preview is None else active_preview.status,
                commit_status="NONE" if active_commit is None else active_commit.status,
                output_version_status=output_status,
                review_decision_id=(
                    returned_feedback.review_decision.id if returned_feedback is not None else None
                ),
                successor_draft_id=(
                    returned_feedback.successor_draft_id if returned_feedback is not None else None
                ),
                review_finding_count=(
                    str(len(returned_feedback.findings)) if returned_feedback is not None else "0"
                ),
                review_summary=review_summary,
                created_at=_as_datetime(row["created_at"]),
                updated_at=_as_datetime(row["updated_at"]),
            )
            edl = CleaningEdl(
                edl_revision=str(revision),
                etag=draft.etag,
                operation_hash=signature,
                operations=operations,
                validation=validation,
                summary=summary,
                updated_at=edl_updated_at,
            )
            composition = (
                self._successor_composition(cursor, scope=scope, row=row)
                if isinstance(origin, ReviewReturnOrigin)
                else None
            )
            return CleaningWorkbenchState(
                scope=scope,
                workbench_version=workbench_version,
                draft=draft,
                base=base,
                streams=streams,
                edl=edl,
                successor_composition=composition,
                active_preview=active_preview,
                active_commit=active_commit,
                review_feedback=review_feedback,
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise CleaningWorkbenchIntegrityError(
                f"CleaningDraft {draft_id!r} has invalid durable P11 facts"
            ) from exc

    def _active_preview(
        self,
        cursor: DbApiCursor,
        *,
        scope: CleaningWorkbenchScope,
        draft_id: str,
        edl_revision: int,
        operation_hash: str,
        now: datetime,
    ) -> CleaningPreview | None:
        cursor.execute(
            """
            SELECT preview_document
              FROM manual_cleaning.cleaning_draft_previews
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND draft_id = %s
             ORDER BY created_at DESC, preview_id DESC
             LIMIT 1
            """,
            (*_scope_key(scope), draft_id),
        )
        raw = cursor.fetchone()
        if raw is None:
            return None
        preview = _PREVIEW.validate_python(_decode_json(_row(cursor, raw)["preview_document"]))
        stale = (
            preview.edl_revision != str(edl_revision) or preview.operation_hash != operation_hash
        )
        expired = getattr(preview, "expires_at", None)
        if stale or (expired is not None and expired <= now):
            return PreviewExpiredOrStale(
                preview_id=preview.preview_id,
                draft_id=preview.draft_id,
                base_revision_id=preview.base_revision_id,
                edl_revision=preview.edl_revision,
                operation_hash=preview.operation_hash,
                job_id=preview.job_id,
                created_at=preview.created_at,
                status="STALE" if stale else "EXPIRED",
                expires_at=expired,
            )
        return preview

    def _active_commit(
        self, cursor: DbApiCursor, *, scope: CleaningWorkbenchScope, draft_id: str
    ) -> CleaningCommit | None:
        cursor.execute(
            """
            SELECT commit_document
              FROM manual_cleaning.cleaning_workbench_commits
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND draft_id = %s
             ORDER BY created_at DESC, commit_id DESC
             LIMIT 1
            """,
            (*_scope_key(scope), draft_id),
        )
        raw = cursor.fetchone()
        return (
            None
            if raw is None
            else _COMMIT.validate_python(_decode_json(_row(cursor, raw)["commit_document"]))
        )

    def _output_status_and_feedback(
        self,
        cursor: DbApiCursor,
        *,
        scope: CleaningWorkbenchScope,
        row: Mapping[str, object],
        active_commit: CleaningCommit | None,
    ) -> tuple[str | None, ReviewReturnFeedback | None]:
        returned_from = _optional_text(row.get("returned_from_version_id"))
        returned_decision = _optional_text(row.get("returned_from_review_decision_id"))
        supersedes = _optional_text(row.get("supersedes_draft_id"))
        if returned_from is not None and returned_decision is not None and supersedes is not None:
            feedback = self._review_feedback(
                cursor,
                scope=scope,
                dataset_id=str(row["dataset_id"]),
                output_version_id=returned_from,
                decision_id=returned_decision,
                supersedes_draft_id=supersedes,
            )
            return None, feedback
        if not isinstance(active_commit, CommitSucceeded):
            return None, None
        output_version_id = active_commit.output_version.version_id
        cursor.execute(
            """
            SELECT version_status, version_document
              FROM dataset_registry.dataset_versions
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s AND version_id = %s
            """,
            (*_scope_key(scope), str(row["dataset_id"]), output_version_id),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise CleaningWorkbenchIntegrityError("successful commit output version is missing")
        version = _row(cursor, raw)
        status = str(version["version_status"])
        if status != "RETURNED":
            return status, None
        document = _decode_json(version["version_document"])
        if not isinstance(document, Mapping):
            raise CleaningWorkbenchIntegrityError("returned output version document is invalid")
        decision_id = document.get("review_decision_id")
        supersedes_draft_id = document.get("supersedes_draft_id")
        if not isinstance(decision_id, str) or not isinstance(supersedes_draft_id, str):
            raise CleaningWorkbenchIntegrityError("returned output has incomplete review lineage")
        feedback = self._review_feedback(
            cursor,
            scope=scope,
            dataset_id=str(row["dataset_id"]),
            output_version_id=output_version_id,
            decision_id=decision_id,
            supersedes_draft_id=supersedes_draft_id,
        )
        return "RETURNED", feedback

    def _review_feedback(
        self,
        cursor: DbApiCursor,
        *,
        scope: CleaningWorkbenchScope,
        dataset_id: str,
        output_version_id: str,
        decision_id: str,
        supersedes_draft_id: str,
    ) -> ReviewReturnFeedback:
        cursor.execute(
            """
            SELECT decision_document
             FROM dataset_registry.dataset_version_review_decisions
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s AND version_id = %s
               AND review_decision_id = %s AND decision = 'RETURNED'
            """,
            (*_scope_key(scope), dataset_id, output_version_id, decision_id),
        )
        raw_decision = cursor.fetchone()
        if raw_decision is None:
            raise CleaningWorkbenchIntegrityError("returned review decision is missing")
        decision = ImmutableReviewDecision.model_validate(
            _decode_json(_row(cursor, raw_decision)["decision_document"])
        )
        cursor.execute(
            """
            SELECT finding_document
             FROM dataset_registry.dataset_version_review_findings
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s AND version_id = %s AND review_decision_id = %s
             ORDER BY created_at ASC, review_finding_id ASC
            """,
            (*_scope_key(scope), dataset_id, output_version_id, decision_id),
        )
        findings = tuple(
            ReviewFindingProjection.model_validate(
                _decode_json(_row(cursor, item)["finding_document"])
            )
            for item in cursor.fetchall()
        )
        if not findings:
            raise CleaningWorkbenchIntegrityError("returned review has no findings")
        cursor.execute(
            """
            SELECT commit_id
              FROM manual_cleaning.cleaning_workbench_commits
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND output_version_id = %s AND status = 'SUCCEEDED'
             ORDER BY completed_at DESC, commit_id DESC LIMIT 1
            """,
            (*_scope_key(scope), output_version_id),
        )
        raw_commit = cursor.fetchone()
        if raw_commit is None:
            raise CleaningWorkbenchIntegrityError("returned output lacks its P11 commit")
        commit_id = str(_row(cursor, raw_commit)["commit_id"])
        cursor.execute(
            """
            SELECT successor_draft_id
             FROM dataset_registry.dataset_version_successor_drafts
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s AND version_id = %s AND review_decision_id = %s
             ORDER BY created_at DESC, successor_draft_id DESC LIMIT 1
            """,
            (*_scope_key(scope), dataset_id, output_version_id, decision_id),
        )
        raw_successor = cursor.fetchone()
        if raw_successor is None:
            raise CleaningWorkbenchIntegrityError("returned review has no successor handoff")
        successor = str(_row(cursor, raw_successor)["successor_draft_id"])
        return ReviewReturnFeedback(
            review_decision=decision,
            output_version=CleaningOutputVersion(
                version_id=output_version_id,
                status="RETURNED",
                draft_id=supersedes_draft_id,
                commit_id=commit_id,
            ),
            output_version_id=output_version_id,
            output_version_status="RETURNED",
            findings=findings,
            review_finding_ids=tuple(finding.id for finding in findings),
            successor_draft_id=successor,
            supersedes_draft_id=supersedes_draft_id,
            returned_from_version_id=output_version_id,
            returned_from_review_decision_id=decision_id,
        )

    def _successor_composition(
        self,
        cursor: DbApiCursor,
        *,
        scope: CleaningWorkbenchScope,
        row: Mapping[str, object],
    ) -> ReviewSuccessorComposition:
        cursor.execute(
            """
            SELECT revision_id, episode_id, ordinal
              FROM dataset_registry.dataset_version_episode_revisions
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s AND version_id = %s
             ORDER BY ordinal ASC, episode_id ASC
            """,
            (*_scope_key(scope), str(row["dataset_id"]), str(row["base_version_id"])),
        )
        target = str(row["base_revision_id"])
        members = tuple(
            ReviewSuccessorCompositionMember(
                source_revision_id=str(item["revision_id"]),
                source_ordinal=_integer(item["ordinal"], field="successor member ordinal"),
                handling="EDITABLE_BASE" if str(item["revision_id"]) == target else "CARRY_FORWARD",
            )
            for raw in cursor.fetchall()
            for item in (_row(cursor, raw),)
        )
        if sum(member.handling == "EDITABLE_BASE" for member in members) != 1:
            raise CleaningWorkbenchIntegrityError(
                "review successor does not have one editable base"
            )
        member_document = [member.model_dump(mode="json") for member in members]
        signature = f"sha256:{canonical_hash(member_document)}"
        return ReviewSuccessorComposition(
            source_version_id=str(row["base_version_id"]),
            editable_base_revision_id=target,
            members=members,
            composition_hash=signature,
        )

    def _create_output_version(
        self,
        cursor: DbApiCursor,
        *,
        state: CleaningWorkbenchState,
        commit: CommitSucceeded,
        output_members: tuple[WorkbenchOutputMember, ...],
    ) -> None:
        """Clone only safe P07 projections into a new immutable REVIEWING version."""

        if not output_members:
            raise CleaningWorkbenchSourceIncomplete("a commit needs at least one output revision")
        editable = tuple(member for member in output_members if member.member_mode == "EDIT_RESULT")
        if len(editable) != 1 or editable[0].source_revision_id != state.base.revision_id:
            raise CleaningWorkbenchSourceIncomplete(
                "a commit needs exactly one editable base revision"
            )
        scope = state.scope
        dataset_id = state.base.dataset_id
        source_version_id = state.base.version_id
        output_version_id = commit.output_version.version_id
        cursor.execute(
            """
            SELECT version_document
              FROM dataset_registry.dataset_versions
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s AND version_id = %s
            """,
            (*_scope_key(scope), dataset_id, source_version_id),
        )
        source_version = cursor.fetchone()
        if source_version is None:
            raise CleaningWorkbenchSourceIncomplete("the immutable source version is missing")
        cursor.execute(
            """
            SELECT content_snapshot_id, content_snapshot_hash, manifest_id, manifest_sha256,
                   operational_revision, content_document
              FROM dataset_registry.dataset_version_content_projections
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s AND version_id = %s
            """,
            (*_scope_key(scope), dataset_id, source_version_id),
        )
        source_content = cursor.fetchone()
        if source_content is None:
            raise CleaningWorkbenchSourceIncomplete("the source content projection is missing")
        source_content_row = _row(cursor, source_content)
        cursor.execute(
            """
            SELECT schema_snapshot_id, channel_count, schema_document
              FROM dataset_registry.dataset_version_schema_details
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s AND version_id = %s
            """,
            (*_scope_key(scope), dataset_id, source_version_id),
        )
        source_schema = cursor.fetchone()
        if source_schema is None:
            raise CleaningWorkbenchSourceIncomplete("the source schema detail is missing")
        source_schema_row = _row(cursor, source_schema)
        cursor.execute(
            """
            SELECT capacity_state, capacity_document
              FROM dataset_registry.dataset_version_capacity_facts
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s AND version_id = %s
            """,
            (*_scope_key(scope), dataset_id, source_version_id),
        )
        source_capacity = cursor.fetchone()
        if (
            source_capacity is None
            or str(_row(cursor, source_capacity)["capacity_state"]) != "SETTLED"
        ):
            raise CleaningWorkbenchSourceIncomplete("the source capacity facts are not settled")
        source_capacity_row = _row(cursor, source_capacity)

        source_member_rows = self._source_members(
            cursor, state=state, output_members=output_members
        )
        source_ids = set(source_member_rows)
        if source_ids != {member.source_revision_id for member in output_members}:
            raise CleaningWorkbenchSourceIncomplete("a selected source revision is missing")
        output_hashes = {
            member.source_revision_id: _output_content_hash(
                source_member_rows[member.source_revision_id]["revision_document"],
                commit=commit,
                edited=member.member_mode == "EDIT_RESULT",
            )
            for member in output_members
        }
        output_by_source = {member.source_revision_id: member for member in output_members}

        output_version_document = {
            "scope": scope.model_dump(mode="json"),
            "dataset_id": dataset_id,
            "version_id": output_version_id,
            "display_version": f"cleaned-{commit.commit_id.removeprefix('commit_')[:20]}",
            "kind": "CLEANED",
            "created_at": commit.completed_at.isoformat(),
            "etag": f'"{output_version_id}:v1"',
            "version_token": canonical_hash(
                {"commit_id": commit.commit_id, "operation_hash": commit.operation_hash}
            ),
            "status": "REVIEWING",
            "source_draft_id": state.draft.draft_id,
            "delivery_status": "LOGICAL_SUCCEEDED",
            "approved_review_decision_id": None,
            "allowed_actions": [{"action": "OPEN_VERSION", "allowed": True, "blocked_reasons": []}],
        }
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_versions (
                organization_id, project_id, region_code, dataset_id, version_id,
                display_version, version_kind, version_status, created_at, published_at,
                version_document
            ) VALUES (%s, %s, %s, %s, %s, %s, 'CLEANED', 'REVIEWING', %s, NULL, %s::jsonb)
            """,
            (
                *_scope_key(scope),
                dataset_id,
                output_version_id,
                output_version_document["display_version"],
                commit.completed_at,
                _document(output_version_document),
            ),
        )

        self._clone_content_projection(
            cursor,
            state=state,
            commit=commit,
            source_content=source_content_row,
            output_members=output_members,
            output_hashes=output_hashes,
        )
        self._clone_schema_detail(
            cursor,
            scope=scope,
            dataset_id=dataset_id,
            output_version_id=output_version_id,
            source_schema=source_schema_row,
        )
        self._clone_capacity_facts(
            cursor,
            scope=scope,
            dataset_id=dataset_id,
            output_version_id=output_version_id,
            source_capacity=source_capacity_row,
            completed_at=commit.completed_at,
            commit_id=commit.commit_id,
        )
        self._clone_episodes_and_revisions(
            cursor,
            state=state,
            commit=commit,
            output_members=output_members,
            source_members=source_member_rows,
            output_hashes=output_hashes,
        )
        self._clone_manifest_entries(
            cursor,
            state=state,
            commit=commit,
            source_ids=source_ids,
            output_by_source=output_by_source,
            output_hashes=output_hashes,
        )
        self._increment_dataset_review_count(cursor, state=state, completed_at=commit.completed_at)

    def _source_members(
        self,
        cursor: DbApiCursor,
        *,
        state: CleaningWorkbenchState,
        output_members: tuple[WorkbenchOutputMember, ...],
    ) -> dict[str, dict[str, object]]:
        source_ids = tuple(member.source_revision_id for member in output_members)
        cursor.execute(
            """
            SELECT revision.revision_id, revision.episode_id, revision.ordinal,
                   revision.revision_document, episode.episode_document,
                   episode.included, episode.success_state, episode.task, episode.robot_id,
                   episode.started_at, episode.started_at_ns, episode.change_type
              FROM dataset_registry.dataset_version_episode_revisions AS revision
              JOIN dataset_registry.dataset_version_episodes AS episode
                ON episode.organization_id = revision.organization_id
               AND episode.project_id = revision.project_id
               AND episode.region_code = revision.region_code
               AND episode.dataset_id = revision.dataset_id
               AND episode.version_id = revision.version_id
               AND episode.episode_id = revision.episode_id
               AND episode.revision_id = revision.revision_id
             WHERE revision.organization_id = %s AND revision.project_id = %s
               AND revision.region_code = %s AND revision.dataset_id = %s
               AND revision.version_id = %s AND revision.revision_id = ANY(%s::text[])
            """,
            (
                *_scope_key(state.scope),
                state.base.dataset_id,
                state.base.version_id,
                list(source_ids),
            ),
        )
        return {
            str(item["revision_id"]): item
            for raw in cursor.fetchall()
            for item in (_row(cursor, raw),)
        }

    def _clone_content_projection(
        self,
        cursor: DbApiCursor,
        *,
        state: CleaningWorkbenchState,
        commit: CommitSucceeded,
        source_content: Mapping[str, object],
        output_members: tuple[WorkbenchOutputMember, ...],
        output_hashes: Mapping[str, str],
    ) -> None:
        document = _json_object(source_content["content_document"])
        output_version_id = commit.output_version.version_id
        content_identity = canonical_hash(
            {"commit_id": commit.commit_id, "operation_hash": commit.operation_hash}
        )
        snapshot = _json_object(document.get("content_snapshot"))
        snapshot["content_snapshot_id"] = f"snapshot_{content_identity[:40]}"
        snapshot["content_snapshot_hash"] = canonical_hash(
            {
                "source_snapshot": source_content["content_snapshot_hash"],
                "members": [member.revision_id for member in output_members],
                "operation_hash": commit.operation_hash,
            }
        )
        snapshot["revision_refs"] = [
            {
                "episode_id": member.episode_id,
                "revision_id": member.revision_id,
                "ordinal": member.ordinal,
                "content_sha256": output_hashes[member.source_revision_id],
            }
            for member in output_members
        ]
        manifest = _json_object(document.get("manifest"))
        manifest["manifest_id"] = f"manifest_{content_identity[:40]}"
        manifest["sha256"] = canonical_hash(
            {
                "source_manifest": source_content["manifest_sha256"],
                "content_snapshot_hash": snapshot["content_snapshot_hash"],
            }
        )
        document["version_id"] = output_version_id
        document["content_snapshot"] = snapshot
        document["manifest"] = manifest
        document["operational_revision"] = f"cleaning:{commit.commit_id}"
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_version_content_projections (
                organization_id, project_id, region_code, dataset_id, version_id,
                content_snapshot_id, content_snapshot_hash, manifest_id, manifest_sha256,
                operational_revision, content_document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                *_scope_key(state.scope),
                state.base.dataset_id,
                output_version_id,
                snapshot["content_snapshot_id"],
                snapshot["content_snapshot_hash"],
                manifest["manifest_id"],
                manifest["sha256"],
                document["operational_revision"],
                _document(document),
            ),
        )

    def _clone_schema_detail(
        self,
        cursor: DbApiCursor,
        *,
        scope: CleaningWorkbenchScope,
        dataset_id: str,
        output_version_id: str,
        source_schema: Mapping[str, object],
    ) -> None:
        document = _json_object(source_schema["schema_document"])
        document["version_id"] = output_version_id
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_version_schema_details (
                organization_id, project_id, region_code, dataset_id, version_id,
                schema_snapshot_id, channel_count, schema_document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                *_scope_key(scope),
                dataset_id,
                output_version_id,
                source_schema["schema_snapshot_id"],
                source_schema["channel_count"],
                _document(document),
            ),
        )

    def _clone_capacity_facts(
        self,
        cursor: DbApiCursor,
        *,
        scope: CleaningWorkbenchScope,
        dataset_id: str,
        output_version_id: str,
        source_capacity: Mapping[str, object],
        completed_at: datetime,
        commit_id: str,
    ) -> None:
        document = _json_object(source_capacity["capacity_document"])
        document["version_id"] = output_version_id
        document["calculated_at"] = completed_at.isoformat()
        document["basis_revision"] = f"cleaning:{commit_id}"
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_version_capacity_facts (
                organization_id, project_id, region_code, dataset_id, version_id,
                capacity_state, calculated_at, capacity_document
            ) VALUES (%s, %s, %s, %s, %s, 'SETTLED', %s, %s::jsonb)
            """,
            (*_scope_key(scope), dataset_id, output_version_id, completed_at, _document(document)),
        )

    def _clone_episodes_and_revisions(
        self,
        cursor: DbApiCursor,
        *,
        state: CleaningWorkbenchState,
        commit: CommitSucceeded,
        output_members: tuple[WorkbenchOutputMember, ...],
        source_members: Mapping[str, Mapping[str, object]],
        output_hashes: Mapping[str, str],
    ) -> None:
        output_version_id = commit.output_version.version_id
        for member in output_members:
            source = source_members[member.source_revision_id]
            revision_document = _json_object(source["revision_document"])
            episode_document = _json_object(source["episode_document"])
            edited = member.member_mode == "EDIT_RESULT"
            revision_document["version_id"] = output_version_id
            revision_document["revision_id"] = member.revision_id
            revision_document["ordinal"] = member.ordinal
            revision_document["content_sha256"] = output_hashes[member.source_revision_id]
            if edited:
                revision_document["duration_ns"] = state.edl.summary.output_duration_ns
                revision_document["streams"] = _logical_output_streams(
                    revision_document.get("streams"),
                    output_duration_ns=state.edl.summary.output_duration_ns,
                )
            selected = _json_object(episode_document.get("selected_revision"))
            selected["revision_id"] = member.revision_id
            selected["ordinal"] = member.ordinal
            selected["content_sha256"] = output_hashes[member.source_revision_id]
            episode_document["version_id"] = output_version_id
            episode_document["selected_revision"] = selected
            episode_document["review_status"] = None
            episode_document["review_finding_count"] = None
            episode_document["has_finding"] = False
            cursor.execute(
                """
                INSERT INTO dataset_registry.dataset_version_episodes (
                    organization_id, project_id, region_code, dataset_id, version_id,
                    episode_id, revision_id, ordinal, started_at, started_at_ns, included,
                    success_state, task, robot_id, review_status, review_finding_count,
                    has_finding, change_type, episode_document
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          NULL, 0, FALSE, %s, %s::jsonb)
                """,
                (
                    *_scope_key(state.scope),
                    state.base.dataset_id,
                    output_version_id,
                    member.episode_id,
                    member.revision_id,
                    member.ordinal,
                    source["started_at"],
                    source["started_at_ns"],
                    source["included"],
                    source["success_state"],
                    source["task"],
                    source["robot_id"],
                    source["change_type"],
                    _document(episode_document),
                ),
            )
            cursor.execute(
                """
                INSERT INTO dataset_registry.dataset_version_episode_revisions (
                    organization_id, project_id, region_code, dataset_id, version_id,
                    episode_id, revision_id, ordinal, revision_document
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    *_scope_key(state.scope),
                    state.base.dataset_id,
                    output_version_id,
                    member.episode_id,
                    member.revision_id,
                    member.ordinal,
                    _document(revision_document),
                ),
            )

    def _clone_manifest_entries(
        self,
        cursor: DbApiCursor,
        *,
        state: CleaningWorkbenchState,
        commit: CommitSucceeded,
        source_ids: set[str],
        output_by_source: Mapping[str, WorkbenchOutputMember],
        output_hashes: Mapping[str, str],
    ) -> None:
        cursor.execute(
            """
            SELECT entry_id, episode_id, revision_id, entry_role, size_bytes,
                   entry_document
              FROM dataset_registry.dataset_version_manifest_entries
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s AND version_id = %s
             ORDER BY entry_id ASC
            """,
            (*_scope_key(state.scope), state.base.dataset_id, state.base.version_id),
        )
        rows = tuple(_row(cursor, raw) for raw in cursor.fetchall())
        selected = tuple(row for row in rows if str(row["revision_id"]) in source_ids)
        if not selected:
            raise CleaningWorkbenchSourceIncomplete("the source manifest has no selected revisions")
        for row in selected:
            source_id = str(row["revision_id"])
            member = output_by_source[source_id]
            document = _json_object(row["entry_document"])
            document["revision_id"] = member.revision_id
            document["episode_id"] = member.episode_id
            if member.member_mode == "EDIT_RESULT":
                document["sha256"] = output_hashes[source_id]
                document["safe_locator"] = None
            cursor.execute(
                """
                INSERT INTO dataset_registry.dataset_version_manifest_entries (
                    organization_id, project_id, region_code, dataset_id, version_id,
                    entry_id, episode_id, revision_id, entry_role, size_bytes, entry_document
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    *_scope_key(state.scope),
                    state.base.dataset_id,
                    commit.output_version.version_id,
                    row["entry_id"],
                    member.episode_id,
                    member.revision_id,
                    row["entry_role"],
                    row["size_bytes"],
                    _document(document),
                ),
            )

    def _increment_dataset_review_count(
        self,
        cursor: DbApiCursor,
        *,
        state: CleaningWorkbenchState,
        completed_at: datetime,
    ) -> None:
        cursor.execute(
            """
            SELECT pending_review_version_count, dataset_document
              FROM dataset_registry.datasets
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s
             FOR UPDATE
            """,
            (*_scope_key(state.scope), state.base.dataset_id),
        )
        raw_dataset = cursor.fetchone()
        if raw_dataset is None:
            raise CleaningWorkbenchSourceIncomplete("the output Dataset record is missing")
        dataset = _row(cursor, raw_dataset)
        pending = (
            _integer(dataset["pending_review_version_count"], field="pending review count") + 1
        )
        document = _json_object(dataset["dataset_document"])
        document["pending_review_version_count"] = str(pending)
        document["updated_at"] = completed_at.isoformat()
        document["activity_at"] = completed_at.isoformat()
        document["etag"] = f'"{state.base.dataset_id}:p11:{canonical_hash(document)[:16]}"'
        cursor.execute(
            """
            UPDATE dataset_registry.datasets
               SET pending_review_version_count = %s, version = version + 1,
                   dataset_document = %s::jsonb, updated_at = %s, activity_at = %s
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s
            """,
            (
                pending,
                _document(document),
                completed_at,
                completed_at,
                *_scope_key(state.scope),
                state.base.dataset_id,
            ),
        )
        cursor.execute(
            """
            SELECT pending_review_version_count, calculation_state, fact_document
              FROM dataset_registry.dataset_detail_facts
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s
             FOR UPDATE
            """,
            (*_scope_key(state.scope), state.base.dataset_id),
        )
        raw_facts = cursor.fetchone()
        if raw_facts is None:
            return
        facts = _row(cursor, raw_facts)
        fact_document = _json_object(facts["fact_document"])
        summary = _json_object(fact_document.get("summary"))
        summary["pending_review_version_count"] = str(pending)
        fact_document["summary"] = summary
        cursor.execute(
            """
            UPDATE dataset_registry.dataset_detail_facts
               SET pending_review_version_count = %s, calculated_at = %s, fact_document = %s::jsonb
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s
            """,
            (
                pending,
                completed_at,
                _document(fact_document),
                *_scope_key(state.scope),
                state.base.dataset_id,
            ),
        )

    def _update_legacy_issue_handoff(
        self, cursor: DbApiCursor, *, state: CleaningWorkbenchState, commit: CommitSucceeded
    ) -> None:
        """Keep P09's resolution evidence authoritative for issue-derived drafts."""

        cursor.execute(
            """
            SELECT draft_document
              FROM manual_cleaning.cleaning_drafts
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND draft_id = %s
             FOR UPDATE
            """,
            (*_scope_key(state.scope), state.draft.draft_id),
        )
        raw = cursor.fetchone()
        if raw is None:
            return
        document = _json_object(_row(cursor, raw)["draft_document"])
        document["status"] = "COMMITTED"
        document["updated_at"] = commit.completed_at.isoformat()
        cursor.execute(
            """
            UPDATE manual_cleaning.cleaning_drafts
               SET status = 'COMMITTED', draft_document = %s::jsonb, updated_at = %s
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND draft_id = %s
            """,
            (
                _document(document),
                commit.completed_at,
                *_scope_key(state.scope),
                state.draft.draft_id,
            ),
        )
        cursor.execute(
            """
            INSERT INTO manual_cleaning.cleaning_draft_commits (
                organization_id, project_id, region_code, commit_id, draft_id, dataset_id,
                output_version_id, status, committed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'SUCCEEDED', %s)
            ON CONFLICT (organization_id, project_id, region_code, commit_id) DO NOTHING
            """,
            (
                *_scope_key(state.scope),
                commit.commit_id,
                state.draft.draft_id,
                state.base.dataset_id,
                commit.output_version.version_id,
                commit.completed_at,
            ),
        )

    @staticmethod
    def _insert_job(cursor: DbApiCursor, job: CleaningAsyncJob) -> None:
        cursor.execute(
            """
            INSERT INTO manual_cleaning.cleaning_workbench_jobs (
                organization_id, project_id, region_code, job_id, draft_id, job_kind,
                job_document, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (organization_id, project_id, region_code, job_id) DO NOTHING
            """,
            (
                *_scope_key(job.scope),
                job.job_id,
                job.resource_ref["resource_id"],
                job.kind,
                _document(job),
                job.created_at,
            ),
        )

    @staticmethod
    def _insert_audit(cursor: DbApiCursor, event: CleaningWorkbenchAuditEvent) -> None:
        cursor.execute(
            """
            INSERT INTO core.audit_events (
                audit_id, project_id, region_code, actor_id, action, resource_type,
                resource_id, request_id, details, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, 'CLEANING_DRAFT', %s, %s, %s::jsonb, %s)
            """,
            (
                str(uuid4()),
                event.project_id,
                event.region_code,
                event.actor_id,
                event.action,
                event.resource_id,
                event.request_id,
                _document(
                    {
                        "before_hash": event.before_hash,
                        "after_hash": event.after_hash,
                        "outcome": "SUCCEEDED",
                        **(event.details or {}),
                    }
                ),
                event.occurred_at,
            ),
        )


def _streams_from_revision(document: Mapping[str, object]) -> tuple[CleaningStream, ...]:
    raw_streams = document.get("streams")
    if not isinstance(raw_streams, list) or not raw_streams:
        raise CleaningWorkbenchIntegrityError("base revision has no durable streams")
    streams: list[CleaningStream] = []
    for raw in raw_streams:
        if not isinstance(raw, Mapping):
            raise CleaningWorkbenchIntegrityError("base revision stream is not an object")
        start, end = raw.get("t_start_ns"), raw.get("t_end_ns")
        if start is None or end is None:
            raise CleaningWorkbenchIntegrityError("base revision stream has no time bounds")
        duration = int(str(end)) - int(str(start))
        if duration <= 0:
            raise CleaningWorkbenchIntegrityError("base revision stream duration must be positive")
        streams.append(
            CleaningStream(
                stream_id=str(raw["episode_stream_id"]),
                channel_path=str(raw["channel_path"]),
                kind=str(raw["kind"]),
                duration_ns=str(duration),
            )
        )
    return tuple(streams)


def _origin_from_row(row: Mapping[str, object]) -> IssueDerivedOrigin | ReviewReturnOrigin:
    origin_type = str(row["origin_type"])
    if origin_type == "ISSUE_DERIVED":
        issue_id = _optional_text(row.get("source_issue_id"))
        if issue_id is None:
            raise CleaningWorkbenchIntegrityError("issue-derived draft has no ManualIssue")
        return IssueDerivedOrigin(
            manual_issue_context=IssueDerivedContext(
                dataset_id=str(row["dataset_id"]),
                base_version_id=str(row["base_version_id"]),
                episode_id=str(row["episode_id"]),
                base_revision_id=str(row["base_revision_id"]),
                selected_stream_id=str(row["selected_stream_id"]),
                selected_channel_path=_optional_text(row.get("selected_channel_path")),
                start_ns=str(row["start_ns"]),
                end_ns=str(row["end_ns"]),
                manual_issue_ids=(issue_id,),
            )
        )
    if origin_type == "REVIEW_RETURN":
        supersedes = _optional_text(row.get("supersedes_draft_id"))
        returned_version = _optional_text(row.get("returned_from_version_id"))
        decision = _optional_text(row.get("returned_from_review_decision_id"))
        if supersedes is None or returned_version is None or decision is None:
            raise CleaningWorkbenchIntegrityError("review-return draft has incomplete lineage")
        return ReviewReturnOrigin(
            review_return_lineage=ReviewReturnLineage(
                supersedes_draft_id=supersedes,
                returned_from_version_id=returned_version,
                returned_from_review_decision_id=decision,
            )
        )
    raise CleaningWorkbenchIntegrityError(f"unknown P11 origin type {origin_type!r}")


def _review_summary(feedback: ReviewReturnFeedback) -> ReviewReturnSummary:
    return ReviewReturnSummary(
        review_decision_id=feedback.review_decision.id,
        review_finding_ids=feedback.review_finding_ids,
        finding_count=str(len(feedback.findings)),
        successor_draft_id=feedback.successor_draft_id,
        supersedes_draft_id=feedback.supersedes_draft_id,
        returned_from_version_id=feedback.returned_from_version_id,
        returned_from_review_decision_id=feedback.returned_from_review_decision_id,
    )


def _assert_editable_state(
    *,
    state: CleaningWorkbenchState,
    expected_etag: str,
    expected_edl_revision: int,
    expected_operation_hash: str | None,
) -> None:
    if (
        state.draft.status != "EDITING"
        or state.draft.etag != expected_etag
        or int(state.edl.edl_revision) != expected_edl_revision
        or (
            expected_operation_hash is not None
            and state.edl.operation_hash != expected_operation_hash
        )
    ):
        raise CleaningWorkbenchPreconditionError


def _operations_document(operations: Sequence[CleaningOperation]) -> list[dict[str, object]]:
    return [operation.model_dump(mode="json") for operation in operations]


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _integer(value: object, *, field: str) -> int:
    """Parse a persisted numeric field with an explicit integrity failure."""

    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise CleaningWorkbenchIntegrityError(f"{field} is not an integer") from exc


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    raise CleaningWorkbenchIntegrityError("timestamp projection is invalid")


def _json_object(value: object) -> dict[str, object]:
    decoded = _decode_json(value)
    if not isinstance(decoded, Mapping):
        raise CleaningWorkbenchSourceIncomplete("a required source document is not an object")
    return deepcopy(dict(decoded))


def _output_content_hash(value: object, *, commit: CommitSucceeded, edited: bool) -> str:
    document = _json_object(value)
    source_hash = document.get("content_sha256")
    if not isinstance(source_hash, str) or len(source_hash) != 64:
        raise CleaningWorkbenchSourceIncomplete("source revision has no content hash")
    if not edited:
        return source_hash
    return canonical_hash(
        {
            "source_revision_hash": source_hash,
            "operation_hash": commit.operation_hash,
            "commit_id": commit.commit_id,
        }
    )


def _logical_output_streams(value: object, *, output_duration_ns: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise CleaningWorkbenchSourceIncomplete("source revision has no streams")
    result: list[dict[str, object]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise CleaningWorkbenchSourceIncomplete("source revision stream is invalid")
        stream = deepcopy(dict(raw))
        stream["t_start_ns"] = "0"
        stream["t_end_ns"] = output_duration_ns
        result.append(stream)
    return result
