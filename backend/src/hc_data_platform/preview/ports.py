from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from .models import (
    EncodedPreviewArtifactV1,
    PreviewArtifactV1,
    PreviewCacheRecordV1,
    PreviewFrameV1,
    PreviewJobV1,
    PreviewObjectV1,
    PreviewRequestV1,
    PreviewScopeV1,
    PreviewSessionV1,
    PublishedPreviewArtifactV1,
    RenderFrameV1,
    StepRangeV1,
)


class StepReaderPort(Protocol):
    """Implemented by the Lance catalog without exposing physical row numbers."""

    def read_steps(
        self,
        *,
        project_id: str,
        dataset_id: str,
        rollout_id: str,
        lance_version: str,
        camera_id: str,
        start_step: int | None,
        end_step: int | None,
    ) -> Iterable[PreviewFrameV1]: ...


class EffectiveExclusionPort(Protocol):
    def effective_ranges(
        self, *, project_id: str, rollout_id: str, annotation_revision: int
    ) -> Sequence[StepRangeV1]: ...


class MediaEncoderPort(Protocol):
    """A real adapter may stream frames into FFmpeg and produce HLS/fMP4."""

    def encode(
        self,
        *,
        cache_key: str,
        request: PreviewRequestV1,
        frames: Iterable[RenderFrameV1],
        cancelled: Callable[[], bool] | None = None,
    ) -> EncodedPreviewArtifactV1: ...

    def cleanup(self, encoded: EncodedPreviewArtifactV1) -> None: ...


class PreviewRepositoryPort(Protocol):
    """Durable artifact, job, and session state shared by every replica."""

    def find_artifact(
        self, scope: PreviewScopeV1, artifact_key: str, *, now: datetime
    ) -> PreviewArtifactV1 | None: ...

    def ensure_artifact_job(
        self,
        *,
        scope: PreviewScopeV1,
        artifact_key: str,
        request: PreviewRequestV1,
        pipeline_revision: str,
        rebuild_source_id: str,
        artifact_ttl: timedelta,
        now: datetime,
    ) -> tuple[PreviewArtifactV1, PreviewJobV1 | None]: ...

    def get_job(
        self, scope: PreviewScopeV1, job_id: str
    ) -> PreviewJobV1 | None: ...

    def mark_job_running(self, scope: PreviewScopeV1, job_id: str, *, now: datetime) -> None: ...

    def update_job_progress(
        self, scope: PreviewScopeV1, job_id: str, *, progress: int
    ) -> None: ...

    def record_publication(
        self,
        *,
        scope: PreviewScopeV1,
        artifact_key: str,
        publication: PublishedPreviewArtifactV1,
    ) -> None: ...

    def mark_ready(
        self,
        *,
        scope: PreviewScopeV1,
        job_id: str,
        artifact_key: str,
        publication: PublishedPreviewArtifactV1,
        frame_count: int,
        duration_seconds: float,
        now: datetime,
    ) -> PreviewArtifactV1: ...

    def mark_failed(
        self,
        *,
        scope: PreviewScopeV1,
        job_id: str,
        artifact_key: str,
        error_code: str,
        now: datetime,
    ) -> None: ...

    def create_session(
        self,
        *,
        scope: PreviewScopeV1,
        artifact: PreviewArtifactV1,
        request: PreviewRequestV1,
        session_ttl: timedelta,
        now: datetime,
    ) -> PreviewSessionV1: ...

    def get_session(
        self, scope: PreviewScopeV1, session_id: str, *, now: datetime
    ) -> tuple[PreviewSessionV1, PreviewArtifactV1] | None: ...

    def gc_candidates(
        self,
        *,
        now: datetime,
        project_quota_bytes: int,
        global_quota_bytes: int,
        high_watermark_percent: int,
        low_watermark_percent: int,
        limit: int,
    ) -> Sequence[PreviewArtifactV1]: ...

    def mark_deleting(
        self, scope: PreviewScopeV1, artifact_id: str, *, expected_version: int
    ) -> bool: ...

    def deleting_artifacts(self, *, limit: int) -> Sequence[PreviewArtifactV1]: ...

    def delete_artifact_metadata(self, scope: PreviewScopeV1, artifact_id: str) -> None: ...

    def cleanup_expired_metadata(self, *, now: datetime) -> int: ...

    def storage_totals(self) -> tuple[int, int]: ...


class PreviewArtifactStorePort(Protocol):
    """Immutable preview object publication and exact-manifest deletion."""

    def publish(
        self,
        *,
        project_id: str,
        artifact_key: str,
        encoded: EncodedPreviewArtifactV1,
    ) -> PublishedPreviewArtifactV1: ...

    def read_playlist(self, playlist_key: str) -> str: ...

    def authorize_object(self, object_key: str, *, expires_in: timedelta) -> str: ...

    def delete_exact(self, objects: Sequence[PreviewObjectV1]) -> int: ...

    def resolve_local_object(self, object_key: str) -> Path | None: ...


class PreviewJobQueuePort(Protocol):
    def enqueue(self, job: PreviewJobV1) -> Awaitable[None]: ...


class PreviewCachePort(Protocol):
    def get(self, cache_key: str, *, now: datetime) -> PreviewCacheRecordV1 | None: ...

    def get_by_session(self, session_id: str, *, now: datetime) -> PreviewCacheRecordV1 | None: ...

    def put(self, record: PreviewCacheRecordV1) -> None: ...


class UrlSignerPort(Protocol):
    """Issue and verify a capability URL for one preview asset.

    Media requests originate from an HLS player rather than the API client, so they
    cannot rely on a bearer header.  The capability must consequently bind the
    preview session, committed artifact, requested asset and expiry together.
    """

    def sign(
        self,
        *,
        session_id: str,
        artifact_uri: str,
        asset_name: str,
        expires_at: datetime,
    ) -> str: ...

    def verify(
        self,
        *,
        session_id: str,
        artifact_uri: str,
        asset_name: str,
        expires: int,
        signature: str,
        now: datetime,
    ) -> bool: ...


class PreviewMediaReaderPort(Protocol):
    """Resolve a committed preview asset without exposing a filesystem path to clients."""

    def resolve(self, record: PreviewCacheRecordV1, *, asset_name: str) -> Path: ...
