"""PostgreSQL implementation of the resumable ingest persistence port."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from hc_data_platform.core.errors import problem
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.security.psycopg import PsycopgIdempotencyStore

from .models import (
    CollectionJob,
    IngestTriggerStatus,
    IngestWorkflowLocator,
    ManifestPreflightResultV1,
    RawMediaAccessAuditEvent,
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
        preflight: ManifestPreflightResultV1,
    ) -> UploadSession:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                self._upsert_job(cursor, job)
                self._upsert_rollout(cursor, rollout)
                cursor.execute(
                    """
                    INSERT INTO ingest.upload_sessions (
                        session_id, project_id, region_code, rollout_id, data_package_id,
                        object_key, source_type, multipart_upload_id,
                        expected_sha256, expected_size, expected_crc64,
                        manifest_fingerprint, status, etag, failure_code,
                        created_at, updated_at, completed_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s
                    ) ON CONFLICT (project_id, data_package_id) DO NOTHING
                    """,
                    self._session_values(session),
                )
                cursor.execute(
                    """
                    SELECT session_id, project_id, region_code, rollout_id, data_package_id,
                           object_key, source_type, multipart_upload_id,
                           expected_sha256, expected_size, expected_crc64,
                           manifest_fingerprint, status, etag, failure_code,
                           created_at, updated_at, completed_at
                    FROM ingest.upload_sessions
                    WHERE project_id = %s AND data_package_id = %s
                    """,
                    (session.project_id, session.data_package_id),
                )
                persisted = self._model(cursor, cursor.fetchone(), UploadSession)
                if persisted is None:
                    raise RuntimeError("PostgreSQL did not return the registered upload session")
                if (
                    persisted.expected_sha256 != session.expected_sha256
                    or persisted.region_code != session.region_code
                    or persisted.rollout_id != session.rollout_id
                    or persisted.manifest_fingerprint != session.manifest_fingerprint
                    or persisted.source_type != session.source_type
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
                    self._upsert_manifest_preflight(cursor, session.session_id, preflight)
            connection.commit()
            return cast(UploadSession, persisted)
        except Exception as exc:
            connection.rollback()
            if _has_sqlstate(exc, "23505"):
                raise problem(
                    status=409,
                    code="IMMUTABLE_IDENTITY_CONFLICT",
                    title="Upload identity conflict",
                    detail="The package, rollout, sequence, and object identities are unique.",
                ) from exc
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
            SELECT project_id, region_code, collection_job_id, rollout_id,
                   collection_session_id, recording_request_id, data_package_id,
                   pico_instance_id, sequence_no, robot_id, source_sha256,
                   status, created_at, updated_at
            FROM ingest.rollouts WHERE project_id = %s AND rollout_id = %s
            """,
            (project_id, rollout_id),
            Rollout,
        )

    def get_rollout_by_package(self, project_id: str, data_package_id: str) -> Rollout | None:
        return self._get_model(
            """
            SELECT project_id, region_code, collection_job_id, rollout_id,
                   collection_session_id, recording_request_id, data_package_id,
                   pico_instance_id, sequence_no, robot_id, source_sha256,
                   status, created_at, updated_at
            FROM ingest.rollouts WHERE project_id = %s AND data_package_id = %s
            """,
            (project_id, data_package_id),
            Rollout,
        )

    def save_rollout(self, rollout: Rollout) -> None:
        self._write(lambda cursor: self._upsert_rollout(cursor, rollout))

    def get_session(self, session_id: str) -> UploadSession | None:
        session = self._get_model(
            """
            SELECT session_id, project_id, region_code, rollout_id, data_package_id,
                   object_key, source_type, multipart_upload_id,
                   expected_sha256, expected_size, expected_crc64,
                   manifest_fingerprint, status, etag, failure_code,
                   created_at, updated_at, completed_at
            FROM ingest.upload_sessions WHERE session_id = %s
            """,
            (session_id,),
            UploadSession,
        )
        if session is None:
            return None
        return cast(UploadSession, session).model_copy(
            update={"workflow": self.get_workflow_trigger(session_id)}
        )

    def find_session(self, project_id: str, rollout_id: str) -> UploadSession | None:
        return self._get_model(
            """
            SELECT session_id, project_id, region_code, rollout_id, data_package_id,
                   object_key, source_type, multipart_upload_id,
                   expected_sha256, expected_size, expected_crc64,
                   manifest_fingerprint, status, etag, failure_code,
                   created_at, updated_at, completed_at
            FROM ingest.upload_sessions WHERE project_id = %s AND rollout_id = %s
            """,
            (project_id, rollout_id),
            UploadSession,
        )

    def find_session_by_package(
        self, project_id: str, data_package_id: str
    ) -> UploadSession | None:
        return self._get_model(
            """
            SELECT session_id, project_id, region_code, rollout_id, data_package_id,
                   object_key, source_type, multipart_upload_id,
                   expected_sha256, expected_size, expected_crc64,
                   manifest_fingerprint, status, etag, failure_code,
                   created_at, updated_at, completed_at
            FROM ingest.upload_sessions
            WHERE project_id = %s AND data_package_id = %s
            """,
            (project_id, data_package_id),
            UploadSession,
        )

    def list_sessions(
        self,
        project_id: str,
        region_code: str,
        *,
        status: str | None = None,
        data_package_id: str | None = None,
        after: tuple[datetime, str] | None = None,
        limit: int = 50,
    ) -> list[UploadSession]:
        clauses = ["project_id = %s", "region_code = %s"]
        params: list[object] = [project_id, region_code]
        if status is not None:
            clauses.append("status = %s")
            params.append(status)
        if data_package_id is not None:
            clauses.append("data_package_id = %s")
            params.append(data_package_id)
        if after is not None:
            clauses.append("(updated_at, session_id) < (%s, %s::uuid)")
            params.extend(after)
        params.append(limit)
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT session_id, project_id, region_code, rollout_id, data_package_id,
                           object_key, source_type, multipart_upload_id,
                           expected_sha256, expected_size, expected_crc64,
                           manifest_fingerprint, status, etag, failure_code,
                           created_at, updated_at, completed_at
                    FROM ingest.upload_sessions
                    WHERE {" AND ".join(clauses)}
                    ORDER BY updated_at DESC, session_id DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                return [
                    cast(UploadSession, self._model(cursor, row, UploadSession))
                    for row in cursor.fetchall()
                ]
        finally:
            connection.close()

    def save_session(self, session: UploadSession) -> None:
        def save(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO ingest.upload_sessions (
                    session_id, project_id, region_code, rollout_id, data_package_id,
                    object_key, source_type, multipart_upload_id,
                    expected_sha256, expected_size, expected_crc64,
                    manifest_fingerprint, status, etag, failure_code,
                    created_at, updated_at, completed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                ) ON CONFLICT (session_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    etag = EXCLUDED.etag,
                    failure_code = EXCLUDED.failure_code,
                    updated_at = EXCLUDED.updated_at,
                    completed_at = EXCLUDED.completed_at
                WHERE ingest.upload_sessions.project_id = EXCLUDED.project_id
                  AND ingest.upload_sessions.region_code = EXCLUDED.region_code
                  AND ingest.upload_sessions.rollout_id = EXCLUDED.rollout_id
                  AND ingest.upload_sessions.data_package_id = EXCLUDED.data_package_id
                  AND ingest.upload_sessions.object_key = EXCLUDED.object_key
                  AND ingest.upload_sessions.source_type = EXCLUDED.source_type
                  AND ingest.upload_sessions.multipart_upload_id
                      IS NOT DISTINCT FROM EXCLUDED.multipart_upload_id
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
                           etag, size, crc64, retry_count, failure_code,
                           authorization_expires_at, updated_at
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
                    etag, size, crc64, retry_count, failure_code,
                    authorization_expires_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id, part_number) DO UPDATE SET
                    status = EXCLUDED.status,
                    etag = EXCLUDED.etag,
                    size = EXCLUDED.size,
                    crc64 = EXCLUDED.crc64,
                    retry_count = EXCLUDED.retry_count,
                    failure_code = EXCLUDED.failure_code,
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
                    part.retry_count,
                    part.failure_code,
                    part.authorization_expires_at,
                    part.updated_at,
                ),
            )
            self._require_write(cursor, "upload part")

        self._write(save)

    def save_manifest_preflight(self, session_id: str, result: ManifestPreflightResultV1) -> None:
        self._write(lambda cursor: self._upsert_manifest_preflight(cursor, session_id, result))

    def get_manifest_preflight(self, session_id: str) -> ManifestPreflightResultV1 | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT preflight_json
                    FROM ingest.manifest_discoveries WHERE session_id = %s
                    """,
                    (session_id,),
                )
                row = cursor.fetchone()
                return None if row is None else ManifestPreflightResultV1.model_validate(row[0])
        finally:
            connection.close()

    def get_committed(self, project_id: str, rollout_id: str) -> RawObjectCommittedV1 | None:
        return self._get_model(
            """
            SELECT project_id, region_code, rollout_id, data_package_id,
                   object_key, manifest_key,
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
                    project_id, region_code, rollout_id, data_package_id,
                    object_key, manifest_key,
                    source_sha256, crc64, file_size, status, committed_at
                )
                SELECT %s, %s, %s, %s, %s, %s, %s,
                       expected_crc64, %s, 'COMMITTED', %s
                FROM ingest.upload_sessions
                WHERE project_id = %s AND rollout_id = %s
                ON CONFLICT (project_id, rollout_id) DO NOTHING
                """,
                (
                    event.project_id,
                    event.region_code,
                    event.rollout_id,
                    event.data_package_id,
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
                SELECT source_sha256, data_package_id, object_key, manifest_key
                FROM ingest.rollout_objects
                WHERE project_id = %s AND rollout_id = %s
                """,
                (event.project_id, event.rollout_id),
            )
            row = cursor.fetchone()
            if row is None or tuple(map(str, row)) != (
                event.sha256,
                event.data_package_id,
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

    def commit_raw_and_stage_workflow(
        self,
        *,
        session: UploadSession,
        event: RawObjectCommittedV1,
        workflow: IngestWorkflowLocator,
        actor_id: str,
        request_id: str,
    ) -> IngestWorkflowLocator:
        """Commit Raw state, trigger, outbox, and audit in one database transaction."""

        envelope = DomainEventEnvelope(
            event_id=workflow.event_id,
            event_type="ingest.workflow.requested.v1",
            aggregate_type="upload_session",
            aggregate_id=session.session_id,
            project_id=session.project_id,
            region_code=session.region_code,
            trace_id=request_id,
            payload={
                "session_id": session.session_id,
                "rollout_id": session.rollout_id,
                "data_package_id": session.data_package_id,
                "workflow_id": workflow.workflow_id,
            },
        )
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT status FROM ingest.upload_sessions
                    WHERE session_id = %s AND project_id = %s AND region_code = %s
                    FOR UPDATE
                    """,
                    (session.session_id, session.project_id, session.region_code),
                )
                if cursor.fetchone() is None:
                    raise problem(
                        status=404,
                        code="UPLOAD_SESSION_NOT_FOUND",
                        title="Upload session not found",
                        detail="The upload session does not exist in this scope.",
                    )
                cursor.execute(
                    """
                    INSERT INTO ingest.rollout_objects (
                        project_id, region_code, rollout_id, data_package_id,
                        object_key, manifest_key, source_sha256, crc64,
                        file_size, status, committed_at
                    )
                    SELECT %s, %s, %s, %s, %s, %s, %s,
                           expected_crc64, %s, 'COMMITTED', %s
                    FROM ingest.upload_sessions
                    WHERE session_id = %s AND project_id = %s AND region_code = %s
                    ON CONFLICT (project_id, rollout_id) DO NOTHING
                    """,
                    (
                        event.project_id,
                        event.region_code,
                        event.rollout_id,
                        event.data_package_id,
                        event.object_key,
                        event.manifest_key,
                        event.sha256,
                        event.file_size,
                        event.committed_at,
                        session.session_id,
                        session.project_id,
                        session.region_code,
                    ),
                )
                cursor.execute(
                    """
                    SELECT data_package_id, object_key, manifest_key, source_sha256
                    FROM ingest.rollout_objects
                    WHERE project_id = %s AND region_code = %s AND rollout_id = %s
                    """,
                    (event.project_id, event.region_code, event.rollout_id),
                )
                raw = cursor.fetchone()
                if raw is None or tuple(map(str, raw)) != (
                    event.data_package_id,
                    event.object_key,
                    event.manifest_key,
                    event.sha256,
                ):
                    raise problem(
                        status=409,
                        code="ROLLOUT_CONTENT_CONFLICT",
                        title="Rollout content conflict",
                        detail="The committed rollout object is immutable.",
                    )
                cursor.execute(
                    """
                    UPDATE ingest.upload_objects
                    SET actual_size = %s, actual_sha256 = %s,
                        status = 'COMMITTED', updated_at = %s
                    WHERE session_id = %s AND project_id = %s AND region_code = %s
                      AND rollout_id = %s AND object_key = %s
                    """,
                    (
                        event.file_size,
                        event.sha256,
                        event.committed_at,
                        session.session_id,
                        session.project_id,
                        session.region_code,
                        session.rollout_id,
                        session.object_key,
                    ),
                )
                self._require_write(cursor, "upload object")
                cursor.execute(
                    """
                    UPDATE ingest.upload_sessions
                    SET status = 'RAW_COMMITTED', updated_at = %s
                    WHERE session_id = %s AND project_id = %s AND region_code = %s
                      AND rollout_id = %s AND data_package_id = %s
                    """,
                    (
                        event.committed_at,
                        session.session_id,
                        session.project_id,
                        session.region_code,
                        session.rollout_id,
                        session.data_package_id,
                    ),
                )
                self._require_write(cursor, "upload session")
                cursor.execute(
                    """
                    UPDATE ingest.rollouts
                    SET status = 'RAW_COMMITTED', updated_at = %s
                    WHERE project_id = %s AND region_code = %s AND rollout_id = %s
                    """,
                    (
                        event.committed_at,
                        session.project_id,
                        session.region_code,
                        session.rollout_id,
                    ),
                )
                self._require_write(cursor, "rollout")
                cursor.execute(
                    """
                    INSERT INTO ingest.workflow_triggers (
                        session_id, project_id, region_code, rollout_id, event_id,
                        workflow_id, status, attempts, last_error_code,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'PENDING', 0, NULL, %s, %s)
                    ON CONFLICT (session_id) DO NOTHING
                    """,
                    (
                        session.session_id,
                        session.project_id,
                        session.region_code,
                        session.rollout_id,
                        workflow.event_id,
                        workflow.workflow_id,
                        workflow.updated_at,
                        workflow.updated_at,
                    ),
                )
                cursor.execute(
                    """
                    SELECT event_id, workflow_id, status, attempts,
                           last_error_code, updated_at
                    FROM ingest.workflow_triggers
                    WHERE session_id = %s AND project_id = %s AND region_code = %s
                    """,
                    (session.session_id, session.project_id, session.region_code),
                )
                trigger_raw = cursor.fetchone()
                if trigger_raw is None:
                    raise RuntimeError("PostgreSQL did not return the ingest workflow trigger")
                trigger = self._trigger_model(cursor, trigger_raw)
                if (
                    trigger.event_id != workflow.event_id
                    or trigger.workflow_id != workflow.workflow_id
                ):
                    raise problem(
                        status=409,
                        code="INGEST_TRIGGER_IDENTITY_CONFLICT",
                        title="Ingest trigger identity conflict",
                        detail="This upload already identifies another workflow trigger.",
                    )
                cursor.execute(
                    """
                    INSERT INTO core.outbox_events (
                        event_id, project_id, region_code, event_type, envelope,
                        occurred_at, published_at, publish_attempts, available_at
                    ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, NULL, 0, clock_timestamp())
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    (
                        envelope.event_id,
                        envelope.project_id,
                        envelope.region_code,
                        envelope.event_type,
                        envelope.model_dump_json(exclude_none=True),
                        envelope.occurred_at,
                    ),
                )
                audit_id = str(uuid5(NAMESPACE_URL, f"{workflow.event_id}:staged"))
                safe_after = {
                    "status": "RAW_COMMITTED",
                    "workflow_id": workflow.workflow_id,
                }
                cursor.execute(
                    """
                    INSERT INTO core.audit_events (
                        audit_id, project_id, region_code, actor_id, action,
                        resource_type, resource_id, request_id, before_hash,
                        after_hash, details, occurred_at
                    ) VALUES (%s, %s, %s, %s, 'ingest.workflow.staged',
                              'upload_session', %s, %s, NULL, %s, %s::jsonb, %s)
                    ON CONFLICT (audit_id) DO NOTHING
                    """,
                    (
                        audit_id,
                        session.project_id,
                        session.region_code,
                        actor_id,
                        session.session_id,
                        request_id,
                        canonical_hash(safe_after),
                        json.dumps({"workflow_id": workflow.workflow_id}),
                        envelope.occurred_at,
                    ),
                )
            connection.commit()
            return trigger
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_workflow_trigger(self, session_id: str) -> IngestWorkflowLocator | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT event_id, workflow_id, status, attempts,
                           last_error_code, updated_at
                    FROM ingest.workflow_triggers WHERE session_id = %s
                    """,
                    (session_id,),
                )
                raw = cursor.fetchone()
                return None if raw is None else self._trigger_model(cursor, raw)
        finally:
            connection.close()

    def record_raw_media_access(self, event: RawMediaAccessAuditEvent) -> None:
        def insert(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO core.audit_events (
                    audit_id, project_id, region_code, actor_id, action, resource_type,
                    resource_id, request_id, before_hash, after_hash, details, occurred_at
                ) VALUES (
                    %s, %s, %s, %s, 'raw.media.access_authorized',
                    'RAW_MEDIA', %s, %s, NULL, NULL, %s::jsonb, %s
                )
                """,
                (
                    str(uuid4()),
                    event.project_id,
                    event.region_code,
                    event.actor_id,
                    event.rollout_id,
                    event.request_id,
                    json.dumps(
                        {
                            "byte_length": event.byte_length,
                            "format": "MCAP",
                            "outcome": "AUTHORIZED",
                            "session_id": event.session_id,
                        },
                        sort_keys=True,
                    ),
                    event.occurred_at,
                ),
            )
            self._require_write(cursor, "raw media access audit")

        self._write(insert)

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
                project_id, region_code, collection_job_id, rollout_id,
                collection_session_id, recording_request_id, data_package_id,
                pico_instance_id, sequence_no, robot_id, source_sha256,
                status, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (project_id, data_package_id) DO UPDATE SET
                status = EXCLUDED.status, updated_at = EXCLUDED.updated_at
            WHERE ingest.rollouts.region_code = EXCLUDED.region_code
              AND ingest.rollouts.collection_job_id = EXCLUDED.collection_job_id
              AND ingest.rollouts.rollout_id = EXCLUDED.rollout_id
              AND ingest.rollouts.collection_session_id = EXCLUDED.collection_session_id
              AND ingest.rollouts.recording_request_id = EXCLUDED.recording_request_id
              AND ingest.rollouts.pico_instance_id IS NOT DISTINCT FROM EXCLUDED.pico_instance_id
              AND ingest.rollouts.sequence_no = EXCLUDED.sequence_no
              AND ingest.rollouts.robot_id = EXCLUDED.robot_id
              AND ingest.rollouts.source_sha256 = EXCLUDED.source_sha256
            """,
            (
                rollout.project_id,
                rollout.region_code,
                rollout.collection_job_id,
                rollout.rollout_id,
                rollout.collection_session_id,
                rollout.recording_request_id,
                rollout.data_package_id,
                rollout.pico_instance_id,
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
            session.data_package_id,
            session.object_key,
            session.source_type.value,
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

    @staticmethod
    def _upsert_manifest_preflight(
        cursor: Any,
        session_id: str,
        result: ManifestPreflightResultV1,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO ingest.manifest_discoveries (
                session_id, project_id, region_code, data_package_id,
                manifest_fingerprint, preflight_json, created_at
            )
            SELECT %s, s.project_id, s.region_code, s.data_package_id,
                   %s, %s::jsonb, now()
            FROM ingest.upload_sessions s WHERE s.session_id = %s
            ON CONFLICT (session_id) DO UPDATE SET
                preflight_json = EXCLUDED.preflight_json
            WHERE ingest.manifest_discoveries.manifest_fingerprint =
                  EXCLUDED.manifest_fingerprint
            """,
            (
                session_id,
                result.manifest_fingerprint,
                result.model_dump_json(),
                session_id,
            ),
        )
        PostgresIngestPersistence._require_write(cursor, "manifest discovery")

    def _write(self, operation: Callable[[Any], None]) -> None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                operation(cursor)
            connection.commit()
        except Exception as exc:
            connection.rollback()
            if _has_sqlstate(exc, "23505"):
                raise problem(
                    status=409,
                    code="IMMUTABLE_IDENTITY_CONFLICT",
                    title="Ingest identity conflict",
                    detail="The immutable ingest identity is already owned by another resource.",
                ) from exc
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
    def _trigger_model(cursor: Any, row: Sequence[object]) -> IngestWorkflowLocator:
        names = [str(item.name) for item in cursor.description]
        values = [str(value) if isinstance(value, UUID) else value for value in row]
        data = dict(zip(names, values, strict=True))
        data["status"] = IngestTriggerStatus(str(data["status"]))
        return IngestWorkflowLocator.model_validate(data)

    @staticmethod
    def _require_write(cursor: Any, resource: str) -> None:
        if cursor.rowcount != 1:
            raise problem(
                status=409,
                code="IMMUTABLE_IDENTITY_CONFLICT",
                title=f"{resource.title()} identity conflict",
                detail=f"The immutable identity fields of this {resource} cannot be changed.",
            )


def _has_sqlstate(error: BaseException, expected: str) -> bool:
    current: BaseException | None = error
    while current is not None:
        if getattr(current, "sqlstate", None) == expected:
            return True
        cause = current.__cause__
        current = cause if isinstance(cause, BaseException) else None
    return False
