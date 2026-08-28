"""RLS-scoped persistence for P09 ManualIssue facts.

The repository stores only the minimal Draft handoff that P09 is entitled to
create.  Later P10/P11 projections may extend those rows, but P09 never
substitutes a ReviewFinding or fabricates a READY output version.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from threading import RLock
from typing import Any, Protocol, cast
from uuid import uuid4

from hc_data_platform.dataset_registry.models import DatasetPageEpisodeRevision

from .models import (
    ManualCleaningDraftRecord,
    ManualIssueAuditEvent,
    ManualIssueRecord,
    ManualIssueResolutionCandidate,
    ManualIssueScope,
    ManualIssueSourceFacts,
)


@dataclass(frozen=True, slots=True)
class ManualIssueFilters:
    query: str | None = None
    dataset_id: str | None = None
    version_id: str | None = None
    episode_id: str | None = None
    statuses: tuple[str, ...] = ()
    issue_types: tuple[str, ...] = ()
    severities: tuple[str, ...] = ()
    discovery_sources: tuple[str, ...] = ()
    assignee_id: str | None = None


class ManualIssueDuplicateError(RuntimeError):
    pass


class ManualIssuePreconditionError(RuntimeError):
    pass


class ManualIssueRepository(Protocol):
    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool: ...

    def list_issues(
        self, *, scope: ManualIssueScope, filters: ManualIssueFilters
    ) -> tuple[ManualIssueRecord, ...]: ...

    def get_issue(self, *, scope: ManualIssueScope, issue_id: str) -> ManualIssueRecord | None: ...

    def source_facts(
        self,
        *,
        scope: ManualIssueScope,
        version_id: str,
        episode_id: str,
        revision_id: str,
        stream_id: str,
    ) -> ManualIssueSourceFacts | None: ...

    def source_facts_for_annotation_task(
        self,
        *,
        scope: ManualIssueScope,
        task_id: str,
        stream_ref: str,
    ) -> ManualIssueSourceFacts | None: ...

    def create_issue(
        self, *, record: ManualIssueRecord, audit_event: ManualIssueAuditEvent
    ) -> ManualIssueRecord: ...

    def update_issue(
        self,
        *,
        expected_etag: str,
        record: ManualIssueRecord,
        audit_event: ManualIssueAuditEvent,
    ) -> None: ...

    def list_drafts_for_issue(
        self, *, scope: ManualIssueScope, issue_id: str
    ) -> tuple[ManualCleaningDraftRecord, ...]: ...

    def create_draft(
        self,
        *,
        expected_etag: str,
        next_issue: ManualIssueRecord,
        draft: ManualCleaningDraftRecord,
        audit_event: ManualIssueAuditEvent,
    ) -> None: ...

    def resolution_candidate(
        self,
        *,
        scope: ManualIssueScope,
        issue_id: str,
        resolution_version_id: str,
    ) -> ManualIssueResolutionCandidate | None: ...

    def append_audit(self, event: ManualIssueAuditEvent) -> None: ...


def _scope_key(scope: ManualIssueScope) -> tuple[str, str, str]:
    return (scope.organization_id, scope.project_id, scope.region_code)


def _issue_key(scope: ManualIssueScope, issue_id: str) -> tuple[str, str, str, str]:
    return (*_scope_key(scope), issue_id)


def _source_key(
    scope: ManualIssueScope,
    version_id: str,
    episode_id: str,
    revision_id: str,
    stream_id: str,
) -> tuple[str, str, str, str, str, str, str]:
    return (*_scope_key(scope), version_id, episode_id, revision_id, stream_id)


def _candidate_key(
    scope: ManualIssueScope, issue_id: str, resolution_version_id: str
) -> tuple[str, str, str, str, str]:
    return (*_issue_key(scope, issue_id), resolution_version_id)


def _matches(record: ManualIssueRecord, filters: ManualIssueFilters) -> bool:
    if filters.query:
        needle = filters.query.casefold()
        if not any(
            needle in value.casefold() for value in (record.id, record.episode_id, record.note)
        ):
            return False
    if filters.dataset_id is not None and record.dataset_id != filters.dataset_id:
        return False
    if filters.version_id is not None and record.origin_dataset_version_id != filters.version_id:
        return False
    if filters.episode_id is not None and record.episode_id != filters.episode_id:
        return False
    if filters.statuses and record.status not in filters.statuses:
        return False
    if filters.issue_types and record.issue_type not in filters.issue_types:
        return False
    if filters.severities and record.severity not in filters.severities:
        return False
    if filters.discovery_sources and record.discovery_source not in filters.discovery_sources:
        return False
    return (
        filters.assignee_id is None
        or record.assignee is not None
        and (record.assignee.id == filters.assignee_id)
    )


class InMemoryManualIssueRepository:
    """Thread-safe reference store with the same P09 scope and transition rules."""

    def __init__(
        self,
        *,
        organization_projects: tuple[tuple[str, str], ...] = (),
        issues: tuple[ManualIssueRecord, ...] = (),
        source_facts: tuple[ManualIssueSourceFacts, ...] = (),
        annotation_source_facts: tuple[tuple[str, str, ManualIssueSourceFacts], ...] = (),
        drafts: tuple[ManualCleaningDraftRecord, ...] = (),
        resolution_candidates: tuple[ManualIssueResolutionCandidate, ...] = (),
    ) -> None:
        self._organization_projects = set(organization_projects)
        self._organization_projects.update(
            (item.scope.organization_id, item.scope.project_id) for item in issues
        )
        self._issues = {_issue_key(item.scope, item.id): item for item in issues}
        self._sources = {
            _source_key(
                item.scope,
                item.version_id,
                item.episode_id,
                item.revision_id,
                item.stream_id,
            ): item
            for item in source_facts
        }
        self._annotation_sources = {
            (*_scope_key(item.scope), task_id, stream_ref): item
            for task_id, stream_ref, item in annotation_source_facts
        }
        self._drafts = {_issue_key(item.scope, item.draft_id): item for item in drafts}
        self._resolution_candidates = {
            _candidate_key(item.scope, item.manual_issue_id, item.version_id): item
            for item in resolution_candidates
        }
        self.audit_events: list[ManualIssueAuditEvent] = []
        self._lock = RLock()

    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool:
        with self._lock:
            return (organization_id, project_id) in self._organization_projects

    def list_issues(
        self, *, scope: ManualIssueScope, filters: ManualIssueFilters
    ) -> tuple[ManualIssueRecord, ...]:
        with self._lock:
            return tuple(
                record
                for key, record in self._issues.items()
                if key[:3] == _scope_key(scope) and _matches(record, filters)
            )

    def get_issue(self, *, scope: ManualIssueScope, issue_id: str) -> ManualIssueRecord | None:
        with self._lock:
            return self._issues.get(_issue_key(scope, issue_id))

    def source_facts(
        self,
        *,
        scope: ManualIssueScope,
        version_id: str,
        episode_id: str,
        revision_id: str,
        stream_id: str,
    ) -> ManualIssueSourceFacts | None:
        with self._lock:
            return self._sources.get(
                _source_key(scope, version_id, episode_id, revision_id, stream_id)
            )

    def source_facts_for_annotation_task(
        self,
        *,
        scope: ManualIssueScope,
        task_id: str,
        stream_ref: str,
    ) -> ManualIssueSourceFacts | None:
        with self._lock:
            return self._annotation_sources.get((*_scope_key(scope), task_id, stream_ref))

    def create_issue(
        self, *, record: ManualIssueRecord, audit_event: ManualIssueAuditEvent
    ) -> ManualIssueRecord:
        with self._lock:
            key = _issue_key(record.scope, record.id)
            if key in self._issues:
                raise ManualIssueDuplicateError
            self._issues[key] = record
            self.audit_events.append(audit_event)
            return record

    def update_issue(
        self,
        *,
        expected_etag: str,
        record: ManualIssueRecord,
        audit_event: ManualIssueAuditEvent,
    ) -> None:
        with self._lock:
            key = _issue_key(record.scope, record.id)
            current = self._issues.get(key)
            if current is None or current.etag != expected_etag:
                raise ManualIssuePreconditionError
            self._issues[key] = record
            self.audit_events.append(audit_event)

    def list_drafts_for_issue(
        self, *, scope: ManualIssueScope, issue_id: str
    ) -> tuple[ManualCleaningDraftRecord, ...]:
        with self._lock:
            return tuple(
                draft
                for draft in self._drafts.values()
                if draft.scope == scope and draft.source_issue_id == issue_id
            )

    def create_draft(
        self,
        *,
        expected_etag: str,
        next_issue: ManualIssueRecord,
        draft: ManualCleaningDraftRecord,
        audit_event: ManualIssueAuditEvent,
    ) -> None:
        with self._lock:
            issue_key = _issue_key(next_issue.scope, next_issue.id)
            current = self._issues.get(issue_key)
            draft_key = _issue_key(draft.scope, draft.draft_id)
            if current is None or current.etag != expected_etag or draft_key in self._drafts:
                raise ManualIssuePreconditionError
            self._issues[issue_key] = next_issue
            self._drafts[draft_key] = draft
            self.audit_events.append(audit_event)

    def resolution_candidate(
        self,
        *,
        scope: ManualIssueScope,
        issue_id: str,
        resolution_version_id: str,
    ) -> ManualIssueResolutionCandidate | None:
        with self._lock:
            candidate = self._resolution_candidates.get(
                _candidate_key(scope, issue_id, resolution_version_id)
            )
            if candidate is None:
                return None
            roots = {
                item.draft_id
                for item in self._drafts.values()
                if item.scope == scope and item.source_issue_id == issue_id
            }
            return candidate if candidate.root_issue_draft_id in roots else None

    def append_audit(self, event: ManualIssueAuditEvent) -> None:
        with self._lock:
            self.audit_events.append(event)


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


ConnectionFactory = Callable[[], DbApiConnection]


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


def _decode_json(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


def _document(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = cast(Any, value).model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _issue(value: object) -> ManualIssueRecord:
    return ManualIssueRecord.model_validate(_decode_json(value))


def _draft(value: object) -> ManualCleaningDraftRecord:
    return ManualCleaningDraftRecord.model_validate(_decode_json(value))


class PostgresManualIssueRepository:
    """Production PostgreSQL persistence scoped by the active request context."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT 1
                  FROM registry.organization_projects
                 WHERE organization_id = %s AND project_id = %s
                """,
                (organization_id, project_id),
            )
            return cursor.fetchone() is not None
        finally:
            cursor.close()
            connection.close()

    def list_issues(
        self, *, scope: ManualIssueScope, filters: ManualIssueFilters
    ) -> tuple[ManualIssueRecord, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT issue_document
                  FROM manual_cleaning.manual_issues
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND (
                        %s::text IS NULL
                        OR manual_issue_id ILIKE '%%' || %s || '%%'
                        OR episode_id ILIKE '%%' || %s || '%%'
                        OR note ILIKE '%%' || %s || '%%'
                   )
                   AND (%s::text IS NULL OR dataset_id = %s)
                   AND (%s::text IS NULL OR origin_dataset_version_id = %s)
                   AND (%s::text IS NULL OR episode_id = %s)
                   AND (cardinality(%s::text[]) = 0 OR status = ANY(%s::text[]))
                   AND (cardinality(%s::text[]) = 0 OR issue_type = ANY(%s::text[]))
                   AND (cardinality(%s::text[]) = 0 OR severity = ANY(%s::text[]))
                   AND (
                        cardinality(%s::text[]) = 0
                        OR COALESCE(issue_document ->> 'discovery_source', 'DATA_VIEWER')
                           = ANY(%s::text[])
                   )
                   AND (%s::text IS NULL OR assignee_id = %s)
                """,
                (
                    *_scope_key(scope),
                    filters.query,
                    filters.query,
                    filters.query,
                    filters.query,
                    filters.dataset_id,
                    filters.dataset_id,
                    filters.version_id,
                    filters.version_id,
                    filters.episode_id,
                    filters.episode_id,
                    list(filters.statuses),
                    list(filters.statuses),
                    list(filters.issue_types),
                    list(filters.issue_types),
                    list(filters.severities),
                    list(filters.severities),
                    list(filters.discovery_sources),
                    list(filters.discovery_sources),
                    filters.assignee_id,
                    filters.assignee_id,
                ),
            )
            return tuple(_issue(_row(cursor, raw)["issue_document"]) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def get_issue(self, *, scope: ManualIssueScope, issue_id: str) -> ManualIssueRecord | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT issue_document
                  FROM manual_cleaning.manual_issues
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND manual_issue_id = %s
                """,
                (*_scope_key(scope), issue_id),
            )
            raw = cursor.fetchone()
            return None if raw is None else _issue(_row(cursor, raw)["issue_document"])
        finally:
            cursor.close()
            connection.close()

    def source_facts(
        self,
        *,
        scope: ManualIssueScope,
        version_id: str,
        episode_id: str,
        revision_id: str,
        stream_id: str,
    ) -> ManualIssueSourceFacts | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT revision.revision_document, schema.schema_snapshot_id
                  FROM dataset_registry.dataset_version_episode_revisions AS revision
                  JOIN dataset_registry.dataset_version_schema_details AS schema
                    ON schema.organization_id = revision.organization_id
                   AND schema.project_id = revision.project_id
                   AND schema.region_code = revision.region_code
                   AND schema.dataset_id = revision.dataset_id
                   AND schema.version_id = revision.version_id
                 WHERE revision.organization_id = %s
                   AND revision.project_id = %s
                   AND revision.region_code = %s
                   AND revision.version_id = %s
                   AND revision.episode_id = %s
                   AND revision.revision_id = %s
                """,
                (*_scope_key(scope), version_id, episode_id, revision_id),
            )
            raw = cursor.fetchone()
            if raw is None:
                return None
            row = _row(cursor, raw)
            revision = DatasetPageEpisodeRevision.model_validate(
                _decode_json(row["revision_document"])
            )
            stream = next(
                (item for item in revision.streams if item.episode_stream_id == stream_id), None
            )
            if stream is None:
                return None
            return ManualIssueSourceFacts(
                scope=scope,
                dataset_id=revision.dataset_id,
                version_id=revision.version_id,
                episode_id=revision.episode_id,
                revision_id=revision.revision_id,
                stream_id=stream.episode_stream_id,
                stream_channel_path=stream.channel_path,
                stream_start_ns=stream.t_start_ns,
                stream_end_ns=stream.t_end_ns,
                schema_snapshot_id=str(row["schema_snapshot_id"]),
            )
        finally:
            cursor.close()
            connection.close()

    def source_facts_for_annotation_task(
        self,
        *,
        scope: ManualIssueScope,
        task_id: str,
        stream_ref: str,
    ) -> ManualIssueSourceFacts | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT task.rollout_id, revision.revision_document,
                       schema.schema_snapshot_id
                  FROM annotation.annotation_tasks AS task
                  JOIN dataset_registry.dataset_version_episode_revisions AS revision
                    ON revision.organization_id = %s
                   AND revision.project_id = task.project_id
                   AND revision.region_code = task.region_code
                   AND revision.dataset_id = task.dataset_id
                   AND revision.version_id = 'version_lance_' || task.dataset_version::text
                  JOIN dataset_registry.dataset_version_schema_details AS schema
                    ON schema.organization_id = revision.organization_id
                   AND schema.project_id = revision.project_id
                   AND schema.region_code = revision.region_code
                   AND schema.dataset_id = revision.dataset_id
                   AND schema.version_id = revision.version_id
                 WHERE task.task_id = %s
                   AND task.project_id = %s
                   AND task.region_code = %s
                 ORDER BY revision.ordinal DESC
                """,
                (scope.organization_id, task_id, scope.project_id, scope.region_code),
            )
            for raw in cursor.fetchall():
                row = _row(cursor, raw)
                rollout_id = str(row["rollout_id"])
                revision = DatasetPageEpisodeRevision.model_validate(
                    _decode_json(row["revision_document"])
                )
                stream = next(
                    (
                        item
                        for item in revision.streams
                        if (
                            item.episode_stream_id == stream_ref
                            or item.channel_path == stream_ref
                            or item.preview_binding is not None
                            and item.preview_binding.camera_id == stream_ref
                            or item.data_binding is not None
                            and item.data_binding.modality_key == stream_ref
                        )
                        and (
                            item.preview_binding is not None
                            and item.preview_binding.rollout_id == rollout_id
                            or item.data_binding is not None
                            and item.data_binding.rollout_id == rollout_id
                        )
                    ),
                    None,
                )
                if stream is None:
                    continue
                return ManualIssueSourceFacts(
                    scope=scope,
                    dataset_id=revision.dataset_id,
                    version_id=revision.version_id,
                    episode_id=revision.episode_id,
                    revision_id=revision.revision_id,
                    stream_id=stream.episode_stream_id,
                    stream_channel_path=stream.channel_path,
                    stream_start_ns=stream.t_start_ns,
                    stream_end_ns=stream.t_end_ns,
                    schema_snapshot_id=str(row["schema_snapshot_id"]),
                )
            return None
        finally:
            cursor.close()
            connection.close()

    def create_issue(
        self, *, record: ManualIssueRecord, audit_event: ManualIssueAuditEvent
    ) -> ManualIssueRecord:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO manual_cleaning.manual_issues (
                    organization_id, project_id, region_code, manual_issue_id, dataset_id,
                    origin_dataset_version_id, episode_id, episode_revision_id,
                    episode_stream_id, schema_snapshot_id, robot_model_version_id,
                    calibration_set_id, start_ns, end_ns, issue_type, severity, status,
                    note, assignee_id, assignee_display_name, version, resolution_version_id,
                    resolution_note, resolved_at, resolved_by_id, resolved_by_display_name,
                    issue_document, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s
                ) ON CONFLICT DO NOTHING
                RETURNING manual_issue_id
                """,
                self._issue_values(record),
            )
            if cursor.fetchone() is None:
                raise ManualIssueDuplicateError
            self._insert_audit(cursor, audit_event)
            connection.commit()
            return record
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def update_issue(
        self,
        *,
        expected_etag: str,
        record: ManualIssueRecord,
        audit_event: ManualIssueAuditEvent,
    ) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._update_issue(cursor, expected_etag=expected_etag, record=record)
            self._insert_audit(cursor, audit_event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def list_drafts_for_issue(
        self, *, scope: ManualIssueScope, issue_id: str
    ) -> tuple[ManualCleaningDraftRecord, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT draft_document
                  FROM manual_cleaning.cleaning_drafts
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND source_issue_id = %s
                """,
                (*_scope_key(scope), issue_id),
            )
            return tuple(_draft(_row(cursor, raw)["draft_document"]) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def create_draft(
        self,
        *,
        expected_etag: str,
        next_issue: ManualIssueRecord,
        draft: ManualCleaningDraftRecord,
        audit_event: ManualIssueAuditEvent,
    ) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._update_issue(cursor, expected_etag=expected_etag, record=next_issue)
            cursor.execute(
                """
                INSERT INTO manual_cleaning.cleaning_drafts (
                    organization_id, project_id, region_code, draft_id, source_issue_id,
                    dataset_id, base_version_id, episode_id, base_revision_id,
                    selected_stream_id, selected_channel_path, start_ns, end_ns, status,
                    draft_document, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s, %s
                )
                """,
                (
                    draft.scope.organization_id,
                    draft.scope.project_id,
                    draft.scope.region_code,
                    draft.draft_id,
                    draft.source_issue_id,
                    draft.dataset_id,
                    draft.base_version_id,
                    draft.episode_id,
                    draft.base_revision_id,
                    draft.selected_stream_id,
                    draft.selected_channel_path,
                    int(draft.start_ns),
                    int(draft.end_ns),
                    draft.status,
                    _document(draft),
                    draft.created_at,
                    draft.updated_at,
                ),
            )
            cursor.execute(
                """
                INSERT INTO manual_cleaning.manual_issue_draft_links (
                    organization_id, project_id, region_code, manual_issue_id, draft_id,
                    linked_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (*_scope_key(draft.scope), draft.source_issue_id, draft.draft_id, draft.created_at),
            )
            cursor.execute(
                """
                INSERT INTO manual_cleaning.cleaning_draft_ancestry (
                    organization_id, project_id, region_code, root_draft_id,
                    descendant_draft_id, lineage_depth, recorded_at
                ) VALUES (%s, %s, %s, %s, %s, 0, %s)
                """,
                (*_scope_key(draft.scope), draft.draft_id, draft.draft_id, draft.created_at),
            )
            self._insert_audit(cursor, audit_event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def resolution_candidate(
        self,
        *,
        scope: ManualIssueScope,
        issue_id: str,
        resolution_version_id: str,
    ) -> ManualIssueResolutionCandidate | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT committed.output_version_id,
                       committed.draft_id AS producer_draft_id,
                       linked.draft_id AS root_draft_id,
                       ancestry.lineage_depth
                  FROM manual_cleaning.manual_issue_draft_links AS linked
                  JOIN manual_cleaning.cleaning_draft_ancestry AS ancestry
                    ON ancestry.organization_id = linked.organization_id
                   AND ancestry.project_id = linked.project_id
                   AND ancestry.region_code = linked.region_code
                   AND ancestry.root_draft_id = linked.draft_id
                  JOIN manual_cleaning.cleaning_draft_commits AS committed
                    ON committed.organization_id = ancestry.organization_id
                   AND committed.project_id = ancestry.project_id
                   AND committed.region_code = ancestry.region_code
                   AND committed.draft_id = ancestry.descendant_draft_id
                  JOIN dataset_registry.dataset_versions AS output_version
                    ON output_version.organization_id = committed.organization_id
                   AND output_version.project_id = committed.project_id
                   AND output_version.region_code = committed.region_code
                   AND output_version.dataset_id = committed.dataset_id
                   AND output_version.version_id = committed.output_version_id
                 WHERE linked.organization_id = %s
                   AND linked.project_id = %s
                   AND linked.region_code = %s
                   AND linked.manual_issue_id = %s
                   AND committed.output_version_id = %s
                   AND committed.status = 'SUCCEEDED'
                   AND output_version.version_status = 'READY'
                 ORDER BY ancestry.lineage_depth ASC, committed.draft_id ASC
                 LIMIT 1
                """,
                (*_scope_key(scope), issue_id, resolution_version_id),
            )
            raw = cursor.fetchone()
            if raw is None:
                return None
            row = _row(cursor, raw)
            return ManualIssueResolutionCandidate(
                scope=scope,
                manual_issue_id=issue_id,
                version_id=str(row["output_version_id"]),
                producer_draft_id=str(row["producer_draft_id"]),
                root_issue_draft_id=str(row["root_draft_id"]),
                lineage_depth=str(row["lineage_depth"]),
            )
        finally:
            cursor.close()
            connection.close()

    def append_audit(self, event: ManualIssueAuditEvent) -> None:
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

    @staticmethod
    def _issue_values(record: ManualIssueRecord) -> tuple[object, ...]:
        assignee = record.assignee
        resolved_by = record.resolved_by
        return (
            record.scope.organization_id,
            record.scope.project_id,
            record.scope.region_code,
            record.id,
            record.dataset_id,
            record.origin_dataset_version_id,
            record.episode_id,
            record.episode_revision_id,
            record.episode_stream_id,
            record.schema_snapshot_id,
            record.robot_model_version_id,
            record.calibration_set_id,
            int(record.start_ns),
            int(record.end_ns),
            record.issue_type,
            record.severity,
            record.status,
            record.note,
            None if assignee is None else assignee.id,
            None if assignee is None else assignee.display_name,
            _resource_version(record.etag),
            None if record.resolution_version is None else record.resolution_version.version_id,
            record.resolution_note,
            record.resolved_at,
            None if resolved_by is None else resolved_by.id,
            None if resolved_by is None else resolved_by.display_name,
            _document(record),
            record.created_at,
            record.updated_at,
        )

    @staticmethod
    def _update_issue(
        cursor: DbApiCursor, *, expected_etag: str, record: ManualIssueRecord
    ) -> None:
        assignee = record.assignee
        resolved_by = record.resolved_by
        cursor.execute(
            """
            UPDATE manual_cleaning.manual_issues
               SET severity = %s,
                   status = %s,
                   note = %s,
                   assignee_id = %s,
                   assignee_display_name = %s,
                   version = %s,
                   resolution_version_id = %s,
                   resolution_note = %s,
                   resolved_at = %s,
                   resolved_by_id = %s,
                   resolved_by_display_name = %s,
                   issue_document = %s::jsonb,
                   updated_at = %s
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND manual_issue_id = %s AND issue_document ->> 'etag' = %s
            RETURNING manual_issue_id
            """,
            (
                record.severity,
                record.status,
                record.note,
                None if assignee is None else assignee.id,
                None if assignee is None else assignee.display_name,
                _resource_version(record.etag),
                None if record.resolution_version is None else record.resolution_version.version_id,
                record.resolution_note,
                record.resolved_at,
                None if resolved_by is None else resolved_by.id,
                None if resolved_by is None else resolved_by.display_name,
                _document(record),
                record.updated_at,
                record.scope.organization_id,
                record.scope.project_id,
                record.scope.region_code,
                record.id,
                expected_etag,
            ),
        )
        if cursor.fetchone() is None:
            raise ManualIssuePreconditionError

    @staticmethod
    def _insert_audit(cursor: DbApiCursor, event: ManualIssueAuditEvent) -> None:
        cursor.execute(
            """
            INSERT INTO core.audit_events (
                audit_id, project_id, region_code, actor_id, action, resource_type,
                resource_id, request_id, before_hash, after_hash, details, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, 'MANUAL_ISSUE', %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                str(uuid4()),
                event.project_id,
                event.region_code,
                event.actor_id,
                event.action,
                event.resource_id,
                event.request_id,
                event.before_hash,
                event.after_hash,
                _document({"outcome": event.outcome, **(event.details or {})}),
                event.occurred_at,
            ),
        )


def _resource_version(etag: str) -> int:
    from hc_data_platform.security.versioning import ResourceVersion

    return ResourceVersion.from_etag(etag).value
