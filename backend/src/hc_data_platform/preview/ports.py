from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .models import (
    EncodedPreviewArtifactV1,
    PreviewCacheRecordV1,
    PreviewFrameV1,
    PreviewRequestV1,
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
    ) -> Sequence[PreviewFrameV1]: ...


class EffectiveExclusionPort(Protocol):
    def effective_ranges(
        self, *, project_id: str, rollout_id: str, annotation_revision: int
    ) -> Sequence[StepRangeV1]: ...


class MediaEncoderPort(Protocol):
    """A real adapter may stream frames into FFmpeg and produce HLS/fMP4."""

    def encode(
        self, *, cache_key: str, request: PreviewRequestV1, frames: Sequence[RenderFrameV1]
    ) -> EncodedPreviewArtifactV1: ...


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
