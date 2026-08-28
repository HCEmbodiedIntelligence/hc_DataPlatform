from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from .models import (
    PreviewArtifactStatus,
    PreviewArtifactV1,
    PreviewJobStatus,
    PreviewJobV1,
    PreviewObjectV1,
    PreviewRequestV1,
    PreviewScopeV1,
    PreviewSessionV1,
    PublishedPreviewArtifactV1,
)

_ARTIFACT_COLUMNS = """
    artifact_id, artifact_key, organization_id, project_id, region_code,
    dataset_id, rollout_id, lance_version, camera_id, profile_id,
    pipeline_revision, source_start_step, source_end_step, status,
    object_prefix, playlist_key, object_manifest, total_bytes, frame_count,
    duration_seconds, content_sha256, rebuild_source_id, created_at, ready_at,
    last_accessed_at, expires_at, failure_code, version,
    active_reference_count, legal_hold, governance_hold, retention_until
"""

_JOB_COLUMNS = """
    job_id, artifact_id, artifact_key, organization_id, project_id, region_code,
    status, attempt, progress, error_code, request_json,
    created_at, started_at, completed_at
"""


class PostgresPreviewRepository:
    """Transactional preview state with database-enforced concurrent deduplication."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def find_artifact(
        self, scope: PreviewScopeV1, artifact_key: str, *, now: datetime
    ) -> PreviewArtifactV1 | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT {_ARTIFACT_COLUMNS}
                FROM preview.artifacts
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND artifact_key = %s AND expires_at > %s
                """,
                (*_scope_values(scope), artifact_key, now),
            )
            row = cursor.fetchone()
            return None if row is None else _artifact(row)
        finally:
            cursor.close()
            connection.close()

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
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            artifact_id = str(uuid4())
            cursor.execute(
                """
                INSERT INTO preview.artifacts (
                    organization_id, project_id, region_code, artifact_id, artifact_key,
                    dataset_id, rollout_id, lance_version, camera_id, profile_id,
                    pipeline_revision, source_start_step, source_end_step, status,
                    rebuild_source_id, created_at, last_accessed_at, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, 'GENERATING', %s, %s, %s, %s
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
                    request.lance_version,
                    request.camera_id,
                    request.profile_id,
                    pipeline_revision,
                    request.start_step,
                    request.end_step,
                    rebuild_source_id,
                    now,
                    now,
                    now + artifact_ttl,
                ),
            )
            cursor.execute(
                f"""
                SELECT {_ARTIFACT_COLUMNS}
                FROM preview.artifacts
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND artifact_key = %s
                FOR UPDATE
                """,
                (*_scope_values(scope), artifact_key),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("preview artifact upsert was not visible")
            artifact = _artifact(row)
            if (
                artifact.status is PreviewArtifactStatus.READY
                and artifact.expires_at > now
            ):
                connection.commit()
                return artifact, None
            if artifact.status is PreviewArtifactStatus.DELETING:
                cursor.execute(
                    f"""
                    SELECT {_JOB_COLUMNS}
                    FROM preview.jobs
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                      AND artifact_key = %s
                      AND error_code = 'PREVIEW_ARTIFACT_DELETING'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (*_scope_values(scope), artifact_key),
                )
                deleting_job = cursor.fetchone()
                if deleting_job is None:
                    job_id = str(uuid4())
                    cursor.execute(
                        f"""
                        INSERT INTO preview.jobs (
                            organization_id, project_id, region_code, job_id,
                            artifact_id, artifact_key, status, progress, request_json,
                            error_code, created_at, completed_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, 'FAILED', 0, %s::jsonb,
                            'PREVIEW_ARTIFACT_DELETING', %s, %s
                        )
                        RETURNING {_JOB_COLUMNS}
                        """,
                        (
                            *_scope_values(scope),
                            job_id,
                            artifact.artifact_id,
                            artifact_key,
                            request.model_dump_json(),
                            now,
                            now,
                        ),
                    )
                    deleting_job = cursor.fetchone()
                if deleting_job is None:
                    raise RuntimeError("preview deletion retry job was not persisted")
                connection.commit()
                return artifact, _job(deleting_job)
            if artifact.status is PreviewArtifactStatus.FAILED or artifact.expires_at <= now:
                cursor.execute(
                    """
                    UPDATE preview.artifacts
                    SET status = 'GENERATING', failure_code = NULL,
                        expires_at = %s, version = version + 1
                    WHERE organization_id = %s AND project_id = %s AND region_code = %s
                      AND artifact_id = %s
                    """,
                    (now + artifact_ttl, *_scope_values(scope), artifact.artifact_id),
                )
            cursor.execute(
                f"""
                SELECT {_JOB_COLUMNS}
                FROM preview.jobs
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND artifact_key = %s AND status IN ('QUEUED', 'RUNNING')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (*_scope_values(scope), artifact_key),
            )
            job_row = cursor.fetchone()
            if job_row is None:
                job_id = str(uuid4())
                cursor.execute(
                    """
                    INSERT INTO preview.jobs (
                        organization_id, project_id, region_code, job_id,
                        artifact_id, artifact_key, status, request_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'QUEUED', %s::jsonb, %s)
                    RETURNING
                        job_id, artifact_id, artifact_key,
                        organization_id, project_id, region_code,
                        status, attempt, progress, error_code, request_json,
                        created_at, started_at, completed_at
                    """,
                    (
                        *_scope_values(scope),
                        job_id,
                        artifact.artifact_id,
                        artifact_key,
                        request.model_dump_json(),
                        now,
                    ),
                )
                job_row = cursor.fetchone()
            if job_row is None:
                raise RuntimeError("preview job insert returned no row")
            cursor.execute(
                f"""
                SELECT {_ARTIFACT_COLUMNS}
                FROM preview.artifacts
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND artifact_key = %s
                """,
                (*_scope_values(scope), artifact_key),
            )
            refreshed = cursor.fetchone()
            if refreshed is None:
                raise RuntimeError("preview artifact disappeared during job creation")
            connection.commit()
            return _artifact(refreshed), _job(job_row)
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def get_job(self, scope: PreviewScopeV1, job_id: str) -> PreviewJobV1 | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT {_JOB_COLUMNS}
                FROM preview.jobs
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND job_id = %s
                """,
                (*_scope_values(scope), job_id),
            )
            row = cursor.fetchone()
            return None if row is None else _job(row)
        finally:
            cursor.close()
            connection.close()

    def mark_job_running(self, scope: PreviewScopeV1, job_id: str, *, now: datetime) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE preview.jobs
                SET status = 'RUNNING', attempt = attempt + 1,
                    progress = GREATEST(progress, 1), started_at = COALESCE(started_at, %s),
                    completed_at = NULL, error_code = NULL
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND job_id = %s AND status <> 'SUCCEEDED'
                """,
                (now, *_scope_values(scope), job_id),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def update_job_progress(
        self, scope: PreviewScopeV1, job_id: str, *, progress: int
    ) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE preview.jobs SET progress = GREATEST(progress, %s)
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND job_id = %s AND status = 'RUNNING'
                """,
                (progress, *_scope_values(scope), job_id),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def record_publication(
        self,
        *,
        scope: PreviewScopeV1,
        artifact_key: str,
        publication: PublishedPreviewArtifactV1,
    ) -> None:
        manifest = json.dumps(
            [item.model_dump(mode="json") for item in publication.objects],
            sort_keys=True,
            separators=(",", ":"),
        )
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE preview.artifacts
                SET object_prefix = %s, playlist_key = %s,
                    object_manifest = %s::jsonb, total_bytes = %s,
                    content_sha256 = %s
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND artifact_key = %s AND status = 'GENERATING'
                """,
                (
                    publication.object_prefix,
                    publication.playlist_key,
                    manifest,
                    publication.total_bytes,
                    publication.content_sha256,
                    *_scope_values(scope),
                    artifact_key,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("preview artifact rejected its publication receipt")
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
        scope: PreviewScopeV1,
        job_id: str,
        artifact_key: str,
        publication: PublishedPreviewArtifactV1,
        frame_count: int,
        duration_seconds: float,
        now: datetime,
    ) -> PreviewArtifactV1:
        manifest = json.dumps(
            [item.model_dump(mode="json") for item in publication.objects],
            sort_keys=True,
            separators=(",", ":"),
        )
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                UPDATE preview.artifacts
                SET status = 'READY', object_prefix = %s, playlist_key = %s,
                    object_manifest = %s::jsonb, total_bytes = %s,
                    frame_count = %s, duration_seconds = %s,
                    content_sha256 = %s, ready_at = %s, last_accessed_at = %s,
                    failure_code = NULL, version = version + 1
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND artifact_key = %s AND status = 'GENERATING'
                RETURNING {_ARTIFACT_COLUMNS}
                """,
                (
                    publication.object_prefix,
                    publication.playlist_key,
                    manifest,
                    publication.total_bytes,
                    frame_count,
                    duration_seconds,
                    publication.content_sha256,
                    now,
                    now,
                    *_scope_values(scope),
                    artifact_key,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("preview artifact was not in GENERATING state")
            cursor.execute(
                """
                UPDATE preview.jobs
                SET status = 'SUCCEEDED', progress = 100, completed_at = %s,
                    error_code = NULL
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND job_id = %s AND artifact_key = %s
                """,
                (now, *_scope_values(scope), job_id, artifact_key),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("preview generation job was not updated")
            connection.commit()
            return _artifact(row)
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def mark_failed(
        self,
        *,
        scope: PreviewScopeV1,
        job_id: str,
        artifact_key: str,
        error_code: str,
        now: datetime,
    ) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE preview.artifacts
                SET status = 'FAILED', failure_code = %s,
                    expires_at = LEAST(expires_at, %s), version = version + 1
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND artifact_key = %s AND status <> 'READY'
                """,
                (error_code, now, *_scope_values(scope), artifact_key),
            )
            cursor.execute(
                """
                UPDATE preview.jobs
                SET status = 'FAILED', error_code = %s, completed_at = %s
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND job_id = %s AND status <> 'SUCCEEDED'
                """,
                (error_code, now, *_scope_values(scope), job_id),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def create_session(
        self,
        *,
        scope: PreviewScopeV1,
        artifact: PreviewArtifactV1,
        request: PreviewRequestV1,
        session_ttl: timedelta,
        now: datetime,
    ) -> PreviewSessionV1:
        session = PreviewSessionV1(
            session_id=str(uuid4()),
            artifact_id=artifact.artifact_id,
            artifact_key=artifact.artifact_key,
            scope=scope,
            request=request,
            created_at=now,
            expires_at=now + session_ttl,
        )
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO preview.sessions (
                    organization_id, project_id, region_code, session_id,
                    artifact_id, artifact_key, request_json, created_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                """,
                (
                    *_scope_values(scope),
                    session.session_id,
                    session.artifact_id,
                    session.artifact_key,
                    request.model_dump_json(),
                    session.created_at,
                    session.expires_at,
                ),
            )
            cursor.execute(
                """
                UPDATE preview.artifacts
                SET last_accessed_at = %s,
                    active_reference_count = active_reference_count + 1
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND artifact_id = %s AND status = 'READY' AND expires_at > %s
                """,
                (now, *_scope_values(scope), artifact.artifact_id, now),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("only an unexpired READY artifact can receive a session")
            connection.commit()
            return session
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def get_session(
        self, scope: PreviewScopeV1, session_id: str, *, now: datetime
    ) -> tuple[PreviewSessionV1, PreviewArtifactV1] | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT s.session_id, s.artifact_id, s.artifact_key,
                       s.organization_id, s.project_id, s.region_code,
                       s.request_json, s.created_at, s.expires_at,
                       {_prefixed_artifact_columns('a')}
                FROM preview.sessions AS s
                JOIN preview.artifacts AS a
                  ON a.organization_id = s.organization_id
                 AND a.project_id = s.project_id
                 AND a.region_code = s.region_code
                 AND a.artifact_id = s.artifact_id
                WHERE s.organization_id = %s AND s.project_id = %s AND s.region_code = %s
                  AND s.session_id = %s AND s.expires_at > %s
                  AND a.status = 'READY' AND a.expires_at > %s
                """,
                (*_scope_values(scope), session_id, now, now),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            session = PreviewSessionV1(
                session_id=str(row[0]),
                artifact_id=str(row[1]),
                artifact_key=str(row[2]),
                scope=PreviewScopeV1(
                    organization_id=str(row[3]),
                    project_id=str(row[4]),
                    region_code=str(row[5]),
                ),
                request=PreviewRequestV1.model_validate(_json(row[6])),
                created_at=row[7],
                expires_at=row[8],
            )
            artifact = _artifact(row[9:])
            cursor.execute(
                """
                UPDATE preview.artifacts SET last_accessed_at = %s
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND artifact_id = %s
                """,
                (now, *_scope_values(scope), artifact.artifact_id),
            )
            connection.commit()
            return session, artifact
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

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
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT COALESCE(sum(total_bytes), 0)
                FROM preview.artifacts WHERE status = 'READY'
                """
            )
            total = int(cursor.fetchone()[0])
            # RLS binds this repository instance to one exact project/region scope.
            # A privileged global coordinator may supply the smaller global budget;
            # this scoped collector must always enforce the project budget itself.
            quota = min(project_quota_bytes, global_quota_bytes)
            over_high = total * 100 >= quota * high_watermark_percent
            cursor.execute(
                f"""
                SELECT {_ARTIFACT_COLUMNS}
                FROM preview.artifacts
                WHERE status IN ('READY', 'FAILED') AND active_reference_count = 0
                  AND legal_hold = false AND governance_hold = false
                  AND (retention_until IS NULL OR retention_until <= %s)
                  AND (expires_at <= %s OR %s)
                ORDER BY (expires_at <= %s) DESC, last_accessed_at, artifact_id
                LIMIT %s
                """,
                (now, now, over_high, now, limit),
            )
            return tuple(_artifact(row) for row in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def mark_deleting(
        self, scope: PreviewScopeV1, artifact_id: str, *, expected_version: int
    ) -> bool:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE preview.artifacts
                SET status = 'DELETING', version = version + 1
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND artifact_id = %s AND version = %s
                  AND status IN ('READY', 'FAILED')
                  AND active_reference_count = 0
                  AND legal_hold = false AND governance_hold = false
                  AND (retention_until IS NULL OR retention_until <= now())
                """,
                (*_scope_values(scope), artifact_id, expected_version),
            )
            changed = bool(cursor.rowcount == 1)
            connection.commit()
            return changed
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def delete_artifact_metadata(self, scope: PreviewScopeV1, artifact_id: str) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                DELETE FROM preview.artifacts
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND artifact_id = %s AND status = 'DELETING'
                  AND active_reference_count = 0
                """,
                (*_scope_values(scope), artifact_id),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def deleting_artifacts(self, *, limit: int) -> Sequence[PreviewArtifactV1]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT {_ARTIFACT_COLUMNS}
                FROM preview.artifacts
                WHERE status = 'DELETING' AND active_reference_count = 0
                ORDER BY last_accessed_at, artifact_id
                LIMIT %s
                """,
                (limit,),
            )
            return tuple(_artifact(row) for row in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def cleanup_expired_metadata(self, *, now: datetime) -> int:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                WITH expired AS (
                    DELETE FROM preview.sessions
                    WHERE expires_at <= %s
                    RETURNING organization_id, project_id, region_code, artifact_id
                ), counts AS (
                    SELECT organization_id, project_id, region_code, artifact_id, count(*) AS n
                    FROM expired
                    GROUP BY organization_id, project_id, region_code, artifact_id
                ), updated AS (
                    UPDATE preview.artifacts AS a
                    SET active_reference_count = GREATEST(0, a.active_reference_count - counts.n)
                    FROM counts
                    WHERE a.organization_id = counts.organization_id
                      AND a.project_id = counts.project_id
                      AND a.region_code = counts.region_code
                      AND a.artifact_id = counts.artifact_id
                    RETURNING 1
                )
                SELECT count(*) FROM expired
                """,
                (now,),
            )
            deleted_sessions = int(cursor.fetchone()[0])
            cursor.execute(
                """
                DELETE FROM preview.jobs
                WHERE completed_at IS NOT NULL AND completed_at <= %s - interval '24 hours'
                """,
                (now,),
            )
            connection.commit()
            return deleted_sessions
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def storage_totals(self) -> tuple[int, int]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT COALESCE(sum(total_bytes), 0), count(*)
                FROM preview.artifacts WHERE status = 'READY'
                """
            )
            row = cursor.fetchone()
            return int(row[0]), int(row[1])
        finally:
            cursor.close()
            connection.close()


def _scope_values(scope: PreviewScopeV1) -> tuple[str, str, str]:
    return scope.organization_id, scope.project_id, scope.region_code


def _json(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


def _artifact(row: Sequence[object]) -> PreviewArtifactV1:
    manifest = _json(row[16])
    if not isinstance(manifest, list):
        raise TypeError("preview object_manifest must be an array")
    return PreviewArtifactV1(
        artifact_id=str(row[0]),
        artifact_key=str(row[1]),
        scope=PreviewScopeV1(
            organization_id=str(row[2]),
            project_id=str(row[3]),
            region_code=str(row[4]),
        ),
        dataset_id=str(row[5]),
        rollout_id=str(row[6]),
        lance_version=str(row[7]),
        camera_id=str(row[8]),
        profile_id=str(row[9]),
        pipeline_revision=str(row[10]),
        source_start_step=None if row[11] is None else _as_int(row[11]),
        source_end_step=None if row[12] is None else _as_int(row[12]),
        status=PreviewArtifactStatus(str(row[13])),
        object_prefix=None if row[14] is None else str(row[14]),
        playlist_key=None if row[15] is None else str(row[15]),
        objects=tuple(PreviewObjectV1.model_validate(item) for item in manifest),
        total_bytes=_as_int(row[17]),
        frame_count=_as_int(row[18]),
        duration_seconds=_as_float(row[19]),
        content_sha256=None if row[20] is None else str(row[20]),
        rebuild_source_id=str(row[21]),
        created_at=row[22],
        ready_at=row[23],
        last_accessed_at=row[24],
        expires_at=row[25],
        failure_code=None if row[26] is None else str(row[26]),
        version=_as_int(row[27]),
        active_reference_count=_as_int(row[28]),
        legal_hold=bool(row[29]),
        governance_hold=bool(row[30]),
        retention_until=row[31],
    )


def _job(row: Sequence[object]) -> PreviewJobV1:
    return PreviewJobV1(
        job_id=str(row[0]),
        artifact_id=str(row[1]),
        artifact_key=str(row[2]),
        scope=PreviewScopeV1(
            organization_id=str(row[3]),
            project_id=str(row[4]),
            region_code=str(row[5]),
        ),
        status=PreviewJobStatus(str(row[6])),
        attempt=_as_int(row[7]),
        progress=_as_int(row[8]),
        error_code=None if row[9] is None else str(row[9]),
        request=PreviewRequestV1.model_validate(_json(row[10])),
        created_at=row[11],
        started_at=row[12],
        completed_at=row[13],
    )


def _prefixed_artifact_columns(alias: str) -> str:
    return ", ".join(
        f"{alias}.{column.strip()}"
        for column in _ARTIFACT_COLUMNS.split(",")
        if column.strip()
    )


def _as_int(value: object) -> int:
    if isinstance(value, (int, str)):
        return int(value)
    raise TypeError("database value is not an integer")


def _as_float(value: object) -> float:
    if isinstance(value, (int, float, str)):
        return float(value)
    raise TypeError("database value is not numeric")
