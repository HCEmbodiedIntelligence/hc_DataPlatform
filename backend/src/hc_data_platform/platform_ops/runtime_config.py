"""Allowlisted, revisioned runtime configuration with bounded convergence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import datetime, timezone
from threading import RLock
from typing import Annotated, Any, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)

from hc_data_platform.core.dbapi import normalize_postgres_dsn

from .maintenance import EnvironmentId, SafeActorId

RUNTIME_CONFIG_SCHEMA_VERSION: Literal["hc-runtime-config/v1"] = "hc-runtime-config/v1"
RUNTIME_CONFIG_NOTIFICATION_CHANNEL = "platform_runtime_config"
# Leave one second of the 30-second convergence budget for the authoritative
# PostgreSQL read and event-loop scheduling after a lost notification.
RUNTIME_CONFIG_POLL_INTERVAL_SECONDS = 29.0
RUNTIME_CONFIG_ALLOWLIST = (
    "scheduling.media_maintenance_interval_seconds",
    "scheduling.storage_inventory_interval_seconds",
    "ui.maintenance_banner_enabled",
)
RuntimeConfigReason = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=8, max_length=500),
]

logger = logging.getLogger(__name__)

_SENSITIVE_KEY = re.compile(
    r"(?i)(?:password|passwd|secret|token|credential|dsn|private[_-]?key|access[_-]?key|"
    r"client[_-]?key|tls|certificate|cert|image|migration|schema)"
)
_SENSITIVE_REASON = re.compile(
    r"(?i)(?:password|passwd|secret|token|credential|dsn|private[_ -]?key|"
    r"postgres(?:ql)?://|https?://|-----BEGIN)"
)


class RuntimeConfigError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RuntimeConfigValues(BaseModel):
    """The complete effective snapshot; aliases are the only external keys."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=False)

    media_maintenance_interval_seconds: Annotated[int, Field(strict=True, ge=10, le=86_400)] = (
        Field(default=300, alias="scheduling.media_maintenance_interval_seconds")
    )
    storage_inventory_interval_seconds: Annotated[int, Field(strict=True, ge=10, le=86_400)] = (
        Field(default=3_600, alias="scheduling.storage_inventory_interval_seconds")
    )
    maintenance_banner_enabled: Annotated[bool, Field(strict=True)] = Field(
        default=False,
        alias="ui.maintenance_banner_enabled",
    )

    def external(self) -> dict[str, bool | int]:
        return self.model_dump(by_alias=True)


def runtime_config_boot_values(
    *,
    media_maintenance_interval_seconds: float,
    storage_inventory_interval_seconds: float,
) -> RuntimeConfigValues:
    if (
        not float(media_maintenance_interval_seconds).is_integer()
        or not float(storage_inventory_interval_seconds).is_integer()
    ):
        raise ValueError("hot-reloadable scheduling intervals must be whole seconds")
    return RuntimeConfigValues.model_validate(
        {
            "scheduling.media_maintenance_interval_seconds": int(
                media_maintenance_interval_seconds
            ),
            "scheduling.storage_inventory_interval_seconds": int(
                storage_inventory_interval_seconds
            ),
            "ui.maintenance_banner_enabled": False,
        }
    )


class RuntimeConfigSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal["hc-runtime-config-snapshot/v1"] = "hc-runtime-config-snapshot/v1"
    environment_id: EnvironmentId
    revision: int = Field(ge=0)
    schema_version: Literal["hc-runtime-config/v1"] = RUNTIME_CONFIG_SCHEMA_VERSION
    values: dict[str, bool | int]
    content_sha256: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    actor_id: SafeActorId | None = None
    reason: RuntimeConfigReason | None = None
    request_id: SafeActorId | None = None
    rollback_of_revision: int | None = Field(default=None, ge=0)
    created_at: datetime | None = None


class RuntimeConfigHistoryPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal["hc-runtime-config-history/v1"] = "hc-runtime-config-history/v1"
    count: int = Field(ge=0)
    revisions: tuple[RuntimeConfigSnapshot, ...]


class RuntimeConfigPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    schema_version: Literal["hc-runtime-config/v1"]
    values: dict[str, object] = Field(min_length=1, max_length=len(RUNTIME_CONFIG_ALLOWLIST))
    reason: RuntimeConfigReason


class RuntimeConfigRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    reason: RuntimeConfigReason


def _safe_reason(reason: str) -> str:
    normalized = reason.strip()
    if _SENSITIVE_REASON.search(normalized):
        raise RuntimeConfigError(
            "PLATFORM_RUNTIME_CONFIG_SENSITIVE_METADATA",
            "runtime configuration metadata contains a prohibited sensitive marker",
        )
    return normalized


def _validate_patch(
    current: Mapping[str, bool | int], patch: Mapping[str, object]
) -> RuntimeConfigValues:
    keys = tuple(patch)
    if any(_SENSITIVE_KEY.search(key) for key in keys):
        raise RuntimeConfigError(
            "PLATFORM_RUNTIME_CONFIG_FORBIDDEN_KEY",
            "release-owned or sensitive configuration cannot be hot reloaded",
        )
    unknown = sorted(set(keys) - set(RUNTIME_CONFIG_ALLOWLIST))
    if unknown:
        raise RuntimeConfigError(
            "PLATFORM_RUNTIME_CONFIG_KEY_NOT_ALLOWLISTED",
            "runtime configuration contains a key outside the allowlist",
        )
    merged: dict[str, object] = dict(current)
    merged.update(patch)
    try:
        return RuntimeConfigValues.model_validate(merged)
    except ValidationError as exc:
        raise RuntimeConfigError(
            "PLATFORM_RUNTIME_CONFIG_VALUE_INVALID",
            "runtime configuration contains an invalid value type or range",
        ) from exc


def _canonical_content(values: Mapping[str, bool | int]) -> bytes:
    return json.dumps(
        {"schema_version": RUNTIME_CONFIG_SCHEMA_VERSION, "values": values},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def runtime_config_digest(values: Mapping[str, bool | int]) -> str:
    return f"sha256:{hashlib.sha256(_canonical_content(values)).hexdigest()}"


def baseline_runtime_config(
    environment_id: str,
    baseline_values: RuntimeConfigValues | None = None,
) -> RuntimeConfigSnapshot:
    values = (baseline_values or RuntimeConfigValues()).external()
    return RuntimeConfigSnapshot(
        environment_id=environment_id,
        revision=0,
        values=values,
        content_sha256=runtime_config_digest(values),
    )


class RuntimeConfigRepository(Protocol):
    def current(self, environment_id: str) -> RuntimeConfigSnapshot: ...

    def publish(
        self,
        environment_id: str,
        *,
        expected_revision: int,
        schema_version: str,
        patch: Mapping[str, object],
        actor_id: str,
        reason: str,
        request_id: str,
    ) -> RuntimeConfigSnapshot: ...

    def rollback(
        self,
        environment_id: str,
        *,
        target_revision: int,
        expected_revision: int,
        actor_id: str,
        reason: str,
        request_id: str,
    ) -> RuntimeConfigSnapshot: ...

    def history(self, environment_id: str, *, limit: int) -> RuntimeConfigHistoryPage: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryRuntimeConfigRepository:
    def __init__(
        self,
        *,
        baseline_values: RuntimeConfigValues | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._clock = clock
        self._baseline_values = baseline_values or RuntimeConfigValues()
        self._revisions: dict[str, list[RuntimeConfigSnapshot]] = {}
        self._lock = RLock()

    def current(self, environment_id: str) -> RuntimeConfigSnapshot:
        with self._lock:
            revisions = self._revisions.get(environment_id, [])
            return (
                revisions[-1]
                if revisions
                else baseline_runtime_config(environment_id, self._baseline_values)
            )

    def publish(
        self,
        environment_id: str,
        *,
        expected_revision: int,
        schema_version: str,
        patch: Mapping[str, object],
        actor_id: str,
        reason: str,
        request_id: str,
    ) -> RuntimeConfigSnapshot:
        if schema_version != RUNTIME_CONFIG_SCHEMA_VERSION:
            raise RuntimeConfigError(
                "PLATFORM_RUNTIME_CONFIG_SCHEMA_UNSUPPORTED",
                "runtime configuration schema is not supported",
            )
        with self._lock:
            current = self.current(environment_id)
            if current.revision != expected_revision:
                raise RuntimeConfigError(
                    "PLATFORM_RUNTIME_CONFIG_REVISION_CONFLICT",
                    "runtime configuration expected revision is stale",
                )
            values = _validate_patch(current.values, patch).external()
            snapshot = RuntimeConfigSnapshot(
                environment_id=environment_id,
                revision=current.revision + 1,
                values=values,
                content_sha256=runtime_config_digest(values),
                actor_id=actor_id,
                reason=_safe_reason(reason),
                request_id=request_id,
                created_at=self._clock(),
            )
            self._revisions.setdefault(environment_id, []).append(snapshot)
            return snapshot

    def rollback(
        self,
        environment_id: str,
        *,
        target_revision: int,
        expected_revision: int,
        actor_id: str,
        reason: str,
        request_id: str,
    ) -> RuntimeConfigSnapshot:
        with self._lock:
            current = self.current(environment_id)
            if current.revision != expected_revision:
                raise RuntimeConfigError(
                    "PLATFORM_RUNTIME_CONFIG_REVISION_CONFLICT",
                    "runtime configuration expected revision is stale",
                )
            target: RuntimeConfigSnapshot | None
            if target_revision == 0:
                target = baseline_runtime_config(environment_id, self._baseline_values)
            else:
                target = next(
                    (
                        item
                        for item in self._revisions.get(environment_id, [])
                        if item.revision == target_revision
                    ),
                    None,
                )
                if target is None:
                    raise RuntimeConfigError(
                        "PLATFORM_RUNTIME_CONFIG_REVISION_NOT_FOUND",
                        "runtime configuration target revision does not exist",
                    )
            snapshot = RuntimeConfigSnapshot(
                environment_id=environment_id,
                revision=current.revision + 1,
                values=dict(target.values),
                content_sha256=runtime_config_digest(target.values),
                actor_id=actor_id,
                reason=_safe_reason(reason),
                request_id=request_id,
                rollback_of_revision=target_revision,
                created_at=self._clock(),
            )
            self._revisions.setdefault(environment_id, []).append(snapshot)
            return snapshot

    def history(self, environment_id: str, *, limit: int) -> RuntimeConfigHistoryPage:
        with self._lock:
            revisions = tuple(reversed(self._revisions.get(environment_id, [])[-limit:]))
            return RuntimeConfigHistoryPage(count=len(revisions), revisions=revisions)


class DbApiCursor(Protocol):
    description: Sequence[Sequence[object] | object] | None

    def execute(self, query: str, params: Sequence[object] | None = None) -> object: ...

    def fetchone(self) -> Mapping[str, Any] | None: ...

    def fetchall(self) -> Sequence[Mapping[str, Any]]: ...


class DbApiConnection(Protocol):
    def __enter__(self) -> DbApiConnection: ...

    def __exit__(self, *args: object) -> None: ...

    def cursor(self) -> DbApiCursor: ...


def _snapshot_from_row(row: Mapping[str, Any]) -> RuntimeConfigSnapshot:
    values = RuntimeConfigValues.model_validate(row["values_json"]).external()
    digest = runtime_config_digest(values)
    stored_digest = f"sha256:{row['content_sha256']}"
    if digest != stored_digest:
        raise RuntimeConfigError(
            "PLATFORM_RUNTIME_CONFIG_DIGEST_MISMATCH",
            "runtime configuration revision content digest does not match",
        )
    return RuntimeConfigSnapshot(
        environment_id=row["environment_id"],
        revision=row["revision"],
        schema_version=row["schema_version"],
        values=values,
        content_sha256=digest,
        actor_id=row["actor_id"],
        reason=row["reason"],
        request_id=row["request_id"],
        rollback_of_revision=row["rollback_of_revision"],
        created_at=row["created_at"],
    )


class PostgresRuntimeConfigRepository:
    def __init__(
        self,
        connection_factory: Callable[[], DbApiConnection],
        *,
        baseline_values: RuntimeConfigValues | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._baseline_values = baseline_values or RuntimeConfigValues()

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        baseline_values: RuntimeConfigValues | None = None,
    ) -> PostgresRuntimeConfigRepository:
        normalized = normalize_postgres_dsn(dsn)

        def connect() -> DbApiConnection:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(normalized, row_factory=dict_row)  # type: ignore[return-value]

        return cls(connect, baseline_values=baseline_values)

    def current(self, environment_id: str) -> RuntimeConfigSnapshot:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT revision.environment_id, revision.revision,
                       revision.schema_version, revision.values_json,
                       revision.content_sha256, revision.actor_id, revision.reason,
                       revision.request_id, revision.rollback_of_revision,
                       revision.created_at
                FROM platform.platform_runtime_config_heads AS head
                JOIN platform.platform_runtime_config_revisions AS revision
                  ON revision.environment_id = head.environment_id
                 AND revision.revision = head.current_revision
                WHERE head.environment_id = %s
                """,
                (environment_id,),
            )
            row = cursor.fetchone()
        return (
            baseline_runtime_config(environment_id, self._baseline_values)
            if row is None
            else _snapshot_from_row(row)
        )

    def publish(
        self,
        environment_id: str,
        *,
        expected_revision: int,
        schema_version: str,
        patch: Mapping[str, object],
        actor_id: str,
        reason: str,
        request_id: str,
    ) -> RuntimeConfigSnapshot:
        if schema_version != RUNTIME_CONFIG_SCHEMA_VERSION:
            raise RuntimeConfigError(
                "PLATFORM_RUNTIME_CONFIG_SCHEMA_UNSUPPORTED",
                "runtime configuration schema is not supported",
            )
        return self._write_revision(
            environment_id,
            expected_revision=expected_revision,
            patch=patch,
            target_revision=None,
            actor_id=actor_id,
            reason=reason,
            request_id=request_id,
        )

    def rollback(
        self,
        environment_id: str,
        *,
        target_revision: int,
        expected_revision: int,
        actor_id: str,
        reason: str,
        request_id: str,
    ) -> RuntimeConfigSnapshot:
        return self._write_revision(
            environment_id,
            expected_revision=expected_revision,
            patch=None,
            target_revision=target_revision,
            actor_id=actor_id,
            reason=reason,
            request_id=request_id,
        )

    def _write_revision(
        self,
        environment_id: str,
        *,
        expected_revision: int,
        patch: Mapping[str, object] | None,
        target_revision: int | None,
        actor_id: str,
        reason: str,
        request_id: str,
    ) -> RuntimeConfigSnapshot:
        from psycopg.types.json import Jsonb

        safe_reason = _safe_reason(reason)
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO platform.platform_runtime_config_heads (environment_id)
                VALUES (%s)
                ON CONFLICT (environment_id) DO NOTHING
                """,
                (environment_id,),
            )
            cursor.execute(
                """
                SELECT current_revision
                FROM platform.platform_runtime_config_heads
                WHERE environment_id = %s
                FOR UPDATE
                """,
                (environment_id,),
            )
            head = cursor.fetchone()
            if head is None:
                raise RuntimeError("runtime configuration head was not created")
            current_revision = int(head["current_revision"])
            if current_revision != expected_revision:
                raise RuntimeConfigError(
                    "PLATFORM_RUNTIME_CONFIG_REVISION_CONFLICT",
                    "runtime configuration expected revision is stale",
                )
            current_values = self._locked_values(cursor, environment_id, current_revision)
            if patch is not None:
                values = _validate_patch(current_values, patch).external()
                event_kind = "APPLY"
            else:
                if target_revision is None or target_revision < 0:
                    raise RuntimeConfigError(
                        "PLATFORM_RUNTIME_CONFIG_REVISION_NOT_FOUND",
                        "runtime configuration target revision does not exist",
                    )
                values = self._locked_values(cursor, environment_id, target_revision)
                event_kind = "ROLLBACK"
            revision = current_revision + 1
            digest = runtime_config_digest(values)
            cursor.execute(
                """
                INSERT INTO platform.platform_runtime_config_revisions (
                    environment_id, revision, schema_version, values_json,
                    content_sha256, actor_id, reason, request_id, rollback_of_revision
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    environment_id,
                    revision,
                    RUNTIME_CONFIG_SCHEMA_VERSION,
                    Jsonb(values),
                    digest.removeprefix("sha256:"),
                    actor_id,
                    safe_reason,
                    request_id,
                    target_revision,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("runtime configuration revision returned no row")
            cursor.execute(
                """
                INSERT INTO platform.platform_runtime_config_events (
                    environment_id, event_kind, previous_revision, revision,
                    target_revision, actor_id, reason, request_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    environment_id,
                    event_kind,
                    current_revision,
                    revision,
                    target_revision,
                    actor_id,
                    safe_reason,
                    request_id,
                ),
            )
            cursor.execute(
                """
                UPDATE platform.platform_runtime_config_heads
                SET current_revision = %s, updated_at = statement_timestamp()
                WHERE environment_id = %s
                """,
                (revision, environment_id),
            )
            cursor.execute(
                "SELECT pg_notify(%s, %s)",
                (RUNTIME_CONFIG_NOTIFICATION_CHANNEL, f"{environment_id}:{revision}"),
            )
        return _snapshot_from_row(row)

    def _locked_values(
        self, cursor: DbApiCursor, environment_id: str, revision: int
    ) -> dict[str, bool | int]:
        if revision == 0:
            return self._baseline_values.external()
        cursor.execute(
            """
            SELECT values_json
            FROM platform.platform_runtime_config_revisions
            WHERE environment_id = %s AND revision = %s
            """,
            (environment_id, revision),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeConfigError(
                "PLATFORM_RUNTIME_CONFIG_REVISION_NOT_FOUND",
                "runtime configuration target revision does not exist",
            )
        return RuntimeConfigValues.model_validate(row["values_json"]).external()

    def history(self, environment_id: str, *, limit: int) -> RuntimeConfigHistoryPage:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT environment_id, revision, schema_version, values_json,
                       content_sha256, actor_id, reason, request_id,
                       rollback_of_revision, created_at
                FROM platform.platform_runtime_config_revisions
                WHERE environment_id = %s
                ORDER BY revision DESC
                LIMIT %s
                """,
                (environment_id, limit),
            )
            revisions = tuple(_snapshot_from_row(row) for row in cursor.fetchall())
        return RuntimeConfigHistoryPage(count=len(revisions), revisions=revisions)


class RuntimeConfigState:
    def __init__(
        self,
        environment_id: str,
        *,
        baseline_values: RuntimeConfigValues | None = None,
    ) -> None:
        self._snapshot = baseline_runtime_config(environment_id, baseline_values)
        self._lock = RLock()

    def current(self) -> RuntimeConfigSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def revision(self) -> int:
        return self.current().revision

    def value(self, key: str) -> bool | int:
        if key not in RUNTIME_CONFIG_ALLOWLIST:
            raise KeyError(key)
        return self.current().values[key]

    def apply(self, snapshot: RuntimeConfigSnapshot) -> bool:
        validated = RuntimeConfigValues.model_validate(snapshot.values).external()
        if runtime_config_digest(validated) != snapshot.content_sha256:
            raise RuntimeConfigError(
                "PLATFORM_RUNTIME_CONFIG_DIGEST_MISMATCH",
                "runtime configuration revision content digest does not match",
            )
        with self._lock:
            if snapshot.environment_id != self._snapshot.environment_id:
                raise RuntimeConfigError(
                    "PLATFORM_RUNTIME_CONFIG_ENVIRONMENT_MISMATCH",
                    "runtime configuration snapshot belongs to another environment",
                )
            if snapshot.revision < self._snapshot.revision:
                return False
            if snapshot.revision == self._snapshot.revision:
                if snapshot.content_sha256 != self._snapshot.content_sha256:
                    raise RuntimeConfigError(
                        "PLATFORM_RUNTIME_CONFIG_REVISION_COLLISION",
                        "runtime configuration revision content changed",
                    )
                return False
            self._snapshot = snapshot
            return True


class RuntimeConfigService:
    def __init__(
        self,
        repository: RuntimeConfigRepository,
        state: RuntimeConfigState,
        environment_id: str,
    ) -> None:
        self.repository = repository
        self.state = state
        self.environment_id = environment_id

    def current(self) -> RuntimeConfigSnapshot:
        return self.state.current()

    def refresh(self) -> RuntimeConfigSnapshot:
        snapshot = self.repository.current(self.environment_id)
        self.state.apply(snapshot)
        return self.state.current()

    def publish(
        self,
        *,
        expected_revision: int,
        schema_version: str,
        patch: Mapping[str, object],
        actor_id: str,
        reason: str,
        request_id: str,
    ) -> RuntimeConfigSnapshot:
        snapshot = self.repository.publish(
            self.environment_id,
            expected_revision=expected_revision,
            schema_version=schema_version,
            patch=patch,
            actor_id=actor_id,
            reason=reason,
            request_id=request_id,
        )
        self.state.apply(snapshot)
        return snapshot

    def rollback(
        self,
        *,
        target_revision: int,
        expected_revision: int,
        actor_id: str,
        reason: str,
        request_id: str,
    ) -> RuntimeConfigSnapshot:
        snapshot = self.repository.rollback(
            self.environment_id,
            target_revision=target_revision,
            expected_revision=expected_revision,
            actor_id=actor_id,
            reason=reason,
            request_id=request_id,
        )
        self.state.apply(snapshot)
        return snapshot

    def history(self, *, limit: int) -> RuntimeConfigHistoryPage:
        return self.repository.history(self.environment_id, limit=limit)


class RuntimeConfigSubscriber(Protocol):
    async def start(self) -> None: ...

    async def wait(self, timeout_seconds: float) -> bool: ...

    async def close(self) -> None: ...


class PollingRuntimeConfigSubscriber:
    async def start(self) -> None:
        return None

    async def wait(self, timeout_seconds: float) -> bool:
        await asyncio.sleep(timeout_seconds)
        return False

    async def close(self) -> None:
        return None


class PostgresRuntimeConfigSubscriber:
    def __init__(self, dsn: str, environment_id: str) -> None:
        self._dsn = normalize_postgres_dsn(dsn)
        self._environment_id = environment_id
        self._queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self._connection: Any | None = None

    async def start(self) -> None:
        await self._ensure_connection()

    async def _ensure_connection(self) -> None:
        if self._connection is not None and not self._connection.is_closed():
            return
        import asyncpg

        self._connection = await asyncpg.connect(self._dsn)
        await self._connection.add_listener(
            RUNTIME_CONFIG_NOTIFICATION_CHANNEL,
            self._on_notification,
        )

    def _on_notification(
        self,
        _connection: object,
        _pid: int,
        _channel: str,
        payload: str,
    ) -> None:
        if not payload.startswith(f"{self._environment_id}:"):
            return
        if self._queue.empty():
            self._queue.put_nowait(None)

    async def wait(self, timeout_seconds: float) -> bool:
        await self._ensure_connection()
        try:
            await asyncio.wait_for(self._queue.get(), timeout=timeout_seconds)
            return True
        except TimeoutError:
            return False

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None


class RuntimeConfigSynchronizer:
    """Refresh immediately on notification and at least once every 30 seconds."""

    def __init__(
        self,
        service: RuntimeConfigService,
        subscriber: RuntimeConfigSubscriber,
        *,
        poll_interval_seconds: float = RUNTIME_CONFIG_POLL_INTERVAL_SECONDS,
    ) -> None:
        if poll_interval_seconds <= 0 or poll_interval_seconds > 30:
            raise ValueError("runtime configuration poll interval must be in (0, 30]")
        self._service = service
        self._subscriber = subscriber
        self._poll_interval_seconds = poll_interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("runtime configuration synchronizer is already running")
        await self._subscriber.start()
        await asyncio.to_thread(self._service.refresh)
        self._task = asyncio.create_task(self._run(), name="runtime-config-synchronizer")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
        await self._subscriber.close()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                wait_task = asyncio.create_task(self._subscriber.wait(self._poll_interval_seconds))
                stop_task = asyncio.create_task(self._stop.wait())
                done, pending = await asyncio.wait(
                    {wait_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                if stop_task in done and stop_task.result():
                    return
                await asyncio.to_thread(self._service.refresh)
            except Exception:
                logger.exception("runtime configuration synchronization failed")
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=min(self._poll_interval_seconds, 1.0)
                    )
