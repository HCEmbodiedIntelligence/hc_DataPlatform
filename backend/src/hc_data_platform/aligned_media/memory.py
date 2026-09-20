from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from threading import RLock
from uuid import uuid4

from .models import (
    AlignedMediaArtifactStatus,
    AlignedMediaArtifactV1,
    AlignedMediaGenerationRequestV1,
    AlignedMediaJobStatus,
    AlignedMediaJobV1,
    AlignedMediaObjectV1,
    AlignedMediaScopeV1,
    AlignedMediaSelectorV1,
    AlignedMediaTimelineV1,
    EncodedAlignedMediaV1,
    PublishedAlignedMediaV1,
)


class InMemoryAlignedMediaRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._artifacts: dict[tuple[str, str, str, str], AlignedMediaArtifactV1] = {}
        self._jobs: dict[tuple[str, str, str, str], AlignedMediaJobV1] = {}

    @staticmethod
    def _scope(scope: AlignedMediaScopeV1) -> tuple[str, str, str]:
        return scope.organization_id, scope.project_id, scope.region_code

    def find_by_selector(
        self,
        scope: AlignedMediaScopeV1,
        selector: AlignedMediaSelectorV1,
        *,
        profile_id: str,
    ) -> AlignedMediaArtifactV1 | None:
        with self._lock:
            matches = [
                artifact
                for key, artifact in self._artifacts.items()
                if key[:3] == self._scope(scope)
                and artifact.dataset_id == selector.dataset_id
                and artifact.rollout_id == selector.rollout_id
                and artifact.dataset_version == selector.dataset_version
                and artifact.camera_id == selector.camera_id
                and artifact.profile_id == profile_id
            ]
            if len(matches) > 1:
                raise RuntimeError("aligned media selector is not unique")
            return matches[0] if matches else None

    def ensure_generation(
        self,
        *,
        scope: AlignedMediaScopeV1,
        artifact_key: str,
        request: AlignedMediaGenerationRequestV1,
        profile_version: str,
        now: datetime,
    ) -> tuple[AlignedMediaArtifactV1, AlignedMediaJobV1 | None]:
        key = (*self._scope(scope), artifact_key)
        with self._lock:
            artifact = self._artifacts.get(key)
            if artifact is None:
                artifact = AlignedMediaArtifactV1(
                    artifact_id=str(uuid4()),
                    artifact_key=artifact_key,
                    scope=scope,
                    dataset_id=request.dataset_id,
                    rollout_id=request.rollout_id,
                    dataset_version=request.expected_dataset_version,
                    camera_id=request.camera_id,
                    profile_id=request.profile_id,
                    profile_version=profile_version,
                    alignment_version=request.alignment.alignment_version,
                    source_sha256=request.source_sha256,
                    status=AlignedMediaArtifactStatus.GENERATING,
                    created_at=now,
                )
                self._artifacts[key] = artifact
            if artifact.status is AlignedMediaArtifactStatus.READY:
                return artifact, None
            if artifact.status is AlignedMediaArtifactStatus.ABANDONING:
                raise RuntimeError("aligned media artifact cleanup is still in progress")
            active = next(
                (
                    job
                    for job_key, job in self._jobs.items()
                    if job_key[:3] == self._scope(scope)
                    and job.artifact_key == artifact_key
                    and job.status in {AlignedMediaJobStatus.QUEUED, AlignedMediaJobStatus.RUNNING}
                ),
                None,
            )
            if active is None:
                active = AlignedMediaJobV1(
                    job_id=str(uuid4()),
                    artifact_id=artifact.artifact_id,
                    artifact_key=artifact_key,
                    scope=scope,
                    status=AlignedMediaJobStatus.QUEUED,
                    request=request,
                    created_at=now,
                )
                self._jobs[(*self._scope(scope), active.job_id)] = active
            if artifact.status is AlignedMediaArtifactStatus.FAILED:
                artifact = artifact.model_copy(
                    update={
                        "status": AlignedMediaArtifactStatus.GENERATING,
                        "failure_code": None,
                        "version": artifact.version + 1,
                    }
                )
                self._artifacts[key] = artifact
            return artifact, active

    def claim_job(
        self,
        scope: AlignedMediaScopeV1,
        job_id: str,
        *,
        owner_id: str,
        attempt_token: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> AlignedMediaJobV1 | None:
        key = (*self._scope(scope), job_id)
        with self._lock:
            job = self._jobs.get(key)
            if job is None or not (
                job.status is AlignedMediaJobStatus.QUEUED
                or (
                    job.status is AlignedMediaJobStatus.RUNNING
                    and (job.lease_expires_at is None or job.lease_expires_at <= now)
                )
            ):
                return None
            claimed = job.model_copy(
                update={
                    "status": AlignedMediaJobStatus.RUNNING,
                    "attempt": job.attempt + 1,
                    "progress": max(job.progress, 1),
                    "owner_id": owner_id,
                    "attempt_token": attempt_token,
                    "lease_expires_at": lease_expires_at,
                    "started_at": job.started_at or now,
                    "completed_at": None,
                    "error_code": None,
                }
            )
            self._jobs[key] = claimed
            return claimed

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
    ) -> bool:
        key = (*self._scope(scope), job_id)
        with self._lock:
            job = self._jobs.get(key)
            if (
                job is None
                or job.status is not AlignedMediaJobStatus.RUNNING
                or job.owner_id != owner_id
                or job.attempt_token != attempt_token
                or job.lease_expires_at is None
                or job.lease_expires_at <= now
            ):
                return False
            self._jobs[key] = job.model_copy(
                update={
                    "lease_expires_at": lease_expires_at,
                    "progress": max(job.progress, progress or job.progress),
                }
            )
            return True

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
    ) -> None:
        del now
        with self._lock:
            job = self._jobs.get((*self._scope(scope), job_id))
            artifact_key_tuple = (*self._scope(scope), artifact_key)
            artifact = self._artifacts.get(artifact_key_tuple)
            if (
                job is None
                or artifact is None
                or job.status is not AlignedMediaJobStatus.RUNNING
                or job.owner_id != owner_id
                or job.attempt_token != attempt_token
            ):
                raise RuntimeError("aligned media publication lost its job fence")
            timeline = AlignedMediaTimelineV1(
                frame_count=encoded.frame_count,
                start_timestamp_ns=encoded.first_timestamp_ns,
                original_source=encoded.original_source,
            )
            self._artifacts[artifact_key_tuple] = artifact.model_copy(
                update={
                    "object_prefix": publication.object_prefix,
                    "media_object_key": publication.media_object_key,
                    "objects": publication.objects,
                    "total_bytes": publication.total_bytes,
                    "frame_count": encoded.frame_count,
                    "duration_seconds": encoded.duration_seconds,
                    "width": encoded.width,
                    "height": encoded.height,
                    "content_sha256": publication.content_sha256,
                    "publication_token": publication.publication_token,
                    "timeline": timeline,
                    "placeholder_count": encoded.placeholder_count,
                }
            )

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
    ) -> AlignedMediaArtifactV1:
        del publication, encoded
        with self._lock:
            job_key = (*self._scope(scope), job_id)
            artifact_key_tuple = (*self._scope(scope), artifact_key)
            job = self._jobs.get(job_key)
            artifact = self._artifacts.get(artifact_key_tuple)
            if (
                job is None
                or artifact is None
                or artifact.publication_token is None
                or job.status is not AlignedMediaJobStatus.RUNNING
                or job.owner_id != owner_id
                or job.attempt_token != attempt_token
            ):
                raise RuntimeError("aligned media READY transition lost its fence")
            ready = artifact.model_copy(
                update={
                    "status": AlignedMediaArtifactStatus.READY,
                    "ready_at": now,
                    "commit_lease_expires_at": now + timedelta(hours=24),
                    "failure_code": None,
                    "version": artifact.version + 1,
                }
            )
            self._artifacts[artifact_key_tuple] = ready
            self._jobs[job_key] = job.model_copy(
                update={
                    "status": AlignedMediaJobStatus.SUCCEEDED,
                    "progress": 100,
                    "completed_at": now,
                    "owner_id": None,
                    "attempt_token": None,
                    "lease_expires_at": None,
                }
            )
            return ready

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
    ) -> None:
        with self._lock:
            job = self._jobs.get((*self._scope(scope), job_id))
            key = (*self._scope(scope), artifact_key)
            artifact = self._artifacts.get(key)
            if (
                job is None
                or artifact is None
                or artifact.status is not AlignedMediaArtifactStatus.GENERATING
                or artifact.publication_token != publication_token
                or job.status is not AlignedMediaJobStatus.RUNNING
                or job.owner_id != owner_id
                or job.attempt_token != attempt_token
                or job.lease_expires_at is None
                or job.lease_expires_at <= now
            ):
                raise RuntimeError("aligned media receipt discard lost its job fence")
            self._artifacts[key] = artifact.model_copy(
                update={
                    "object_prefix": None,
                    "media_object_key": None,
                    "objects": (),
                    "total_bytes": 0,
                    "frame_count": 0,
                    "duration_seconds": 0,
                    "width": None,
                    "height": None,
                    "content_sha256": None,
                    "publication_token": None,
                    "timeline": None,
                    "placeholder_count": 0,
                    "ready_at": None,
                    "commit_lease_expires_at": None,
                    "version": artifact.version + 1,
                }
            )

    def release_job_for_retry(
        self,
        *,
        scope: AlignedMediaScopeV1,
        job_id: str,
        owner_id: str,
        attempt_token: str,
        error_code: str,
        now: datetime,
    ) -> bool:
        del now
        key = (*self._scope(scope), job_id)
        with self._lock:
            job = self._jobs.get(key)
            if (
                job is None
                or job.status is not AlignedMediaJobStatus.RUNNING
                or job.owner_id != owner_id
                or job.attempt_token != attempt_token
            ):
                return False
            self._jobs[key] = job.model_copy(
                update={
                    "status": AlignedMediaJobStatus.QUEUED,
                    "owner_id": None,
                    "attempt_token": None,
                    "lease_expires_at": None,
                    "error_code": error_code,
                }
            )
            return True

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
    ) -> None:
        with self._lock:
            job_key = (*self._scope(scope), job_id)
            artifact_key_tuple = (*self._scope(scope), artifact_key)
            job = self._jobs.get(job_key)
            artifact = self._artifacts.get(artifact_key_tuple)
            if job is not None and job.owner_id == owner_id and job.attempt_token == attempt_token:
                self._jobs[job_key] = job.model_copy(
                    update={
                        "status": AlignedMediaJobStatus.FAILED,
                        "error_code": error_code,
                        "completed_at": now,
                        "owner_id": None,
                        "attempt_token": None,
                        "lease_expires_at": None,
                    }
                )
            if artifact is not None and artifact.status is not AlignedMediaArtifactStatus.READY:
                self._artifacts[artifact_key_tuple] = artifact.model_copy(
                    update={
                        "status": AlignedMediaArtifactStatus.FAILED,
                        "failure_code": error_code,
                        "version": artifact.version + 1,
                    }
                )

    def begin_dataset_commit(
        self,
        *,
        scope: AlignedMediaScopeV1,
        artifact_ids: Sequence[str],
        lease_expires_at: datetime,
        now: datetime,
    ) -> None:
        ids = _artifact_ids(artifact_ids)
        with self._lock:
            artifacts = self._selected(scope, ids)
            if len(artifacts) != len(ids) or any(
                artifact.status is not AlignedMediaArtifactStatus.READY
                for _key, artifact in artifacts
            ):
                raise RuntimeError("aligned media commit lease rejected an incomplete bundle")
            for key, artifact in artifacts:
                self._artifacts[key] = artifact.model_copy(
                    update={
                        "commit_started_at": (
                            None if artifact.dataset_committed_at is not None else now
                        ),
                        "commit_lease_expires_at": (
                            None if artifact.dataset_committed_at is not None else lease_expires_at
                        ),
                    }
                )

    def mark_dataset_committed(
        self,
        *,
        scope: AlignedMediaScopeV1,
        artifact_ids: Sequence[str],
        now: datetime,
    ) -> None:
        ids = _artifact_ids(artifact_ids)
        with self._lock:
            artifacts = self._selected(scope, ids)
            if len(artifacts) != len(ids) or any(
                artifact.status is not AlignedMediaArtifactStatus.READY
                for _key, artifact in artifacts
            ):
                raise RuntimeError("aligned media commit marker rejected an incomplete bundle")
            for key, artifact in artifacts:
                self._artifacts[key] = artifact.model_copy(
                    update={
                        "dataset_committed_at": artifact.dataset_committed_at or now,
                        "commit_lease_expires_at": None,
                        "commit_started_at": None,
                        "version": (
                            artifact.version + 1
                            if artifact.dataset_committed_at is None
                            else artifact.version
                        ),
                    }
                )

    def begin_abandon_uncommitted(
        self,
        *,
        scope: AlignedMediaScopeV1,
        artifact_ids: Sequence[str],
        error_code: str,
        now: datetime,
    ) -> tuple[AlignedMediaObjectV1, ...]:
        ids = _artifact_ids(artifact_ids)
        objects: list[AlignedMediaObjectV1] = []
        with self._lock:
            for key, artifact in self._selected(scope, ids):
                if artifact.dataset_committed_at is not None:
                    continue
                if artifact.status is AlignedMediaArtifactStatus.READY:
                    if (
                        artifact.commit_started_at is not None
                        and artifact.commit_lease_expires_at is not None
                        and artifact.commit_lease_expires_at > now
                    ):
                        continue
                    artifact = artifact.model_copy(
                        update={
                            "status": AlignedMediaArtifactStatus.ABANDONING,
                            "failure_code": error_code,
                            "commit_lease_expires_at": None,
                            "commit_started_at": None,
                            "version": artifact.version + 1,
                        }
                    )
                    self._artifacts[key] = artifact
                if artifact.status is AlignedMediaArtifactStatus.ABANDONING:
                    objects.extend(artifact.objects)
        return tuple(objects)

    def complete_abandon_uncommitted(
        self,
        *,
        scope: AlignedMediaScopeV1,
        artifact_ids: Sequence[str],
        now: datetime,
    ) -> None:
        del now
        ids = _artifact_ids(artifact_ids)
        with self._lock:
            for key, artifact in self._selected(scope, ids):
                if (
                    artifact.dataset_committed_at is not None
                    or artifact.status is not AlignedMediaArtifactStatus.ABANDONING
                ):
                    continue
                self._artifacts[key] = artifact.model_copy(
                    update={
                        "status": AlignedMediaArtifactStatus.FAILED,
                        "object_prefix": None,
                        "media_object_key": None,
                        "objects": (),
                        "total_bytes": 0,
                        "frame_count": 0,
                        "duration_seconds": 0,
                        "width": None,
                        "height": None,
                        "content_sha256": None,
                        "publication_token": None,
                        "timeline": None,
                        "placeholder_count": 0,
                        "ready_at": None,
                        "commit_lease_expires_at": None,
                        "commit_started_at": None,
                        "version": artifact.version + 1,
                    }
                )

    def begin_retire_dataset_version(
        self,
        *,
        scope: AlignedMediaScopeV1,
        dataset_id: str,
        dataset_version: int,
        now: datetime,
    ) -> tuple[AlignedMediaObjectV1, ...]:
        objects: list[AlignedMediaObjectV1] = []
        with self._lock:
            for key, artifact in tuple(self._artifacts.items()):
                if (
                    key[:3] != self._scope(scope)
                    or artifact.dataset_id != dataset_id
                    or artifact.dataset_version != dataset_version
                    or artifact.dataset_committed_at is None
                    or artifact.deleted_at is not None
                ):
                    continue
                if artifact.retired_at is None:
                    artifact = artifact.model_copy(
                        update={
                            "retired_at": now,
                            "commit_lease_expires_at": None,
                            "commit_started_at": None,
                            "version": artifact.version + 1,
                        }
                    )
                    self._artifacts[key] = artifact
                objects.extend(artifact.objects)
        return tuple(objects)

    def complete_retire_dataset_version(
        self,
        *,
        scope: AlignedMediaScopeV1,
        dataset_id: str,
        dataset_version: int,
        now: datetime,
    ) -> None:
        with self._lock:
            for key, artifact in tuple(self._artifacts.items()):
                if (
                    key[:3] == self._scope(scope)
                    and artifact.dataset_id == dataset_id
                    and artifact.dataset_version == dataset_version
                    and artifact.retired_at is not None
                    and artifact.deleted_at is None
                ):
                    self._artifacts[key] = artifact.model_copy(
                        update={"deleted_at": now, "version": artifact.version + 1}
                    )

    def publication_is_referenced(
        self,
        scope: AlignedMediaScopeV1,
        artifact_key: str,
        publication_token: str,
        *,
        now: datetime,
    ) -> bool:
        with self._lock:
            artifact = self._artifacts.get((*self._scope(scope), artifact_key))
            if (
                artifact is not None
                and artifact.publication_token == publication_token
                and artifact.retired_at is None
                and (
                    artifact.dataset_committed_at is not None
                    or (
                        artifact.commit_lease_expires_at is not None
                        and artifact.commit_lease_expires_at > now
                    )
                )
            ):
                return True
            return (
                artifact is not None
                and artifact.retired_at is None
                and any(
                    job.artifact_id == artifact.artifact_id
                    and job.status is AlignedMediaJobStatus.RUNNING
                    and job.lease_expires_at is not None
                    and job.lease_expires_at > now
                    for job in self._jobs.values()
                )
            )

    def _selected(
        self,
        scope: AlignedMediaScopeV1,
        artifact_ids: Sequence[str],
    ) -> list[tuple[tuple[str, str, str, str], AlignedMediaArtifactV1]]:
        selected = set(artifact_ids)
        return [
            (key, artifact)
            for key, artifact in self._artifacts.items()
            if key[:3] == self._scope(scope) and artifact.artifact_id in selected
        ]


def _artifact_ids(values: Sequence[str]) -> tuple[str, ...]:
    result = tuple(dict.fromkeys(values))
    if not result or len(result) != len(values):
        raise ValueError("aligned media artifact ids must be non-empty and unique")
    return result
