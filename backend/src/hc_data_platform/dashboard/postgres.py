"""Synchronous PostgreSQL adapter for bounded, exact-scope dashboard fact reads."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import uuid4

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.security.auth import AuthContext

from .models import (
    DashboardActivityEventType,
    DashboardPendingItemType,
    DashboardPendingSeverity,
    DashboardResourceType,
)
from .repository import (
    CollectionObservationFact,
    CommittedObjectFact,
    DashboardBusinessEventFact,
    DashboardBusinessEventPage,
    DashboardFactPage,
    DashboardPendingFact,
    DashboardPendingFactPage,
    DashboardQueryAudit,
    DashboardScope,
    DashboardWindow,
    PublicationLineageSummary,
    enforce_dashboard_scope,
)


class DbApiCursor(Protocol):
    description: Sequence[Sequence[Any]] | None

    def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    def fetchall(self) -> Sequence[object]: ...

    def close(self) -> None: ...


class DbApiConnection(Protocol):
    def cursor(self) -> DbApiCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[], DbApiConnection]

_SCOPE_CTE = """
requested_scope AS (
    SELECT %s::text AS principal_id,
           %s::text AS project_id,
           %s::text AS region_code,
           %s::timestamptz AS range_start,
           %s::timestamptz AS range_end,
           %s::text AS authorized_principal_id
)
""".strip()

COMMITTED_OBJECTS_QUERY = f"""
WITH {_SCOPE_CTE}
SELECT fact.project_id, fact.region_code, fact.rollout_id, fact.data_package_id,
       fact.file_size, fact.committed_at
FROM requested_scope scope
JOIN ingest.rollout_objects fact
  ON fact.project_id = scope.project_id
 AND fact.region_code = scope.region_code
WHERE scope.principal_id = scope.authorized_principal_id
  AND fact.committed_at >= scope.range_start
  AND fact.committed_at < scope.range_end
  AND (%s::timestamptz IS NULL OR (fact.committed_at, fact.rollout_id) < (%s, %s))
ORDER BY fact.committed_at DESC, fact.rollout_id DESC
LIMIT %s
""".strip()

COLLECTION_OBSERVATIONS_QUERY = f"""
WITH {_SCOPE_CTE},
bounded_rollouts AS (
    SELECT rollout.project_id, rollout.region_code, rollout.collection_job_id,
           rollout.rollout_id, rollout.robot_id, rollout.created_at
    FROM requested_scope scope
    JOIN ingest.rollouts rollout
      ON rollout.project_id = scope.project_id
     AND rollout.region_code = scope.region_code
    WHERE scope.principal_id = scope.authorized_principal_id
      AND rollout.created_at >= scope.range_start
      AND rollout.created_at < scope.range_end
      AND (%s::timestamptz IS NULL OR (rollout.created_at, rollout.rollout_id) < (%s, %s))
    ORDER BY rollout.created_at DESC, rollout.rollout_id DESC
    LIMIT %s
)
SELECT rollout.project_id, rollout.region_code, rollout.rollout_id,
       job.task_id, rollout.robot_id, rollout.created_at AS observed_at
FROM bounded_rollouts rollout
JOIN ingest.collection_jobs job
  ON job.project_id = rollout.project_id
 AND job.region_code = rollout.region_code
 AND job.collection_job_id = rollout.collection_job_id
ORDER BY rollout.created_at DESC, rollout.rollout_id DESC
""".strip()

_ACTIVITY_EVENTS = """
SELECT object.project_id, object.region_code,
       'UPLOAD_COMMITTED'::text AS event_type,
       object.data_package_id::text AS source_id,
       object.committed_at AS occurred_at,
       object.status::text AS source_state,
       CASE WHEN session.session_id IS NULL THEN 'ROLLOUT' ELSE 'UPLOAD_SESSION' END::text
           AS target_resource_type,
       COALESCE(session.session_id::text, object.rollout_id)::text AS target_resource_id,
       NULL::text AS target_resource_version
FROM requested_scope scope
JOIN ingest.rollout_objects object
  ON object.project_id = scope.project_id
 AND object.region_code = scope.region_code
LEFT JOIN ingest.upload_sessions session
  ON session.project_id = object.project_id
 AND session.region_code = object.region_code
 AND session.rollout_id = object.rollout_id
WHERE scope.principal_id = scope.authorized_principal_id
  AND object.committed_at >= scope.range_start
  AND object.committed_at < scope.range_end
UNION ALL
SELECT report.project_id, report.region_code,
       'QC_COMPLETED'::text,
       report.report_sha256::text,
       report.created_at,
       report.status::text,
       CASE WHEN session.session_id IS NULL THEN 'ROLLOUT' ELSE 'UPLOAD_SESSION' END::text,
       COALESCE(session.session_id::text, report.rollout_id)::text,
       NULL::text
FROM requested_scope scope
JOIN qc_reports report
  ON report.project_id = scope.project_id
 AND report.region_code = scope.region_code
LEFT JOIN ingest.upload_sessions session
  ON session.project_id = report.project_id
 AND session.region_code = report.region_code
 AND session.rollout_id = report.rollout_id
WHERE scope.principal_id = scope.authorized_principal_id
  AND report.created_at >= scope.range_start
  AND report.created_at < scope.range_end
UNION ALL
SELECT task.project_id, rollout.region_code,
       'TAG_REVIEW_DECIDED'::text,
       review.review_id::text,
       review.created_at,
       review.decision::text,
       'ANNOTATION_TASK'::text,
       task.task_id::text,
       review.revision::text
FROM requested_scope scope
JOIN ingest.rollouts rollout
  ON rollout.project_id = scope.project_id
 AND rollout.region_code = scope.region_code
JOIN annotation.annotation_tasks task
  ON task.project_id = rollout.project_id
 AND task.rollout_id = rollout.rollout_id
JOIN annotation.annotation_reviews review
  ON review.task_id = task.task_id
WHERE scope.principal_id = scope.authorized_principal_id
  AND review.created_at >= scope.range_start
  AND review.created_at < scope.range_end
""".strip()

_PUBLICATION_ACTIVITY = """
UNION ALL
SELECT lineage.project_id, lineage.region_code,
       'DATASET_PUBLISHED'::text,
       lineage.publication_identity::text,
       lineage.published_at,
       'PUBLISHED'::text,
       'DATASET_VERSION'::text,
       lineage.dataset_id::text,
       lineage.dataset_version::text
FROM requested_scope scope
JOIN publishing.rollout_publication_lineage lineage
  ON lineage.project_id = scope.project_id
 AND lineage.region_code = scope.region_code
WHERE scope.principal_id = scope.authorized_principal_id
  AND lineage.published_at >= scope.range_start
  AND lineage.published_at < scope.range_end
GROUP BY lineage.project_id, lineage.region_code, lineage.publication_identity,
         lineage.dataset_id, lineage.dataset_version, lineage.published_at
""".strip()


def _activity_query(include_publication: bool) -> str:
    publication = f"\n{_PUBLICATION_ACTIVITY}" if include_publication else ""
    return f"""
WITH {_SCOPE_CTE},
events AS (
{_ACTIVITY_EVENTS}
{publication}
)
SELECT project_id, region_code, event_type, source_id, occurred_at, source_state,
       target_resource_type, target_resource_id, target_resource_version
FROM events
WHERE (%s::timestamptz IS NULL OR (occurred_at, event_type, source_id) < (%s, %s, %s))
ORDER BY occurred_at DESC, event_type DESC, source_id DESC
LIMIT %s
""".strip()


_PENDING_FACTS = """
SELECT session.project_id, session.region_code,
       'UPLOAD_FAILED'::text AS item_type,
       session.session_id::text AS source_id,
       session.status::text AS source_state,
       'HIGH'::text AS severity,
       3::integer AS severity_rank,
       session.updated_at AS opened_at,
       'UPLOAD_SESSION'::text AS target_resource_type,
       session.session_id::text AS target_resource_id,
       NULL::text AS target_resource_version
FROM requested_scope scope
JOIN allowed_sources allowed ON allowed.item_type = 'UPLOAD_FAILED'
JOIN ingest.upload_sessions session
  ON session.project_id = scope.project_id
 AND session.region_code = scope.region_code
WHERE scope.principal_id = scope.authorized_principal_id
  AND session.status = 'FAILED'
  AND session.updated_at >= scope.range_start
  AND session.updated_at < scope.range_end
UNION ALL
SELECT summary.project_id, summary.region_code,
       'QC_ANOMALY'::text,
       summary.report_sha256::text,
       summary.status::text,
       CASE WHEN summary.status = 'REJECT' THEN 'CRITICAL' ELSE 'HIGH' END::text,
       CASE WHEN summary.status = 'REJECT' THEN 4 ELSE 3 END::integer,
       summary.updated_at,
       'UPLOAD_SESSION'::text,
       session.session_id::text,
       NULL::text
FROM requested_scope scope
JOIN allowed_sources allowed ON allowed.item_type = 'QC_ANOMALY'
JOIN quality_rollout_summaries summary
  ON summary.project_id = scope.project_id
 AND summary.region_code = scope.region_code
JOIN ingest.upload_sessions session
  ON session.project_id = summary.project_id
 AND session.region_code = summary.region_code
 AND session.rollout_id = summary.rollout_id
WHERE scope.principal_id = scope.authorized_principal_id
  AND summary.status IN ('RISK', 'REJECT')
  AND summary.updated_at >= scope.range_start
  AND summary.updated_at < scope.range_end
UNION ALL
SELECT task.project_id, rollout.region_code,
       'TAG_REVIEW_PENDING'::text,
       task.task_id::text,
       task.status::text,
       'MEDIUM'::text,
       2::integer,
       task.updated_at,
       'ANNOTATION_TASK'::text,
       task.task_id::text,
       task.current_revision::text
FROM requested_scope scope
JOIN allowed_sources allowed ON allowed.item_type = 'TAG_REVIEW_PENDING'
JOIN ingest.rollouts rollout
  ON rollout.project_id = scope.project_id
 AND rollout.region_code = scope.region_code
JOIN annotation.annotation_tasks task
  ON task.project_id = rollout.project_id
 AND task.rollout_id = rollout.rollout_id
WHERE scope.principal_id = scope.authorized_principal_id
  AND task.status = 'SUBMITTED'
  AND task.updated_at >= scope.range_start
  AND task.updated_at < scope.range_end
""".strip()

_PUBLICATION_PENDING = """
UNION ALL
SELECT task.project_id, rollout.region_code,
       'PUBLICATION_PENDING'::text,
       task.task_id::text,
       task.status::text,
       'LOW'::text,
       1::integer,
       task.updated_at,
       'DATASET_VERSION'::text,
       task.dataset_id::text,
       task.dataset_version::text
FROM requested_scope scope
JOIN allowed_sources allowed ON allowed.item_type = 'PUBLICATION_PENDING'
JOIN ingest.rollouts rollout
  ON rollout.project_id = scope.project_id
 AND rollout.region_code = scope.region_code
JOIN annotation.annotation_tasks task
  ON task.project_id = rollout.project_id
 AND task.rollout_id = rollout.rollout_id
WHERE scope.principal_id = scope.authorized_principal_id
  AND task.status = 'APPROVED'
  AND task.updated_at >= scope.range_start
  AND task.updated_at < scope.range_end
  AND NOT EXISTS (
      SELECT 1
      FROM publishing.rollout_publication_lineage lineage
      WHERE lineage.project_id = task.project_id
        AND lineage.region_code = rollout.region_code
        AND lineage.rollout_id = task.rollout_id
  )
""".strip()


def _pending_query(include_publication: bool) -> str:
    publication = f"\n{_PUBLICATION_PENDING}" if include_publication else ""
    return f"""
WITH {_SCOPE_CTE},
allowed_sources AS (
    SELECT unnest(%s::text[]) AS item_type
),
cursor_boundary AS (
    SELECT %s::integer AS severity_rank,
           %s::timestamptz AS opened_at,
           %s::text AS item_type,
           %s::text AS source_id
),
pending AS (
{_PENDING_FACTS}
{publication}
)
SELECT pending.project_id, pending.region_code, pending.item_type, pending.source_id,
       pending.source_state, pending.severity, pending.severity_rank, pending.opened_at,
       pending.target_resource_type, pending.target_resource_id,
       pending.target_resource_version
FROM pending, cursor_boundary boundary
WHERE boundary.severity_rank IS NULL
   OR pending.severity_rank < boundary.severity_rank
   OR (
       pending.severity_rank = boundary.severity_rank
       AND (pending.opened_at, pending.item_type, pending.source_id)
           > (boundary.opened_at, boundary.item_type, boundary.source_id)
   )
ORDER BY pending.severity_rank DESC, pending.opened_at ASC,
         pending.item_type ASC, pending.source_id ASC
LIMIT %s
""".strip()


def _row(cursor: DbApiCursor, raw: object) -> dict[str, object]:
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    if cursor.description is None:
        raise RuntimeError("database cursor did not describe the dashboard result")
    values = cast(Sequence[object], raw)
    names = [str(column[0]) for column in cursor.description]
    return dict(zip(names, values, strict=True))


class PostgresDashboardRepository:
    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        statement_timeout_ms: int = 1500,
    ) -> None:
        if statement_timeout_ms < 100 or statement_timeout_ms > 30_000:
            raise ValueError("dashboard statement timeout must be between 100 and 30000 ms")
        self._connection_factory = connection_factory
        self._statement_timeout_ms = statement_timeout_ms

    def enforce_scope(self, auth: AuthContext, scope: DashboardScope) -> None:
        enforce_dashboard_scope(auth, scope)

    def record_query(self, auth: AuthContext, audit: DashboardQueryAudit) -> None:
        scope = DashboardScope(audit.principal_id, audit.project_id, audit.region_code)
        self.enforce_scope(auth, scope)
        context = current_request_context()
        if context.subject_id != auth.subject_id:
            raise problem(
                status=403,
                code="DASHBOARD_PRINCIPAL_SCOPE_DENIED",
                title="Dashboard scope denied",
                detail="The requested dashboard scope is not available to this principal.",
            )
        details = {
            "endpoint": audit.endpoint,
            "query_fingerprint": canonical_hash(
                {
                    "project_id": audit.project_id,
                    "region_code": audit.region_code,
                    "from": audit.range_start.isoformat(),
                    "to": audit.range_end.isoformat(),
                    "timezone": audit.timezone,
                }
            ),
            "result_status": audit.result_status,
        }
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO core.audit_events (
                    audit_id, project_id, region_code, actor_id, action,
                    resource_type, resource_id, request_id, details, occurred_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
                (
                    str(uuid4()),
                    audit.project_id,
                    audit.region_code,
                    audit.principal_id,
                    "dashboard.query",
                    "dashboard",
                    audit.endpoint,
                    context.request_id,
                    json.dumps(details, sort_keys=True, separators=(",", ":")),
                    audit.occurred_at,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def business_events(
        self,
        *,
        auth: AuthContext,
        scope: DashboardScope,
        window: DashboardWindow,
        limit: int,
        after: tuple[datetime, str, str] | None = None,
    ) -> DashboardBusinessEventPage:
        self.enforce_scope(auth, scope)
        _validate_limit(limit)
        include_publication = self._lineage_available()
        after_time, after_type, after_id = after if after is not None else (None, None, None)
        rows = self._read(
            _activity_query(include_publication),
            (
                *self._scope_params(auth, scope, window),
                after_time,
                after_time,
                after_type,
                after_id,
                limit + 1,
            ),
        )
        facts = tuple(
            DashboardBusinessEventFact(
                project_id=str(row["project_id"]),
                region_code=str(row["region_code"]),
                event_type=DashboardActivityEventType(str(row["event_type"])),
                source_id=str(row["source_id"]),
                occurred_at=cast(datetime, row["occurred_at"]),
                source_state=str(row["source_state"]),
                target_resource_type=DashboardResourceType(str(row["target_resource_type"])),
                target_resource_id=str(row["target_resource_id"]),
                target_resource_version=(
                    None
                    if row["target_resource_version"] is None
                    else str(row["target_resource_version"])
                ),
            )
            for row in rows
        )
        unavailable = () if include_publication else ("rollout_publication_lineage",)
        return DashboardBusinessEventPage(facts[:limit], len(facts) > limit, unavailable)

    def pending_facts(
        self,
        *,
        auth: AuthContext,
        scope: DashboardScope,
        window: DashboardWindow,
        allowed_types: tuple[DashboardPendingItemType, ...],
        limit: int,
        after: tuple[int, datetime, str, str] | None = None,
    ) -> DashboardPendingFactPage:
        self.enforce_scope(auth, scope)
        _validate_limit(limit)
        include_publication = self._lineage_available()
        rank, opened_at, item_type, source_id = (
            after if after is not None else (None, None, None, None)
        )
        rows = self._read(
            _pending_query(include_publication),
            (
                *self._scope_params(auth, scope, window),
                [item.value for item in allowed_types],
                rank,
                opened_at,
                item_type,
                source_id,
                limit + 1,
            ),
        )
        facts = tuple(
            DashboardPendingFact(
                project_id=str(row["project_id"]),
                region_code=str(row["region_code"]),
                item_type=DashboardPendingItemType(str(row["item_type"])),
                source_id=str(row["source_id"]),
                source_state=str(row["source_state"]),
                severity=DashboardPendingSeverity(str(row["severity"])),
                severity_rank=int(cast(int, row["severity_rank"])),
                opened_at=cast(datetime, row["opened_at"]),
                target_resource_type=DashboardResourceType(str(row["target_resource_type"])),
                target_resource_id=str(row["target_resource_id"]),
                target_resource_version=(
                    None
                    if row["target_resource_version"] is None
                    else str(row["target_resource_version"])
                ),
            )
            for row in rows
        )
        unavailable = (
            (DashboardPendingItemType.PUBLICATION_PENDING,)
            if DashboardPendingItemType.PUBLICATION_PENDING in allowed_types
            and not include_publication
            else ()
        )
        return DashboardPendingFactPage(facts[:limit], len(facts) > limit, unavailable)

    def publication_lineage_summary(
        self,
        *,
        auth: AuthContext,
        scope: DashboardScope,
        window: DashboardWindow,
    ) -> PublicationLineageSummary:
        self.enforce_scope(auth, scope)
        if not self._lineage_available():
            return PublicationLineageSummary(False)
        rows = self._read(
            """
            SELECT lineage_count, publication_count, unresolved_history_count,
                   latest_published_at
            FROM publishing.dashboard_publication_lineage_summary(%s, %s, %s, %s, %s)
            """,
            (
                scope.principal_id,
                scope.project_id,
                scope.region_code,
                window.start,
                window.end,
            ),
        )
        if len(rows) != 1:
            raise RuntimeError("publication lineage summary did not return one row")
        row = rows[0]
        return PublicationLineageSummary(
            True,
            lineage_count=int(cast(int, row["lineage_count"])),
            publication_count=int(cast(int, row["publication_count"])),
            unresolved_history_count=int(cast(int, row["unresolved_history_count"])),
            latest_published_at=cast(datetime | None, row["latest_published_at"]),
        )

    def committed_objects(
        self,
        *,
        auth: AuthContext,
        scope: DashboardScope,
        window: DashboardWindow,
        limit: int,
        after: tuple[datetime, str] | None = None,
    ) -> DashboardFactPage:
        self.enforce_scope(auth, scope)
        _validate_limit(limit)
        after_time, after_id = after if after is not None else (None, None)
        rows = self._read(
            COMMITTED_OBJECTS_QUERY,
            (
                *self._scope_params(auth, scope, window),
                after_time,
                after_time,
                after_id,
                limit + 1,
            ),
        )
        facts = tuple(
            CommittedObjectFact(
                project_id=str(row["project_id"]),
                region_code=str(row["region_code"]),
                rollout_id=str(row["rollout_id"]),
                data_package_id=str(row["data_package_id"]),
                file_size=int(cast(int, row["file_size"])),
                committed_at=cast(datetime, row["committed_at"]),
            )
            for row in rows
        )
        return DashboardFactPage(facts[:limit], len(facts) > limit)

    def collection_observations(
        self,
        *,
        auth: AuthContext,
        scope: DashboardScope,
        window: DashboardWindow,
        limit: int,
        after: tuple[datetime, str] | None = None,
    ) -> DashboardFactPage:
        self.enforce_scope(auth, scope)
        _validate_limit(limit)
        after_time, after_id = after if after is not None else (None, None)
        rows = self._read(
            COLLECTION_OBSERVATIONS_QUERY,
            (
                *self._scope_params(auth, scope, window),
                after_time,
                after_time,
                after_id,
                limit + 1,
            ),
        )
        facts = tuple(
            CollectionObservationFact(
                project_id=str(row["project_id"]),
                region_code=str(row["region_code"]),
                rollout_id=str(row["rollout_id"]),
                task_id=str(row["task_id"]),
                robot_id=str(row["robot_id"]),
                observed_at=cast(datetime, row["observed_at"]),
            )
            for row in rows
        )
        return DashboardFactPage(facts[:limit], len(facts) > limit)

    def _scope_params(
        self,
        auth: AuthContext,
        scope: DashboardScope,
        window: DashboardWindow,
    ) -> tuple[object, ...]:
        return (
            scope.principal_id,
            scope.project_id,
            scope.region_code,
            window.start,
            window.end,
            auth.subject_id,
        )

    def _lineage_available(self) -> bool:
        rows = self._read(
            """
            SELECT to_regclass('publishing.rollout_publication_lineage') IS NOT NULL
               AND to_regprocedure(
                   'publishing.dashboard_publication_lineage_summary(text,text,text,timestamptz,timestamptz)'
               ) IS NOT NULL AS available
            """,
            (),
        )
        return bool(rows and rows[0]["available"])

    def _read(self, query: str, params: Sequence[object]) -> tuple[dict[str, object], ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (str(self._statement_timeout_ms),),
            )
            cursor.execute(query, params)
            return tuple(_row(cursor, raw) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()


def _validate_limit(limit: int) -> None:
    if limit < 1 or limit > 100:
        raise ValueError("dashboard repository limit must be between 1 and 100")
