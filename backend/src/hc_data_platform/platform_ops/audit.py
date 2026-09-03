"""Tamper-evident, redacted P19 projection for global platform operations."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from threading import RLock
from typing import Annotated, Any, Literal, Protocol, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.core.errors import problem
from hc_data_platform.core.pagination import CursorCodec
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import (
    CAPABILITY_PLATFORM_ADMIN,
    CAPABILITY_PLATFORM_MAINTENANCE_VERIFY,
    CAPABILITY_PLATFORM_OPERATIONS_READ,
)

PlatformAuditDomain = Literal["BACKUP", "RESTORE", "RELEASE", "PLATFORM"]
PlatformAuditOutcome = Literal["SUCCEEDED", "DENIED", "FAILED"]
PlatformAuditIntegrityStatus = Literal["PASSED", "FAILED"]
SafeIdentifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9._:/-]{0,253}[A-Za-z0-9])?$",
    ),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlatformAuditEventRecord(_StrictModel):
    """Internal immutable source record; ``safe_details`` never crosses the API."""

    event_id: UUID
    actor_id: SafeIdentifier
    action: SafeIdentifier
    resource_type: SafeIdentifier
    resource_id: SafeIdentifier
    request_id: SafeIdentifier
    outcome: PlatformAuditOutcome
    safe_details: Mapping[str, object] = Field(default_factory=dict)
    occurred_at: datetime
    sequence_no: int = Field(gt=0)


class PlatformAuditSubjectRef(_StrictModel):
    type: Literal["USER_OR_SERVICE"] = "USER_OR_SERVICE"
    id_ref: str = Field(pattern=r"^id-hmac-sha256:[0-9a-f]{64}$")


class PlatformAuditResourceRef(_StrictModel):
    type: str = Field(min_length=1, max_length=128)
    id_ref: str = Field(pattern=r"^id-hmac-sha256:[0-9a-f]{64}$")


class PlatformAuditRedaction(_StrictModel):
    policy_version: Literal["platform-audit-redaction/v1"] = "platform-audit-redaction/v1"
    omitted_field_classes: tuple[str, ...] = (
        "actor_identifier",
        "resource_identifier",
        "safe_details_values",
    )
    retained_detail_keys: tuple[str, ...] = ()


class PlatformAuditEventProjection(_StrictModel):
    schema_version: Literal["hc-platform-audit-event/v1"] = "hc-platform-audit-event/v1"
    event_id: UUID
    sequence_no: int = Field(gt=0)
    domain: PlatformAuditDomain
    event_name: str = Field(min_length=1, max_length=255)
    occurred_at: datetime
    actor: PlatformAuditSubjectRef
    resource: PlatformAuditResourceRef
    request_id: SafeIdentifier
    outcome: PlatformAuditOutcome
    integrity: Literal["CHAINED"] = "CHAINED"
    redaction: PlatformAuditRedaction


class PlatformAuditPage(_StrictModel):
    format_version: Literal["hc-platform-audit-page/v1"] = "hc-platform-audit-page/v1"
    snapshot_at: datetime
    count: int = Field(ge=0)
    items: tuple[PlatformAuditEventProjection, ...]
    next_cursor: str | None = Field(default=None, max_length=16_384)


class PlatformAuditIntegrity(_StrictModel):
    format_version: Literal["hc-platform-audit-integrity/v1"] = "hc-platform-audit-integrity/v1"
    status: PlatformAuditIntegrityStatus
    checked_at: datetime
    checked_event_count: int = Field(ge=0)
    checked_chain_count: int = Field(ge=0, le=1)
    verified_through: datetime | None = None


class PlatformAuditExport(_StrictModel):
    media_type: Literal["application/x-ndjson"] = "application/x-ndjson"
    filename: str = Field(pattern=r"^platform-audit-[0-9TZ-]+\.jsonl$")
    event_count: int = Field(ge=0, le=10_000)
    payload: bytes


class PlatformAuditRepository(Protocol):
    def list_records(
        self,
        *,
        snapshot_at: datetime,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
        anchor: tuple[datetime, UUID] | None,
        limit: int,
    ) -> tuple[PlatformAuditEventRecord, ...]: ...

    def integrity(self) -> PlatformAuditIntegrity: ...

    def append_event(
        self,
        *,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
        outcome: PlatformAuditOutcome,
        safe_details: Mapping[str, object],
    ) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _domain(record: PlatformAuditEventRecord) -> PlatformAuditDomain:
    action = record.action.lower()
    operation_kind = str(record.safe_details.get("operation_kind", "")).upper()
    if action.startswith("platform.backup") or operation_kind == "BACKUP":
        return "BACKUP"
    if action.startswith("platform.restore") or operation_kind == "RESTORE":
        return "RESTORE"
    if (
        action.startswith("platform.release")
        or action.startswith("platform.runtime.config")
        or operation_kind in {"RELEASE", "MIGRATION"}
    ):
        return "RELEASE"
    return "PLATFORM"


def _retained_detail_keys(details: Mapping[str, object]) -> tuple[str, ...]:
    allowed = {
        "capability_key",
        "changed_keys",
        "environment_id",
        "format_version",
        "operation_kind",
        "previous_revision",
        "reason_code",
        "reason_recorded",
        "revision",
        "schema_version",
        "source_environment_id",
        "state",
        "state_version",
        "status",
        "target_revision",
    }
    return tuple(sorted(str(key) for key in details if str(key) in allowed))


class InMemoryPlatformAuditRepository:
    def __init__(
        self,
        records: Sequence[PlatformAuditEventRecord] = (),
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._records = list(records)
        self._clock = clock
        self._lock = RLock()

    def list_records(
        self,
        *,
        snapshot_at: datetime,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
        anchor: tuple[datetime, UUID] | None,
        limit: int,
    ) -> tuple[PlatformAuditEventRecord, ...]:
        with self._lock:
            selected = [
                record
                for record in self._records
                if record.occurred_at <= snapshot_at
                and (occurred_from is None or record.occurred_at >= occurred_from)
                and (occurred_to is None or record.occurred_at < occurred_to)
                and (
                    anchor is None or (record.occurred_at, record.event_id) < (anchor[0], anchor[1])
                )
            ]
            selected.sort(key=lambda value: (value.occurred_at, value.event_id), reverse=True)
            return tuple(selected[:limit])

    def integrity(self) -> PlatformAuditIntegrity:
        with self._lock:
            records = sorted(self._records, key=lambda value: value.sequence_no)
            valid = all(record.sequence_no == index for index, record in enumerate(records, 1))
            return PlatformAuditIntegrity(
                status="PASSED" if valid else "FAILED",
                checked_at=_utc_now(),
                checked_event_count=len(records),
                checked_chain_count=1 if records else 0,
                verified_through=max((record.occurred_at for record in records), default=None),
            )

    def append_event(
        self,
        *,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
        outcome: PlatformAuditOutcome,
        safe_details: Mapping[str, object],
    ) -> None:
        with self._lock:
            self._records.append(
                PlatformAuditEventRecord(
                    event_id=uuid4(),
                    actor_id=actor_id,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    request_id=request_id,
                    outcome=outcome,
                    safe_details=dict(safe_details),
                    occurred_at=self._clock(),
                    sequence_no=len(self._records) + 1,
                )
            )


ConnectionFactory = Callable[[], Any]


class PostgresPlatformAuditRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(cls, dsn: str) -> PostgresPlatformAuditRepository:
        normalized = normalize_postgres_dsn(dsn)

        def connect() -> Any:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(normalized, row_factory=dict_row)

        return cls(connect)

    def list_records(
        self,
        *,
        snapshot_at: datetime,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
        anchor: tuple[datetime, UUID] | None,
        limit: int,
    ) -> tuple[PlatformAuditEventRecord, ...]:
        clauses = ["audit.scope_kind = 'PLATFORM'", "audit.occurred_at <= %s"]
        params: list[object] = [snapshot_at]
        if occurred_from is not None:
            clauses.append("audit.occurred_at >= %s")
            params.append(occurred_from)
        if occurred_to is not None:
            clauses.append("audit.occurred_at < %s")
            params.append(occurred_to)
        if anchor is not None:
            clauses.append("(audit.occurred_at, audit.event_id) < (%s, %s)")
            params.extend(anchor)
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT audit.event_id, audit.actor_id, audit.action,
                           audit.resource_type, audit.resource_id, audit.request_id,
                           audit.outcome, audit.safe_details, audit.occurred_at,
                           integrity.sequence_no
                      FROM access_control.audit_events audit
                      JOIN platform.platform_audit_integrity_entries integrity
                        ON integrity.event_id = audit.event_id
                     WHERE {" AND ".join(clauses)}
                     ORDER BY audit.occurred_at DESC, audit.event_id DESC
                     LIMIT %s
                    """,
                    (*params, limit),
                )
                return tuple(PlatformAuditEventRecord.model_validate(row) for row in cursor)
        finally:
            connection.close()

    def integrity(self) -> PlatformAuditIntegrity:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    WITH visible AS (
                        SELECT audit.event_id, audit.actor_id, audit.action,
                               audit.resource_type, audit.resource_id, audit.request_id,
                               audit.outcome, audit.safe_details, audit.occurred_at,
                               entry.sequence_no, entry.previous_event_hash, entry.event_hash,
                               row_number() OVER (ORDER BY entry.sequence_no NULLS LAST)
                                   AS expected_sequence_no,
                               lag(entry.event_hash) OVER (
                                   ORDER BY entry.sequence_no NULLS LAST
                               ) AS expected_previous_event_hash
                          FROM access_control.audit_events audit
                          LEFT JOIN platform.platform_audit_integrity_entries entry
                            ON entry.event_id = audit.event_id
                         WHERE audit.scope_kind = 'PLATFORM'
                    ), event_check AS (
                        SELECT count(*)::bigint AS checked_event_count,
                               max(occurred_at) AS verified_through,
                               COALESCE(bool_and(
                                   sequence_no = expected_sequence_no
                                   AND previous_event_hash IS NOT DISTINCT FROM
                                       expected_previous_event_hash
                                   AND event_hash = platform.platform_audit_event_hash(
                                       previous_event_hash, event_id, actor_id, action,
                                       resource_type, resource_id, request_id, outcome,
                                       safe_details, occurred_at
                                   )
                               ), true) AS events_valid
                          FROM visible
                    ), tail AS (
                        SELECT event_hash FROM visible
                         ORDER BY expected_sequence_no DESC LIMIT 1
                    ), head AS (
                        SELECT last_sequence, last_event_hash
                          FROM platform.platform_audit_integrity_head WHERE singleton
                    )
                    SELECT checked_event_count,
                           CASE WHEN checked_event_count > 0 THEN 1 ELSE 0 END
                               AS checked_chain_count,
                           verified_through,
                           CASE WHEN checked_event_count = 0
                               THEN events_valid AND NOT EXISTS (SELECT 1 FROM head)
                               ELSE events_valid AND EXISTS (
                                   SELECT 1 FROM head, tail
                                    WHERE head.last_sequence = checked_event_count
                                      AND head.last_event_hash = tail.event_hash
                               )
                           END AS verified
                      FROM event_check
                    """
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("platform audit integrity returned no result")
                checked = int(row["checked_event_count"])
                chains = int(row["checked_chain_count"])
                return PlatformAuditIntegrity(
                    status="PASSED" if row["verified"] is True else "FAILED",
                    checked_at=_utc_now(),
                    checked_event_count=checked,
                    checked_chain_count=chains,
                    verified_through=row["verified_through"],
                )
        finally:
            connection.close()

    def append_event(
        self,
        *,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
        outcome: PlatformAuditOutcome,
        safe_details: Mapping[str, object],
    ) -> None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO access_control.audit_events (
                        event_id, scope_kind, organization_id, project_id, actor_id,
                        action, resource_type, resource_id, request_id, outcome,
                        safe_details
                    ) VALUES (
                        %s, 'PLATFORM', NULL, NULL, %s, %s, %s, %s, %s, %s, %s::jsonb
                    )
                    """,
                    (
                        uuid4(),
                        actor_id,
                        action,
                        resource_type,
                        resource_id,
                        request_id,
                        outcome,
                        json.dumps(dict(safe_details), sort_keys=True, separators=(",", ":")),
                    ),
                )
            connection.commit()
        finally:
            connection.close()


class PlatformAuditService:
    def __init__(
        self,
        repository: PlatformAuditRepository,
        *,
        cursor_secret: str,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.repository = repository
        self._cursor = CursorCodec(cursor_secret)
        self._reference_key = cursor_secret.encode()
        self._clock = clock

    def list_events(
        self,
        *,
        auth: AuthContext,
        request_id: str,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> PlatformAuditPage:
        auth.require_exact_platform_capability(
            CAPABILITY_PLATFORM_ADMIN,
            CAPABILITY_PLATFORM_OPERATIONS_READ,
        )
        if limit < 1 or limit > 200:
            raise ValueError("platform audit limit must be between 1 and 200")
        self._validate_window(occurred_from, occurred_to)
        snapshot_at, anchor = self._decode_cursor(
            cursor,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
        )
        if snapshot_at is None:
            snapshot_at = self._clock()
        records = self.repository.list_records(
            snapshot_at=snapshot_at,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
            anchor=anchor,
            limit=limit + 1,
        )
        page_records = records[:limit]
        next_cursor = None
        if len(records) > limit and page_records:
            last = page_records[-1]
            next_cursor = self._cursor.encode(
                {
                    "kind": "platform-audit/v1",
                    "snapshot_at": snapshot_at.isoformat(),
                    "occurred_at": last.occurred_at.isoformat(),
                    "event_id": str(last.event_id),
                    "occurred_from": (
                        occurred_from.isoformat() if occurred_from is not None else None
                    ),
                    "occurred_to": occurred_to.isoformat() if occurred_to is not None else None,
                }
            )
        self.repository.append_event(
            actor_id=auth.subject_id,
            action="platform.audit.events.listed",
            resource_type="platform_audit",
            resource_id="platform-audit",
            request_id=request_id,
            outcome="SUCCEEDED",
            safe_details={"event_count": len(page_records), "redacted": True},
        )
        return PlatformAuditPage(
            snapshot_at=snapshot_at,
            count=len(page_records),
            items=tuple(self._project(record) for record in page_records),
            next_cursor=next_cursor,
        )

    def verify_integrity(
        self,
        *,
        auth: AuthContext,
        request_id: str,
    ) -> PlatformAuditIntegrity:
        auth.require_exact_platform_capability(CAPABILITY_PLATFORM_MAINTENANCE_VERIFY)
        result = self.repository.integrity()
        self.repository.append_event(
            actor_id=auth.subject_id,
            action="platform.audit.integrity.verified",
            resource_type="platform_audit",
            resource_id="platform-audit",
            request_id=request_id,
            outcome="SUCCEEDED" if result.status == "PASSED" else "FAILED",
            safe_details={
                "checked_event_count": result.checked_event_count,
                "status": result.status,
            },
        )
        return result

    def export(
        self,
        *,
        auth: AuthContext,
        request_id: str,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
    ) -> PlatformAuditExport:
        auth.require_exact_platform_capability(CAPABILITY_PLATFORM_MAINTENANCE_VERIFY)
        self._validate_window(occurred_from, occurred_to)
        integrity = self.repository.integrity()
        if integrity.status != "PASSED":
            raise problem(
                status=409,
                code="PLATFORM_AUDIT_INTEGRITY_FAILED",
                title="Platform audit integrity failed",
                detail="The platform audit chain failed verification; no export was created.",
            )
        snapshot_at = self._clock()
        records = self.repository.list_records(
            snapshot_at=snapshot_at,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
            anchor=None,
            limit=10_001,
        )
        if len(records) > 10_000:
            raise problem(
                status=422,
                code="PLATFORM_AUDIT_EXPORT_TOO_LARGE",
                title="Platform audit export too large",
                detail="Narrow the requested time window before exporting platform audit events.",
            )
        payload = b"".join(
            (self._project(record).model_dump_json(exclude_none=False) + "\n").encode("utf-8")
            for record in records
        )
        self.repository.append_event(
            actor_id=auth.subject_id,
            action="platform.audit.exported",
            resource_type="platform_audit_export",
            resource_id=str(uuid4()),
            request_id=request_id,
            outcome="SUCCEEDED",
            safe_details={"event_count": len(records), "redacted": True},
        )
        return PlatformAuditExport(
            filename=f"platform-audit-{snapshot_at.strftime('%Y-%m-%dT%H%M%SZ')}.jsonl",
            event_count=len(records),
            payload=payload,
        )

    def _project(self, record: PlatformAuditEventRecord) -> PlatformAuditEventProjection:
        return PlatformAuditEventProjection(
            event_id=record.event_id,
            sequence_no=record.sequence_no,
            domain=_domain(record),
            event_name=record.action,
            occurred_at=record.occurred_at,
            actor=PlatformAuditSubjectRef(id_ref=self._reference(record.actor_id)),
            resource=PlatformAuditResourceRef(
                type=record.resource_type.upper(),
                id_ref=self._reference(record.resource_id),
            ),
            request_id=record.request_id,
            outcome=record.outcome,
            redaction=PlatformAuditRedaction(
                retained_detail_keys=_retained_detail_keys(record.safe_details)
            ),
        )

    def _reference(self, value: str) -> str:
        digest = hmac.new(self._reference_key, value.encode(), hashlib.sha256).hexdigest()
        return f"id-hmac-sha256:{digest}"

    @staticmethod
    def _validate_window(
        occurred_from: datetime | None,
        occurred_to: datetime | None,
    ) -> None:
        if (
            (occurred_from is not None and occurred_from.tzinfo is None)
            or (occurred_to is not None and occurred_to.tzinfo is None)
            or (
                occurred_from is not None
                and occurred_to is not None
                and occurred_from >= occurred_to
            )
        ):
            raise problem(
                status=422,
                code="PLATFORM_AUDIT_TIME_WINDOW_INVALID",
                title="Platform audit time window invalid",
                detail=(
                    "Audit time bounds must be timezone-aware and occurred_from must be "
                    "earlier than occurred_to."
                ),
            )

    def _decode_cursor(
        self,
        cursor: str | None,
        *,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
    ) -> tuple[datetime | None, tuple[datetime, UUID] | None]:
        if cursor is None:
            return None, None
        payload = self._cursor.decode(cursor)
        try:
            if payload.get("kind") != "platform-audit/v1":
                raise ValueError("cursor kind mismatch")
            snapshot_at = datetime.fromisoformat(cast(str, payload["snapshot_at"]))
            occurred_at = datetime.fromisoformat(cast(str, payload["occurred_at"]))
            event_id = UUID(cast(str, payload["event_id"]))
            cursor_from_raw = payload["occurred_from"]
            cursor_to_raw = payload["occurred_to"]
            cursor_from = (
                None
                if cursor_from_raw is None
                else datetime.fromisoformat(cast(str, cursor_from_raw))
            )
            cursor_to = (
                None if cursor_to_raw is None else datetime.fromisoformat(cast(str, cursor_to_raw))
            )
            if snapshot_at.tzinfo is None or occurred_at.tzinfo is None:
                raise ValueError("cursor timestamps must be timezone-aware")
            if (
                (cursor_from is not None and cursor_from.tzinfo is None)
                or (cursor_to is not None and cursor_to.tzinfo is None)
                or cursor_from != occurred_from
                or cursor_to != occurred_to
            ):
                raise ValueError("cursor filter mismatch")
            return snapshot_at, (occurred_at, event_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid pagination cursor",
                detail="The cursor is malformed or was issued for another platform view.",
            ) from exc
