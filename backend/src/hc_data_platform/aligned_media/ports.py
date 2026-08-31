from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from .models import (
    AlignedMediaArtifactV1,
    AlignedMediaGenerationRequestV1,
    AlignedMediaJobV1,
    AlignedMediaObjectV1,
    AlignedMediaScopeV1,
    AlignedMediaSelectorV1,
    EncodedAlignedMediaV1,
    PublishedAlignedMediaV1,
)


class AlignedFrameV1(Protocol):
    @property
    def step_index(self) -> int: ...

    @property
    def timestamp_ns(self) -> int: ...

    @property
    def image(self) -> bytes | None: ...

    @property
    def valid(self) -> bool: ...

    @property
    def repeated(self) -> bool: ...


class AlignedFrameReaderPort(Protocol):
    def read_camera_frames(
        self,
        request: AlignedMediaGenerationRequestV1,
    ) -> Iterable[AlignedFrameV1]: ...


class AlignedMediaEncoderPort(Protocol):
    def encode(
        self,
        *,
        artifact_key: str,
        request: AlignedMediaGenerationRequestV1,
        frames: Iterable[AlignedFrameV1],
        cancelled: Callable[[], bool] | None = None,
    ) -> EncodedAlignedMediaV1: ...

    def cleanup(self, encoded: EncodedAlignedMediaV1) -> None: ...


class AlignedMediaRepositoryPort(Protocol):
    def find_by_selector(
        self,
        scope: AlignedMediaScopeV1,
        selector: AlignedMediaSelectorV1,
        *,
        profile_id: str,
    ) -> AlignedMediaArtifactV1 | None: ...

    def ensure_generation(
        self,
        *,
        scope: AlignedMediaScopeV1,
        artifact_key: str,
        request: AlignedMediaGenerationRequestV1,
        profile_version: str,
        now: datetime,
    ) -> tuple[AlignedMediaArtifactV1, AlignedMediaJobV1 | None]: ...

    def claim_job(
        self,
        scope: AlignedMediaScopeV1,
        job_id: str,
        *,
        owner_id: str,
        attempt_token: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> AlignedMediaJobV1 | None: ...

    def renew_job_lease(
        self,
        scope: AlignedMediaScopeV1,
        job_id: str,
        *,
        owner_id: str,
        attempt_token: str,
        lease_expires_at: datetime,
        now: datetime,
        progress: int | None = None,
    ) -> bool: ...

    def record_publication(
        self,
        *,
        scope: AlignedMediaScopeV1,
        job_id: str,
        artifact_key: str,
        owner_id: str,
        attempt_token: str,
        publication: PublishedAlignedMediaV1,
        encoded: EncodedAlignedMediaV1,
        now: datetime,
    ) -> None: ...

    def discard_publication_receipt(
        self,
        *,
        scope: AlignedMediaScopeV1,
        job_id: str,
        artifact_key: str,
        owner_id: str,
        attempt_token: str,
        publication_token: str,
        now: datetime,
    ) -> None: ...

    def mark_ready(
        self,
        *,
        scope: AlignedMediaScopeV1,
        job_id: str,
        artifact_key: str,
        owner_id: str,
        attempt_token: str,
        publication: PublishedAlignedMediaV1,
        encoded: EncodedAlignedMediaV1,
        now: datetime,
    ) -> AlignedMediaArtifactV1: ...

    def release_job_for_retry(
        self,
        *,
        scope: AlignedMediaScopeV1,
        job_id: str,
        owner_id: str,
        attempt_token: str,
        error_code: str,
        now: datetime,
    ) -> bool: ...

    def mark_failed(
        self,
        *,
        scope: AlignedMediaScopeV1,
        job_id: str,
        artifact_key: str,
        owner_id: str,
        attempt_token: str,
        error_code: str,
        now: datetime,
    ) -> None: ...

    def begin_dataset_commit(
        self,
        *,
        scope: AlignedMediaScopeV1,
        artifact_ids: Sequence[str],
        lease_expires_at: datetime,
        now: datetime,
    ) -> None: ...

    def mark_dataset_committed(
        self,
        *,
        scope: AlignedMediaScopeV1,
        artifact_ids: Sequence[str],
        now: datetime,
    ) -> None: ...

    def begin_abandon_uncommitted(
        self,
        *,
        scope: AlignedMediaScopeV1,
        artifact_ids: Sequence[str],
        error_code: str,
        now: datetime,
    ) -> tuple[AlignedMediaObjectV1, ...]: ...

    def complete_abandon_uncommitted(
        self,
        *,
        scope: AlignedMediaScopeV1,
        artifact_ids: Sequence[str],
        now: datetime,
    ) -> None: ...

    def begin_retire_dataset_version(
        self,
        *,
        scope: AlignedMediaScopeV1,
        dataset_id: str,
        dataset_version: int,
        now: datetime,
    ) -> tuple[AlignedMediaObjectV1, ...]: ...

    def complete_retire_dataset_version(
        self,
        *,
        scope: AlignedMediaScopeV1,
        dataset_id: str,
        dataset_version: int,
        now: datetime,
    ) -> None: ...

    def publication_is_referenced(
        self,
        scope: AlignedMediaScopeV1,
        artifact_key: str,
        publication_token: str,
        *,
        now: datetime,
    ) -> bool: ...


class AlignedMediaArtifactStorePort(Protocol):
    def publish(
        self,
        *,
        scope: AlignedMediaScopeV1,
        request: AlignedMediaGenerationRequestV1,
        artifact_key: str,
        publication_token: str,
        encoded: EncodedAlignedMediaV1,
    ) -> PublishedAlignedMediaV1: ...

    def rollback_publication(self, publication: PublishedAlignedMediaV1) -> int: ...

    def verify_publication(self, publication: PublishedAlignedMediaV1) -> bool: ...

    def authorize_object(self, object_key: str, *, expires_in: timedelta) -> str: ...

    def delete_exact(self, objects: Sequence[AlignedMediaObjectV1]) -> int: ...

    def resolve_local_object(self, object_key: str) -> Path | None: ...


class MediaCapacityGatePort(Protocol):
    def acquire(
        self,
        *,
        owner_id: str,
        attempt_token: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> int | None: ...

    def renew(
        self,
        *,
        slot_id: int,
        owner_id: str,
        attempt_token: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> bool: ...

    def release(self, *, slot_id: int, owner_id: str, attempt_token: str) -> bool: ...
