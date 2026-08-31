"""Durable platform process identity, heartbeat, and node-directory service."""

from __future__ import annotations

import asyncio
import logging
import platform
import socket
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.platform_control.release_identity import (
    PlatformReleaseIdentityV1,
    ReleaseDigest,
    ReleaseId,
)

HEARTBEAT_INTERVAL_SECONDS = 30
STALE_AFTER_SECONDS = 90
STALE_RETENTION_SECONDS = 7 * 24 * 60 * 60

logger = logging.getLogger(__name__)

SafeMetadata = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=253,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9._:/-]*[A-Za-z0-9])?$",
    ),
]
InstanceRole = Literal["frontend", "api", "worker", "media-worker", "maintenance-controller"]
ReadinessState = Literal["starting", "ready", "not_ready", "draining"]


class InstanceReadinessSummary(BaseModel):
    """Small allowlisted readiness projection; never stores dependency error text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ReadinessState
    failed_checks: tuple[
        Annotated[
            str,
            StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$"),
        ],
        ...,
    ] = ()


class PlatformInstanceIdentity(BaseModel):
    """Immutable facts assigned once when a process starts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instance_id: UUID
    node_name: SafeMetadata
    role: InstanceRole
    release_id: ReleaseId
    release_manifest_digest: ReleaseDigest
    component_image_digest: ReleaseDigest
    runtime_version: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    pod_name: SafeMetadata | None = None
    kubernetes_node_name: SafeMetadata | None = None
    kubernetes_zone: SafeMetadata | None = None


class PlatformInstance(BaseModel):
    """One database-observed platform process in the node directory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instance_id: UUID
    node_name: SafeMetadata
    role: InstanceRole
    release_id: ReleaseId
    release_manifest_digest: ReleaseDigest
    component_image_digest: ReleaseDigest
    runtime_version: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    pod_name: SafeMetadata | None = None
    kubernetes_node_name: SafeMetadata | None = None
    kubernetes_zone: SafeMetadata | None = None
    started_at: datetime
    last_heartbeat_at: datetime
    readiness: InstanceReadinessSummary
    applied_config_revision: int = Field(ge=0)
    stale: bool


class PlatformInstancePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal["hc-platform-instance-directory/v1"] = (
        "hc-platform-instance-directory/v1"
    )
    observed_at: datetime
    heartbeat_interval_seconds: Literal[30] = 30
    stale_after_seconds: Literal[90] = 90
    count: int = Field(ge=0)
    instances: tuple[PlatformInstance, ...]


class PlatformInstanceRepository(Protocol):
    def heartbeat(
        self,
        identity: PlatformInstanceIdentity,
        readiness: InstanceReadinessSummary,
        applied_config_revision: int = 0,
    ) -> PlatformInstance: ...

    def list_instances(
        self,
        *,
        stale: bool | None,
        role: InstanceRole | None,
        stale_after_seconds: int,
    ) -> PlatformInstancePage: ...

    def cleanup_stale(self, *, retention_seconds: int) -> int: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryPlatformInstanceRepository:
    """Deterministic reference adapter for API and stale-boundary tests."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._records: dict[UUID, PlatformInstance] = {}
        self._lock = RLock()

    def heartbeat(
        self,
        identity: PlatformInstanceIdentity,
        readiness: InstanceReadinessSummary,
        applied_config_revision: int = 0,
    ) -> PlatformInstance:
        with self._lock:
            now = self._clock()
            existing = self._records.get(identity.instance_id)
            record = PlatformInstance(
                **identity.model_dump(),
                started_at=now if existing is None else existing.started_at,
                last_heartbeat_at=now,
                readiness=readiness,
                applied_config_revision=applied_config_revision,
                stale=False,
            )
            self._records[identity.instance_id] = record
            return record

    def list_instances(
        self,
        *,
        stale: bool | None,
        role: InstanceRole | None,
        stale_after_seconds: int,
    ) -> PlatformInstancePage:
        with self._lock:
            observed_at = self._clock()
            instances = tuple(
                sorted(
                    (
                        item.model_copy(
                            update={
                                "stale": item.last_heartbeat_at
                                < observed_at - timedelta(seconds=stale_after_seconds)
                            }
                        )
                        for item in self._records.values()
                    ),
                    key=lambda item: (item.role, item.node_name, str(item.instance_id)),
                )
            )
            filtered = tuple(
                item
                for item in instances
                if (stale is None or item.stale is stale) and (role is None or item.role == role)
            )
            return PlatformInstancePage(
                observed_at=observed_at,
                count=len(filtered),
                instances=filtered,
            )

    def cleanup_stale(self, *, retention_seconds: int) -> int:
        with self._lock:
            cutoff = self._clock() - timedelta(seconds=retention_seconds)
            targets = tuple(
                instance_id
                for instance_id, item in self._records.items()
                if item.last_heartbeat_at < cutoff
            )
            for instance_id in targets:
                del self._records[instance_id]
            return len(targets)


class DbApiCursor(Protocol):
    description: Sequence[Sequence[object] | object] | None

    def execute(self, query: str, params: Sequence[object] | None = None) -> object: ...

    def fetchone(self) -> Mapping[str, Any] | None: ...

    def fetchall(self) -> Sequence[Mapping[str, Any]]: ...


class DbApiConnection(Protocol):
    def __enter__(self) -> DbApiConnection: ...

    def __exit__(self, *args: object) -> None: ...

    def cursor(self) -> DbApiCursor: ...


class PostgresPlatformInstanceRepository:
    """Unscoped adapter for global process liveness; never accepts request scope."""

    def __init__(
        self,
        connection_factory: Callable[[], DbApiConnection],
        *,
        supports_runtime_config_revision: bool = True,
    ) -> None:
        self._connection_factory = connection_factory
        self._supports_runtime_config_revision = supports_runtime_config_revision

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        supports_runtime_config_revision: bool = True,
    ) -> PostgresPlatformInstanceRepository:
        normalized = normalize_postgres_dsn(dsn)

        def connect() -> DbApiConnection:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(normalized, row_factory=dict_row)  # type: ignore[return-value]

        return cls(
            connect,
            supports_runtime_config_revision=supports_runtime_config_revision,
        )

    def heartbeat(
        self,
        identity: PlatformInstanceIdentity,
        readiness: InstanceReadinessSummary,
        applied_config_revision: int = 0,
    ) -> PlatformInstance:
        from psycopg.types.json import Jsonb

        revision_column = (
            ", applied_config_revision" if self._supports_runtime_config_revision else ""
        )
        revision_placeholder = ", %s" if self._supports_runtime_config_revision else ""
        revision_update = (
            ", applied_config_revision = EXCLUDED.applied_config_revision"
            if self._supports_runtime_config_revision
            else ""
        )
        revision_projection = (
            "*"
            if self._supports_runtime_config_revision
            else "*, 0::bigint AS applied_config_revision"
        )
        parameters: tuple[object, ...] = (
            identity.instance_id,
            identity.node_name,
            identity.role,
            identity.release_id,
            identity.release_manifest_digest,
            identity.component_image_digest,
            identity.runtime_version,
            identity.pod_name,
            identity.kubernetes_node_name,
            identity.kubernetes_zone,
            Jsonb(readiness.model_dump(mode="json")),
        )
        if self._supports_runtime_config_revision:
            parameters = (*parameters, applied_config_revision)
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"""
                INSERT INTO platform.platform_instances (
                    instance_id,
                    node_name,
                    role,
                    release_id,
                    release_manifest_digest,
                    component_image_digest,
                    runtime_version,
                    pod_name,
                    kubernetes_node_name,
                    kubernetes_zone,
                    readiness_summary
                    {revision_column}
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s{revision_placeholder})
                ON CONFLICT (instance_id) DO UPDATE
                SET node_name = EXCLUDED.node_name,
                    role = EXCLUDED.role,
                    release_id = EXCLUDED.release_id,
                    release_manifest_digest = EXCLUDED.release_manifest_digest,
                    component_image_digest = EXCLUDED.component_image_digest,
                    runtime_version = EXCLUDED.runtime_version,
                    pod_name = EXCLUDED.pod_name,
                    kubernetes_node_name = EXCLUDED.kubernetes_node_name,
                    kubernetes_zone = EXCLUDED.kubernetes_zone,
                    readiness_summary = EXCLUDED.readiness_summary
                    {revision_update},
                    last_heartbeat_at = statement_timestamp()
                RETURNING {revision_projection}, false AS stale
                """,
                parameters,
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("platform instance heartbeat returned no row")
        return _instance_from_row(row)

    def list_instances(
        self,
        *,
        stale: bool | None,
        role: InstanceRole | None,
        stale_after_seconds: int,
    ) -> PlatformInstancePage:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                WITH observation AS (
                    SELECT statement_timestamp() AS observed_at
                ), directory AS (
                    SELECT instance.*,
                           observation.observed_at,
                           instance.last_heartbeat_at
                               < observation.observed_at
                                 - (%s * interval '1 second') AS stale
                    FROM platform.platform_instances AS instance
                    CROSS JOIN observation
                )
                SELECT *
                FROM directory
                WHERE (%s::boolean IS NULL OR stale = %s::boolean)
                  AND (%s::text IS NULL OR role = %s::text)
                ORDER BY role, node_name, instance_id
                LIMIT 1000
                """,
                (stale_after_seconds, stale, stale, role, role),
            )
            rows = cursor.fetchall()
            cursor.execute("SELECT statement_timestamp() AS observed_at")
            observation = cursor.fetchone()
        if observation is None:
            raise RuntimeError("platform instance directory returned no database clock")
        observed_at = observation["observed_at"]
        instances = tuple(_instance_from_row(row) for row in rows)
        return PlatformInstancePage(
            observed_at=observed_at,
            count=len(instances),
            instances=instances,
        )

    def cleanup_stale(self, *, retention_seconds: int) -> int:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                DELETE FROM platform.platform_instances
                WHERE last_heartbeat_at
                    < statement_timestamp() - (%s * interval '1 second')
                RETURNING instance_id
                """,
                (retention_seconds,),
            )
            return len(cursor.fetchall())


def _instance_from_row(row: Mapping[str, Any]) -> PlatformInstance:
    return PlatformInstance(
        instance_id=row["instance_id"],
        node_name=row["node_name"],
        role=row["role"],
        release_id=row["release_id"],
        release_manifest_digest=row["release_manifest_digest"],
        component_image_digest=row["component_image_digest"],
        runtime_version=row["runtime_version"],
        pod_name=row["pod_name"],
        kubernetes_node_name=row["kubernetes_node_name"],
        kubernetes_zone=row["kubernetes_zone"],
        started_at=row["started_at"],
        last_heartbeat_at=row["last_heartbeat_at"],
        readiness=InstanceReadinessSummary.model_validate(row["readiness_summary"]),
        applied_config_revision=int(row["applied_config_revision"]),
        stale=bool(row["stale"]),
    )


class PlatformInstanceService:
    def __init__(
        self,
        repository: PlatformInstanceRepository,
        identity: PlatformInstanceIdentity,
        *,
        applied_config_revision_provider: Callable[[], int] = lambda: 0,
    ) -> None:
        self.repository = repository
        self.identity = identity
        self._applied_config_revision_provider = applied_config_revision_provider

    def heartbeat(self, readiness: InstanceReadinessSummary) -> PlatformInstance:
        record = self.repository.heartbeat(
            self.identity,
            readiness,
            self._applied_config_revision_provider(),
        )
        self.repository.cleanup_stale(retention_seconds=STALE_RETENTION_SECONDS)
        return record

    def list_instances(
        self,
        *,
        stale: bool | None = None,
        role: InstanceRole | None = None,
    ) -> PlatformInstancePage:
        return self.repository.list_instances(
            stale=stale,
            role=role,
            stale_after_seconds=STALE_AFTER_SECONDS,
        )


ReadinessProvider = Callable[[], Awaitable[InstanceReadinessSummary]]


class PlatformInstanceHeartbeater:
    """Runs one immediate heartbeat and then refreshes at a fixed bounded cadence."""

    def __init__(
        self,
        service: PlatformInstanceService,
        *,
        interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self._service = service
        self._interval_seconds = interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(
        self,
        readiness_provider: ReadinessProvider,
        *,
        initial_readiness: InstanceReadinessSummary | None = None,
    ) -> None:
        if self._task is not None:
            raise RuntimeError("platform instance heartbeater is already running")
        readiness = initial_readiness or await readiness_provider()
        await asyncio.to_thread(self._service.heartbeat, readiness)
        self._task = asyncio.create_task(
            self._run(readiness_provider),
            name="platform-instance-heartbeat",
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run(self, readiness_provider: ReadinessProvider) -> None:
        while True:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
                return
            except TimeoutError:
                pass
            try:
                readiness = await readiness_provider()
                await asyncio.to_thread(self._service.heartbeat, readiness)
            except Exception:
                logger.exception(
                    "platform instance heartbeat failed instance_id=%s role=%s",
                    self._service.identity.instance_id,
                    self._service.identity.role,
                )


class InstanceSettings(Protocol):
    instance_id: UUID | None
    node_name: str | None
    pod_name: str | None
    kubernetes_node_name: str | None
    kubernetes_zone: str | None


def platform_instance_identity(
    settings: InstanceSettings,
    release_identity: PlatformReleaseIdentityV1,
) -> PlatformInstanceIdentity:
    role: InstanceRole
    if release_identity.component == "api":
        role = "api"
    elif release_identity.component == "worker":
        role = "worker"
    elif release_identity.component == "media-worker":
        role = "media-worker"
    else:
        raise ValueError(
            f"component {release_identity.component!r} does not run a platform heartbeat"
        )
    instance_id = settings.instance_id or uuid4()
    node_name = settings.node_name or settings.pod_name or socket.gethostname()
    return PlatformInstanceIdentity(
        instance_id=instance_id,
        node_name=node_name,
        role=role,
        release_id=release_identity.release_id,
        release_manifest_digest=release_identity.release_manifest_digest,
        component_image_digest=release_identity.component_image_digest,
        runtime_version=f"{platform.python_implementation()} {platform.python_version()}",
        pod_name=settings.pod_name,
        kubernetes_node_name=settings.kubernetes_node_name,
        kubernetes_zone=settings.kubernetes_zone,
    )


async def constant_ready() -> InstanceReadinessSummary:
    return InstanceReadinessSummary(status="ready")
