"""RLS-scoped P10 CleaningDraft read projections.

P10 lists the server-owned P11 workbench Drafts, including the durable P09
handoff sources and the P07-return successors that P11 creates atomically.
The endpoints remain read-only; they never synthesize editing state.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Literal, Protocol, cast
from uuid import uuid4

from pydantic import ValidationError

from .models import (
    CleaningDraftAuditEvent,
    CleaningDraftEvent,
    CleaningDraftEventType,
    CleaningDraftIssueContext,
    CleaningDraftIssueDerivedOrigin,
    CleaningDraftOrigin,
    CleaningDraftProjection,
    CleaningDraftRecord,
    CleaningDraftRelationships,
    CleaningDraftReviewReturnLineage,
    CleaningDraftReviewReturnOrigin,
    CleaningDraftReviewSummary,
    CleaningDraftScope,
)


@dataclass(frozen=True, slots=True)
class CleaningDraftFilters:
    scope: str = "all"
    status: str = "active"
    query: str | None = None
    dataset_id: str | None = None
    base_version_id: str | None = None
    episode_id: str | None = None
    robot_id: str | None = None
    creator_id: str | None = None
    updated_from: datetime | None = None
    updated_to: datetime | None = None
    preview_statuses: tuple[str, ...] = ()
    commit_statuses: tuple[str, ...] = ()
    version_review_statuses: tuple[str, ...] = ()
    finding_types: tuple[str, ...] = ()
    finding_severities: tuple[str, ...] = ()


class CleaningDraftProjectionIntegrityError(RuntimeError):
    """A durable record cannot be represented by the strict P10 contract."""


class CleaningDraftRepository(Protocol):
    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool: ...

    def list_drafts(self, *, scope: CleaningDraftScope) -> tuple[CleaningDraftProjection, ...]: ...

    def get_draft(
        self, *, scope: CleaningDraftScope, draft_id: str
    ) -> CleaningDraftProjection | None: ...

    def list_events(
        self, *, scope: CleaningDraftScope, draft_id: str
    ) -> tuple[CleaningDraftEvent, ...]: ...

    def append_audit(self, event: CleaningDraftAuditEvent) -> None: ...


def _scope_key(scope: CleaningDraftScope) -> tuple[str, str, str]:
    return (scope.organization_id, scope.project_id, scope.region_code)


def _draft_key(scope: CleaningDraftScope, draft_id: str) -> tuple[str, str, str, str]:
    return (*_scope_key(scope), draft_id)


class InMemoryCleaningDraftRepository:
    """Thread-safe reference repository for P10 API and contract tests."""

    def __init__(
        self,
        *,
        organization_projects: tuple[tuple[str, str], ...] = (),
        drafts: tuple[CleaningDraftProjection, ...] = (),
        events: tuple[tuple[CleaningDraftScope, str, CleaningDraftEvent], ...] = (),
    ) -> None:
        self._organization_projects = set(organization_projects)
        self._records: dict[tuple[str, str, str, str], CleaningDraftProjection] = {}
        for projection in drafts:
            scope = projection.scope
            self._organization_projects.add((scope.organization_id, scope.project_id))
            self._records[_draft_key(scope, projection.draft.draft_id)] = projection
        self._events = list(events)
        self.audit_events: list[CleaningDraftAuditEvent] = []
        self._lock = RLock()

    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool:
        with self._lock:
            return (organization_id, project_id) in self._organization_projects

    def list_drafts(self, *, scope: CleaningDraftScope) -> tuple[CleaningDraftProjection, ...]:
        with self._lock:
            return tuple(
                projection
                for key, projection in self._records.items()
                if key[:3] == _scope_key(scope)
            )

    def get_draft(
        self, *, scope: CleaningDraftScope, draft_id: str
    ) -> CleaningDraftProjection | None:
        with self._lock:
            return self._records.get(_draft_key(scope, draft_id))

    def list_events(
        self, *, scope: CleaningDraftScope, draft_id: str
    ) -> tuple[CleaningDraftEvent, ...]:
        with self._lock:
            return tuple(
                event
                for event_scope, event_draft_id, event in self._events
                if event_scope == scope and event_draft_id == draft_id
            )

    def append_audit(self, event: CleaningDraftAuditEvent) -> None:
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


def _text_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    raise CleaningDraftProjectionIntegrityError("CleaningDraft aggregate is not an array")


class PostgresCleaningDraftRepository:
    """Production P10 repository with exact project/region predicates and RLS."""

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

    def list_drafts(self, *, scope: CleaningDraftScope) -> tuple[CleaningDraftProjection, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(_DRAFT_PROJECTION_SQL, (*_scope_key(scope), None, None))
            return tuple(
                _projection_from_row(_row(cursor, raw), scope) for raw in cursor.fetchall()
            )
        finally:
            cursor.close()
            connection.close()

    def get_draft(
        self, *, scope: CleaningDraftScope, draft_id: str
    ) -> CleaningDraftProjection | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(_DRAFT_PROJECTION_SQL, (*_scope_key(scope), draft_id, draft_id))
            raw = cursor.fetchone()
            return None if raw is None else _projection_from_row(_row(cursor, raw), scope)
        finally:
            cursor.close()
            connection.close()

    def list_events(
        self, *, scope: CleaningDraftScope, draft_id: str
    ) -> tuple[CleaningDraftEvent, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                _EVENTS_SQL,
                (*_scope_key(scope), draft_id, scope.project_id, scope.region_code, draft_id),
            )
            return tuple(_event_from_row(_row(cursor, raw)) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def append_audit(self, event: CleaningDraftAuditEvent) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
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
                    _audit_document(event),
                    event.occurred_at,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()


def _audit_document(event: CleaningDraftAuditEvent) -> str:
    import json

    return json.dumps(
        {"outcome": "SUCCEEDED", **(event.details or {})},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _projection_from_row(
    row: Mapping[str, object], scope: CleaningDraftScope
) -> CleaningDraftProjection:
    try:
        review_decision_id = _optional_text(row.get("review_decision_id"))
        review_finding_ids = _text_tuple(row.get("review_finding_ids"))
        returned = review_decision_id is not None
        review_summary = (
            None
            if not returned
            else CleaningDraftReviewSummary(
                review_decision_id=cast(str, review_decision_id),
                review_finding_ids=review_finding_ids,
                finding_count=str(len(review_finding_ids)),
                successor_draft_id=_required_text(row, "successor_draft_id"),
                supersedes_draft_id=_required_text(row, "review_supersedes_draft_id"),
                returned_from_version_id=_required_text(row, "review_returned_from_version_id"),
                returned_from_review_decision_id=_required_text(
                    row, "review_returned_from_review_decision_id"
                ),
            )
        )
        origin: CleaningDraftOrigin
        origin_type = _required_text(row, "origin_type")
        if origin_type == "ISSUE_DERIVED":
            origin = CleaningDraftIssueDerivedOrigin(
                manual_issue_context=CleaningDraftIssueContext(
                    dataset_id=_required_text(row, "dataset_id"),
                    base_version_id=_required_text(row, "base_version_id"),
                    episode_id=_required_text(row, "episode_id"),
                    base_revision_id=_required_text(row, "base_revision_id"),
                    selected_stream_id=_required_text(row, "selected_stream_id"),
                    selected_channel_path=_optional_text(row.get("selected_channel_path")),
                    start_ns=str(row["start_ns"]),
                    end_ns=str(row["end_ns"]),
                    manual_issue_ids=(_required_text(row, "source_issue_id"),),
                )
            )
            manual_issue_count: Literal["0", "1"] = "1"
        elif origin_type == "REVIEW_RETURN":
            origin = CleaningDraftReviewReturnOrigin(
                review_return_lineage=CleaningDraftReviewReturnLineage(
                    supersedes_draft_id=_required_text(row, "origin_supersedes_draft_id"),
                    returned_from_version_id=_required_text(row, "origin_returned_from_version_id"),
                    returned_from_review_decision_id=_required_text(
                        row, "origin_returned_from_review_decision_id"
                    ),
                )
            )
            manual_issue_count = "0"
        else:
            raise ValueError(f"unsupported origin_type {origin_type!r}")
        draft_id = _required_text(row, "draft_id")
        draft = CleaningDraftRecord(
            draft_id=draft_id,
            etag=f'"{draft_id}:workbench:{int(str(row["projection_version"]))}"',
            status=_required_text(row, "status"),
            origin=origin,
            base_version_id=_required_text(row, "base_version_id"),
            base_revision_id=_required_text(row, "base_revision_id"),
            episode_id=_required_text(row, "episode_id"),
            manual_issue_count=manual_issue_count,
            preview_status=_optional_text(row.get("preview_status")) or "NONE",
            commit_status=_optional_text(row.get("commit_status")) or "NONE",
            output_version_status=_optional_text(row.get("output_version_status")),
            review_decision_id=review_decision_id,
            successor_draft_id=(
                None if review_summary is None else review_summary.successor_draft_id
            ),
            review_finding_count=str(len(review_finding_ids)),
            review_summary=review_summary,
            # The service adds only capability-authorized navigation actions.
            allowed_actions=(),
            created_at=cast(datetime, row["created_at"]),
            updated_at=cast(datetime, row["updated_at"]),
        )
        relationships = CleaningDraftRelationships(
            commit_id=_optional_text(row.get("commit_id")),
            output_version_id=_optional_text(row.get("output_version_id")),
            output_revision_ids=_text_tuple(row.get("output_revision_ids")),
            review_decision_id=review_decision_id,
            review_finding_ids=review_finding_ids,
            successor_draft_id=(
                None if review_summary is None else review_summary.successor_draft_id
            ),
            supersedes_draft_id=(
                None if review_summary is None else review_summary.supersedes_draft_id
            ),
            returned_from_version_id=(
                None if review_summary is None else review_summary.returned_from_version_id
            ),
            returned_from_review_decision_id=(
                None if review_summary is None else review_summary.returned_from_review_decision_id
            ),
        )
        return CleaningDraftProjection(
            scope=scope,
            draft=draft,
            relationships=relationships,
            dataset_id=_required_text(row, "dataset_id"),
            creator_id=_optional_text(row.get("creator_id")),
            robot_id=_optional_text(row.get("robot_id")),
            review_finding_types=_text_tuple(row.get("review_finding_types")),
            review_finding_severities=cast(
                tuple[Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"], ...],
                _text_tuple(row.get("review_finding_severities")),
            ),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise CleaningDraftProjectionIntegrityError(
            "A durable CleaningDraft record cannot be projected safely."
        ) from exc


def _required_text(row: Mapping[str, object], key: str) -> str:
    value = _optional_text(row.get(key))
    if value is None:
        raise ValueError(f"missing {key}")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


_EVENT_TYPES = {
    "cleaning.draft.created": "cleaning.draft.created",
    "cleaning.draft.updated": "cleaning.draft.updated",
    "cleaning.preview.requested": "cleaning.preview.requested",
    "cleaning.preview.completed": "cleaning.preview.completed",
    "cleaning.submit.requested": "cleaning.submit.requested",
    "cleaning.submit.completed": "cleaning.submit.completed",
    "cleaning.draft.successor_created": "cleaning.draft.successor_created",
    "dataset.version_review_returned": "dataset_version.review.returned",
}
_EVENT_SUMMARIES = {
    "cleaning.draft.created": "Draft created from a ManualIssue.",
    "cleaning.draft.updated": "Draft read model was updated.",
    "cleaning.preview.requested": "Preview request was recorded.",
    "cleaning.preview.completed": "Preview request completed.",
    "cleaning.submit.requested": "Commit request was recorded.",
    "cleaning.submit.completed": "Commit request completed.",
    "cleaning.draft.successor_created": "A successor Draft was recorded.",
    "dataset.version_review_returned": (
        "Dataset version review returned the output to a successor Draft."
    ),
}


def _event_from_row(row: Mapping[str, object]) -> CleaningDraftEvent:
    action = _required_text(row, "action")
    event_type = _EVENT_TYPES.get(action)
    if event_type is None:
        raise CleaningDraftProjectionIntegrityError("unsupported CleaningDraft audit action")
    outcome = _optional_text(row.get("outcome"))
    return CleaningDraftEvent(
        event_id=_required_text(row, "event_id"),
        event_type=cast(CleaningDraftEventType, event_type),
        occurred_at=cast(datetime, row["occurred_at"]),
        result="SUCCESS" if outcome in (None, "SUCCEEDED") else "FAILURE",
        safe_summary=_EVENT_SUMMARIES[action],
        request_id=_required_text(row, "request_id"),
    )


_DRAFT_PROJECTION_SQL = """
SELECT draft.draft_id,
       draft.origin_type,
       draft.source_issue_id,
       draft.dataset_id,
       draft.base_version_id,
       draft.episode_id,
       draft.base_revision_id,
       draft.selected_stream_id,
       draft.selected_channel_path,
       draft.start_ns,
       draft.end_ns,
       draft.supersedes_draft_id AS origin_supersedes_draft_id,
       draft.returned_from_version_id AS origin_returned_from_version_id,
       draft.returned_from_review_decision_id AS origin_returned_from_review_decision_id,
       draft.status,
       draft.workbench_version AS projection_version,
       draft.created_at,
       draft.updated_at,
       creator.actor_id AS creator_id,
       base_episode.robot_id,
       COALESCE(latest_preview.preview_status, 'NONE') AS preview_status,
       chosen_commit.commit_id,
       chosen_commit.status AS commit_status,
       COALESCE(review_return.returned_version_id, chosen_commit.output_version_id)
           AS output_version_id,
       COALESCE(returned_version.version_status, committed_version.version_status)
           AS output_version_status,
       review_return.review_decision_id,
       review_return.successor_draft_id,
       review_return.supersedes_draft_id AS review_supersedes_draft_id,
       review_return.returned_version_id AS review_returned_from_version_id,
       review_return.review_decision_id AS review_returned_from_review_decision_id,
       COALESCE(review_findings.review_finding_ids, ARRAY[]::text[]) AS review_finding_ids,
       COALESCE(
           review_findings.output_revision_ids,
           output_revisions.output_revision_ids,
           ARRAY[]::text[]
       ) AS output_revision_ids,
       COALESCE(review_findings.review_finding_types, ARRAY[]::text[]) AS review_finding_types,
       COALESCE(review_findings.review_finding_severities, ARRAY[]::text[])
           AS review_finding_severities
  FROM manual_cleaning.cleaning_workbench_drafts AS draft
  LEFT JOIN LATERAL (
      SELECT audit.actor_id
        FROM core.audit_events AS audit
       WHERE audit.project_id = draft.project_id
         AND audit.region_code = draft.region_code
         AND (
             (audit.resource_id = draft.draft_id AND audit.action = 'cleaning.draft.created')
             OR (
                 draft.origin_type = 'REVIEW_RETURN'
                 AND audit.resource_id = draft.returned_from_version_id
                 AND audit.action = 'dataset.version_review_returned'
             )
         )
       ORDER BY audit.occurred_at ASC, audit.audit_id ASC
       LIMIT 1
  ) AS creator ON TRUE
  LEFT JOIN dataset_registry.dataset_version_episodes AS base_episode
    ON base_episode.organization_id = draft.organization_id
   AND base_episode.project_id = draft.project_id
   AND base_episode.region_code = draft.region_code
   AND base_episode.dataset_id = draft.dataset_id
   AND base_episode.version_id = draft.base_version_id
   AND base_episode.episode_id = draft.episode_id
   AND base_episode.revision_id = draft.base_revision_id
  LEFT JOIN LATERAL (
      SELECT edl.edl_revision
        FROM manual_cleaning.cleaning_draft_edl_revisions AS edl
       WHERE edl.organization_id = draft.organization_id
         AND edl.project_id = draft.project_id
         AND edl.region_code = draft.region_code
         AND edl.draft_id = draft.draft_id
       ORDER BY edl.edl_revision DESC
       LIMIT 1
  ) AS latest_edl ON TRUE
  LEFT JOIN LATERAL (
      SELECT CASE
          WHEN preview.edl_revision <> latest_edl.edl_revision THEN 'STALE'
          WHEN preview.expires_at IS NOT NULL AND preview.expires_at <= now() THEN 'EXPIRED'
          ELSE preview.preview_document ->> 'status'
      END AS preview_status
        FROM manual_cleaning.cleaning_draft_previews AS preview
       WHERE preview.organization_id = draft.organization_id
         AND preview.project_id = draft.project_id
         AND preview.region_code = draft.region_code
         AND preview.draft_id = draft.draft_id
       ORDER BY preview.created_at DESC, preview.preview_id DESC
       LIMIT 1
  ) AS latest_preview ON TRUE
  LEFT JOIN LATERAL (
      SELECT successor.dataset_id,
             successor.version_id AS returned_version_id,
             successor.successor_draft_id,
             successor.supersedes_draft_id,
             successor.review_decision_id
        FROM dataset_registry.dataset_version_successor_drafts AS successor
        JOIN dataset_registry.dataset_version_review_decisions AS decision
          ON decision.organization_id = successor.organization_id
         AND decision.project_id = successor.project_id
         AND decision.region_code = successor.region_code
         AND decision.dataset_id = successor.dataset_id
         AND decision.version_id = successor.version_id
         AND decision.review_decision_id = successor.review_decision_id
       WHERE successor.organization_id = draft.organization_id
         AND successor.project_id = draft.project_id
         AND successor.region_code = draft.region_code
         AND successor.supersedes_draft_id = draft.draft_id
         AND decision.decision = 'RETURNED'
       ORDER BY successor.created_at DESC, successor.successor_draft_id DESC
       LIMIT 1
  ) AS review_return ON TRUE
  LEFT JOIN LATERAL (
      SELECT commit.commit_id, commit.status, commit.output_version_id
        FROM manual_cleaning.cleaning_workbench_commits AS commit
       WHERE commit.organization_id = draft.organization_id
         AND commit.project_id = draft.project_id
         AND commit.region_code = draft.region_code
         AND commit.draft_id = draft.draft_id
       ORDER BY commit.created_at DESC,
                commit.commit_id DESC
       LIMIT 1
  ) AS chosen_commit ON TRUE
  LEFT JOIN dataset_registry.dataset_versions AS returned_version
    ON returned_version.organization_id = draft.organization_id
   AND returned_version.project_id = draft.project_id
   AND returned_version.region_code = draft.region_code
   AND returned_version.dataset_id = review_return.dataset_id
   AND returned_version.version_id = review_return.returned_version_id
  LEFT JOIN dataset_registry.dataset_versions AS committed_version
    ON committed_version.organization_id = draft.organization_id
   AND committed_version.project_id = draft.project_id
   AND committed_version.region_code = draft.region_code
   AND committed_version.dataset_id = draft.dataset_id
   AND committed_version.version_id = chosen_commit.output_version_id
  LEFT JOIN LATERAL (
      SELECT array_agg(output.revision_id ORDER BY output.ordinal, output.revision_id)
                 AS output_revision_ids
        FROM manual_cleaning.cleaning_commit_output_revisions AS output
       WHERE output.organization_id = draft.organization_id
         AND output.project_id = draft.project_id
         AND output.region_code = draft.region_code
         AND output.commit_id = chosen_commit.commit_id
  ) AS output_revisions ON TRUE
  LEFT JOIN LATERAL (
      SELECT array_agg(finding.review_finding_id ORDER BY finding.review_finding_id)
                 AS review_finding_ids,
             array_agg(finding.output_revision_id ORDER BY finding.review_finding_id)
                 AS output_revision_ids,
             array_agg(finding.finding_document ->> 'finding_type'
                       ORDER BY finding.review_finding_id) AS review_finding_types,
             array_agg(finding.severity ORDER BY finding.review_finding_id)
                 AS review_finding_severities
        FROM dataset_registry.dataset_version_review_findings AS finding
       WHERE finding.organization_id = draft.organization_id
         AND finding.project_id = draft.project_id
         AND finding.region_code = draft.region_code
         AND finding.dataset_id = review_return.dataset_id
         AND finding.version_id = review_return.returned_version_id
         AND finding.review_decision_id = review_return.review_decision_id
  ) AS review_findings ON TRUE
 WHERE draft.organization_id = %s
   AND draft.project_id = %s
   AND draft.region_code = %s
   AND (%s::text IS NULL OR draft.draft_id = %s)
"""


_EVENTS_SQL = """
WITH returned_versions AS (
    SELECT successor.version_id
      FROM dataset_registry.dataset_version_successor_drafts AS successor
     WHERE successor.organization_id = %s
       AND successor.project_id = %s
       AND successor.region_code = %s
       AND successor.supersedes_draft_id = %s
)
SELECT audit.audit_id::text AS event_id,
       audit.action,
       audit.occurred_at,
       audit.request_id,
       audit.details ->> 'outcome' AS outcome
  FROM core.audit_events AS audit
 WHERE audit.project_id = %s
   AND audit.region_code = %s
   AND (
       (audit.resource_id = %s AND audit.action IN (
           'cleaning.draft.created',
           'cleaning.draft.updated',
           'cleaning.preview.requested',
           'cleaning.preview.completed',
           'cleaning.submit.requested',
           'cleaning.submit.completed',
           'cleaning.draft.successor_created'
       ))
       OR (
           audit.action = 'dataset.version_review_returned'
           AND audit.resource_id IN (SELECT version_id FROM returned_versions)
       )
   )
 ORDER BY audit.occurred_at DESC, audit.audit_id DESC
"""
