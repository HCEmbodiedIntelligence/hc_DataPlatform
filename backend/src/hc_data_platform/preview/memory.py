from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4

from .models import (
    EncodedPreviewArtifactV1,
    PreviewArtifactStatus,
    PreviewArtifactV1,
    PreviewCacheRecordV1,
    PreviewFrameV1,
    PreviewJobStatus,
    PreviewJobV1,
    PreviewObjectV1,
    PreviewRequestV1,
    PreviewScopeV1,
    PreviewSessionV1,
    PublishedPreviewArtifactV1,
    RenderFrameV1,
    StepRangeV1,
)


class InMemoryStepReader:
    def __init__(self, frames: Sequence[PreviewFrameV1] = ()) -> None:
        self.frames = list(frames)
        self.calls = 0

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
    ) -> Sequence[PreviewFrameV1]:
        del project_id, dataset_id, lance_version, camera_id
        self.calls += 1
        return tuple(
            frame
            for frame in self.frames
            if frame.rollout_id == rollout_id
            and (start_step is None or frame.step_index >= start_step)
            and (end_step is None or frame.step_index < end_step)
        )


class InMemoryExclusionReader:
    def __init__(self, ranges: Sequence[StepRangeV1] = ()) -> None:
        self.ranges = tuple(ranges)
        self.calls = 0

    def effective_ranges(
        self, *, project_id: str, rollout_id: str, annotation_revision: int
    ) -> Sequence[StepRangeV1]:
        del project_id, rollout_id, annotation_revision
        self.calls += 1
        return self.ranges


class InMemoryMediaEncoder:
    """Captures encoder inputs and returns a deterministic fake HLS artifact."""

    def __init__(self) -> None:
        self.calls: list[tuple[RenderFrameV1, ...]] = []

    def encode(
        self,
        *,
        cache_key: str,
        request: PreviewRequestV1,
        frames: Iterable[RenderFrameV1],
        cancelled: Callable[[], bool] | None = None,
    ) -> EncodedPreviewArtifactV1:
        if cancelled is not None and cancelled():
            raise RuntimeError("preview generation was cancelled")
        captured = tuple(frames)
        self.calls.append(captured)
        return EncodedPreviewArtifactV1(
            artifact_uri=f"memory://previews/{cache_key}/index.m3u8",
            duration_seconds=len(captured) / request.frequency_hz if captured else 0,
            frame_count=len(captured),
        )

    def cleanup(self, encoded: EncodedPreviewArtifactV1) -> None:
        del encoded


class InMemoryPreviewCache:
    def __init__(self) -> None:
        self._by_key: dict[str, PreviewCacheRecordV1] = {}
        self._by_session: dict[str, PreviewCacheRecordV1] = {}

    def get(self, cache_key: str, *, now: datetime) -> PreviewCacheRecordV1 | None:
        record = self._by_key.get(cache_key)
        if record is None or record.cache_expires_at <= now:
            return None
        return record

    def get_by_session(self, session_id: str, *, now: datetime) -> PreviewCacheRecordV1 | None:
        record = self._by_session.get(session_id)
        if record is None or record.cache_expires_at <= now:
            return None
        return record

    def put(self, record: PreviewCacheRecordV1) -> None:
        self._by_key[record.cache_key] = record
        self._by_session[record.session_id] = record


class HmacUrlSigner:
    """Signs relative API capability URLs, never object-store or file URLs."""

    def __init__(self, secret: bytes = b"preview-development-only") -> None:
        self._secret = secret

    def sign(
        self,
        *,
        session_id: str,
        artifact_uri: str,
        asset_name: str,
        expires_at: datetime,
    ) -> str:
        expires = int(expires_at.timestamp())
        signature = self._signature(
            session_id=session_id,
            artifact_uri=artifact_uri,
            asset_name=asset_name,
            expires=expires,
        )
        return (
            f"/api/v1/previews/sessions/{quote(session_id, safe='')}/media/"
            f"{quote(asset_name, safe='')}?expires={expires}&sig={signature}"
        )

    def verify(
        self,
        *,
        session_id: str,
        artifact_uri: str,
        asset_name: str,
        expires: int,
        signature: str,
        now: datetime,
    ) -> bool:
        if now.tzinfo is None:
            raise ValueError("preview signature clock must be timezone-aware")
        if expires <= int(now.astimezone(timezone.utc).timestamp()):
            return False
        expected = self._signature(
            session_id=session_id,
            artifact_uri=artifact_uri,
            asset_name=asset_name,
            expires=expires,
        )
        return hmac.compare_digest(expected, signature)

    def _signature(
        self,
        *,
        session_id: str,
        artifact_uri: str,
        asset_name: str,
        expires: int,
    ) -> str:
        payload = json.dumps(
            {
                "artifact_uri": artifact_uri,
                "asset_name": asset_name,
                "expires": expires,
                "session_id": session_id,
                "version": 1,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()


class InMemoryPreviewMediaReader:
    """The in-memory preview composition has no physical media to expose."""

    def resolve(self, record: PreviewCacheRecordV1, *, asset_name: str) -> Path:
        del record, asset_name
        raise FileNotFoundError


class InMemoryPreviewRepository:
    """Thread-safe durable-state reference used by API and concurrency tests."""

    def __init__(self) -> None:
        self._artifacts: dict[tuple[str, str, str, str], PreviewArtifactV1] = {}
        self._jobs: dict[tuple[str, str, str, str], PreviewJobV1] = {}
        self._sessions: dict[tuple[str, str, str, str], PreviewSessionV1] = {}
        self._lock = RLock()

    @staticmethod
    def _scope_key(scope: PreviewScopeV1) -> tuple[str, str, str]:
        return scope.organization_id, scope.project_id, scope.region_code

    def find_artifact(
        self, scope: PreviewScopeV1, artifact_key: str, *, now: datetime
    ) -> PreviewArtifactV1 | None:
        with self._lock:
            artifact = self._artifacts.get((*self._scope_key(scope), artifact_key))
            if artifact is None or artifact.expires_at <= now:
                return None
            return artifact

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
    ) -> tuple[PreviewArtifactV1, PreviewJobV1 | None]:
        key = (*self._scope_key(scope), artifact_key)
        with self._lock:
            artifact = self._artifacts.get(key)
            if artifact is None:
                artifact = PreviewArtifactV1(
                    artifact_id=str(uuid4()),
                    artifact_key=artifact_key,
                    scope=scope,
                    dataset_id=request.dataset_id,
                    rollout_id=request.rollout_id,
                    lance_version=request.lance_version,
                    camera_id=request.camera_id,
                    profile_id=request.profile_id,
                    pipeline_revision=pipeline_revision,
                    source_start_step=request.start_step,
                    source_end_step=request.end_step,
                    status=PreviewArtifactStatus.GENERATING,
                    rebuild_source_id=rebuild_source_id,
                    created_at=now,
                    last_accessed_at=now,
                    expires_at=now + artifact_ttl,
                )
                self._artifacts[key] = artifact
            elif artifact.status is PreviewArtifactStatus.READY and artifact.expires_at > now:
                return artifact, None
            elif artifact.status is PreviewArtifactStatus.DELETING:
                deleting_job = next(
                    (
                        job
                        for job in self._jobs.values()
                        if job.scope == scope
                        and job.artifact_key == artifact_key
                        and job.error_code == "PREVIEW_ARTIFACT_DELETING"
                    ),
                    None,
                )
                if deleting_job is None:
                    deleting_job = PreviewJobV1(
                        job_id=str(uuid4()),
                        artifact_id=artifact.artifact_id,
                        artifact_key=artifact_key,
                        scope=scope,
                        status=PreviewJobStatus.FAILED,
                        request=request,
                        error_code="PREVIEW_ARTIFACT_DELETING",
                        created_at=now,
                        completed_at=now,
                    )
                    self._jobs[
                        (*self._scope_key(scope), deleting_job.job_id)
                    ] = deleting_job
                return artifact, deleting_job
            elif (
                artifact.status is PreviewArtifactStatus.FAILED
                or artifact.expires_at <= now
            ):
                artifact = artifact.model_copy(
                    update={
                        "status": PreviewArtifactStatus.GENERATING,
                        "failure_code": None,
                        "expires_at": now + artifact_ttl,
                        "version": artifact.version + 1,
                    }
                )
                self._artifacts[key] = artifact
            active = next(
                (
                    job
                    for job in self._jobs.values()
                    if job.scope == scope
                    and job.artifact_key == artifact_key
                    and job.status in {PreviewJobStatus.QUEUED, PreviewJobStatus.RUNNING}
                ),
                None,
            )
            if active is None:
                active = PreviewJobV1(
                    job_id=str(uuid4()),
                    artifact_id=artifact.artifact_id,
                    artifact_key=artifact_key,
                    scope=scope,
                    status=PreviewJobStatus.QUEUED,
                    request=request,
                    created_at=now,
                )
                self._jobs[(*self._scope_key(scope), active.job_id)] = active
            return artifact, active

    def get_job(self, scope: PreviewScopeV1, job_id: str) -> PreviewJobV1 | None:
        with self._lock:
            return self._jobs.get((*self._scope_key(scope), job_id))

    def mark_job_running(self, scope: PreviewScopeV1, job_id: str, *, now: datetime) -> None:
        key = (*self._scope_key(scope), job_id)
        with self._lock:
            job = self._jobs[key]
            if job.status is PreviewJobStatus.SUCCEEDED:
                return
            self._jobs[key] = job.model_copy(
                update={
                    "status": PreviewJobStatus.RUNNING,
                    "attempt": job.attempt + 1,
                    "progress": max(job.progress, 1),
                    "started_at": job.started_at or now,
                    "error_code": None,
                }
            )

    def update_job_progress(
        self, scope: PreviewScopeV1, job_id: str, *, progress: int
    ) -> None:
        key = (*self._scope_key(scope), job_id)
        with self._lock:
            job = self._jobs[key]
            self._jobs[key] = job.model_copy(update={"progress": progress})

    def record_publication(
        self,
        *,
        scope: PreviewScopeV1,
        artifact_key: str,
        publication: PublishedPreviewArtifactV1,
    ) -> None:
        key = (*self._scope_key(scope), artifact_key)
        with self._lock:
            artifact = self._artifacts[key]
            if artifact.status is PreviewArtifactStatus.READY:
                return
            if artifact.status is not PreviewArtifactStatus.GENERATING:
                raise RuntimeError("preview artifact is not accepting a publication receipt")
            self._artifacts[key] = artifact.model_copy(
                update={
                    "object_prefix": publication.object_prefix,
                    "playlist_key": publication.playlist_key,
                    "objects": publication.objects,
                    "total_bytes": publication.total_bytes,
                    "content_sha256": publication.content_sha256,
                }
            )

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
    ) -> PreviewArtifactV1:
        artifact_map_key = (*self._scope_key(scope), artifact_key)
        job_map_key = (*self._scope_key(scope), job_id)
        with self._lock:
            artifact = self._artifacts[artifact_map_key]
            ready = artifact.model_copy(
                update={
                    "status": PreviewArtifactStatus.READY,
                    "object_prefix": publication.object_prefix,
                    "playlist_key": publication.playlist_key,
                    "objects": publication.objects,
                    "total_bytes": publication.total_bytes,
                    "frame_count": frame_count,
                    "duration_seconds": duration_seconds,
                    "content_sha256": publication.content_sha256,
                    "ready_at": now,
                    "last_accessed_at": now,
                    "failure_code": None,
                    "version": artifact.version + 1,
                }
            )
            self._artifacts[artifact_map_key] = ready
            job = self._jobs[job_map_key]
            self._jobs[job_map_key] = job.model_copy(
                update={
                    "status": PreviewJobStatus.SUCCEEDED,
                    "progress": 100,
                    "completed_at": now,
                    "error_code": None,
                }
            )
            return ready

    def mark_failed(
        self,
        *,
        scope: PreviewScopeV1,
        job_id: str,
        artifact_key: str,
        error_code: str,
        now: datetime,
    ) -> None:
        with self._lock:
            artifact_key_tuple = (*self._scope_key(scope), artifact_key)
            artifact = self._artifacts[artifact_key_tuple]
            if artifact.status is PreviewArtifactStatus.READY:
                return
            self._artifacts[artifact_key_tuple] = artifact.model_copy(
                update={
                    "status": PreviewArtifactStatus.FAILED,
                    "failure_code": error_code,
                    "expires_at": min(artifact.expires_at, now),
                    "version": artifact.version + 1,
                }
            )
            job_key = (*self._scope_key(scope), job_id)
            job = self._jobs[job_key]
            self._jobs[job_key] = job.model_copy(
                update={
                    "status": PreviewJobStatus.FAILED,
                    "error_code": error_code,
                    "completed_at": now,
                }
            )

    def create_session(
        self,
        *,
        scope: PreviewScopeV1,
        artifact: PreviewArtifactV1,
        request: PreviewRequestV1,
        session_ttl: timedelta,
        now: datetime,
    ) -> PreviewSessionV1:
        with self._lock:
            session = PreviewSessionV1(
                session_id=str(uuid4()),
                artifact_id=artifact.artifact_id,
                artifact_key=artifact.artifact_key,
                scope=scope,
                request=request,
                created_at=now,
                expires_at=now + session_ttl,
            )
            self._sessions[(*self._scope_key(scope), session.session_id)] = session
            artifact_key = (*self._scope_key(scope), artifact.artifact_key)
            current = self._artifacts[artifact_key]
            self._artifacts[artifact_key] = current.model_copy(
                update={
                    "last_accessed_at": now,
                    "active_reference_count": current.active_reference_count + 1,
                }
            )
            return session

    def get_session(
        self, scope: PreviewScopeV1, session_id: str, *, now: datetime
    ) -> tuple[PreviewSessionV1, PreviewArtifactV1] | None:
        with self._lock:
            session = self._sessions.get((*self._scope_key(scope), session_id))
            if session is None or session.expires_at <= now:
                return None
            artifact = self._artifacts.get(
                (*self._scope_key(scope), session.artifact_key)
            )
            if (
                artifact is None
                or artifact.status is not PreviewArtifactStatus.READY
                or artifact.expires_at <= now
            ):
                return None
            self._artifacts[(*self._scope_key(scope), artifact.artifact_key)] = (
                artifact.model_copy(update={"last_accessed_at": now})
            )
            return session, artifact

    def gc_candidates(
        self,
        *,
        now: datetime,
        project_quota_bytes: int,
        global_quota_bytes: int,
        high_watermark_percent: int,
        low_watermark_percent: int,
        limit: int,
    ) -> Sequence[PreviewArtifactV1]:
        del low_watermark_percent
        with self._lock:
            ready = [
                artifact
                for artifact in self._artifacts.values()
                if artifact.status is PreviewArtifactStatus.READY
            ]
            reclaimable = [
                artifact
                for artifact in self._artifacts.values()
                if artifact.status
                in {PreviewArtifactStatus.READY, PreviewArtifactStatus.FAILED}
            ]
            total = sum(item.total_bytes for item in ready)
            global_over_high = (
                total * 100 >= global_quota_bytes * high_watermark_percent
            )
            project_totals: dict[tuple[str, str, str], int] = {}
            for artifact in ready:
                key = self._scope_key(artifact.scope)
                project_totals[key] = project_totals.get(key, 0) + artifact.total_bytes
            candidates = [
                artifact
                for artifact in reclaimable
                if (
                    artifact.expires_at <= now
                    or global_over_high
                    or project_totals.get(self._scope_key(artifact.scope), 0) * 100
                    >= project_quota_bytes * high_watermark_percent
                )
                and artifact.active_reference_count == 0
                and not artifact.legal_hold
                and not artifact.governance_hold
                and (artifact.retention_until is None or artifact.retention_until <= now)
            ]
            return tuple(
                sorted(
                    candidates,
                    key=lambda item: (
                        item.expires_at > now,
                        item.last_accessed_at,
                        item.artifact_id,
                    ),
                )[:limit]
            )

    def mark_deleting(
        self, scope: PreviewScopeV1, artifact_id: str, *, expected_version: int
    ) -> bool:
        with self._lock:
            match = next(
                (
                    (key, value)
                    for key, value in self._artifacts.items()
                    if value.scope == scope and value.artifact_id == artifact_id
                ),
                None,
            )
            if (
                match is None
                or match[1].version != expected_version
                or match[1].status
                not in {PreviewArtifactStatus.READY, PreviewArtifactStatus.FAILED}
            ):
                return False
            key, artifact = match
            self._artifacts[key] = artifact.model_copy(
                update={
                    "status": PreviewArtifactStatus.DELETING,
                    "version": artifact.version + 1,
                }
            )
            return True

    def delete_artifact_metadata(self, scope: PreviewScopeV1, artifact_id: str) -> None:
        with self._lock:
            keys = [
                key
                for key, artifact in self._artifacts.items()
                if artifact.scope == scope and artifact.artifact_id == artifact_id
            ]
            for key in keys:
                self._artifacts.pop(key, None)
            self._jobs = {
                key: job
                for key, job in self._jobs.items()
                if not (job.scope == scope and job.artifact_id == artifact_id)
            }
            self._sessions = {
                key: session
                for key, session in self._sessions.items()
                if not (session.scope == scope and session.artifact_id == artifact_id)
            }

    def deleting_artifacts(self, *, limit: int) -> Sequence[PreviewArtifactV1]:
        with self._lock:
            return tuple(
                item
                for item in self._artifacts.values()
                if item.status is PreviewArtifactStatus.DELETING
            )[:limit]

    def cleanup_expired_metadata(self, *, now: datetime) -> int:
        with self._lock:
            expired = [key for key, item in self._sessions.items() if item.expires_at <= now]
            for key in expired:
                session = self._sessions.pop(key)
                artifact_key = (*self._scope_key(session.scope), session.artifact_key)
                artifact = self._artifacts.get(artifact_key)
                if artifact is not None:
                    self._artifacts[artifact_key] = artifact.model_copy(
                        update={
                            "active_reference_count": max(
                                0, artifact.active_reference_count - 1
                            )
                        }
                    )
            return len(expired)

    def storage_totals(self) -> tuple[int, int]:
        with self._lock:
            ready = [
                item
                for item in self._artifacts.values()
                if item.status is PreviewArtifactStatus.READY
            ]
            return sum(item.total_bytes for item in ready), len(ready)

    def set_protection(
        self,
        scope: PreviewScopeV1,
        artifact_key: str,
        *,
        legal_hold: bool = False,
        governance_hold: bool = False,
        retention_until: datetime | None = None,
    ) -> None:
        """In-memory policy fixture equivalent to governance-owned SQL updates."""

        with self._lock:
            key = (*self._scope_key(scope), artifact_key)
            artifact = self._artifacts[key]
            self._artifacts[key] = artifact.model_copy(
                update={
                    "legal_hold": legal_hold,
                    "governance_hold": governance_hold,
                    "retention_until": retention_until,
                }
            )


class InMemoryPreviewArtifactStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.delete_calls: list[tuple[str, ...]] = []

    def publish(
        self,
        *,
        project_id: str,
        artifact_key: str,
        encoded: EncodedPreviewArtifactV1,
    ) -> PublishedPreviewArtifactV1:
        prefix = f"derived/previews/{project_id}/{artifact_key}"
        parsed = urlparse(encoded.artifact_uri)
        files: list[tuple[str, bytes]] = []
        if parsed.scheme == "file":
            playlist = Path(unquote(parsed.path))
            for path in sorted(playlist.parent.iterdir()):
                if path.is_file():
                    files.append((path.name, path.read_bytes()))
        else:
            files = [
                ("init.mp4", b"init"),
                ("segment_00000.m4s", b"segment"),
                (
                    "index.m3u8",
                    b'#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:1,\nsegment_00000.m4s\n#EXT-X-ENDLIST\n',
                ),
            ]
        records: list[PreviewObjectV1] = []
        # Playlist is intentionally made visible last.
        files.sort(key=lambda item: item[0] == "index.m3u8")
        content = hashlib.sha256()
        for name, body in files:
            key = f"{prefix}/{name}"
            self.objects[key] = body
            digest = hashlib.sha256(body).hexdigest()
            content.update(name.encode())
            content.update(b"\0")
            content.update(body)
            records.append(
                PreviewObjectV1(
                    key=key,
                    size=len(body),
                    sha256=digest,
                    etag=digest,
                    media_type=mimetypes.guess_type(name)[0]
                    or "application/octet-stream",
                )
            )
        return PublishedPreviewArtifactV1(
            object_prefix=prefix,
            playlist_key=f"{prefix}/index.m3u8",
            objects=tuple(records),
            total_bytes=sum(item.size for item in records),
            content_sha256=content.hexdigest(),
        )

    def read_playlist(self, playlist_key: str) -> str:
        return self.objects[playlist_key].decode("utf-8")

    def authorize_object(self, object_key: str, *, expires_in: timedelta) -> str:
        return f"https://objects.invalid/{quote(object_key)}?ttl={int(expires_in.total_seconds())}"

    def delete_exact(self, objects: Sequence[PreviewObjectV1]) -> int:
        self.delete_calls.append(tuple(item.key for item in objects))
        deleted = 0
        for item in objects:
            if self.objects.pop(item.key, None) is not None:
                deleted += item.size
        return deleted

    def resolve_local_object(self, object_key: str) -> Path | None:
        del object_key
        return None
