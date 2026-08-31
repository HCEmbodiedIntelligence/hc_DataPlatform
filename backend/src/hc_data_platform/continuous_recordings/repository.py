from __future__ import annotations

import json
from collections.abc import Callable
from threading import RLock
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.core.errors import problem
from hc_data_platform.security.audit import canonical_hash

from .asset_models import EpisodeProcessing, EpisodeProcessingStatus
from .models import (
    ContinuousRecording,
    EpisodeSlice,
    RecordingScope,
    RecordingSliceRevision,
    SliceRevisionStatus,
)


class ContinuousRecordingRepository(Protocol):
    def create(
        self, recording: ContinuousRecording, *, actor_id: str, request_id: str
    ) -> ContinuousRecording: ...

    def get(self, scope: RecordingScope, recording_id: str) -> ContinuousRecording | None: ...

    def get_by_upload_session(
        self, scope: RecordingScope, upload_session_id: str
    ) -> ContinuousRecording | None: ...

    def list(self, scope: RecordingScope) -> tuple[ContinuousRecording, ...]: ...

    def get_revision(
        self, scope: RecordingScope, recording_id: str, revision: int
    ) -> RecordingSliceRevision | None: ...

    def save_revision(
        self,
        *,
        recording: ContinuousRecording,
        revision: RecordingSliceRevision,
        expected_version: int,
        actor_id: str,
        request_id: str,
    ) -> ContinuousRecording: ...

    def list_episode_processing(
        self, scope: RecordingScope, recording_id: str
    ) -> tuple[EpisodeProcessing, ...]: ...

    def save_episode_processing(
        self,
        episode: EpisodeProcessing,
        *,
        expected_status: EpisodeProcessingStatus,
    ) -> EpisodeProcessing: ...


class InMemoryContinuousRecordingRepository:
    def __init__(self) -> None:
        self._recordings: dict[tuple[str, str, str, str], ContinuousRecording] = {}
        self._by_upload: dict[tuple[str, str, str, str], str] = {}
        self._revisions: dict[tuple[str, str, str, str, int], RecordingSliceRevision] = {}
        self._episode_processing: dict[tuple[str, str, str, str, str], EpisodeProcessing] = {}
        self._lock = RLock()

    def create(
        self, recording: ContinuousRecording, *, actor_id: str, request_id: str
    ) -> ContinuousRecording:
        del actor_id, request_id
        key = _recording_key(recording.scope, recording.recording_id)
        upload_key = _upload_key(recording.scope, str(recording.upload_session_id))
        with self._lock:
            existing = self._recordings.get(key)
            upload_owner = self._by_upload.get(upload_key)
            if existing is not None:
                if existing != recording:
                    raise _identity_conflict()
                return existing
            if upload_owner is not None and upload_owner != recording.recording_id:
                raise _identity_conflict()
            self._recordings[key] = recording
            self._by_upload[upload_key] = recording.recording_id
            return recording

    def get(self, scope: RecordingScope, recording_id: str) -> ContinuousRecording | None:
        with self._lock:
            return self._recordings.get(_recording_key(scope, recording_id))

    def get_by_upload_session(
        self, scope: RecordingScope, upload_session_id: str
    ) -> ContinuousRecording | None:
        with self._lock:
            recording_id = self._by_upload.get(_upload_key(scope, upload_session_id))
            return (
                None
                if recording_id is None
                else self._recordings[_recording_key(scope, recording_id)]
            )

    def list(self, scope: RecordingScope) -> tuple[ContinuousRecording, ...]:
        with self._lock:
            values = [item for item in self._recordings.values() if item.scope == scope]
        return tuple(
            sorted(
                values,
                key=lambda item: (item.created_at, item.recording_id),
                reverse=True,
            )
        )

    def get_revision(
        self, scope: RecordingScope, recording_id: str, revision: int
    ) -> RecordingSliceRevision | None:
        with self._lock:
            return self._revisions.get((*_recording_key(scope, recording_id), revision))

    def save_revision(
        self,
        *,
        recording: ContinuousRecording,
        revision: RecordingSliceRevision,
        expected_version: int,
        actor_id: str,
        request_id: str,
    ) -> ContinuousRecording:
        del actor_id, request_id
        key = _recording_key(recording.scope, recording.recording_id)
        revision_key = (*key, revision.revision)
        with self._lock:
            current = self._recordings.get(key)
            if current is None:
                raise _not_found()
            if current.current_revision != expected_version:
                raise _version_conflict(current.etag)
            if revision_key in self._revisions:
                raise _version_conflict(current.etag)
            self._revisions[revision_key] = revision
            if revision.status is SliceRevisionStatus.FINALIZED:
                for episode in revision.slices:
                    processing = EpisodeProcessing(
                        scope=episode.scope,
                        recording_id=episode.recording_id,
                        episode_id=episode.episode_id,
                        finalized_revision=episode.revision,
                        start_offset_ns=episode.start_offset_ns,
                        end_offset_ns=episode.end_offset_ns,
                        created_at=revision.created_at,
                        updated_at=revision.created_at,
                    )
                    self._episode_processing[(*key, episode.episode_id)] = processing
            self._recordings[key] = recording
            return recording

    def list_episode_processing(
        self, scope: RecordingScope, recording_id: str
    ) -> tuple[EpisodeProcessing, ...]:
        prefix = _recording_key(scope, recording_id)
        with self._lock:
            values = [item for key, item in self._episode_processing.items() if key[:4] == prefix]
        return tuple(sorted(values, key=lambda item: (item.start_offset_ns, item.episode_id)))

    def save_episode_processing(
        self,
        episode: EpisodeProcessing,
        *,
        expected_status: EpisodeProcessingStatus,
    ) -> EpisodeProcessing:
        key = (*_recording_key(episode.scope, episode.recording_id), episode.episode_id)
        with self._lock:
            current = self._episode_processing.get(key)
            if current is None:
                raise _not_found()
            if current.status is not expected_status:
                raise _episode_status_conflict(current.status)
            self._episode_processing[key] = episode
            return episode


class PostgresContinuousRecordingRepository:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def create(
        self, recording: ContinuousRecording, *, actor_id: str, request_id: str
    ) -> ContinuousRecording:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ingest.continuous_recordings (
                        organization_id, project_id, region_code, recording_id,
                        upload_session_id, recording_upload_id, rollout_id, data_package_id,
                        collection_task_id, collection_job_id, robot_id, device_id,
                        capture_started_at, capture_ended_at, duration_ns, source_sha256,
                        manifest_fingerprint, video_asset_count, status, current_revision,
                        finalized_revision, resource_version, recording_document,
                        created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s::jsonb, %s, %s
                    )
                    ON CONFLICT (organization_id, project_id, region_code, recording_id)
                    DO NOTHING
                    """,
                    _recording_values(recording),
                )
                existing = self._get_locked(cursor, recording.scope, recording.recording_id)
                if existing is None:
                    raise RuntimeError("PostgreSQL did not return the continuous recording")
                if existing != recording:
                    raise _identity_conflict()
                self._append_audit(
                    cursor,
                    recording=recording,
                    actor_id=actor_id,
                    request_id=request_id,
                    action="continuous_recording.registered",
                )
            connection.commit()
            return existing
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, scope: RecordingScope, recording_id: str) -> ContinuousRecording | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                return self._get_locked(cursor, scope, recording_id, for_update=False)
        finally:
            connection.close()

    def get_by_upload_session(
        self, scope: RecordingScope, upload_session_id: str
    ) -> ContinuousRecording | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT recording_document FROM ingest.continuous_recordings
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                      AND upload_session_id = %s
                    """,
                    (*_scope_values(scope), upload_session_id),
                )
                row = cursor.fetchone()
                return None if row is None else ContinuousRecording.model_validate(row[0])
        finally:
            connection.close()

    def list(self, scope: RecordingScope) -> tuple[ContinuousRecording, ...]:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT recording_document FROM ingest.continuous_recordings
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                    ORDER BY created_at DESC, recording_id DESC
                    """,
                    _scope_values(scope),
                )
                return tuple(
                    ContinuousRecording.model_validate(row[0]) for row in cursor.fetchall()
                )
        finally:
            connection.close()

    def get_revision(
        self, scope: RecordingScope, recording_id: str, revision: int
    ) -> RecordingSliceRevision | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT revision_document FROM ingest.recording_slice_revisions
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                      AND recording_id = %s AND revision = %s
                    """,
                    (*_scope_values(scope), recording_id, revision),
                )
                row = cursor.fetchone()
                return None if row is None else RecordingSliceRevision.model_validate(row[0])
        finally:
            connection.close()

    def save_revision(
        self,
        *,
        recording: ContinuousRecording,
        revision: RecordingSliceRevision,
        expected_version: int,
        actor_id: str,
        request_id: str,
    ) -> ContinuousRecording:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                current = self._get_locked(cursor, recording.scope, recording.recording_id)
                if current is None:
                    raise _not_found()
                if current.current_revision != expected_version:
                    raise _version_conflict(current.etag)
                cursor.execute(
                    """
                    INSERT INTO ingest.recording_slice_revisions (
                        organization_id, project_id, region_code, recording_id,
                        revision, status, revision_document, created_by, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    """,
                    (
                        *_scope_values(recording.scope),
                        recording.recording_id,
                        revision.revision,
                        revision.status.value,
                        revision.model_dump_json(),
                        revision.created_by,
                        revision.created_at,
                    ),
                )
                for episode in revision.slices:
                    self._insert_episode(cursor, episode)
                    if revision.status is SliceRevisionStatus.FINALIZED:
                        self._insert_episode_processing(cursor, episode, revision.created_at)
                cursor.execute(
                    """
                    UPDATE ingest.continuous_recordings
                    SET status = %s, current_revision = %s, finalized_revision = %s,
                        resource_version = %s, recording_document = %s::jsonb,
                        updated_at = %s
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                      AND recording_id = %s AND current_revision = %s
                    """,
                    (
                        recording.status.value,
                        recording.current_revision,
                        recording.finalized_revision,
                        recording.current_revision + 1,
                        recording.model_dump_json(),
                        recording.updated_at,
                        *_scope_values(recording.scope),
                        recording.recording_id,
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise _version_conflict(current.etag)
                self._append_audit(
                    cursor,
                    recording=recording,
                    actor_id=actor_id,
                    request_id=request_id,
                    action=(
                        "continuous_recording.slices_finalized"
                        if revision.status.value == "FINALIZED"
                        else "continuous_recording.slice_draft_saved"
                    ),
                )
            connection.commit()
            return recording
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_episode_processing(
        self, scope: RecordingScope, recording_id: str
    ) -> tuple[EpisodeProcessing, ...]:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT episode_document FROM ingest.recording_episode_processing
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                      AND recording_id = %s
                    ORDER BY start_offset_ns, episode_id
                    """,
                    (*_scope_values(scope), recording_id),
                )
                return tuple(EpisodeProcessing.model_validate(row[0]) for row in cursor.fetchall())
        finally:
            connection.close()

    def save_episode_processing(
        self,
        episode: EpisodeProcessing,
        *,
        expected_status: EpisodeProcessingStatus,
    ) -> EpisodeProcessing:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE ingest.recording_episode_processing
                    SET status = %s, qc_report_id = %s, alignment_attempt_id = %s,
                        episode_document = %s::jsonb, updated_at = %s
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                      AND recording_id = %s AND episode_id = %s AND status = %s
                    """,
                    (
                        episode.status.value,
                        episode.qc_report_id,
                        episode.alignment_attempt_id,
                        episode.model_dump_json(),
                        episode.updated_at,
                        *_scope_values(episode.scope),
                        episode.recording_id,
                        episode.episode_id,
                        expected_status.value,
                    ),
                )
                if cursor.rowcount != 1:
                    cursor.execute(
                        """
                        SELECT status FROM ingest.recording_episode_processing
                        WHERE organization_id = %s AND project_id = %s AND region_code = %s
                          AND recording_id = %s AND episode_id = %s
                        """,
                        (
                            *_scope_values(episode.scope),
                            episode.recording_id,
                            episode.episode_id,
                        ),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise _not_found()
                    raise _episode_status_conflict(EpisodeProcessingStatus(row[0]))
            connection.commit()
            return episode
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _insert_episode(cursor: Any, episode: EpisodeSlice) -> None:
        cursor.execute(
            """
            INSERT INTO ingest.recording_episode_slices (
                organization_id, project_id, region_code, recording_id, revision,
                episode_id, ordinal, start_offset_ns, end_offset_ns,
                started_at, ended_at, episode_document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                *_scope_values(episode.scope),
                episode.recording_id,
                episode.revision,
                episode.episode_id,
                episode.ordinal,
                episode.start_offset_ns,
                episode.end_offset_ns,
                episode.started_at,
                episode.ended_at,
                episode.model_dump_json(),
            ),
        )

    @staticmethod
    def _insert_episode_processing(cursor: Any, episode: EpisodeSlice, created_at: object) -> None:
        processing = EpisodeProcessing(
            scope=episode.scope,
            recording_id=episode.recording_id,
            episode_id=episode.episode_id,
            finalized_revision=episode.revision,
            start_offset_ns=episode.start_offset_ns,
            end_offset_ns=episode.end_offset_ns,
            created_at=created_at,
            updated_at=created_at,
        )
        cursor.execute(
            """
            INSERT INTO ingest.recording_episode_processing (
                organization_id, project_id, region_code, recording_id, episode_id,
                finalized_revision, start_offset_ns, end_offset_ns, status,
                episode_document, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            """,
            (
                *_scope_values(episode.scope),
                episode.recording_id,
                episode.episode_id,
                episode.revision,
                episode.start_offset_ns,
                episode.end_offset_ns,
                processing.status.value,
                processing.model_dump_json(),
                processing.created_at,
                processing.updated_at,
            ),
        )
        cursor.execute(
            """
            INSERT INTO ingest.recording_episode_asset_windows (
                organization_id, project_id, region_code, recording_id, episode_id,
                upload_id, asset_id, role, camera_id, media_type, source_sha256,
                start_offset_ns, end_offset_ns, created_at
            )
            SELECT
                a.organization_id, a.project_id, a.region_code, %s, %s,
                a.upload_id, a.asset_id, a.role, a.camera_id, a.media_type,
                a.expected_sha256, %s, %s, %s
            FROM ingest.continuous_recordings AS r
            JOIN ingest.recording_upload_assets AS a
              ON a.organization_id = r.organization_id
             AND a.project_id = r.project_id
             AND a.region_code = r.region_code
             AND a.upload_id = r.recording_upload_id
            WHERE r.organization_id = %s AND r.project_id = %s AND r.region_code = %s
              AND r.recording_id = %s AND a.status = 'COMMITTED'
            """,
            (
                episode.recording_id,
                episode.episode_id,
                episode.start_offset_ns,
                episode.end_offset_ns,
                processing.created_at,
                *_scope_values(episode.scope),
                episode.recording_id,
            ),
        )

    @staticmethod
    def _get_locked(
        cursor: Any,
        scope: RecordingScope,
        recording_id: str,
        *,
        for_update: bool = True,
    ) -> ContinuousRecording | None:
        cursor.execute(
            """
            SELECT recording_document FROM ingest.continuous_recordings
            WHERE organization_id = %s AND project_id = %s AND region_code = %s
              AND recording_id = %s
            """
            + (" FOR UPDATE" if for_update else ""),
            (*_scope_values(scope), recording_id),
        )
        row = cursor.fetchone()
        return None if row is None else ContinuousRecording.model_validate(row[0])

    @staticmethod
    def _append_audit(
        cursor: Any,
        *,
        recording: ContinuousRecording,
        actor_id: str,
        request_id: str,
        action: str,
    ) -> None:
        audit_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{recording.scope.organization_id}:{recording.recording_id}:"
                f"{recording.current_revision}:{action}",
            )
        )
        safe_after = {
            "recording_id": recording.recording_id,
            "status": recording.status.value,
            "current_revision": recording.current_revision,
        }
        cursor.execute(
            """
            INSERT INTO core.audit_events (
                audit_id, project_id, region_code, actor_id, action,
                resource_type, resource_id, request_id, before_hash,
                after_hash, details, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, 'continuous_recording', %s, %s,
                      NULL, %s, %s::jsonb, %s)
            ON CONFLICT (audit_id) DO NOTHING
            """,
            (
                audit_id,
                recording.scope.project_id,
                recording.scope.region_code,
                actor_id,
                action,
                recording.recording_id,
                request_id,
                canonical_hash(safe_after),
                json.dumps(safe_after),
                recording.updated_at,
            ),
        )


def _scope_values(scope: RecordingScope) -> tuple[str, str, str]:
    return scope.organization_id, scope.project_id, scope.region_code


def _recording_key(scope: RecordingScope, recording_id: str) -> tuple[str, str, str, str]:
    return (*_scope_values(scope), recording_id)


def _upload_key(scope: RecordingScope, upload_session_id: str) -> tuple[str, str, str, str]:
    return (*_scope_values(scope), upload_session_id)


def _recording_values(recording: ContinuousRecording) -> tuple[object, ...]:
    return (
        *_scope_values(recording.scope),
        recording.recording_id,
        recording.upload_session_id,
        recording.recording_upload_id,
        recording.rollout_id,
        recording.data_package_id,
        recording.collection_task_id,
        recording.collection_job_id,
        recording.robot_id,
        recording.device_id,
        recording.capture_started_at,
        recording.capture_ended_at,
        recording.duration_ns,
        recording.source_sha256,
        recording.manifest_fingerprint,
        recording.video_asset_count,
        recording.status.value,
        recording.current_revision,
        recording.finalized_revision,
        1,
        recording.model_dump_json(),
        recording.created_at,
        recording.updated_at,
    )


def _identity_conflict() -> Exception:
    return problem(
        status=409,
        code="CONTINUOUS_RECORDING_IDENTITY_CONFLICT",
        title="Continuous recording identity conflict",
        detail="The upload or recording id already identifies different immutable facts.",
    )


def _not_found() -> Exception:
    return problem(
        status=404,
        code="CONTINUOUS_RECORDING_NOT_FOUND",
        title="Continuous recording not found",
        detail="The continuous recording does not exist in this scope.",
    )


def _version_conflict(etag: str) -> Exception:
    return problem(
        status=412,
        code="CONTINUOUS_RECORDING_VERSION_MISMATCH",
        title="Continuous recording changed",
        detail="Reload the recording before saving another slice revision.",
        details={"expected": etag},
    )


def _episode_status_conflict(status: EpisodeProcessingStatus) -> Exception:
    return problem(
        status=409,
        code="EPISODE_PROCESSING_STATUS_CONFLICT",
        title="Episode processing status changed",
        detail="Reload the Episode before advancing its QC/alignment state.",
        details={"current_status": status.value},
    )
