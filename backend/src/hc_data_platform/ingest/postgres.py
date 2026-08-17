"""PostgreSQL implementation of the resumable ingest persistence port."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, cast
from uuid import UUID

from hc_data_platform.core.errors import problem
from hc_data_platform.security.psycopg import PsycopgIdempotencyStore

from .models import (
    CollectionJob,
    RawObjectCommittedV1,
    Rollout,
    UploadObject,
    UploadPart,
    UploadSession,
)


class PostgresIngestPersistence:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory
        self.idempotency_store = PsycopgIdempotencyStore(
            connection_factory,
            response_decoder=UploadSession.model_validate,
        )

    def register_upload(
        self,
        job: CollectionJob,
        rollout: Rollout,
        session: UploadSession,
        upload_object: UploadObject,
    ) -> UploadSession:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                self._upsert_job(cursor, job)
                self._upsert_rollout(cursor, rollout)
                cursor.execute(
                    """
                    INSERT INTO ingest.upload_sessions (
                        session_id, project_id, region_code, rollout_id, object_key,
                        multipart_upload_id, expected_sha256, expected_size, expected_crc64,
                        manifest_fingerprint, status, etag, failure_code,
                        created_at, updated_at, completed_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    ) ON CONFLICT (project_id, rollout_id) DO NOTHING
                    """,
                    self._session_values(session),
                )
                cursor.execute(
                    """
                    SELECT session_id, project_id, region_code, rollout_id, object_key,
                           multipart_upload_id, expected_sha256, expected_size, expected_crc64,
                           manifest_fingerprint, status, etag, failure_code,
                           created_at, updated_at, completed_at
                    FROM ingest.upload_sessions
                    WHERE project_id = %s AND rollout_id = %s
                    """,
                    (session.project_id, session.rollout_id),
                )
                persisted = self._model(cursor, cursor.fetchone(), UploadSession)
                if persisted is None:
                    raise RuntimeError("PostgreSQL did not return the registered upload session")
                if (
                    persisted.expected_sha256 != session.expected_sha256
                    or persisted.region_code != session.region_code
                ):
                    raise problem(
                        status=409,
                        code="ROLLOUT_CONTENT_CONFLICT",
                        title="Rollout content conflict",
                        detail=(
                            "This rollout is already registered with different content or scope."
                        ),
                    )
                if persisted.session_id == session.session_id:
                    self._upsert_upload_object(cursor, upload_object)
            connection.commit()
            return cast(UploadSession, persisted)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_collection_job(self, project_id: str, collection_job_id: str) -> CollectionJob | None:
        return self._get_model(
            """
            SELECT project_id, region_code, task_id, collection_job_id, robot_id,
                   status, created_at, updated_at
            FROM ingest.collection_jobs
            WHERE project_id = %s AND collection_job_id = %s
            """,
            (project_id, collection_job_id),
            CollectionJob,
        )

    def save_collection_job(self, job: CollectionJob) -> None:
        self._write(lambda cursor: self._upsert_job(cursor, job))

    def get_rollout(self, project_id: str, rollout_id: str) -> Rollout | None:
        return self._get_model(
            """
            SELECT project_id, region_code, collection_job_id, rollout_id, sequence_no,
                   robot_id, source_sha256, status, created_at, updated_at
            FROM ingest.rollouts WHERE project_id = %s AND rollout_id = %s
            """,
            (project_id, rollout_id),
            Rollout,
        )

    def save_rollout(self, rollout: Rollout) -> None:
        self._write(lambda cursor: self._upsert_rollout(cursor, rollout))

    def get_session(self, session_id: str) -> UploadSession | None:
        return self._get_model(
            """
            SELECT session_id, project_id, region_code, rollout_id, object_key,
                   multipart_upload_id, expected_sha256, expected_size, expected_crc64,
                   manifest_fingerprint, status, etag, failure_code,
                   created_at, updated_at, completed_at
            FROM ingest.upload_sessions WHERE session_id = %s
            """,
            (session_id,),
            UploadSession,
        )

    def find_session(self, project_id: str, rollout_id: str) -> UploadSession | None:
        return self._get_model(
            """
            SELECT session_id, project_id, region_code, rollout_id, object_key,
                   multipart_upload_id, expected_sha256, expected_size, expected_crc64,
                   manifest_fingerprint, status, etag, failure_code,
                   created_at, updated_at, completed_at
            FROM ingest.upload_sessions WHERE project_id = %s AND rollout_id = %s
            """,
            (project_id, rollout_id),
            UploadSession,
        )

    def save_session(self, session: UploadSession) -> None:
        def save(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO ingest.upload_sessions (
                    session_id, project_id, region_code, rollout_id, object_key,
                    multipart_upload_id, expected_sha256, expected_size, expected_crc64,
                    manifest_fingerprint, status, etag, failure_code,
                    created_at, updated_at, completed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) ON CONFLICT (session_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    etag = EXCLUDED.etag,
                    failure_code = EXCLUDED.failure_code,
                    updated_at = EXCLUDED.updated_at,
                    completed_at = EXCLUDED.completed_at
                WHERE ingest.upload_sessions.project_id = EXCLUDED.project_id
                  AND ingest.upload_sessions.region_code = EXCLUDED.region_code
                  AND ingest.upload_sessions.rollout_id = EXCLUDED.rollout_id
                  AND ingest.upload_sessions.object_key = EXCLUDED.object_key
                  AND ingest.upload_sessions.multipart_upload_id = EXCLUDED.multipart_upload_id
                """,
                self._session_values(session),
            )
            self._require_write(cursor, "upload session")

        self._write(save)

    def get_upload_object(self, session_id: str) -> UploadObject | None:
        return self._get_model(
            """
            SELECT object_id, session_id, project_id, region_code, rollout_id, object_key,
                   expected_size, expected_sha256, expected_crc64, actual_size,
                   actual_crc64, actual_sha256, etag, status, created_at, updated_at
            FROM ingest.upload_objects WHERE session_id = %s
            """,
            (session_id,),
            UploadObject,
        )

    def save_upload_object(self, upload_object: UploadObject) -> None:
        self._write(lambda cursor: self._upsert_upload_object(cursor, upload_object))

    def list_parts(self, session_id: str) -> list[UploadPart]:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT session_id, project_id, region_code, part_number, status,
                           etag, size, crc64, authorization_expires_at, updated_at
                    FROM ingest.upload_parts WHERE session_id = %s ORDER BY part_number
                    """,
                    (session_id,),
                )
                return [
                    cast(UploadPart, self._model(cursor, row, UploadPart))
                    for row in cursor.fetchall()
                ]
        finally:
            connection.close()

    def save_part(self, part: UploadPart) -> None:
        def save(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO ingest.upload_parts (
                    session_id, project_id, region_code, part_number, status,
                    etag, size, crc64, authorization_expires_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id, part_number) DO UPDATE SET
                    status = EXCLUDED.status,
                    etag = EXCLUDED.etag,
                    size = EXCLUDED.size,
                    crc64 = EXCLUDED.crc64,
                    authorization_expires_at = EXCLUDED.authorization_expires_at,
                    updated_at = EXCLUDED.updated_at
                WHERE ingest.upload_parts.project_id = EXCLUDED.project_id
                  AND ingest.upload_parts.region_code = EXCLUDED.region_code
                """,
                (
                    part.session_id,
                    part.project_id,
                    part.region_code,
                    part.part_number,
                    part.status.value,
                    part.etag,
                    part.size,
                    part.crc64,
                    part.authorization_expires_at,
                    part.updated_at,
                ),
            )
            self._require_write(cursor, "upload part")

        self._write(save)

    def get_committed(self, project_id: str, rollout_id: str) -> RawObjectCommittedV1 | None:
        return self._get_model(
            """
            SELECT project_id, region_code, rollout_id, object_key, manifest_key,
                   source_sha256 AS sha256, file_size, committed_at
            FROM ingest.rollout_objects WHERE project_id = %s AND rollout_id = %s
            """,
            (project_id, rollout_id),
            RawObjectCommittedV1,
        )

    def save_committed(self, event: RawObjectCommittedV1) -> None:
        def save(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO ingest.rollout_objects (
                    project_id, region_code, rollout_id, object_key, manifest_key,
                    source_sha256, crc64, file_size, status, committed_at
                )
                SELECT %s, %s, %s, %s, %s, %s, expected_crc64, %s, 'COMMITTED', %s
                FROM ingest.upload_sessions
                WHERE project_id = %s AND rollout_id = %s
                ON CONFLICT (project_id, rollout_id) DO NOTHING
                """,
                (
                    event.project_id,
                    event.region_code,
                    event.rollout_id,
                    event.object_key,
                    event.manifest_key,
                    event.sha256,
                    event.file_size,
                    event.committed_at,
                    event.project_id,
                    event.rollout_id,
                ),
            )
            cursor.execute(
                """
                SELECT source_sha256, object_key, manifest_key FROM ingest.rollout_objects
                WHERE project_id = %s AND rollout_id = %s
                """,
                (event.project_id, event.rollout_id),
            )
            row = cursor.fetchone()
            if row is None or tuple(map(str, row)) != (
                event.sha256,
                event.object_key,
                event.manifest_key,
            ):
                raise problem(
                    status=409,
                    code="ROLLOUT_CONTENT_CONFLICT",
                    title="Rollout content conflict",
                    detail="The committed rollout object is immutable.",
                )

        self._write(save)

    def _upsert_job(self, cursor: Any, job: CollectionJob) -> None:
        cursor.execute(
            """
            INSERT INTO ingest.collection_jobs (
                project_id, region_code, task_id, collection_job_id, robot_id,
                status, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (project_id, collection_job_id) DO UPDATE SET
                status = EXCLUDED.status, updated_at = EXCLUDED.updated_at
            WHERE ingest.collection_jobs.region_code = EXCLUDED.region_code
              AND ingest.collection_jobs.task_id = EXCLUDED.task_id
              AND ingest.collection_jobs.robot_id = EXCLUDED.robot_id
            """,
            (
                job.project_id,
                job.region_code,
                job.task_id,
                job.collection_job_id,
                job.robot_id,
                job.status.value,
                job.created_at,
                job.updated_at,
            ),
        )
        self._require_write(cursor, "collection job")

    def _upsert_rollout(self, cursor: Any, rollout: Rollout) -> None:
        cursor.execute(
            """
            INSERT INTO ingest.rollouts (
                project_id, region_code, collection_job_id, rollout_id, sequence_no,
                robot_id, source_sha256, status, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (project_id, rollout_id) DO UPDATE SET
                status = EXCLUDED.status, updated_at = EXCLUDED.updated_at
            WHERE ingest.rollouts.region_code = EXCLUDED.region_code
              AND ingest.rollouts.collection_job_id = EXCLUDED.collection_job_id
              AND ingest.rollouts.sequence_no = EXCLUDED.sequence_no
              AND ingest.rollouts.robot_id = EXCLUDED.robot_id
              AND ingest.rollouts.source_sha256 = EXCLUDED.source_sha256
            """,
            (
                rollout.project_id,
                rollout.region_code,
                rollout.collection_job_id,
                rollout.rollout_id,
                rollout.sequence_no,
                rollout.robot_id,
                rollout.source_sha256,
                rollout.status.value,
                rollout.created_at,
                rollout.updated_at,
            ),
        )
        self._require_write(cursor, "rollout")

    def _upsert_upload_object(self, cursor: Any, item: UploadObject) -> None:
        cursor.execute(
            """
            INSERT INTO ingest.upload_objects (
                object_id, session_id, project_id, region_code, rollout_id, object_key,
                expected_size, expected_sha256, expected_crc64, actual_size,
                actual_crc64, actual_sha256, etag, status, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_id) DO UPDATE SET
                actual_size = EXCLUDED.actual_size,
                actual_crc64 = EXCLUDED.actual_crc64,
                actual_sha256 = EXCLUDED.actual_sha256,
                etag = EXCLUDED.etag,
                status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at
            WHERE ingest.upload_objects.project_id = EXCLUDED.project_id
              AND ingest.upload_objects.region_code = EXCLUDED.region_code
              AND ingest.upload_objects.rollout_id = EXCLUDED.rollout_id
              AND ingest.upload_objects.object_key = EXCLUDED.object_key
            """,
            (
                item.object_id,
                item.session_id,
                item.project_id,
                item.region_code,
                item.rollout_id,
                item.object_key,
                item.expected_size,
                item.expected_sha256,
                item.expected_crc64,
                item.actual_size,
                item.actual_crc64,
                item.actual_sha256,
                item.etag,
                item.status.value,
                item.created_at,
                item.updated_at,
            ),
        )
        self._require_write(cursor, "upload object")

    @staticmethod
    def _session_values(session: UploadSession) -> tuple[object, ...]:
        return (
            session.session_id,
            session.project_id,
            session.region_code,
            session.rollout_id,
            session.object_key,
            session.multipart_upload_id,
            session.expected_sha256,
            session.expected_size,
            session.expected_crc64,
            session.manifest_fingerprint,
            session.status.value,
            session.etag,
            session.failure_code,
            session.created_at,
            session.updated_at,
            session.completed_at,
        )

    def _write(self, operation: Callable[[Any], None]) -> None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                operation(cursor)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _get_model(
        self,
        query: str,
        params: tuple[object, ...],
        model: Any,
    ) -> Any | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                return self._model(cursor, cursor.fetchone(), model)
        finally:
            connection.close()

    @staticmethod
    def _model(cursor: Any, row: Sequence[object] | None, model: Any) -> Any | None:
        if row is None:
            return None
        names = [str(item.name) for item in cursor.description]
        values = [str(value) if isinstance(value, UUID) else value for value in row]
        return model.model_validate(dict(zip(names, values, strict=True)))

    @staticmethod
    def _require_write(cursor: Any, resource: str) -> None:
        if cursor.rowcount != 1:
            raise problem(
                status=409,
                code="IMMUTABLE_IDENTITY_CONFLICT",
                title=f"{resource.title()} identity conflict",
                detail=f"The immutable identity fields of this {resource} cannot be changed.",
            )
