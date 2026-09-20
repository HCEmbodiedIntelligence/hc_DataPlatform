"""Format-neutral Raw source registry and processing queue.

Raw bytes remain in object storage.  These models persist only immutable
locations, content identity, Episode lineage, and mutable processing state.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import Enum
from threading import RLock
from typing import Any, Protocol, cast
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field

from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.core.events import DomainEventEnvelope

from .models import Identifier, Sha256, utc_now


class RawSourceFormat(str, Enum):
    MCAP = "MCAP"
    CAPTURE_BUNDLE = "CAPTURE_BUNDLE"
    LEROBOT_V3 = "LEROBOT_V3"
    ROSBAG = "ROSBAG"


class RawSourceStatus(str, Enum):
    UPLOADING = "UPLOADING"
    COMMITTED = "COMMITTED"
    INVALID = "INVALID"
    DELETED = "DELETED"


class RawSourceProcessingStatus(str, Enum):
    NOT_REQUESTED = "NOT_REQUESTED"
    PENDING = "PENDING"
    DISCOVERING_EPISODES = "DISCOVERING_EPISODES"
    PROCESSING = "PROCESSING"
    READY = "READY"
    PARTIALLY_FAILED = "PARTIALLY_FAILED"
    FAILED = "FAILED"


class RawSourceEpisodeStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


class RawIngestJobType(str, Enum):
    RAW_STORAGE = "RAW_STORAGE"
    DIRECT_EPISODE_INGEST = "DIRECT_EPISODE_INGEST"
    CONTINUOUS_RECORDING_DISCOVERY = "CONTINUOUS_RECORDING_DISCOVERY"
    LEROBOT_IMPORT = "LEROBOT_IMPORT"


class RawIngestJobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIALLY_FAILED = "PARTIALLY_FAILED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RawSource(BaseModel):
    """Durable business record for immutable bytes stored outside PostgreSQL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_source_id: Identifier
    organization_id: Identifier
    project_id: Identifier
    region_code: Identifier
    upload_id: str = Field(min_length=1, max_length=256)
    dataset_id: Identifier | None = None
    collection_task_id: Identifier | None = None
    robot_id: Identifier | None = None
    source_format: RawSourceFormat
    source_format_version: str = Field(min_length=1, max_length=64)
    manifest_key: str = Field(min_length=1, max_length=2048)
    storage_prefix: str = Field(min_length=1, max_length=2048)
    content_hash: Sha256
    file_count: int = Field(ge=1)
    total_bytes: int = Field(gt=0)
    raw_status: RawSourceStatus = RawSourceStatus.COMMITTED
    processing_status: RawSourceProcessingStatus = RawSourceProcessingStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    committed_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RawSourceEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: Identifier
    project_id: Identifier
    region_code: Identifier
    raw_source_id: Identifier
    episode_id: Identifier
    source_episode_index: int = Field(ge=0)
    status: RawSourceEpisodeStatus = RawSourceEpisodeStatus.PENDING
    frame_count: int | None = Field(default=None, ge=1)
    dataset_version: int | None = Field(default=None, ge=1)
    lance_version: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RawIngestJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: Identifier
    project_id: Identifier
    region_code: Identifier
    job_id: str = Field(min_length=1, max_length=512)
    raw_source_id: Identifier
    workflow_id: str | None = Field(default=None, min_length=1, max_length=512)
    job_type: RawIngestJobType
    adapter_name: str = Field(min_length=1, max_length=128)
    status: RawIngestJobStatus = RawIngestJobStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    last_error_code: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Z0-9_]+$",
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CommittedRawSourceGraph(BaseModel):
    """One atomic registration unit: Raw source, Episodes, and processing job."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: RawSource
    episodes: tuple[RawSourceEpisode, ...] = ()
    job: RawIngestJob


class RawSourceRepositoryPort(Protocol):
    def register_committed(self, graph: CommittedRawSourceGraph) -> CommittedRawSourceGraph: ...

    def get_source(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        raw_source_id: str,
    ) -> RawSource | None: ...

    def list_episodes(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        raw_source_id: str,
    ) -> tuple[RawSourceEpisode, ...]: ...

    def get_job(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        raw_source_id: str,
    ) -> RawIngestJob | None: ...


def _source_identity(source: RawSource) -> tuple[object, ...]:
    return (
        source.organization_id,
        source.project_id,
        source.region_code,
        source.raw_source_id,
        source.upload_id,
        source.dataset_id,
        source.collection_task_id,
        source.robot_id,
        source.source_format,
        source.source_format_version,
        source.manifest_key,
        source.storage_prefix,
        source.content_hash,
        source.file_count,
        source.total_bytes,
        source.raw_status,
    )


def _episode_identity(episode: RawSourceEpisode) -> tuple[object, ...]:
    return (
        episode.organization_id,
        episode.project_id,
        episode.region_code,
        episode.raw_source_id,
        episode.episode_id,
        episode.source_episode_index,
    )


def _job_identity(job: RawIngestJob) -> tuple[object, ...]:
    return (
        job.organization_id,
        job.project_id,
        job.region_code,
        job.job_id,
        job.raw_source_id,
        job.job_type,
        job.adapter_name,
    )


def _identity_conflict(resource: str) -> ProblemException:
    return problem(
        status=409,
        code="RAW_SOURCE_IDENTITY_CONFLICT",
        title="Raw source identity conflict",
        detail=f"The persisted {resource} has different immutable lineage.",
    )


class InMemoryRawSourceRepository:
    """Thread-safe fake with the same idempotent immutable identity as PostgreSQL."""

    def __init__(self) -> None:
        self._sources: dict[tuple[str, str, str, str], RawSource] = {}
        self._episodes: dict[tuple[str, str, str, str, str], RawSourceEpisode] = {}
        self._jobs: dict[tuple[str, str, str, str], RawIngestJob] = {}
        self._lock = RLock()

    def register_committed(self, graph: CommittedRawSourceGraph) -> CommittedRawSourceGraph:
        source = graph.source
        scope = (
            source.organization_id,
            source.project_id,
            source.region_code,
            source.raw_source_id,
        )
        if (
            graph.job.raw_source_id != source.raw_source_id
            or any(
                episode.raw_source_id != source.raw_source_id
                or (
                    episode.organization_id,
                    episode.project_id,
                    episode.region_code,
                )
                != (source.organization_id, source.project_id, source.region_code)
                for episode in graph.episodes
            )
            or (
                graph.job.organization_id,
                graph.job.project_id,
                graph.job.region_code,
            )
            != (source.organization_id, source.project_id, source.region_code)
        ):
            raise ValueError("Raw source graph scope and lineage must agree")
        with self._lock:
            existing = self._sources.get(scope)
            if existing is not None and _source_identity(existing) != _source_identity(source):
                raise _identity_conflict("source")
            if existing is not None:
                persisted_episodes = self.list_episodes(
                    organization_id=source.organization_id,
                    project_id=source.project_id,
                    region_code=source.region_code,
                    raw_source_id=source.raw_source_id,
                )
                if {item.episode_id: _episode_identity(item) for item in persisted_episodes} != {
                    item.episode_id: _episode_identity(item) for item in graph.episodes
                }:
                    raise _identity_conflict("Episode set")
            existing_job = self._jobs.get(scope)
            if existing_job is not None and _job_identity(existing_job) != _job_identity(graph.job):
                raise _identity_conflict("processing job")
            indexes: set[int] = set()
            for episode in graph.episodes:
                if episode.source_episode_index in indexes:
                    raise ValueError("Raw source Episode indexes must be unique")
                indexes.add(episode.source_episode_index)
                key = (*scope, episode.episode_id)
                persisted_episode = self._episodes.get(key)
                if persisted_episode is not None and _episode_identity(
                    persisted_episode
                ) != _episode_identity(episode):
                    raise _identity_conflict("Episode")
            self._sources.setdefault(scope, source)
            self._jobs.setdefault(scope, graph.job)
            for episode in graph.episodes:
                self._episodes.setdefault((*scope, episode.episode_id), episode)
            return CommittedRawSourceGraph(
                source=self._sources[scope],
                episodes=self.list_episodes(
                    organization_id=source.organization_id,
                    project_id=source.project_id,
                    region_code=source.region_code,
                    raw_source_id=source.raw_source_id,
                ),
                job=self._jobs[scope],
            )

    def get_source(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        raw_source_id: str,
    ) -> RawSource | None:
        with self._lock:
            return self._sources.get((organization_id, project_id, region_code, raw_source_id))

    def list_episodes(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        raw_source_id: str,
    ) -> tuple[RawSourceEpisode, ...]:
        scope = (organization_id, project_id, region_code, raw_source_id)
        with self._lock:
            return tuple(
                sorted(
                    (episode for key, episode in self._episodes.items() if key[:4] == scope),
                    key=lambda item: item.source_episode_index,
                )
            )

    def get_job(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        raw_source_id: str,
    ) -> RawIngestJob | None:
        with self._lock:
            return self._jobs.get((organization_id, project_id, region_code, raw_source_id))


class PostgresRawSourceRepository:
    """Atomic PostgreSQL registration for a committed Raw source graph."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def register_committed(self, graph: CommittedRawSourceGraph) -> CommittedRawSourceGraph:
        source = graph.source
        if (
            graph.job.raw_source_id != source.raw_source_id
            or any(
                episode.raw_source_id != source.raw_source_id
                or (
                    episode.organization_id,
                    episode.project_id,
                    episode.region_code,
                )
                != (source.organization_id, source.project_id, source.region_code)
                for episode in graph.episodes
            )
            or (
                graph.job.organization_id,
                graph.job.project_id,
                graph.job.region_code,
            )
            != (source.organization_id, source.project_id, source.region_code)
        ):
            raise ValueError("Raw source graph scope and lineage must agree")
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                source = self._resolve_dataset(cursor, source)
                cursor.execute(
                    """
                    INSERT INTO ingest.raw_sources (
                        organization_id, project_id, region_code, raw_source_id,
                        upload_id, dataset_id, collection_task_id, robot_id,
                        source_format, source_format_version, manifest_key,
                        storage_prefix, content_hash, file_count, total_bytes,
                        raw_status, processing_status, created_at, committed_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    ) ON CONFLICT (
                        organization_id, project_id, region_code, raw_source_id
                    ) DO NOTHING
                    """,
                    (
                        source.organization_id,
                        source.project_id,
                        source.region_code,
                        source.raw_source_id,
                        source.upload_id,
                        source.dataset_id,
                        source.collection_task_id,
                        source.robot_id,
                        source.source_format.value,
                        source.source_format_version,
                        source.manifest_key,
                        source.storage_prefix,
                        source.content_hash,
                        source.file_count,
                        source.total_bytes,
                        source.raw_status.value,
                        source.processing_status.value,
                        source.created_at,
                        source.committed_at,
                        source.updated_at,
                    ),
                )
                inserted_source = cursor.rowcount == 1
                persisted_source = self._select_source(cursor, source)
                if _source_identity(persisted_source) != _source_identity(source):
                    raise _identity_conflict("source")
                if not inserted_source:
                    persisted_before = self._select_episodes(cursor, source)
                    if {item.episode_id: _episode_identity(item) for item in persisted_before} != {
                        item.episode_id: _episode_identity(item) for item in graph.episodes
                    }:
                        raise _identity_conflict("Episode set")
                for episode in graph.episodes:
                    cursor.execute(
                        """
                        INSERT INTO ingest.raw_source_episodes (
                            organization_id, project_id, region_code, raw_source_id,
                            episode_id, source_episode_index, status, frame_count,
                            dataset_version, lance_version, created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        ) ON CONFLICT (
                            organization_id, project_id, region_code,
                            raw_source_id, episode_id
                        ) DO NOTHING
                        """,
                        (
                            episode.organization_id,
                            episode.project_id,
                            episode.region_code,
                            episode.raw_source_id,
                            episode.episode_id,
                            episode.source_episode_index,
                            episode.status.value,
                            episode.frame_count,
                            episode.dataset_version,
                            episode.lance_version,
                            episode.created_at,
                            episode.updated_at,
                        ),
                    )
                job = graph.job
                cursor.execute(
                    """
                    INSERT INTO ingest.raw_ingest_jobs (
                        organization_id, project_id, region_code, job_id,
                        raw_source_id, workflow_id, job_type, adapter_name, status, attempts,
                        last_error_code, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    ) ON CONFLICT (
                        organization_id, project_id, region_code, raw_source_id
                    ) DO NOTHING
                    """,
                    (
                        job.organization_id,
                        job.project_id,
                        job.region_code,
                        job.job_id,
                        job.raw_source_id,
                        job.workflow_id,
                        job.job_type.value,
                        job.adapter_name,
                        job.status.value,
                        job.attempts,
                        job.last_error_code,
                        job.created_at,
                        job.updated_at,
                    ),
                )
                persisted_job = self._select_job(
                    cursor,
                    organization_id=source.organization_id,
                    project_id=source.project_id,
                    region_code=source.region_code,
                    raw_source_id=source.raw_source_id,
                )
                if persisted_job is None:
                    raise RuntimeError("PostgreSQL did not return the Raw ingest job")
                if _job_identity(persisted_job) != _job_identity(job):
                    raise _identity_conflict("processing job")
                persisted_episodes = self._select_episodes(cursor, source)
                expected_by_id = {item.episode_id: item for item in graph.episodes}
                for episode in persisted_episodes:
                    expected = expected_by_id.get(episode.episode_id)
                    if expected is None or (
                        _episode_identity(episode) != _episode_identity(expected)
                    ):
                        raise _identity_conflict("Episode")
                if len(persisted_episodes) != len(graph.episodes):
                    raise _identity_conflict("Episode set")
                if job.job_type is RawIngestJobType.LEROBOT_IMPORT and job.workflow_id:
                    event = DomainEventEnvelope(
                        event_id=str(
                            uuid5(
                                NAMESPACE_URL,
                                f"lerobot-dispatch:{source.organization_id}:{job.workflow_id}",
                            )
                        ),
                        event_type="lerobot.import.requested.v1",
                        aggregate_type="raw_source",
                        aggregate_id=source.raw_source_id,
                        organization_id=source.organization_id,
                        project_id=source.project_id,
                        region_code=source.region_code,
                        payload={
                            "raw_source_id": source.raw_source_id,
                            "workflow_id": job.workflow_id,
                        },
                    )
                    cursor.execute(
                        """INSERT INTO core.outbox_events (
                        event_id, organization_id, project_id, region_code, event_type, envelope,
                        occurred_at, available_at)
                        VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,clock_timestamp())
                        ON CONFLICT (event_id) DO NOTHING""",
                        (
                            event.event_id,
                            event.organization_id,
                            event.project_id,
                            event.region_code,
                            event.event_type,
                            event.model_dump_json(),
                            event.occurred_at,
                        ),
                    )
            connection.commit()
            return CommittedRawSourceGraph(
                source=persisted_source,
                episodes=persisted_episodes,
                job=persisted_job,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _resolve_dataset(cursor: Any, source: RawSource) -> RawSource:
        if source.dataset_id is not None or source.collection_task_id is None:
            return source
        cursor.execute(
            """
            SELECT dataset_id
            FROM collection_tasks.collection_tasks
            WHERE organization_id = %s AND project_id = %s
              AND collection_task_id = %s
            """,
            (
                source.organization_id,
                source.project_id,
                source.collection_task_id,
            ),
        )
        row = cursor.fetchone()
        return source if row is None else source.model_copy(update={"dataset_id": str(row[0])})

    def get_source(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        raw_source_id: str,
    ) -> RawSource | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                return self._select_source_by_scope(
                    cursor,
                    organization_id=organization_id,
                    project_id=project_id,
                    region_code=region_code,
                    raw_source_id=raw_source_id,
                )
        finally:
            connection.close()

    def list_episodes(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        raw_source_id: str,
    ) -> tuple[RawSourceEpisode, ...]:
        source = self.get_source(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            raw_source_id=raw_source_id,
        )
        if source is None:
            return ()
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                return self._select_episodes(cursor, source)
        finally:
            connection.close()

    def get_job(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        raw_source_id: str,
    ) -> RawIngestJob | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                return self._select_job(
                    cursor,
                    organization_id=organization_id,
                    project_id=project_id,
                    region_code=region_code,
                    raw_source_id=raw_source_id,
                    required=False,
                )
        finally:
            connection.close()

    def _select_source(self, cursor: Any, source: RawSource) -> RawSource:
        persisted = self._select_source_by_scope(
            cursor,
            organization_id=source.organization_id,
            project_id=source.project_id,
            region_code=source.region_code,
            raw_source_id=source.raw_source_id,
        )
        if persisted is None:
            raise RuntimeError("PostgreSQL did not return the Raw source")
        return persisted

    def _select_source_by_scope(
        self,
        cursor: Any,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        raw_source_id: str,
    ) -> RawSource | None:
        cursor.execute(
            """
            SELECT raw_source_id, organization_id, project_id, region_code,
                   upload_id, dataset_id, collection_task_id, robot_id,
                   source_format, source_format_version, manifest_key,
                   storage_prefix, content_hash, file_count, total_bytes,
                   raw_status, processing_status, created_at, committed_at, updated_at
            FROM ingest.raw_sources
            WHERE organization_id = %s AND project_id = %s AND region_code = %s
              AND raw_source_id = %s
            """,
            (organization_id, project_id, region_code, raw_source_id),
        )
        return cast(RawSource | None, self._model(cursor, cursor.fetchone(), RawSource))

    def _select_episodes(
        self,
        cursor: Any,
        source: RawSource,
    ) -> tuple[RawSourceEpisode, ...]:
        cursor.execute(
            """
            SELECT organization_id, project_id, region_code, raw_source_id,
                   episode_id, source_episode_index, status, frame_count,
                   dataset_version, lance_version, created_at, updated_at
            FROM ingest.raw_source_episodes
            WHERE organization_id = %s AND project_id = %s AND region_code = %s
              AND raw_source_id = %s
            ORDER BY source_episode_index
            """,
            (
                source.organization_id,
                source.project_id,
                source.region_code,
                source.raw_source_id,
            ),
        )
        return tuple(
            cast(RawSourceEpisode, self._model(cursor, row, RawSourceEpisode))
            for row in cursor.fetchall()
        )

    def _select_job(
        self,
        cursor: Any,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        raw_source_id: str,
        required: bool = True,
    ) -> RawIngestJob | None:
        cursor.execute(
            """
            SELECT organization_id, project_id, region_code, job_id, raw_source_id,
                   workflow_id, job_type, adapter_name, status, attempts, last_error_code,
                   created_at, updated_at
            FROM ingest.raw_ingest_jobs
            WHERE organization_id = %s AND project_id = %s AND region_code = %s
              AND raw_source_id = %s
            """,
            (organization_id, project_id, region_code, raw_source_id),
        )
        model = cast(RawIngestJob | None, self._model(cursor, cursor.fetchone(), RawIngestJob))
        if required and model is None:
            raise RuntimeError("PostgreSQL did not return the Raw ingest job")
        return model

    @staticmethod
    def _model(cursor: Any, row: Any, model_type: type[BaseModel]) -> BaseModel | None:
        if row is None:
            return None
        columns = [item.name for item in cursor.description]
        return model_type.model_validate(dict(zip(columns, row, strict=True)))
