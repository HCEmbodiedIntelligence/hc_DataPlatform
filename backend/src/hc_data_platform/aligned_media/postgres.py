from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any
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

_ARTIFACT_COLUMNS = """
    artifact_id, artifact_key, organization_id, project_id, region_code,
    dataset_id, rollout_id, dataset_version, camera_id, profile_id,
    profile_version, alignment_version, source_sha256, status,
    object_prefix, media_object_key, object_manifest, total_bytes, frame_count,
    duration_seconds, fps, media_width, media_height, content_sha256,
    publication_token, timeline_json, placeholder_count, created_at, ready_at,
    commit_lease_expires_at, commit_started_at, dataset_committed_at,
    retired_at, deleted_at, failure_code, version
"""

_JOB_COLUMNS = """
    job_id, artifact_id, artifact_key, organization_id, project_id, region_code,
    status, attempt, progress, error_code, request_json, created_at, started_at,
    completed_at, owner_id, lease_expires_at, attempt_token
"""


class PostgresAlignedMediaRepository:
    """Attempt-fenced canonical media state independent of legacy preview tables."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def find_by_selector(
        self,
        scope: AlignedMediaScopeV1,
        selector: AlignedMediaSelectorV1,
        *,
        profile_id: str,
    ) -> AlignedMediaArtifactV1 | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT {_ARTIFACT_COLUMNS}
                  FROM aligned_media.artifacts
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND dataset_id = %s AND rollout_id = %s AND dataset_version = %s
                   AND camera_id = %s AND profile_id = %s
                """,
                (
                    *_scope_values(scope),
                    selector.dataset_id,
                    selector.rollout_id,
                    selector.dataset_version,
                    selector.camera_id,
                    profile_id,
                ),
            )
            row = cursor.fetchone()
            return None if row is None else _artifact(row)
        finally:
            cursor.close()
            connection.close()

    def ensure_generation(
        self,
        *,
        scope: AlignedMediaScopeV1,
        artifact_key: str,
        request: AlignedMediaGenerationRequestV1,
        profile_version: str,
        now: datetime,
    ) -> tuple[AlignedMediaArtifactV1, AlignedMediaJobV1 | None]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            artifact_id = str(uuid4())
            cursor.execute(
                """
                INSERT INTO aligned_media.artifacts (
                    organization_id, project_id, region_code, artifact_id, artifact_key,
                    dataset_id, rollout_id, dataset_version, camera_id,
                    profile_id, profile_version, pipeline_revision, alignment_version,
                    source_sha256, status, created_at, fps
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, 'aligned-mp4-image2pipe-v1', %s, %s,
                    'GENERATING', %s, 30
                )
                ON CONFLICT (organization_id, project_id, region_code, artifact_key)
                DO NOTHING
                """,
                (
                    *_scope_values(scope),
                    artifact_id,
                    artifact_key,
                    request.dataset_id,
                    request.rollout_id,
                    request.expected_dataset_version,
                    request.camera_id,
                    request.profile_id,
                    profile_version,
                    request.alignment.alignment_version,
                    request.source_sha256,
                    now,
                ),
            )
            cursor.execute(
                f"""
                SELECT {_ARTIFACT_COLUMNS}
                  FROM aligned_media.artifacts
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND artifact_key = %s
                 FOR UPDATE
                """,
                (*_scope_values(scope), artifact_key),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("aligned media artifact upsert was not visible")
            artifact = _artifact(row)
            if artifact.status is AlignedMediaArtifactStatus.READY:
                connection.commit()
                return artifact, None
            if artifact.status is AlignedMediaArtifactStatus.ABANDONING:
                raise RuntimeError("aligned media artifact cleanup is still in progress")
            if artifact.status is AlignedMediaArtifactStatus.FAILED:
                cursor.execute(
                    """
                    UPDATE aligned_media.artifacts
                       SET status = 'GENERATING', failure_code = NULL, version = version + 1
                     WHERE organization_id = %s AND project_id = %s AND region_code = %s
                       AND artifact_id = %s
                    """,
                    (*_scope_values(scope), artifact.artifact_id),
                )
            cursor.execute(
                f"""
                SELECT {_JOB_COLUMNS}
                  FROM aligned_media.jobs
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND artifact_key = %s AND status IN ('QUEUED', 'RUNNING')
                 ORDER BY created_at DESC LIMIT 1
                """,
                (*_scope_values(scope), artifact_key),
            )
            job_row = cursor.fetchone()
            if job_row is None:
                cursor.execute(
                    f"""
                    INSERT INTO aligned_media.jobs (
                        organization_id, project_id, region_code, job_id, artifact_id,
                        artifact_key, status, request_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'QUEUED', %s::jsonb, %s)
                    RETURNING {_JOB_COLUMNS}
                    """,
                    (
                        *_scope_values(scope),
                        str(uuid4()),
                        artifact.artifact_id,
                        artifact_key,
                        request.model_dump_json(),
                        now,
                    ),
                )
                job_row = cursor.fetchone()
            if job_row is None:
                raise RuntimeError("aligned media job insert returned no row")
            cursor.execute(
                f"""
                SELECT {_ARTIFACT_COLUMNS}
                  FROM aligned_media.artifacts
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND artifact_key = %s
                """,
                (*_scope_values(scope), artifact_key),
            )
            refreshed = cursor.fetchone()
            if refreshed is None:
                raise RuntimeError("aligned media artifact disappeared during job creation")
            connection.commit()
            return _artifact(refreshed), _job(job_row)
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

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
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                UPDATE aligned_media.jobs
                   SET status = 'RUNNING', attempt = attempt + 1,
                       progress = GREATEST(progress, 1),
                       started_at = COALESCE(started_at, %s), completed_at = NULL,
                       error_code = NULL, owner_id = %s, attempt_token = %s::uuid,
                       lease_expires_at = %s, heartbeat_at = %s
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND job_id = %s
                   AND (status = 'QUEUED' OR (
                       status = 'RUNNING' AND (lease_expires_at IS NULL OR lease_expires_at <= %s)
                   ))
                RETURNING {_JOB_COLUMNS}
                """,
                (
                    now,
                    owner_id,
                    attempt_token,
                    lease_expires_at,
                    now,
                    *_scope_values(scope),
                    job_id,
                    now,
                ),
            )
            row = cursor.fetchone()
            connection.commit()
            return None if row is None else _job(row)
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

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
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE aligned_media.jobs
                   SET progress = GREATEST(progress, COALESCE(%s, progress)),
                       lease_expires_at = %s, heartbeat_at = %s
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND job_id = %s AND status = 'RUNNING' AND owner_id = %s
                   AND attempt_token = %s::uuid AND lease_expires_at > %s
                """,
                (
                    progress,
                    lease_expires_at,
                    now,
                    *_scope_values(scope),
                    job_id,
                    owner_id,
                    attempt_token,
                    now,
                ),
            )
            result = bool(cursor.rowcount == 1)
            connection.commit()
            return result
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

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
        timeline = AlignedMediaTimelineV1(
            frame_count=encoded.frame_count,
            start_timestamp_ns=encoded.first_timestamp_ns,
        )
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE aligned_media.artifacts artifact
                   SET object_prefix = %s, media_object_key = %s,
                       object_manifest = %s::jsonb, total_bytes = %s,
                       frame_count = %s, duration_seconds = %s, fps = 30,
                       media_width = %s, media_height = %s, content_sha256 = %s,
                       publication_token = %s::uuid, publication_receipt_at = %s,
                       timeline_json = %s::jsonb, placeholder_count = %s
                  FROM aligned_media.jobs job
                 WHERE artifact.organization_id = %s AND artifact.project_id = %s
                   AND artifact.region_code = %s AND artifact.artifact_key = %s
                   AND artifact.status = 'GENERATING' AND artifact.artifact_id = job.artifact_id
                   AND job.organization_id = artifact.organization_id
                   AND job.project_id = artifact.project_id
                   AND job.region_code = artifact.region_code
                   AND job.job_id = %s::uuid AND job.status = 'RUNNING'
                   AND job.owner_id = %s AND job.attempt_token = %s::uuid
                   AND job.lease_expires_at > %s
                """,
                (
                    publication.object_prefix,
                    publication.media_object_key,
                    json.dumps([item.model_dump(mode="json") for item in publication.objects]),
                    publication.total_bytes,
                    encoded.frame_count,
                    encoded.duration_seconds,
                    encoded.width,
                    encoded.height,
                    publication.content_sha256,
                    publication.publication_token,
                    now,
                    timeline.model_dump_json(),
                    encoded.placeholder_count,
                    *_scope_values(scope),
                    artifact_key,
                    job_id,
                    owner_id,
                    attempt_token,
                    now,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("aligned media artifact rejected its publication receipt")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

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
        del encoded
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                UPDATE aligned_media.artifacts artifact
                   SET status = 'READY', ready_at = %s, failure_code = NULL,
                       commit_lease_expires_at = %s, version = version + 1
                  FROM aligned_media.jobs job
                 WHERE artifact.organization_id = %s AND artifact.project_id = %s
                   AND artifact.region_code = %s AND artifact.artifact_key = %s
                   AND artifact.status = 'GENERATING'
                   AND artifact.publication_token = %s::uuid
                   AND artifact.artifact_id = job.artifact_id
                   AND job.organization_id = artifact.organization_id
                   AND job.project_id = artifact.project_id
                   AND job.region_code = artifact.region_code
                   AND job.job_id = %s::uuid AND job.status = 'RUNNING'
                   AND job.owner_id = %s AND job.attempt_token = %s::uuid
                   AND job.lease_expires_at > %s
                RETURNING {_prefixed_columns("artifact")}
                """,
                (
                    now,
                    now + timedelta(hours=24),
                    *_scope_values(scope),
                    artifact_key,
                    publication.publication_token,
                    job_id,
                    owner_id,
                    attempt_token,
                    now,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("aligned media READY transition was fenced")
            cursor.execute(
                """
                UPDATE aligned_media.jobs
                   SET status = 'SUCCEEDED', progress = 100, completed_at = %s,
                       error_code = NULL, owner_id = NULL, attempt_token = NULL,
                       lease_expires_at = NULL
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND job_id = %s AND status = 'RUNNING' AND owner_id = %s
                   AND attempt_token = %s::uuid
                """,
                (now, *_scope_values(scope), job_id, owner_id, attempt_token),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("aligned media job READY transition was fenced")
            connection.commit()
            return _artifact(row)
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

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
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE aligned_media.artifacts artifact
                   SET object_prefix = NULL, media_object_key = NULL,
                       object_manifest = '[]'::jsonb, total_bytes = 0, frame_count = 0,
                       duration_seconds = 0, media_width = NULL, media_height = NULL,
                       content_sha256 = NULL, publication_token = NULL,
                       publication_receipt_at = NULL, timeline_json = NULL,
                       placeholder_count = 0, ready_at = NULL,
                       commit_lease_expires_at = NULL, version = version + 1
                  FROM aligned_media.jobs job
                 WHERE artifact.organization_id = %s AND artifact.project_id = %s
                   AND artifact.region_code = %s AND artifact.artifact_key = %s
                   AND artifact.status = 'GENERATING'
                   AND artifact.publication_token = %s::uuid
                   AND artifact.artifact_id = job.artifact_id
                   AND job.organization_id = artifact.organization_id
                   AND job.project_id = artifact.project_id
                   AND job.region_code = artifact.region_code
                   AND job.job_id = %s::uuid AND job.status = 'RUNNING'
                   AND job.owner_id = %s AND job.attempt_token = %s::uuid
                   AND job.lease_expires_at > %s
                """,
                (
                    *_scope_values(scope),
                    artifact_key,
                    publication_token,
                    job_id,
                    owner_id,
                    attempt_token,
                    now,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("aligned media receipt discard lost its job fence")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def begin_dataset_commit(
        self,
        *,
        scope: AlignedMediaScopeV1,
        artifact_ids: Sequence[str],
        lease_expires_at: datetime,
        now: datetime,
    ) -> None:
        ids = _artifact_ids(artifact_ids)
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE aligned_media.artifacts
                   SET commit_lease_expires_at = CASE
                           WHEN dataset_committed_at IS NULL THEN %s
                           ELSE NULL
                       END,
                       commit_started_at = CASE
                           WHEN dataset_committed_at IS NULL THEN %s
                           ELSE NULL
                       END
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND artifact_id = ANY(%s::uuid[]) AND status = 'READY'
                """,
                (lease_expires_at, now, *_scope_values(scope), list(ids)),
            )
            if cursor.rowcount != len(ids):
                raise RuntimeError("aligned media commit lease rejected an incomplete bundle")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def mark_dataset_committed(
        self,
        *,
        scope: AlignedMediaScopeV1,
        artifact_ids: Sequence[str],
        now: datetime,
    ) -> None:
        ids = _artifact_ids(artifact_ids)
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE aligned_media.artifacts
                   SET dataset_committed_at = COALESCE(dataset_committed_at, %s),
                       commit_lease_expires_at = NULL,
                       commit_started_at = NULL,
                       version = CASE
                           WHEN dataset_committed_at IS NULL THEN version + 1
                           ELSE version
                       END
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND artifact_id = ANY(%s::uuid[]) AND status = 'READY'
                """,
                (now, *_scope_values(scope), list(ids)),
            )
            if cursor.rowcount != len(ids):
                raise RuntimeError("aligned media commit marker rejected an incomplete bundle")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def begin_abandon_uncommitted(
        self,
        *,
        scope: AlignedMediaScopeV1,
        artifact_ids: Sequence[str],
        error_code: str,
        now: datetime,
    ) -> tuple[AlignedMediaObjectV1, ...]:
        ids = _artifact_ids(artifact_ids)
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE aligned_media.artifacts
                   SET status = 'ABANDONING', failure_code = %s,
                       commit_lease_expires_at = NULL, commit_started_at = NULL,
                       version = version + 1
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND artifact_id = ANY(%s::uuid[])
                   AND dataset_committed_at IS NULL AND status = 'READY'
                   AND (
                       commit_started_at IS NULL OR commit_lease_expires_at IS NULL
                       OR commit_lease_expires_at <= %s
                   )
                """,
                (error_code, *_scope_values(scope), list(ids), now),
            )
            cursor.execute(
                """
                SELECT object_manifest
                  FROM aligned_media.artifacts
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND artifact_id = ANY(%s::uuid[])
                   AND dataset_committed_at IS NULL AND status = 'ABANDONING'
                 FOR UPDATE
                """,
                (*_scope_values(scope), list(ids)),
            )
            objects: list[AlignedMediaObjectV1] = []
            for row in cursor.fetchall():
                manifest = row[0]
                if isinstance(manifest, str):
                    manifest = json.loads(manifest)
                objects.extend(AlignedMediaObjectV1.model_validate(item) for item in manifest)
            connection.commit()
            return tuple(objects)
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def complete_abandon_uncommitted(
        self,
        *,
        scope: AlignedMediaScopeV1,
        artifact_ids: Sequence[str],
        now: datetime,
    ) -> None:
        del now
        ids = _artifact_ids(artifact_ids)
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE aligned_media.artifacts
                   SET status = 'FAILED', object_prefix = NULL, media_object_key = NULL,
                       object_manifest = '[]'::jsonb, total_bytes = 0, frame_count = 0,
                       duration_seconds = 0, media_width = NULL, media_height = NULL,
                       content_sha256 = NULL, publication_token = NULL,
                       publication_receipt_at = NULL, timeline_json = NULL,
                       placeholder_count = 0, ready_at = NULL,
                       commit_lease_expires_at = NULL, commit_started_at = NULL,
                       version = version + 1
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND artifact_id = ANY(%s::uuid[])
                   AND dataset_committed_at IS NULL AND status = 'ABANDONING'
                """,
                (*_scope_values(scope), list(ids)),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def begin_retire_dataset_version(
        self,
        *,
        scope: AlignedMediaScopeV1,
        dataset_id: str,
        dataset_version: int,
        now: datetime,
    ) -> tuple[AlignedMediaObjectV1, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE aligned_media.artifacts
                   SET retired_at = COALESCE(retired_at, %s),
                       commit_lease_expires_at = NULL, commit_started_at = NULL,
                       version = CASE WHEN retired_at IS NULL THEN version + 1 ELSE version END
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND dataset_id = %s AND dataset_version = %s
                   AND dataset_committed_at IS NOT NULL AND deleted_at IS NULL
                """,
                (now, *_scope_values(scope), dataset_id, dataset_version),
            )
            cursor.execute(
                """
                SELECT object_manifest
                  FROM aligned_media.artifacts
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND dataset_id = %s AND dataset_version = %s
                   AND retired_at IS NOT NULL AND deleted_at IS NULL
                 FOR UPDATE
                """,
                (*_scope_values(scope), dataset_id, dataset_version),
            )
            objects: list[AlignedMediaObjectV1] = []
            for row in cursor.fetchall():
                manifest = row[0]
                if isinstance(manifest, str):
                    manifest = json.loads(manifest)
                objects.extend(AlignedMediaObjectV1.model_validate(item) for item in manifest)
            connection.commit()
            return tuple(objects)
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def complete_retire_dataset_version(
        self,
        *,
        scope: AlignedMediaScopeV1,
        dataset_id: str,
        dataset_version: int,
        now: datetime,
    ) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE aligned_media.artifacts
                   SET deleted_at = COALESCE(deleted_at, %s),
                       version = CASE WHEN deleted_at IS NULL THEN version + 1 ELSE version END
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND dataset_id = %s AND dataset_version = %s
                   AND retired_at IS NOT NULL
                """,
                (now, *_scope_values(scope), dataset_id, dataset_version),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

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
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE aligned_media.jobs
                   SET status = 'QUEUED', owner_id = NULL, attempt_token = NULL,
                       lease_expires_at = NULL, heartbeat_at = %s, error_code = %s
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND job_id = %s AND status = 'RUNNING' AND owner_id = %s
                   AND attempt_token = %s::uuid
                """,
                (
                    now,
                    error_code,
                    *_scope_values(scope),
                    job_id,
                    owner_id,
                    attempt_token,
                ),
            )
            result = bool(cursor.rowcount == 1)
            connection.commit()
            return result
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

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
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE aligned_media.artifacts
                   SET status = 'FAILED', failure_code = %s, version = version + 1
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND artifact_key = %s AND status <> 'READY' AND publication_token IS NULL
                """,
                (error_code, *_scope_values(scope), artifact_key),
            )
            cursor.execute(
                """
                UPDATE aligned_media.jobs
                   SET status = 'FAILED', error_code = %s, completed_at = %s,
                       owner_id = NULL, attempt_token = NULL, lease_expires_at = NULL
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND job_id = %s AND status = 'RUNNING' AND owner_id = %s
                   AND attempt_token = %s::uuid
                """,
                (
                    error_code,
                    now,
                    *_scope_values(scope),
                    job_id,
                    owner_id,
                    attempt_token,
                ),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def publication_is_referenced(
        self,
        scope: AlignedMediaScopeV1,
        artifact_key: str,
        publication_token: str,
        *,
        now: datetime,
    ) -> bool:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM aligned_media.artifacts artifact
                     WHERE artifact.organization_id = %s
                       AND artifact.project_id = %s AND artifact.region_code = %s
                       AND artifact.artifact_key = %s
                       AND artifact.publication_token = %s::uuid
                       AND artifact.retired_at IS NULL
                       AND (
                           artifact.dataset_committed_at IS NOT NULL
                           OR artifact.commit_lease_expires_at > %s
                           OR EXISTS (
                               SELECT 1 FROM aligned_media.jobs job
                                WHERE job.organization_id = artifact.organization_id
                                  AND job.project_id = artifact.project_id
                                  AND job.region_code = artifact.region_code
                                  AND job.artifact_id = artifact.artifact_id
                                  AND job.status = 'RUNNING'
                                  AND job.lease_expires_at > %s
                           )
                       )
                )
                """,
                (*_scope_values(scope), artifact_key, publication_token, now, now),
            )
            row = cursor.fetchone()
            return bool(row and row[0])
        finally:
            cursor.close()
            connection.close()


def _scope_values(scope: AlignedMediaScopeV1) -> tuple[str, str, str]:
    return scope.organization_id, scope.project_id, scope.region_code


def _artifact_ids(values: Sequence[str]) -> tuple[str, ...]:
    result = tuple(dict.fromkeys(values))
    if not result or len(result) != len(values):
        raise ValueError("aligned media artifact ids must be non-empty and unique")
    return result


def _artifact(row: Sequence[Any]) -> AlignedMediaArtifactV1:
    return AlignedMediaArtifactV1(
        artifact_id=str(row[0]),
        artifact_key=str(row[1]),
        scope=AlignedMediaScopeV1(
            organization_id=str(row[2]),
            project_id=str(row[3]),
            region_code=str(row[4]),
        ),
        dataset_id=str(row[5]),
        rollout_id=str(row[6]),
        dataset_version=int(row[7]),
        camera_id=str(row[8]),
        profile_id=str(row[9]),
        profile_version=str(row[10]),
        alignment_version=str(row[11]),
        source_sha256=str(row[12]),
        status=AlignedMediaArtifactStatus(str(row[13])),
        object_prefix=None if row[14] is None else str(row[14]),
        media_object_key=None if row[15] is None else str(row[15]),
        objects=tuple(AlignedMediaObjectV1.model_validate(item) for item in (row[16] or [])),
        total_bytes=int(row[17]),
        frame_count=int(row[18]),
        duration_seconds=float(row[19]),
        fps=int(row[20]),
        width=None if row[21] is None else int(row[21]),
        height=None if row[22] is None else int(row[22]),
        content_sha256=None if row[23] is None else str(row[23]),
        publication_token=None if row[24] is None else str(row[24]),
        timeline=None if row[25] is None else AlignedMediaTimelineV1.model_validate(row[25]),
        placeholder_count=int(row[26]),
        created_at=row[27],
        ready_at=row[28],
        commit_lease_expires_at=row[29],
        commit_started_at=row[30],
        dataset_committed_at=row[31],
        retired_at=row[32],
        deleted_at=row[33],
        failure_code=None if row[34] is None else str(row[34]),
        version=int(row[35]),
    )


def _job(row: Sequence[Any]) -> AlignedMediaJobV1:
    return AlignedMediaJobV1(
        job_id=str(row[0]),
        artifact_id=str(row[1]),
        artifact_key=str(row[2]),
        scope=AlignedMediaScopeV1(
            organization_id=str(row[3]),
            project_id=str(row[4]),
            region_code=str(row[5]),
        ),
        status=AlignedMediaJobStatus(str(row[6])),
        attempt=int(row[7]),
        progress=int(row[8]),
        error_code=None if row[9] is None else str(row[9]),
        request=AlignedMediaGenerationRequestV1.model_validate(row[10]),
        created_at=row[11],
        started_at=row[12],
        completed_at=row[13],
        owner_id=None if row[14] is None else str(row[14]),
        lease_expires_at=row[15],
        attempt_token=None if row[16] is None else str(row[16]),
    )


def _prefixed_columns(alias: str) -> str:
    return ", ".join(f"{alias}.{name.strip()}" for name in _ARTIFACT_COLUMNS.split(","))
