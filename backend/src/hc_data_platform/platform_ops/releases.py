"""Durable, redacted release preflight, history, and four-eyes approval."""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from threading import RLock
from typing import Annotated, Any, Literal, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.platform_control.release_feed import (
    ReleaseFeedError,
    ReleaseFeedVerifier,
    ReleaseImagesV1,
    SignedPlatformReleaseFeedV1,
    public_key_sha256,
)

from .instances import InstanceRole, PlatformInstanceService
from .overview import PlatformOperationsOverviewService

ReleaseState = Literal[
    "PREFLIGHT_BLOCKED",
    "AWAITING_APPROVAL",
    "APPROVED",
    "EXPAND",
    "CANARY",
    "ROLLOUT",
    "CONTRACT_PENDING",
    "COMPLETED",
    "ROLLED_BACK",
    "FAILED",
]
SafeReason = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=8, max_length=500),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReleasePreflightRequest(_StrictModel):
    target_release_id: str = Field(pattern=r"^platform-v[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
    minimum_feed_sequence: int = Field(gt=0)
    source_images: ReleaseImagesV1
    signed_feed: SignedPlatformReleaseFeedV1


class ReleaseApprovalRequest(_StrictModel):
    expected_state_version: int = Field(gt=0)
    reason: SafeReason


class ReleaseTransitionRequest(_StrictModel):
    expected_state_version: int = Field(gt=0)
    next_state: ReleaseState
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")


class ReleaseEvent(_StrictModel):
    state_version: int = Field(gt=0)
    event_kind: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    state: ReleaseState
    actor_ref: str = Field(pattern=r"^id-hmac-sha256:[0-9a-f]{64}$")
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    occurred_at: datetime


class ReleaseRun(_StrictModel):
    format_version: Literal["hc-platform-release-run/v1"] = "hc-platform-release-run/v1"
    release_id: str = Field(pattern=r"^platform-v[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
    source_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    target_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: ReleaseState
    state_version: int = Field(gt=0)
    approval_required: Literal[True] = True
    approved: bool
    created_at: datetime
    updated_at: datetime
    events: tuple[ReleaseEvent, ...]


class ReleaseHistoryPage(_StrictModel):
    format_version: Literal["hc-platform-release-history/v1"] = "hc-platform-release-history/v1"
    count: int = Field(ge=0, le=50)
    items: tuple[ReleaseRun, ...] = Field(max_length=50)


class ReleaseRunRecord(_StrictModel):
    environment_id: str
    release_id: str
    source_version: str
    target_version: str
    manifest_sha256: str
    source_images: ReleaseImagesV1
    target_images: ReleaseImagesV1
    state: ReleaseState
    state_version: int = Field(gt=0)
    requested_by: str
    approved_by: str | None = None
    approval_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class ReleaseEventRecord(_StrictModel):
    state_version: int = Field(gt=0)
    event_kind: str
    state: ReleaseState
    actor_id: str
    reason_code: str
    request_id: str
    occurred_at: datetime


class ReleaseRepository(Protocol):
    def create(self, record: ReleaseRunRecord, event: ReleaseEventRecord) -> ReleaseRunRecord: ...

    def get(self, environment_id: str, release_id: str) -> ReleaseRunRecord: ...

    def list(self, environment_id: str, *, limit: int) -> tuple[ReleaseRunRecord, ...]: ...

    def events(self, environment_id: str, release_id: str) -> tuple[ReleaseEventRecord, ...]: ...

    def approve(
        self,
        environment_id: str,
        release_id: str,
        *,
        expected_state_version: int,
        actor_id: str,
        reason: str,
        request_id: str,
        occurred_at: datetime,
    ) -> ReleaseRunRecord: ...

    def transition(
        self,
        environment_id: str,
        release_id: str,
        *,
        expected_state_version: int,
        next_state: ReleaseState,
        actor_id: str,
        reason_code: str,
        request_id: str,
        occurred_at: datetime,
    ) -> ReleaseRunRecord: ...


class ReleaseOperationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryReleaseRepository:
    def __init__(self) -> None:
        self._runs: dict[tuple[str, str], ReleaseRunRecord] = {}
        self._events: dict[tuple[str, str], list[ReleaseEventRecord]] = {}
        self._lock = RLock()

    def create(self, record: ReleaseRunRecord, event: ReleaseEventRecord) -> ReleaseRunRecord:
        key = (record.environment_id, record.release_id)
        with self._lock:
            existing = self._runs.get(key)
            if existing is not None:
                if existing.manifest_sha256 != record.manifest_sha256:
                    raise ReleaseOperationError(
                        "RELEASE_ID_CONFLICT", "release ID is already bound to another manifest"
                    )
                return existing
            self._runs[key] = record
            self._events[key] = [event]
            return record

    def get(self, environment_id: str, release_id: str) -> ReleaseRunRecord:
        try:
            return self._runs[(environment_id, release_id)]
        except KeyError as exc:
            raise ReleaseOperationError("RELEASE_NOT_FOUND", "release run was not found") from exc

    def list(self, environment_id: str, *, limit: int) -> tuple[ReleaseRunRecord, ...]:
        with self._lock:
            selected = [
                item for item in self._runs.values() if item.environment_id == environment_id
            ]
            selected.sort(key=lambda item: (item.updated_at, item.release_id), reverse=True)
            return tuple(selected[:limit])

    def events(self, environment_id: str, release_id: str) -> tuple[ReleaseEventRecord, ...]:
        return tuple(self._events.get((environment_id, release_id), ()))

    def approve(
        self,
        environment_id: str,
        release_id: str,
        *,
        expected_state_version: int,
        actor_id: str,
        reason: str,
        request_id: str,
        occurred_at: datetime,
    ) -> ReleaseRunRecord:
        key = (environment_id, release_id)
        with self._lock:
            current = self.get(environment_id, release_id)
            _validate_approval(
                current, expected_state_version=expected_state_version, actor_id=actor_id
            )
            updated = current.model_copy(
                update={
                    "state": "APPROVED",
                    "state_version": current.state_version + 1,
                    "approved_by": actor_id,
                    "approval_reason": reason,
                    "updated_at": occurred_at,
                }
            )
            self._runs[key] = updated
            self._events[key].append(
                ReleaseEventRecord(
                    state_version=updated.state_version,
                    event_kind="MANUAL_APPROVAL_GRANTED",
                    state="APPROVED",
                    actor_id=actor_id,
                    reason_code="DISTINCT_RELEASE_OPERATOR_APPROVED",
                    request_id=request_id,
                    occurred_at=occurred_at,
                )
            )
            return updated

    def transition(
        self,
        environment_id: str,
        release_id: str,
        *,
        expected_state_version: int,
        next_state: ReleaseState,
        actor_id: str,
        reason_code: str,
        request_id: str,
        occurred_at: datetime,
    ) -> ReleaseRunRecord:
        key = (environment_id, release_id)
        with self._lock:
            current = self.get(environment_id, release_id)
            _validate_transition(
                current,
                expected_state_version=expected_state_version,
                next_state=next_state,
            )
            updated = current.model_copy(
                update={
                    "state": next_state,
                    "state_version": current.state_version + 1,
                    "updated_at": occurred_at,
                }
            )
            self._runs[key] = updated
            self._events[key].append(
                ReleaseEventRecord(
                    state_version=updated.state_version,
                    event_kind="CONTROLLER_STATE_RECORDED",
                    state=next_state,
                    actor_id=actor_id,
                    reason_code=reason_code,
                    request_id=request_id,
                    occurred_at=occurred_at,
                )
            )
            return updated


def _validate_approval(
    current: ReleaseRunRecord, *, expected_state_version: int, actor_id: str
) -> None:
    if current.state != "AWAITING_APPROVAL":
        raise ReleaseOperationError("RELEASE_NOT_AWAITING_APPROVAL", "release cannot be approved")
    if current.state_version != expected_state_version:
        raise ReleaseOperationError("RELEASE_STATE_CONFLICT", "release state version is stale")
    if current.requested_by == actor_id:
        raise ReleaseOperationError(
            "RELEASE_DISTINCT_APPROVER_REQUIRED",
            "the release requester cannot approve their own release",
        )


_ALLOWED_TRANSITIONS: dict[ReleaseState, frozenset[ReleaseState]] = {
    "AWAITING_APPROVAL": frozenset({"FAILED"}),
    "APPROVED": frozenset({"EXPAND", "FAILED"}),
    "EXPAND": frozenset({"CANARY", "FAILED"}),
    "CANARY": frozenset({"ROLLOUT", "ROLLED_BACK", "FAILED"}),
    "ROLLOUT": frozenset({"CONTRACT_PENDING", "COMPLETED", "ROLLED_BACK", "FAILED"}),
    "CONTRACT_PENDING": frozenset({"COMPLETED", "FAILED"}),
    "PREFLIGHT_BLOCKED": frozenset(),
    "COMPLETED": frozenset(),
    "ROLLED_BACK": frozenset(),
    "FAILED": frozenset(),
}


def _validate_transition(
    current: ReleaseRunRecord,
    *,
    expected_state_version: int,
    next_state: ReleaseState,
) -> None:
    if current.state_version != expected_state_version:
        raise ReleaseOperationError("RELEASE_STATE_CONFLICT", "release state version is stale")
    if next_state not in _ALLOWED_TRANSITIONS[current.state]:
        raise ReleaseOperationError(
            "RELEASE_TRANSITION_INVALID", "release controller transition is not allowed"
        )


class ReleaseService:
    def __init__(
        self,
        repository: ReleaseRepository,
        *,
        environment_id: str,
        current_version: str,
        instance_service: PlatformInstanceService,
        overview_service: PlatformOperationsOverviewService,
        reference_secret: str,
        verifier: ReleaseFeedVerifier | None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repository = repository
        self._environment_id = environment_id
        self._current_version = current_version
        self._instance_service = instance_service
        self._overview_service = overview_service
        self._reference_key = reference_secret.encode()
        self._verifier = verifier
        self._clock = clock

    def preflight(
        self, body: ReleasePreflightRequest, *, actor_id: str, request_id: str
    ) -> ReleaseRun:
        if self._verifier is None:
            raise ReleaseOperationError(
                "RELEASE_FEED_TRUST_NOT_CONFIGURED", "no release-feed signing key is pinned"
            )
        try:
            verified = self._verifier.verify(
                body.signed_feed,
                minimum_sequence=body.minimum_feed_sequence,
                current_version=self._current_version,
            )
        except (ReleaseFeedError, ValueError) as exc:
            code = exc.code if isinstance(exc, ReleaseFeedError) else "RELEASE_FEED_INVALID"
            raise ReleaseOperationError(code, "release feed verification failed") from exc
        candidate = next(
            (
                item
                for item in verified.feed.candidates
                if item.manifest.release_id == body.target_release_id
            ),
            None,
        )
        if candidate is None:
            raise ReleaseOperationError(
                "RELEASE_CANDIDATE_NOT_FOUND", "target release is absent from the verified feed"
            )
        if self._overview_service.overview().upgrade_preflight.status != "READY":
            raise ReleaseOperationError(
                "RELEASE_PLATFORM_PREFLIGHT_BLOCKED", "current platform preflight is blocked"
            )
        self._verify_source_images(body.source_images)
        now = self._clock()
        record = ReleaseRunRecord(
            environment_id=self._environment_id,
            release_id=candidate.manifest.release_id,
            source_version=self._current_version,
            target_version=candidate.manifest.semantic_version,
            manifest_sha256=candidate.manifest_sha256,
            source_images=body.source_images,
            target_images=candidate.manifest.images,
            state="AWAITING_APPROVAL",
            state_version=1,
            requested_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        stored = self._repository.create(
            record,
            ReleaseEventRecord(
                state_version=1,
                event_kind="PREFLIGHT_PASSED",
                state="AWAITING_APPROVAL",
                actor_id=actor_id,
                reason_code="SIGNED_COMPATIBLE_RELEASE_VERIFIED",
                request_id=request_id,
                occurred_at=now,
            ),
        )
        return self._project(stored)

    def approve(
        self,
        release_id: str,
        body: ReleaseApprovalRequest,
        *,
        actor_id: str,
        request_id: str,
    ) -> ReleaseRun:
        stored = self._repository.approve(
            self._environment_id,
            release_id,
            expected_state_version=body.expected_state_version,
            actor_id=actor_id,
            reason=body.reason,
            request_id=request_id,
            occurred_at=self._clock(),
        )
        return self._project(stored)

    def history(self, *, limit: int = 20) -> ReleaseHistoryPage:
        records = self._repository.list(self._environment_id, limit=limit)
        return ReleaseHistoryPage(
            count=len(records), items=tuple(self._project(item) for item in records)
        )

    def transition(
        self,
        release_id: str,
        body: ReleaseTransitionRequest,
        *,
        actor_id: str,
        request_id: str,
    ) -> ReleaseRun:
        stored = self._repository.transition(
            self._environment_id,
            release_id,
            expected_state_version=body.expected_state_version,
            next_state=body.next_state,
            actor_id=actor_id,
            reason_code=body.reason_code,
            request_id=request_id,
            occurred_at=self._clock(),
        )
        return self._project(stored)

    def _verify_source_images(self, images: ReleaseImagesV1) -> None:
        active = tuple(
            item for item in self._instance_service.list_instances().instances if not item.stale
        )
        expected: dict[InstanceRole, str] = {
            "frontend": images.frontend.rsplit("@", 1)[1],
            "api": images.api.rsplit("@", 1)[1],
            "worker": images.worker.rsplit("@", 1)[1],
            "media-worker": images.media_worker.rsplit("@", 1)[1],
        }
        observed_roles = {item.role for item in active}
        if not set(expected) <= observed_roles:
            raise ReleaseOperationError(
                "RELEASE_SOURCE_COMPONENTS_INCOMPLETE",
                "source component observations are incomplete",
            )
        if any(
            item.role in expected and item.component_image_digest != expected[item.role]
            for item in active
        ):
            raise ReleaseOperationError(
                "RELEASE_SOURCE_DIGEST_MISMATCH", "source images differ from active instances"
            )

    def _project(self, record: ReleaseRunRecord) -> ReleaseRun:
        events = tuple(
            ReleaseEvent(
                state_version=event.state_version,
                event_kind=event.event_kind,
                state=event.state,
                actor_ref=self._reference(event.actor_id),
                reason_code=event.reason_code,
                occurred_at=event.occurred_at,
            )
            for event in self._repository.events(record.environment_id, record.release_id)
        )
        return ReleaseRun(
            release_id=record.release_id,
            source_version=record.source_version,
            target_version=record.target_version,
            manifest_sha256=record.manifest_sha256,
            state=record.state,
            state_version=record.state_version,
            approved=record.approved_by is not None,
            created_at=record.created_at,
            updated_at=record.updated_at,
            events=events,
        )

    def _reference(self, value: str) -> str:
        return (
            "id-hmac-sha256:"
            + hmac.new(self._reference_key, value.encode(), hashlib.sha256).hexdigest()
        )


def release_feed_verifier_from_encoded_keys(keys: Sequence[str]) -> ReleaseFeedVerifier | None:
    trusted: dict[str, Ed25519PublicKey] = {}
    for encoded in keys:
        try:
            raw = base64.urlsafe_b64decode(encoded + "==")
            key = Ed25519PublicKey.from_public_bytes(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("release-feed public keys must be raw Ed25519 base64url") from exc
        trusted[public_key_sha256(key)] = key
    return ReleaseFeedVerifier(trusted) if trusted else None


class PostgresReleaseRepository:
    """Production adapter; transitions and events commit in one DB transaction."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(cls, dsn: str) -> PostgresReleaseRepository:
        normalized = normalize_postgres_dsn(dsn)

        def connect() -> Any:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(normalized, row_factory=dict_row)

        return cls(connect)

    def create(self, record: ReleaseRunRecord, event: ReleaseEventRecord) -> ReleaseRunRecord:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO platform.platform_release_runs (
                    environment_id, release_id, source_version, target_version,
                    manifest_sha256, source_images_json, target_images_json, state,
                    state_version, requested_by, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s)
                ON CONFLICT (environment_id, release_id) DO NOTHING
                """,
                (
                    record.environment_id,
                    record.release_id,
                    record.source_version,
                    record.target_version,
                    record.manifest_sha256,
                    record.source_images.model_dump_json(),
                    record.target_images.model_dump_json(),
                    record.state,
                    record.state_version,
                    record.requested_by,
                    record.created_at,
                    record.updated_at,
                ),
            )
            if cursor.rowcount == 1:
                self._insert_event(cursor, record, event)
                return record
        existing = self.get(record.environment_id, record.release_id)
        if existing.manifest_sha256 != record.manifest_sha256:
            raise ReleaseOperationError(
                "RELEASE_ID_CONFLICT", "release ID is already bound to another manifest"
            )
        return existing

    def get(self, environment_id: str, release_id: str) -> ReleaseRunRecord:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM platform.platform_release_runs
                WHERE environment_id = %s AND release_id = %s
                """,
                (environment_id, release_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ReleaseOperationError("RELEASE_NOT_FOUND", "release run was not found")
        return _release_record(row)

    def list(self, environment_id: str, *, limit: int) -> tuple[ReleaseRunRecord, ...]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM platform.platform_release_runs
                WHERE environment_id = %s
                ORDER BY updated_at DESC, release_id DESC LIMIT %s
                """,
                (environment_id, limit),
            )
            rows = cursor.fetchall()
        return tuple(_release_record(row) for row in rows)

    def events(self, environment_id: str, release_id: str) -> tuple[ReleaseEventRecord, ...]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT state_version, event_kind, state, actor_id, reason_code,
                       request_id, occurred_at
                FROM platform.platform_release_events
                WHERE environment_id = %s AND release_id = %s
                ORDER BY state_version
                """,
                (environment_id, release_id),
            )
            rows = cursor.fetchall()
        return tuple(ReleaseEventRecord.model_validate(row) for row in rows)

    def approve(
        self,
        environment_id: str,
        release_id: str,
        *,
        expected_state_version: int,
        actor_id: str,
        reason: str,
        request_id: str,
        occurred_at: datetime,
    ) -> ReleaseRunRecord:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM platform.platform_release_runs
                WHERE environment_id = %s AND release_id = %s FOR UPDATE
                """,
                (environment_id, release_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ReleaseOperationError("RELEASE_NOT_FOUND", "release run was not found")
            current = _release_record(row)
            _validate_approval(
                current, expected_state_version=expected_state_version, actor_id=actor_id
            )
            updated = current.model_copy(
                update={
                    "state": "APPROVED",
                    "state_version": current.state_version + 1,
                    "approved_by": actor_id,
                    "approval_reason": reason,
                    "updated_at": occurred_at,
                }
            )
            cursor.execute(
                """
                UPDATE platform.platform_release_runs
                SET state = 'APPROVED', state_version = %s, approved_by = %s,
                    approval_reason = %s, updated_at = %s
                WHERE environment_id = %s AND release_id = %s
                """,
                (
                    updated.state_version,
                    actor_id,
                    reason,
                    occurred_at,
                    environment_id,
                    release_id,
                ),
            )
            self._insert_event(
                cursor,
                updated,
                ReleaseEventRecord(
                    state_version=updated.state_version,
                    event_kind="MANUAL_APPROVAL_GRANTED",
                    state="APPROVED",
                    actor_id=actor_id,
                    reason_code="DISTINCT_RELEASE_OPERATOR_APPROVED",
                    request_id=request_id,
                    occurred_at=occurred_at,
                ),
            )
        return updated

    def transition(
        self,
        environment_id: str,
        release_id: str,
        *,
        expected_state_version: int,
        next_state: ReleaseState,
        actor_id: str,
        reason_code: str,
        request_id: str,
        occurred_at: datetime,
    ) -> ReleaseRunRecord:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM platform.platform_release_runs
                WHERE environment_id = %s AND release_id = %s FOR UPDATE
                """,
                (environment_id, release_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ReleaseOperationError("RELEASE_NOT_FOUND", "release run was not found")
            current = _release_record(row)
            _validate_transition(
                current,
                expected_state_version=expected_state_version,
                next_state=next_state,
            )
            updated = current.model_copy(
                update={
                    "state": next_state,
                    "state_version": current.state_version + 1,
                    "updated_at": occurred_at,
                }
            )
            cursor.execute(
                """
                UPDATE platform.platform_release_runs
                SET state = %s, state_version = %s, updated_at = %s
                WHERE environment_id = %s AND release_id = %s
                """,
                (
                    next_state,
                    updated.state_version,
                    occurred_at,
                    environment_id,
                    release_id,
                ),
            )
            self._insert_event(
                cursor,
                updated,
                ReleaseEventRecord(
                    state_version=updated.state_version,
                    event_kind="CONTROLLER_STATE_RECORDED",
                    state=next_state,
                    actor_id=actor_id,
                    reason_code=reason_code,
                    request_id=request_id,
                    occurred_at=occurred_at,
                ),
            )
        return updated

    @staticmethod
    def _insert_event(cursor: Any, record: ReleaseRunRecord, event: ReleaseEventRecord) -> None:
        cursor.execute(
            """
            INSERT INTO platform.platform_release_events (
                environment_id, release_id, state_version, event_kind, state,
                actor_id, reason_code, request_id, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.environment_id,
                record.release_id,
                event.state_version,
                event.event_kind,
                event.state,
                event.actor_id,
                event.reason_code,
                event.request_id,
                event.occurred_at,
            ),
        )


def _release_record(row: Any) -> ReleaseRunRecord:
    return ReleaseRunRecord(
        environment_id=str(row["environment_id"]),
        release_id=str(row["release_id"]),
        source_version=str(row["source_version"]),
        target_version=str(row["target_version"]),
        manifest_sha256=str(row["manifest_sha256"]),
        source_images=ReleaseImagesV1.model_validate(row["source_images_json"]),
        target_images=ReleaseImagesV1.model_validate(row["target_images_json"]),
        state=str(row["state"]),
        state_version=int(row["state_version"]),
        requested_by=str(row["requested_by"]),
        approved_by=str(row["approved_by"]) if row["approved_by"] is not None else None,
        approval_reason=(
            str(row["approval_reason"]) if row["approval_reason"] is not None else None
        ),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


__all__ = [
    "InMemoryReleaseRepository",
    "PostgresReleaseRepository",
    "ReleaseApprovalRequest",
    "ReleaseHistoryPage",
    "ReleaseOperationError",
    "ReleasePreflightRequest",
    "ReleaseRun",
    "ReleaseService",
    "ReleaseTransitionRequest",
    "release_feed_verifier_from_encoded_keys",
]
