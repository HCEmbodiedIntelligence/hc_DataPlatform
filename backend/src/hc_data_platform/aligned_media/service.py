from __future__ import annotations

import hashlib
import json
import socket
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from uuid import UUID, uuid4

from hc_data_platform.core.errors import problem

from .capacity import UnlimitedMediaCapacityGate
from .encoder import AlignedMediaEncodingError, AlignedMediaGenerationCancelled
from .models import (
    AlignedMediaArtifactStatus,
    AlignedMediaArtifactV1,
    AlignedMediaAuthorizationV1,
    AlignedMediaGenerationRequestV1,
    AlignedMediaScopeV1,
    AlignedMediaSelectorV1,
    EncodedAlignedMediaV1,
    PublishedAlignedMediaV1,
)
from .ports import (
    AlignedFrameReaderPort,
    AlignedMediaArtifactStorePort,
    AlignedMediaEncoderPort,
    AlignedMediaRepositoryPort,
    MediaCapacityGatePort,
)
from .profiles import DEFAULT_ALIGNED_MEDIA_PROFILES, AlignedMediaProfileCatalog

ALIGNED_MEDIA_PIPELINE_REVISION = "aligned-mp4-image2pipe-v1"
MP4_EPISODE_PIPELINE_REVISION = "aligned-mp4-native-cut-v1"


def aligned_media_artifact_key(request: AlignedMediaGenerationRequestV1) -> str:
    payload = {
        "pipeline_revision": (
            MP4_EPISODE_PIPELINE_REVISION
            if request.mp4_source is not None
            else ALIGNED_MEDIA_PIPELINE_REVISION
        ),
        "project_id": request.project_id,
        "dataset_id": request.dataset_id,
        "rollout_id": request.rollout_id,
        "dataset_version": request.expected_dataset_version,
        "camera_id": request.camera_id,
        "source_sha256": request.source_sha256,
        "alignment_version": request.alignment.alignment_version,
        "alignment_content_sha256": request.alignment.content_sha256,
        "profile_id": request.profile_id,
        "mp4_source": (
            None if request.mp4_source is None else request.mp4_source.model_dump(mode="json")
        ),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _publication_token(artifact_key: str) -> str:
    """Retry-stable UUID so identical publications reuse exact object keys/intent."""

    return str(UUID(hex=artifact_key[:32]))


class AlignedMediaAuthorizationService:
    """Read-only stateless authorization; it cannot create jobs or invoke media tools."""

    def __init__(
        self,
        *,
        repository: AlignedMediaRepositoryPort,
        store: AlignedMediaArtifactStorePort,
        profiles: AlignedMediaProfileCatalog = DEFAULT_ALIGNED_MEDIA_PROFILES,
        profile_id: str = "canonical-h264-crf20-v1",
        signed_url_ttl: timedelta = timedelta(minutes=15),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._store = store
        self._profiles = profiles
        self._profile_id = profile_id
        self._signed_url_ttl = signed_url_ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def authorize(
        self,
        scope: AlignedMediaScopeV1,
        selector: AlignedMediaSelectorV1,
    ) -> AlignedMediaAuthorizationV1:
        if scope.project_id != selector.project_id:
            raise problem(
                status=403,
                code="ALIGNED_MEDIA_SCOPE_MISMATCH",
                title="Aligned media scope mismatch",
                detail="The selected media does not belong to the authorized project.",
            )
        self._profiles.get(self._profile_id)
        artifact = self._repository.find_by_selector(
            scope,
            selector,
            profile_id=self._profile_id,
        )
        if artifact is not None and artifact.retired_at is not None:
            raise problem(
                status=410,
                code="ALIGNED_MEDIA_RETIRED",
                title="Aligned media was retired",
                detail="The selected Dataset version has been retired and is no longer playable.",
            )
        if artifact is None or artifact.status is AlignedMediaArtifactStatus.GENERATING:
            raise problem(
                status=425,
                code="ALIGNED_MEDIA_NOT_READY",
                title="Aligned media is not ready",
                detail="Dataset ingest has not committed all canonical media yet.",
            )
        if artifact.status is AlignedMediaArtifactStatus.FAILED:
            raise problem(
                status=409,
                code=artifact.failure_code or "ALIGNED_MEDIA_FAILED",
                title="Aligned media generation failed",
                detail="The ingest media artifact is in a stable failed state.",
            )
        if artifact.status is AlignedMediaArtifactStatus.ABANDONING:
            raise problem(
                status=409,
                code=artifact.failure_code or "ALIGNED_MEDIA_BUNDLE_ABORTED",
                title="Aligned media bundle was abandoned",
                detail="The uncommitted ingest media bundle is being cleaned up.",
            )
        if artifact.dataset_committed_at is None:
            raise problem(
                status=425,
                code="ALIGNED_MEDIA_NOT_READY",
                title="Aligned media is not ready",
                detail="The canonical MP4 exists, but its Lance Dataset version is not committed.",
            )
        if (
            artifact.media_object_key is None
            or artifact.timeline is None
            or artifact.width is None
            or artifact.height is None
            or artifact.frame_count < 1
        ):
            raise RuntimeError("READY aligned media has an incomplete receipt")
        now = self._clock()
        expires_at = now + self._signed_url_ttl
        return AlignedMediaAuthorizationV1(
            artifact_id=artifact.artifact_id,
            artifact_key=artifact.artifact_key,
            project_id=scope.project_id,
            dataset_id=artifact.dataset_id,
            rollout_id=artifact.rollout_id,
            dataset_version=artifact.dataset_version,
            camera_id=artifact.camera_id,
            media_url=self._store.authorize_object(
                artifact.media_object_key,
                expires_in=self._signed_url_ttl,
            ),
            expires_at=expires_at,
            frame_count=artifact.frame_count,
            duration_seconds=artifact.duration_seconds,
            width=artifact.width,
            height=artifact.height,
            timeline=artifact.timeline,
            alignment_version=artifact.alignment_version,
            profile_id=artifact.profile_id,
            profile_version=artifact.profile_version,
        )


class AlignedMediaLifecycleService:
    """Retire committed Dataset-version media through an exact, retryable receipt."""

    def __init__(
        self,
        *,
        repository: AlignedMediaRepositoryPort,
        store: AlignedMediaArtifactStorePort,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def retire_dataset_version(
        self,
        scope: AlignedMediaScopeV1,
        *,
        dataset_id: str,
        dataset_version: int,
    ) -> int:
        if not dataset_id or dataset_version < 1:
            raise ValueError("a valid Dataset version is required for media retirement")
        now = self._now()
        objects = self._repository.begin_retire_dataset_version(
            scope=scope,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            now=now,
        )
        deleted_bytes = self._store.delete_exact(objects)
        self._repository.complete_retire_dataset_version(
            scope=scope,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            now=self._now(),
        )
        return deleted_bytes

    def _now(self) -> datetime:
        value = self._clock()
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class AlignedMediaGenerationService:
    """Internal media-worker service reading Arrow staging and publishing one MP4."""

    def __init__(
        self,
        *,
        frame_reader: AlignedFrameReaderPort,
        encoder: AlignedMediaEncoderPort,
        repository: AlignedMediaRepositoryPort,
        store: AlignedMediaArtifactStorePort,
        profiles: AlignedMediaProfileCatalog = DEFAULT_ALIGNED_MEDIA_PROFILES,
        capacity_gate: MediaCapacityGatePort | None = None,
        lease_duration: timedelta = timedelta(seconds=90),
        capacity_wait_timeout: timedelta = timedelta(minutes=30),
        heartbeat_interval: timedelta = timedelta(seconds=20),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._reader = frame_reader
        self._encoder = encoder
        self._repository = repository
        self._store = store
        self._profiles = profiles
        self._capacity = capacity_gate or UnlimitedMediaCapacityGate()
        self._lease_duration = lease_duration
        self._capacity_wait_timeout = capacity_wait_timeout
        self._heartbeat_interval = heartbeat_interval
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def generate(
        self,
        scope: AlignedMediaScopeV1,
        request: AlignedMediaGenerationRequestV1,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> AlignedMediaArtifactV1:
        if scope.project_id != request.project_id:
            raise ValueError("aligned media request does not match its worker scope")
        profile = self._profiles.get(request.profile_id)
        artifact_key = aligned_media_artifact_key(request)
        now = self._now()
        artifact, job = self._repository.ensure_generation(
            scope=scope,
            artifact_key=artifact_key,
            request=request,
            profile_version=profile.profile_version,
            now=now,
        )
        if artifact.retired_at is not None:
            raise RuntimeError("retired aligned media cannot be regenerated in place")
        if artifact.status is AlignedMediaArtifactStatus.READY:
            return artifact
        if job is None:
            raise RuntimeError("non-ready aligned media has no internal job")
        owner_id = f"media:{socket.gethostname()}:{uuid4()}"
        attempt_token = str(uuid4())
        claimed = self._repository.claim_job(
            scope,
            job.job_id,
            owner_id=owner_id,
            attempt_token=attempt_token,
            lease_expires_at=now + self._lease_duration,
            now=now,
        )
        if claimed is None:
            raise RuntimeError("aligned media job is owned by another live attempt")

        slot_id: int | None = None
        heartbeat_stop = Event()
        heartbeat_failed = Event()
        heartbeat: Thread | None = None
        encoded: EncodedAlignedMediaV1 | None = None
        publication: PublishedAlignedMediaV1 | None = None
        receipt_recorded = False
        try:
            slot_id = self._acquire_capacity(
                owner_id=owner_id,
                attempt_token=attempt_token,
                cancelled=cancelled,
            )
            heartbeat = Thread(
                target=self._heartbeat,
                kwargs={
                    "scope": scope,
                    "job_id": claimed.job_id,
                    "owner_id": owner_id,
                    "attempt_token": attempt_token,
                    "slot_id": slot_id,
                    "stop": heartbeat_stop,
                    "failed": heartbeat_failed,
                },
                daemon=True,
            )
            heartbeat.start()

            if artifact.publication_token is not None:
                publication, encoded = self._resume_publication(artifact)
                if self._store.verify_publication(publication):
                    receipt_recorded = True
                else:
                    self._store.delete_exact(publication.objects)
                    self._repository.discard_publication_receipt(
                        scope=scope,
                        job_id=claimed.job_id,
                        artifact_key=artifact_key,
                        owner_id=owner_id,
                        attempt_token=attempt_token,
                        publication_token=publication.publication_token,
                        now=self._now(),
                    )
                    publication = None
                    encoded = None
            if publication is None:
                encoded = self._encoder.encode(
                    artifact_key=artifact_key,
                    request=request,
                    frames=self._reader.read_camera_frames(request),
                    cancelled=lambda: self._cancelled(cancelled, heartbeat_failed),
                )
                publication = self._store.publish(
                    scope=scope,
                    request=request,
                    artifact_key=artifact_key,
                    publication_token=_publication_token(artifact_key),
                    encoded=encoded,
                )
                self._repository.record_publication(
                    scope=scope,
                    job_id=claimed.job_id,
                    artifact_key=artifact_key,
                    owner_id=owner_id,
                    attempt_token=attempt_token,
                    publication=publication,
                    encoded=encoded,
                    now=self._now(),
                )
                receipt_recorded = True
            if heartbeat_failed.is_set():
                raise RuntimeError("aligned media generation lost its lease")
            if publication is None or encoded is None:
                raise RuntimeError("aligned media generation produced no publication receipt")
            return self._repository.mark_ready(
                scope=scope,
                job_id=claimed.job_id,
                artifact_key=artifact_key,
                owner_id=owner_id,
                attempt_token=attempt_token,
                publication=publication,
                encoded=encoded,
                now=self._now(),
            )
        except BaseException as exc:
            code = getattr(exc, "code", "ALIGNED_MEDIA_GENERATION_FAILED")
            if not receipt_recorded:
                if publication is not None:
                    self._store.rollback_publication(publication)
                self._repository.mark_failed(
                    scope=scope,
                    job_id=claimed.job_id,
                    artifact_key=artifact_key,
                    owner_id=owner_id,
                    attempt_token=attempt_token,
                    error_code=str(code),
                    now=self._now(),
                )
            else:
                self._repository.release_job_for_retry(
                    scope=scope,
                    job_id=claimed.job_id,
                    owner_id=owner_id,
                    attempt_token=attempt_token,
                    error_code=str(code),
                    now=self._now(),
                )
            raise
        finally:
            heartbeat_stop.set()
            if heartbeat is not None:
                heartbeat.join(timeout=max(1.0, self._heartbeat_interval.total_seconds() * 2))
            if slot_id is not None:
                self._capacity.release(
                    slot_id=slot_id,
                    owner_id=owner_id,
                    attempt_token=attempt_token,
                )
            if encoded is not None:
                self._encoder.cleanup(encoded)

    def _acquire_capacity(
        self,
        *,
        owner_id: str,
        attempt_token: str,
        cancelled: Callable[[], bool] | None,
    ) -> int:
        deadline = time.monotonic() + self._capacity_wait_timeout.total_seconds()
        while True:
            if cancelled is not None and cancelled():
                raise AlignedMediaGenerationCancelled("aligned media generation was cancelled")
            now = self._now()
            slot = self._capacity.acquire(
                owner_id=owner_id,
                attempt_token=attempt_token,
                lease_expires_at=now + self._lease_duration,
                now=now,
            )
            if slot is not None:
                return slot
            if time.monotonic() >= deadline:
                raise AlignedMediaEncodingError("global media capacity wait timed out")
            time.sleep(0.25)

    def _heartbeat(
        self,
        *,
        scope: AlignedMediaScopeV1,
        job_id: str,
        owner_id: str,
        attempt_token: str,
        slot_id: int,
        stop: Event,
        failed: Event,
    ) -> None:
        while not stop.wait(self._heartbeat_interval.total_seconds()):
            now = self._now()
            lease_expires_at = now + self._lease_duration
            try:
                job_ok = self._repository.renew_job_lease(
                    scope,
                    job_id,
                    owner_id=owner_id,
                    attempt_token=attempt_token,
                    lease_expires_at=lease_expires_at,
                    now=now,
                )
                slot_ok = self._capacity.renew(
                    slot_id=slot_id,
                    owner_id=owner_id,
                    attempt_token=attempt_token,
                    lease_expires_at=lease_expires_at,
                    now=now,
                )
            except Exception:
                failed.set()
                return
            if not job_ok or not slot_ok:
                failed.set()
                return

    @staticmethod
    def _cancelled(
        cancelled: Callable[[], bool] | None,
        heartbeat_failed: Event,
    ) -> bool:
        return heartbeat_failed.is_set() or bool(cancelled and cancelled())

    @staticmethod
    def _resume_publication(
        artifact: AlignedMediaArtifactV1,
    ) -> tuple[PublishedAlignedMediaV1, EncodedAlignedMediaV1]:
        if (
            artifact.object_prefix is None
            or artifact.media_object_key is None
            or artifact.content_sha256 is None
            or artifact.publication_token is None
            or artifact.timeline is None
            or artifact.width is None
            or artifact.height is None
        ):
            raise RuntimeError("persisted aligned media receipt is incomplete")
        intent = next(
            (item for item in artifact.objects if item.media_type == "application/json"),
            None,
        )
        if intent is None:
            raise RuntimeError("persisted aligned media receipt has no intent")
        publication = PublishedAlignedMediaV1(
            object_prefix=artifact.object_prefix,
            media_object_key=artifact.media_object_key,
            objects=artifact.objects,
            total_bytes=artifact.total_bytes,
            content_sha256=artifact.content_sha256,
            publication_token=artifact.publication_token,
            intent_key=intent.key,
        )
        encoded = EncodedAlignedMediaV1(
            file_uri="receipt://persisted",
            frame_count=artifact.frame_count,
            duration_seconds=artifact.duration_seconds,
            width=artifact.width,
            height=artifact.height,
            placeholder_count=artifact.placeholder_count,
            first_timestamp_ns=artifact.timeline.start_timestamp_ns,
        )
        return publication, encoded

    def _now(self) -> datetime:
        value = self._clock()
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
