"""RLS-scoped storage access for the P19 audit projection."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Literal, Protocol, cast


@dataclass(frozen=True, slots=True)
class AuditQueryFilters:
    occurred_from: datetime
    occurred_to: datetime
    actor_ids: tuple[str, ...] = ()
    event_names: tuple[str, ...] = ()
    resource_types: tuple[str, ...] = ()
    resource_id: str | None = None
    outcomes: tuple[str, ...] = ()
    risk_levels: tuple[str, ...] = ()
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class AuditEventRecord:
    audit_id: str
    organization_id: str
    project_id: str
    region_code: str | None
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    request_id: str
    before_hash: str | None
    after_hash: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AuditMetrics:
    today: int
    high_risk: int
    failed: int
    active_actors: int


@dataclass(frozen=True, slots=True)
class AuditFacetValues:
    event_names: tuple[str, ...]
    actor_ids: tuple[str, ...]
    resource_types: tuple[str, ...]
    outcomes: tuple[str, ...]
    risk_levels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuditIntegritySummary:
    status: Literal["PASSED", "FAILED"]
    checked_event_count: int
    checked_chain_count: int
    verified_through: datetime | None


class AuditProjectionRepository(Protocol):
    def integrity(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str | None,
    ) -> AuditIntegritySummary: ...

    def metrics(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        filters: AuditQueryFilters,
        snapshot_at: datetime,
    ) -> AuditMetrics: ...

    def facets(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        filters: AuditQueryFilters,
        snapshot_at: datetime,
    ) -> AuditFacetValues: ...

    def list_events(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        filters: AuditQueryFilters,
        snapshot_at: datetime,
        anchor: tuple[datetime, str] | None,
        direction: str,
        limit: int,
    ) -> tuple[AuditEventRecord, ...]: ...

    def get_event(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        event_id: str,
    ) -> AuditEventRecord | None: ...


class InMemoryAuditProjectionRepository:
    """Thread-safe reference store used by P19 unit and router-contract tests."""

    def __init__(
        self,
        events: tuple[AuditEventRecord, ...] = (),
        *,
        integrity_status: Literal["PASSED", "FAILED"] = "PASSED",
    ) -> None:
        if integrity_status not in {"PASSED", "FAILED"}:
            raise ValueError("integrity_status must be PASSED or FAILED")
        self._events = list(events)
        self._lock = RLock()
        self._integrity_status = integrity_status

    def add(self, event: AuditEventRecord) -> None:
        with self._lock:
            self._events.append(event)

    def integrity(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str | None,
    ) -> AuditIntegritySummary:
        with self._lock:
            visible = tuple(
                event
                for event in self._events
                if event.organization_id == organization_id
                and event.project_id == project_id
                and event.region_code == region_code
            )
        return AuditIntegritySummary(
            status=self._integrity_status,
            checked_event_count=len(visible),
            checked_chain_count=1 if visible else 0,
            verified_through=max((event.occurred_at for event in visible), default=None),
        )

    def metrics(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        filters: AuditQueryFilters,
        snapshot_at: datetime,
    ) -> AuditMetrics:
        records = self._filtered(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            filters=filters,
            snapshot_at=snapshot_at,
        )
        return AuditMetrics(
            today=sum(record.occurred_at.date() == snapshot_at.date() for record in records),
            high_risk=sum(_risk(record.action)[0] in {"HIGH", "CRITICAL"} for record in records),
            failed=sum(_outcome(record.action) in {"DENIED", "FAILED"} for record in records),
            active_actors=len({record.actor_id for record in records}),
        )

    def facets(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        filters: AuditQueryFilters,
        snapshot_at: datetime,
    ) -> AuditFacetValues:
        records = self._filtered(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            filters=filters,
            snapshot_at=snapshot_at,
        )
        return AuditFacetValues(
            event_names=tuple(sorted({record.action for record in records}))[:500],
            actor_ids=tuple(sorted({record.actor_id for record in records}))[:500],
            resource_types=tuple(sorted({record.resource_type.upper() for record in records}))[
                :500
            ],
            outcomes=tuple(sorted({_outcome(record.action) for record in records})),
            risk_levels=tuple(sorted({_risk(record.action)[0] for record in records})),
        )

    def list_events(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        filters: AuditQueryFilters,
        snapshot_at: datetime,
        anchor: tuple[datetime, str] | None,
        direction: str,
        limit: int,
    ) -> tuple[AuditEventRecord, ...]:
        records = self._filtered(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            filters=filters,
            snapshot_at=snapshot_at,
        )
        records.sort(key=lambda event: (event.occurred_at, event.audit_id), reverse=True)
        if anchor is not None:
            if direction == "after":
                records = [
                    event for event in records if (event.occurred_at, event.audit_id) < anchor
                ]
            else:
                records = [
                    event for event in records if (event.occurred_at, event.audit_id) > anchor
                ]
                records.reverse()
        return tuple(records[:limit])

    def get_event(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        event_id: str,
    ) -> AuditEventRecord | None:
        with self._lock:
            return next(
                (
                    event
                    for event in self._events
                    if event.audit_id == event_id
                    and event.organization_id == organization_id
                    and event.project_id == project_id
                    and event.region_code == region_code
                ),
                None,
            )

    def _filtered(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        filters: AuditQueryFilters,
        snapshot_at: datetime,
    ) -> list[AuditEventRecord]:
        with self._lock:
            return [
                event
                for event in self._events
                if _matches(
                    event,
                    organization_id=organization_id,
                    project_id=project_id,
                    region_code=region_code,
                    filters=filters,
                    snapshot_at=snapshot_at,
                )
            ]


class DbApiCursor(Protocol):
    description: Sequence[Sequence[object] | object] | None

    def execute(self, query: str, params: Sequence[object] | None = None) -> object: ...

    def fetchone(self) -> object | None: ...

    def fetchall(self) -> list[object]: ...

    def close(self) -> None: ...


class DbApiConnection(Protocol):
    def cursor(self) -> DbApiCursor: ...

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


def _integer(value: object, *, field: str) -> int:
    """Coerce a DB aggregate while rejecting non-numeric adapter values."""

    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"audit aggregate {field} is not an integer") from exc


def _record(row: Mapping[str, object]) -> AuditEventRecord:
    occurred_at = row.get("occurred_at")
    if not isinstance(occurred_at, datetime) or occurred_at.tzinfo is None:
        raise RuntimeError("audit event occurred_at must be timezone-aware")
    return AuditEventRecord(
        audit_id=str(row["audit_id"]),
        organization_id=str(row["organization_id"]),
        project_id=str(row["project_id"]),
        region_code=None if row.get("region_code") is None else str(row["region_code"]),
        actor_id=str(row["actor_id"]),
        action=str(row["action"]),
        resource_type=str(row["resource_type"]),
        resource_id=str(row["resource_id"]),
        request_id=str(row["request_id"]),
        before_hash=None if row.get("before_hash") is None else str(row["before_hash"]),
        after_hash=None if row.get("after_hash") is None else str(row["after_hash"]),
        occurred_at=occurred_at,
    )


class PostgresAuditProjectionRepository:
    """Production P19 reader over ``core.audit_events``.

    The connection factory has already selected the request organization,
    project, and region in PostgreSQL. Explicit predicates below are defense
    in depth and make the intended scope obvious in every query plan.
    """

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def integrity(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str | None,
    ) -> AuditIntegritySummary:
        """Verify exactly the reader's selected organization/project/region chain.

        The query recomputes every event digest from the immutable source
        columns and verifies sequence, predecessor, and head facts.  It never
        selects ``details`` into Python or returns a hash to the caller.
        """

        normalized_region = region_code or ""
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                WITH visible AS (
                    SELECT audit.audit_id,
                           audit.organization_id,
                           audit.project_id,
                           audit.region_code,
                           audit.actor_id,
                           audit.action,
                           audit.resource_type,
                           audit.resource_id,
                           audit.request_id,
                           audit.before_hash,
                           audit.after_hash,
                           audit.details,
                           audit.occurred_at,
                           entry.sequence_no,
                           entry.previous_event_hash,
                           entry.event_hash,
                           row_number() OVER (
                               ORDER BY entry.sequence_no NULLS LAST
                           ) AS expected_sequence_no,
                           lag(entry.event_hash) OVER (
                               ORDER BY entry.sequence_no NULLS LAST
                           ) AS expected_previous_event_hash
                      FROM core.audit_events AS audit
                      LEFT JOIN core.audit_integrity_entries AS entry
                        ON entry.audit_id = audit.audit_id
                       AND entry.organization_id = audit.organization_id
                     WHERE audit.organization_id = %s
                       AND audit.project_id = %s
                       AND ((%s::text = '' AND audit.region_code IS NULL)
                            OR audit.region_code = NULLIF(%s::text, ''))
                ),
                event_check AS (
                    SELECT count(*)::bigint AS checked_event_count,
                           max(occurred_at) AS verified_through,
                           COALESCE(bool_and(
                               sequence_no = expected_sequence_no
                               AND previous_event_hash IS NOT DISTINCT FROM
                                   expected_previous_event_hash
                               AND event_hash = core.audit_integrity_event_hash(
                                   previous_event_hash,
                                   audit_id,
                                   organization_id,
                                   project_id,
                                   region_code,
                                   actor_id,
                                   action,
                                   resource_type,
                                   resource_id,
                                   request_id,
                                   before_hash,
                                   after_hash,
                                   details,
                                   occurred_at
                               )
                           ), true) AS events_valid
                      FROM visible
                ),
                tail AS (
                    SELECT event_hash
                      FROM visible
                     ORDER BY expected_sequence_no DESC
                     LIMIT 1
                ),
                head AS (
                    SELECT last_sequence, last_event_hash
                      FROM core.audit_integrity_heads
                     WHERE organization_id = %s
                       AND project_id = %s
                       AND region_code = %s
                )
                SELECT event_check.checked_event_count,
                       CASE WHEN event_check.checked_event_count > 0 THEN 1 ELSE 0 END
                           AS checked_chain_count,
                       event_check.verified_through,
                       CASE
                           WHEN event_check.checked_event_count = 0
                               THEN event_check.events_valid
                                    AND NOT EXISTS (SELECT 1 FROM head)
                           ELSE event_check.events_valid
                                AND EXISTS (
                                    SELECT 1
                                      FROM head, tail
                                     WHERE head.last_sequence = event_check.checked_event_count
                                       AND head.last_event_hash = tail.event_hash
                                )
                       END AS verified
                  FROM event_check
                """,
                (
                    organization_id,
                    project_id,
                    normalized_region,
                    normalized_region,
                    organization_id,
                    project_id,
                    normalized_region,
                ),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise RuntimeError("audit integrity verification returned no result")
            values = _row(cursor, raw)
            verified = values.get("verified")
            if not isinstance(verified, bool):
                raise RuntimeError("audit integrity verification is not boolean")
            verified_through = values.get("verified_through")
            if verified_through is not None and (
                not isinstance(verified_through, datetime) or verified_through.tzinfo is None
            ):
                raise RuntimeError("audit integrity verified_through must be timezone-aware")
            return AuditIntegritySummary(
                status="PASSED" if verified else "FAILED",
                checked_event_count=_integer(
                    values["checked_event_count"], field="checked_event_count"
                ),
                checked_chain_count=_integer(
                    values["checked_chain_count"], field="checked_chain_count"
                ),
                verified_through=verified_through,
            )
        finally:
            cursor.close()
            connection.close()

    def metrics(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        filters: AuditQueryFilters,
        snapshot_at: datetime,
    ) -> AuditMetrics:
        where, params = _where(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            filters=filters,
            snapshot_at=snapshot_at,
            anchor=None,
            direction="after",
        )
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT
                    count(*) FILTER (
                        WHERE audit.occurred_at >= date_trunc('day', %s::timestamptz)
                    ) AS today,
                    count(*) FILTER (WHERE {_risk_sql()!s} IN ('HIGH', 'CRITICAL')) AS high_risk,
                    count(*) FILTER (WHERE {_outcome_sql()!s} IN ('DENIED', 'FAILED')) AS failed,
                    count(DISTINCT audit.actor_id) AS active_actors
                  FROM core.audit_events AS audit
                 WHERE {where}
                """,
                (snapshot_at, *params),
            )
            raw = cursor.fetchone()
            if raw is None:
                return AuditMetrics(today=0, high_risk=0, failed=0, active_actors=0)
            values = _row(cursor, raw)
            return AuditMetrics(
                today=_integer(values["today"], field="today"),
                high_risk=_integer(values["high_risk"], field="high_risk"),
                failed=_integer(values["failed"], field="failed"),
                active_actors=_integer(values["active_actors"], field="active_actors"),
            )
        finally:
            cursor.close()
            connection.close()

    def facets(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        filters: AuditQueryFilters,
        snapshot_at: datetime,
    ) -> AuditFacetValues:
        where, params = _where(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            filters=filters,
            snapshot_at=snapshot_at,
            anchor=None,
            direction="after",
        )
        selections = (
            "audit.action",
            "audit.actor_id",
            "upper(audit.resource_type)",
            _outcome_sql(),
            _risk_sql(),
        )
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            values: list[tuple[str, ...]] = []
            for selection in selections:
                cursor.execute(
                    f"""
                    SELECT DISTINCT {selection} AS value
                      FROM core.audit_events AS audit
                     WHERE {where}
                     ORDER BY value ASC
                     LIMIT 500
                    """,
                    params,
                )
                values.append(tuple(str(_row(cursor, raw)["value"]) for raw in cursor.fetchall()))
            return AuditFacetValues(
                event_names=values[0],
                actor_ids=values[1],
                resource_types=values[2],
                outcomes=values[3],
                risk_levels=values[4],
            )
        finally:
            cursor.close()
            connection.close()

    def list_events(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        filters: AuditQueryFilters,
        snapshot_at: datetime,
        anchor: tuple[datetime, str] | None,
        direction: str,
        limit: int,
    ) -> tuple[AuditEventRecord, ...]:
        where, params = _where(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            filters=filters,
            snapshot_at=snapshot_at,
            anchor=anchor,
            direction=direction,
        )
        order = (
            "audit.occurred_at ASC, audit.audit_id::text ASC"
            if direction == "before"
            else "audit.occurred_at DESC, audit.audit_id::text DESC"
        )
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT audit.audit_id::text AS audit_id,
                       audit.organization_id,
                       audit.project_id,
                       audit.region_code,
                       audit.actor_id,
                       audit.action,
                       audit.resource_type,
                       audit.resource_id,
                       audit.request_id,
                       audit.before_hash,
                       audit.after_hash,
                       audit.occurred_at
                  FROM core.audit_events AS audit
                 WHERE {where}
                 ORDER BY {order}
                 LIMIT %s
                """,
                (*params, limit),
            )
            rows = tuple(_record(_row(cursor, raw)) for raw in cursor.fetchall())
            return tuple(reversed(rows)) if direction == "before" else rows
        finally:
            cursor.close()
            connection.close()

    def get_event(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        event_id: str,
    ) -> AuditEventRecord | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT audit.audit_id::text AS audit_id,
                       audit.organization_id,
                       audit.project_id,
                       audit.region_code,
                       audit.actor_id,
                       audit.action,
                       audit.resource_type,
                       audit.resource_id,
                       audit.request_id,
                       audit.before_hash,
                       audit.after_hash,
                       audit.occurred_at
                  FROM core.audit_events AS audit
                 WHERE audit.organization_id = %s
                   AND audit.project_id = %s
                   AND audit.audit_id::text = %s
                   AND ((%s::text IS NULL AND audit.region_code IS NULL)
                        OR audit.region_code = %s)
                """,
                (organization_id, project_id, event_id, region_code, region_code),
            )
            raw = cursor.fetchone()
            return None if raw is None else _record(_row(cursor, raw))
        finally:
            cursor.close()
            connection.close()


def _matches(
    event: AuditEventRecord,
    *,
    organization_id: str,
    project_id: str,
    region_code: str | None,
    filters: AuditQueryFilters,
    snapshot_at: datetime,
) -> bool:
    return (
        event.organization_id == organization_id
        and event.project_id == project_id
        and event.region_code == region_code
        and filters.occurred_from <= event.occurred_at < filters.occurred_to
        and event.occurred_at <= snapshot_at
        and (not filters.actor_ids or event.actor_id in filters.actor_ids)
        and (not filters.event_names or event.action in filters.event_names)
        and (not filters.resource_types or event.resource_type.upper() in filters.resource_types)
        and (filters.resource_id is None or event.resource_id == filters.resource_id)
        and (filters.request_id is None or event.request_id == filters.request_id)
        and (not filters.outcomes or _outcome(event.action) in filters.outcomes)
        and (not filters.risk_levels or _risk(event.action)[0] in filters.risk_levels)
    )


def _where(
    *,
    organization_id: str,
    project_id: str,
    region_code: str | None,
    filters: AuditQueryFilters,
    snapshot_at: datetime,
    anchor: tuple[datetime, str] | None,
    direction: str,
) -> tuple[str, tuple[object, ...]]:
    clauses = [
        "audit.organization_id = %s",
        "audit.project_id = %s",
        "audit.occurred_at >= %s",
        "audit.occurred_at < %s",
        "audit.occurred_at <= %s",
    ]
    params: list[object] = [
        organization_id,
        project_id,
        filters.occurred_from,
        filters.occurred_to,
        snapshot_at,
    ]
    if region_code is None:
        clauses.append("audit.region_code IS NULL")
    else:
        clauses.append("audit.region_code = %s")
        params.append(region_code)
    if filters.actor_ids:
        clauses.append("audit.actor_id = ANY(%s)")
        params.append(list(filters.actor_ids))
    if filters.event_names:
        clauses.append("audit.action = ANY(%s)")
        params.append(list(filters.event_names))
    if filters.resource_types:
        clauses.append("upper(audit.resource_type) = ANY(%s)")
        params.append(list(filters.resource_types))
    if filters.resource_id is not None:
        clauses.append("audit.resource_id = %s")
        params.append(filters.resource_id)
    if filters.request_id is not None:
        clauses.append("audit.request_id = %s")
        params.append(filters.request_id)
    if filters.outcomes:
        clauses.append(f"{_outcome_sql()} = ANY(%s)")
        params.append(list(filters.outcomes))
    if filters.risk_levels:
        clauses.append(f"{_risk_sql()} = ANY(%s)")
        params.append(list(filters.risk_levels))
    if anchor is not None:
        comparator = ">" if direction == "before" else "<"
        clauses.append(f"(audit.occurred_at, audit.audit_id::text) {comparator} (%s, %s)")
        params.extend(anchor)
    return "\n                   AND ".join(clauses), tuple(params)


def _outcome_sql() -> str:
    return """
    CASE
        WHEN audit.action ~ '(?:^|\\.)denied(?:$|\\.)' THEN 'DENIED'
        WHEN audit.action ~ '(?:^|\\.)rejected(?:$|\\.)' THEN 'DENIED'
        WHEN audit.action ~ '(?:^|\\.)failed(?:$|\\.)' THEN 'FAILED'
        WHEN audit.action ~ '(?:^|\\.)partial(?:$|\\.)' THEN 'PARTIAL'
        ELSE 'SUCCEEDED'
    END
    """


def _risk_sql() -> str:
    return """
    CASE
        WHEN audit.action ~
            '(credential|password|secret|access|grant|revoke|disable|delete|publish)'
            THEN 'HIGH'
        WHEN audit.action ~ '(review|submit|export|download|restore|rotate)' THEN 'MEDIUM'
        ELSE 'LOW'
    END
    """


def _outcome(action: str) -> str:
    tokens = set(action.split("."))
    if "denied" in tokens or "rejected" in tokens:
        return "DENIED"
    if "failed" in tokens:
        return "FAILED"
    if "partial" in tokens:
        return "PARTIAL"
    return "SUCCEEDED"


def _risk(action: str) -> tuple[str, tuple[str, ...]]:
    normalized = action.lower()
    if any(
        token in normalized
        for token in (
            "credential",
            "password",
            "secret",
            "access",
            "grant",
            "revoke",
            "disable",
            "delete",
            "publish",
        )
    ):
        return ("HIGH", ("PRIVILEGED_OR_DESTRUCTIVE_ACTION",))
    if any(
        token in normalized
        for token in ("review", "submit", "export", "download", "restore", "rotate")
    ):
        return ("MEDIUM", ("CONTROLLED_OPERATION",))
    return ("LOW", ())
