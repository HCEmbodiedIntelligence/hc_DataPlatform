"""Authorization, redaction, and stable keyset cursors for P19."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, cast

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.core.errors import problem
from hc_data_platform.core.pagination import CursorCodec
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.scope import ScopeGuard

from .models import (
    AuditActorSnapshot,
    AuditBootstrap,
    AuditBootstrapEnvelope,
    AuditCursorEnvelope,
    AuditEventEnvelope,
    AuditFacets,
    AuditFacetsEnvelope,
    AuditIntegrityCheck,
    AuditIntegrityEnvelope,
    AuditIntegrityProjection,
    AuditLegalHold,
    AuditOutcomeProjection,
    AuditPageInfo,
    AuditProducer,
    AuditReadProjection,
    AuditRedaction,
    AuditRequestContext,
    AuditResourceRef,
    AuditRetentionPolicy,
    AuditRetentionProjection,
    AuditRiskProjection,
    AuditScope,
)
from .repository import (
    AuditEventRecord,
    AuditProjectionRepository,
    AuditQueryFilters,
    InMemoryAuditProjectionRepository,
    _outcome,
    _risk,
)

Clock = Callable[[], datetime]
RetentionPolicyProvider = Callable[[AuditScope], AuditRetentionPolicy | None]
LegalHoldProvider = Callable[[AuditScope], tuple[AuditLegalHold, ...]]
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_EVENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_RESOURCE_TYPE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_MAX_WINDOW = timedelta(days=31)
_CURSOR_TTL = timedelta(minutes=15)
_POLICY_VERSION = "audit-read-redaction-v1"
_CATALOG_VERSION = "core-audit-catalog-v1"
_INTEGRITY_VERSION = "core-audit-integrity-chain-v1"


@dataclass(frozen=True, slots=True)
class AuditQuery:
    filters: AuditQueryFilters
    scope: AuditScope


class AuditProjectionService:
    def __init__(
        self,
        repository: AuditProjectionRepository,
        *,
        cursor_secret: str = "audit-projection-local-cursor-secret",
        clock: Clock = lambda: datetime.now(timezone.utc),
        retention_policy_provider: RetentionPolicyProvider | None = None,
        legal_hold_provider: LegalHoldProvider | None = None,
    ) -> None:
        self._repository = repository
        self._cursor = CursorCodec(cursor_secret)
        self._clock = clock
        self._retention_policy_provider = retention_policy_provider
        self._legal_hold_provider = legal_hold_provider

    @classmethod
    def in_memory(cls) -> AuditProjectionService:
        return cls(InMemoryAuditProjectionRepository())

    def bootstrap(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        occurred_from: datetime,
        occurred_to: datetime,
        request_id: str,
    ) -> AuditBootstrapEnvelope:
        query = self._query(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
        )
        now = self._now()
        integrity = self._repository.integrity(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        metrics = self._repository.metrics(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            filters=query.filters,
            snapshot_at=now,
        )
        return AuditBootstrapEnvelope(
            data=AuditBootstrap(
                scope=query.scope,
                metrics={
                    "today": str(metrics.today),
                    "high_risk": str(metrics.high_risk),
                    "failed": str(metrics.failed),
                    "active_actors": str(metrics.active_actors),
                },
                as_of=now,
                catalog_version=_CATALOG_VERSION,
                policy_version=self._retention_version(query.scope),
                integrity=integrity.status,
            ),
            scope=query.scope,
            request_id=request_id,
        )

    def integrity(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        request_id: str,
    ) -> AuditIntegrityEnvelope:
        self._authorize(auth, organization_id, project_id, region_code)
        scope = AuditScope(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        checked_at = self._now()
        summary = self._repository.integrity(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        return AuditIntegrityEnvelope(
            data=AuditIntegrityCheck(
                status=summary.status,
                version=_INTEGRITY_VERSION,
                checked_at=checked_at,
                checked_event_count=summary.checked_event_count,
                checked_chain_count=summary.checked_chain_count,
                verified_through=summary.verified_through,
            ),
            scope=scope,
            request_id=request_id,
        )

    def facets(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        filters: AuditQueryFilters,
        request_id: str,
    ) -> AuditFacetsEnvelope:
        query = self._query_from_filters(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            filters=filters,
        )
        values = self._repository.facets(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            filters=query.filters,
            snapshot_at=self._now(),
        )
        return AuditFacetsEnvelope(
            data=AuditFacets(
                event_names=tuple(_safe_event_name(value) for value in values.event_names),
                # Actor IDs are intentionally opaque values and are returned
                # only to callers that also hold access.read.  The core record
                # still remains redacted for all profiles.
                actor_ids=(
                    tuple(value for value in values.actor_ids if _ID.fullmatch(value))
                    if _has_scope_capability(
                        auth,
                        "access.read",
                        organization_id=organization_id,
                        project_id=project_id,
                    )
                    else ()
                ),
                resource_types=tuple(_resource_type(value) for value in values.resource_types),
                outcomes=cast(
                    tuple[Literal["SUCCEEDED", "DENIED", "FAILED", "PARTIAL"], ...],
                    values.outcomes,
                ),
                risk_levels=cast(
                    tuple[Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"], ...],
                    values.risk_levels,
                ),
            ),
            scope=query.scope,
            request_id=request_id,
        )

    def list_events(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        filters: AuditQueryFilters,
        after: str | None,
        before: str | None,
        limit: int,
        request_id: str,
    ) -> AuditCursorEnvelope:
        if after is not None and before is not None:
            raise problem(
                status=422,
                code="CURSOR_DIRECTION_CONFLICT",
                title="Invalid pagination request",
                detail="Use either after or before, not both.",
            )
        if limit not in {20, 50, 100}:
            raise problem(
                status=422,
                code="PAGE_LIMIT_INVALID",
                title="Invalid page size",
                detail="Page size must be 20, 50, or 100.",
            )
        query = self._query_from_filters(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            filters=filters,
        )
        cursor_value = after or before
        direction = "before" if before is not None else "after"
        snapshot_at = self._now()
        anchor: tuple[datetime, str] | None = None
        if cursor_value is not None:
            snapshot_at, anchor = self._decode_cursor(
                cursor_value,
                auth=auth,
                query=query,
            )
        rows = self._repository.list_events(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            filters=query.filters,
            snapshot_at=snapshot_at,
            anchor=anchor,
            direction=direction,
            limit=limit + 1,
        )
        has_extra = len(rows) > limit
        items = rows[:limit]
        has_previous = bool(after) or (bool(before) and has_extra)
        has_next = (
            (bool(before) and bool(items))
            or (bool(after) and has_extra)
            or (cursor_value is None and has_extra)
        )
        start_cursor = (
            self._encode_cursor(query=query, auth=auth, snapshot_at=snapshot_at, record=items[0])
            if items
            else None
        )
        end_cursor = (
            self._encode_cursor(query=query, auth=auth, snapshot_at=snapshot_at, record=items[-1])
            if items
            else None
        )
        retention_policy = self._retention_policy(query.scope)
        legal_holds = self._legal_holds(query.scope)
        return AuditCursorEnvelope(
            items=tuple(
                self._projection(
                    record,
                    query.scope,
                    auth,
                    retention_policy=retention_policy,
                    legal_holds=legal_holds,
                )
                for record in items
            ),
            page_info=AuditPageInfo(
                has_next_page=has_next,
                has_previous_page=has_previous,
                start_cursor=start_cursor,
                end_cursor=end_cursor,
            ),
            snapshot_at=snapshot_at,
            redaction=AuditRedaction(
                policy_version=_POLICY_VERSION,
                omitted_field_classes=(
                    "raw_details",
                    "ip_address",
                    "device_fingerprint",
                    "change_values",
                    "integrity_digest",
                ),
            ),
            scope=query.scope,
            request_id=request_id,
        )

    def event(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        event_id: str,
        request_id: str,
    ) -> AuditEventEnvelope:
        if not _ID.fullmatch(event_id):
            raise problem(
                status=422,
                code="AUDIT_EVENT_ID_INVALID",
                title="Invalid audit event identifier",
                detail="The audit event identifier is not valid.",
            )
        self._authorize(auth, organization_id, project_id, region_code)
        scope = AuditScope(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        record = self._repository.get_event(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            event_id=event_id,
        )
        if record is None:
            raise problem(
                status=404,
                code="AUDIT_EVENT_NOT_VISIBLE",
                title="Audit event not found",
                detail="The audit event is not visible in this scope.",
            )
        return AuditEventEnvelope(
            data=self._projection(
                record,
                scope,
                auth,
                retention_policy=self._retention_policy(scope),
                legal_holds=self._legal_holds(scope),
            ),
            scope=scope,
            request_id=request_id,
        )

    def iter_redacted_export_pages(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        occurred_from: datetime,
        occurred_to: datetime,
        page_size: int = 500,
    ) -> Iterator[tuple[AuditReadProjection, ...]]:
        """Yield a fixed-snapshot export without exposing repository rows.

        This deliberately bypasses the browser cursor's 15 minute TTL while
        retaining the same authorization, filter normalization, scope and
        redaction projection.  The caller can stream each bounded page to an
        artifact sink, so a large export never requires loading all events.
        """

        if page_size < 1 or page_size > 2_000:
            raise ValueError("audit export page_size must be between 1 and 2000")
        query = self._query(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
        )
        snapshot_at = self._now()
        anchor: tuple[datetime, str] | None = None
        retention_policy = self._retention_policy(query.scope)
        legal_holds = self._legal_holds(query.scope)
        while True:
            rows = self._repository.list_events(
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
                filters=query.filters,
                snapshot_at=snapshot_at,
                anchor=anchor,
                direction="after",
                limit=page_size,
            )
            if not rows:
                return
            yield tuple(
                self._projection(
                    record,
                    query.scope,
                    auth,
                    retention_policy=retention_policy,
                    legal_holds=legal_holds,
                )
                for record in rows
            )
            if len(rows) < page_size:
                return
            last = rows[-1]
            anchor = (last.occurred_at, last.audit_id)

    def _query(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        occurred_from: datetime,
        occurred_to: datetime,
    ) -> AuditQuery:
        return self._query_from_filters(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            filters=AuditQueryFilters(
                occurred_from=occurred_from,
                occurred_to=occurred_to,
            ),
        )

    def _query_from_filters(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str | None,
        filters: AuditQueryFilters,
    ) -> AuditQuery:
        self._authorize(auth, organization_id, project_id, region_code)
        normalized = _normalize_filters(filters)
        return AuditQuery(
            filters=normalized,
            scope=AuditScope(
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
            ),
        )

    @staticmethod
    def _authorize(
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str | None,
    ) -> None:
        if auth.organization_scope_triples:
            ScopeGuard.require(auth, project_id, region_code, organization_id)
            auth.require_capability("audit.read", project_id, organization_id)
        else:
            # Compatibility for explicitly legacy tokens. New organization-aware
            # sessions always take the stricter branch above.
            ScopeGuard.require(auth, project_id, region_code)
            auth.require_capability("audit.read", project_id)
        select_request_scope(project_id, region_code, organization_id=organization_id)

    def _encode_cursor(
        self,
        *,
        query: AuditQuery,
        auth: AuthContext,
        snapshot_at: datetime,
        record: AuditEventRecord,
    ) -> str:
        issued_at = self._now()
        return self._cursor.encode(
            {
                "v": 1,
                "binding": _binding(query, auth),
                "authorization_revision": auth.capability_revision,
                "snapshot_at": _instant(snapshot_at),
                "issued_at": _instant(issued_at),
                "occurred_at": _instant(record.occurred_at),
                "event_id": record.audit_id,
            }
        )

    def _decode_cursor(
        self,
        value: str,
        *,
        auth: AuthContext,
        query: AuditQuery,
    ) -> tuple[datetime, tuple[datetime, str]]:
        payload = self._cursor.decode(value)
        try:
            if payload["v"] != 1:
                raise ValueError
            authorization_revision = payload["authorization_revision"]
            if not isinstance(authorization_revision, int):
                raise ValueError
            if authorization_revision != auth.capability_revision:
                raise problem(
                    status=409,
                    code="AUDIT_CURSOR_AUTH_CHANGED",
                    title="Audit authorization changed",
                    detail="Refresh the audit query after authorization changes.",
                )
            if payload["binding"] != _binding(query, auth):
                raise ValueError
            snapshot_at = _parse_instant(payload["snapshot_at"])
            issued_at = _parse_instant(payload["issued_at"])
            occurred_at = _parse_instant(payload["occurred_at"])
            event_id = payload["event_id"]
            if not isinstance(event_id, str) or not _ID.fullmatch(event_id):
                raise ValueError
        except Exception as exc:
            # Preserve an intentionally raised typed API error above.
            from hc_data_platform.core.errors import ProblemException

            if isinstance(exc, ProblemException):
                raise
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid audit cursor",
                detail="The cursor does not belong to this audit query.",
            ) from exc
        if self._now() - issued_at > _CURSOR_TTL:
            raise problem(
                status=410,
                code="AUDIT_CURSOR_EXPIRED",
                title="Audit cursor expired",
                detail="Refresh the audit query to receive a current cursor.",
            )
        return snapshot_at, (occurred_at, event_id)

    def _projection(
        self,
        record: AuditEventRecord,
        scope: AuditScope,
        auth: AuthContext,
        *,
        retention_policy: AuditRetentionPolicy | None = None,
        legal_holds: tuple[AuditLegalHold, ...] | None = None,
    ) -> AuditReadProjection:
        outcome = cast(Literal["SUCCEEDED", "DENIED", "FAILED", "PARTIAL"], _outcome(record.action))
        risk, signals = _risk(record.action)
        actor_visible = _has_scope_capability(
            auth,
            "access.read",
            organization_id=scope.organization_id,
            project_id=scope.project_id,
        )
        actor_type: Literal["SYSTEM", "USER"] = (
            "SYSTEM" if record.actor_id.startswith("system") else "USER"
        )
        policy = retention_policy or self._retention_policy(scope)
        security_event = record.action.startswith(("auth.", "access."))
        hold = next(
            (
                candidate
                for candidate in (
                    legal_holds if legal_holds is not None else self._legal_holds(scope)
                )
                if candidate.status == "ACTIVE"
                and candidate.occurred_from <= record.occurred_at < candidate.occurred_to
            ),
            None,
        )
        retention_days = policy.security_days if security_event else policy.standard_days
        return AuditReadProjection(
            event_id=record.audit_id,
            event_name=_safe_event_name(record.action),
            occurred_at=record.occurred_at,
            recorded_at=record.occurred_at,
            actor=AuditActorSnapshot(
                type=actor_type,
                principal_id=record.actor_id
                if actor_visible and _ID.fullmatch(record.actor_id)
                else None,
                display_name=("已授权主体" if actor_visible else "已脱敏主体"),
            ),
            scope=scope,
            resource=AuditResourceRef(
                type=_resource_type(record.resource_type),
                id=_safe_identifier(record.resource_id, fallback=record.audit_id),
                display_name="已审计资源",
            ),
            request=AuditRequestContext(
                request_id=_safe_identifier(record.request_id, fallback=record.audit_id),
                client_type="SYSTEM" if actor_type == "SYSTEM" else "API",
            ),
            outcome=AuditOutcomeProjection(status=outcome),
            risk=AuditRiskProjection(
                level=cast(Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"], risk),
                signal_codes=signals,
            ),
            retention=AuditRetentionProjection.model_validate(
                {
                    "class": (
                        "LEGAL_HOLD"
                        if hold is not None
                        else ("SECURITY" if security_event else "STANDARD")
                    ),
                    "policy_version": self._retention_version(scope),
                    "retain_until": (
                        None
                        if hold is not None
                        else record.occurred_at + timedelta(days=retention_days)
                    ),
                    "legal_hold": hold is not None,
                }
            ),
            integrity=AuditIntegrityProjection(version="core-audit-integrity-unverified-v1"),
            producer=AuditProducer(service="hc-data-platform", producer_event_id=record.audit_id),
        )

    def _retention_policy(self, scope: AuditScope) -> AuditRetentionPolicy:
        configured = (
            None
            if self._retention_policy_provider is None
            else self._retention_policy_provider(scope)
        )
        if configured is not None:
            return configured
        return AuditRetentionPolicy(
            scope=scope,
            policy_version=1,
            standard_days=365,
            security_days=2555,
            etag='"audit-retention:default"',
            updated_by="system",
            updated_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
        )

    def _retention_version(self, scope: AuditScope) -> str:
        return f"audit-retention-policy-v{self._retention_policy(scope).policy_version}"

    def _legal_holds(self, scope: AuditScope) -> tuple[AuditLegalHold, ...]:
        if self._legal_hold_provider is None:
            return ()
        return self._legal_hold_provider(scope)

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise RuntimeError("audit projection clock must return a timezone-aware datetime")
        return now.astimezone(timezone.utc)


def _has_scope_capability(
    auth: AuthContext,
    capability: str,
    *,
    organization_id: str,
    project_id: str,
) -> bool:
    return auth.has_capability(
        capability,
        project_id,
        organization_id if auth.organization_scope_triples else None,
    )


def _normalize_filters(value: AuditQueryFilters) -> AuditQueryFilters:
    if value.occurred_from.tzinfo is None or value.occurred_to.tzinfo is None:
        raise problem(
            status=422,
            code="AUDIT_TIMEZONE_REQUIRED",
            title="Timezone-aware audit range required",
            detail="Audit times must include a UTC offset.",
        )
    occurred_from = value.occurred_from.astimezone(timezone.utc)
    occurred_to = value.occurred_to.astimezone(timezone.utc)
    if occurred_from >= occurred_to:
        raise problem(
            status=422,
            code="AUDIT_TIME_RANGE_INVALID",
            title="Invalid audit time range",
            detail="The start time must be earlier than the end time.",
        )
    if occurred_to - occurred_from > _MAX_WINDOW:
        raise problem(
            status=422,
            code="AUDIT_TIME_RANGE_TOO_LARGE",
            title="Audit time range too large",
            detail="Audit queries may cover at most 31 days.",
        )
    return AuditQueryFilters(
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        actor_ids=_normalized_ids(value.actor_ids),
        event_names=_normalized_events(value.event_names),
        resource_types=_normalized_resource_types(value.resource_types),
        resource_id=_normalized_optional_id(value.resource_id),
        outcomes=_normalized_enum(
            value.outcomes, {"SUCCEEDED", "DENIED", "FAILED", "PARTIAL"}, "AUDIT_OUTCOME_INVALID"
        ),
        risk_levels=_normalized_enum(
            value.risk_levels, {"LOW", "MEDIUM", "HIGH", "CRITICAL"}, "AUDIT_RISK_LEVEL_INVALID"
        ),
        request_id=_normalized_optional_id(value.request_id),
    )


def _normalized_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({_validated_id(value) for value in values}))


def _normalized_events(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted({value for value in values}))
    if any(not _EVENT.fullmatch(value) for value in normalized):
        raise problem(
            status=422,
            code="AUDIT_EVENT_NAME_INVALID",
            title="Invalid audit event filter",
            detail="Audit event names must be known-safe identifiers.",
        )
    return normalized


def _normalized_resource_types(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted({value.upper() for value in values}))
    if any(not _RESOURCE_TYPE.fullmatch(value) for value in normalized):
        raise problem(
            status=422,
            code="AUDIT_RESOURCE_TYPE_INVALID",
            title="Invalid audit resource filter",
            detail="Audit resource types must be uppercase safe identifiers.",
        )
    return normalized


def _normalized_optional_id(value: str | None) -> str | None:
    return None if value is None else _validated_id(value)


def _validated_id(value: str) -> str:
    if not _ID.fullmatch(value):
        raise problem(
            status=422,
            code="AUDIT_IDENTIFIER_INVALID",
            title="Invalid audit filter",
            detail="Audit identifiers must be opaque safe identifiers.",
        )
    return value


def _normalized_enum(values: tuple[str, ...], allowed: set[str], code: str) -> tuple[str, ...]:
    normalized = tuple(sorted(set(values)))
    if any(value not in allowed for value in normalized):
        raise problem(
            status=422,
            code=code,
            title="Invalid audit filter",
            detail="The audit filter includes an unsupported value.",
        )
    return normalized


def _resource_type(value: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()
    if not candidate or not _RESOURCE_TYPE.fullmatch(candidate):
        return "AUDIT_RESOURCE"
    return candidate


def _safe_identifier(value: str, *, fallback: str) -> str:
    return value if _ID.fullmatch(value) else fallback


def _safe_event_name(value: str) -> str:
    return value if _EVENT.fullmatch(value) else "audit.event.legacy"


def _binding(query: AuditQuery, auth: AuthContext) -> str:
    document = {
        "subject_id": auth.subject_id,
        "capability_revision": auth.capability_revision,
        "scope": query.scope.model_dump(mode="json"),
        "filters": {
            "occurred_from": _instant(query.filters.occurred_from),
            "occurred_to": _instant(query.filters.occurred_to),
            "actor_ids": query.filters.actor_ids,
            "event_names": query.filters.event_names,
            "resource_types": query.filters.resource_types,
            "resource_id": query.filters.resource_id,
            "outcomes": query.filters.outcomes,
            "risk_levels": query.filters.risk_levels,
            "request_id": query.filters.request_id,
        },
    }
    return hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _instant(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError
    return parsed.astimezone(timezone.utc)
