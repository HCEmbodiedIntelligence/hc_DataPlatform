from __future__ import annotations

from datetime import datetime
from threading import RLock
from typing import Protocol

from hc_data_platform.core.errors import problem

from .models import (
    CollectionJob,
    IngestWorkflowLocator,
    ManifestPreflightResultV1,
    RawMediaAccessAuditEvent,
    RawObjectCommittedV1,
    Rollout,
    RolloutStatus,
    UploadObject,
    UploadObjectStatus,
    UploadPart,
    UploadSession,
    UploadStatus,
    utc_now,
)


class IngestPersistencePort(Protocol):
    def register_upload(
        self,
        job: CollectionJob,
        rollout: Rollout,
        session: UploadSession,
        upload_object: UploadObject,
        preflight: ManifestPreflightResultV1,
    ) -> UploadSession: ...

    def get_collection_job(
        self,
        project_id: str,
        collection_job_id: str,
    ) -> CollectionJob | None: ...

    def save_collection_job(self, job: CollectionJob) -> None: ...

    def get_rollout(self, project_id: str, rollout_id: str) -> Rollout | None: ...

    def get_rollout_by_package(self, project_id: str, data_package_id: str) -> Rollout | None: ...

    def get_rollout_by_source_fingerprint(
        self, project_id: str, source_fingerprint: str
    ) -> Rollout | None: ...

    def save_rollout(self, rollout: Rollout) -> None: ...

    def get_session(self, session_id: str) -> UploadSession | None: ...

    def find_session(self, project_id: str, rollout_id: str) -> UploadSession | None: ...

    def find_session_by_package(
        self, project_id: str, data_package_id: str
    ) -> UploadSession | None: ...

    def list_sessions(
        self,
        project_id: str,
        region_code: str,
        *,
        status: str | None = None,
        data_package_id: str | None = None,
        after: tuple[datetime, str] | None = None,
        limit: int = 50,
    ) -> list[UploadSession]: ...

    def save_session(self, session: UploadSession) -> None: ...

    def get_upload_object(self, session_id: str) -> UploadObject | None: ...

    def save_upload_object(self, upload_object: UploadObject) -> None: ...

    def list_parts(self, session_id: str) -> list[UploadPart]: ...

    def save_part(self, part: UploadPart) -> None: ...

    def save_manifest_preflight(
        self, session_id: str, result: ManifestPreflightResultV1
    ) -> None: ...

    def get_manifest_preflight(self, session_id: str) -> ManifestPreflightResultV1 | None: ...

    def get_committed(
        self,
        project_id: str,
        rollout_id: str,
    ) -> RawObjectCommittedV1 | None: ...

    def save_committed(self, event: RawObjectCommittedV1) -> None: ...

    def commit_raw_and_stage_workflow(
        self,
        *,
        session: UploadSession,
        event: RawObjectCommittedV1,
        workflow: IngestWorkflowLocator,
        actor_id: str,
        request_id: str,
    ) -> IngestWorkflowLocator: ...

    def commit_raw_without_workflow(
        self,
        *,
        session: UploadSession,
        event: RawObjectCommittedV1,
        actor_id: str,
        request_id: str,
    ) -> None: ...

    def record_raw_media_access(self, event: RawMediaAccessAuditEvent) -> None: ...

    def get_workflow_trigger(self, session_id: str) -> IngestWorkflowLocator | None: ...


class InMemoryIngestPersistence:
    """Thread-safe fake mirroring the uniqueness constraints in the SQL migration."""

    def __init__(self) -> None:
        self._jobs: dict[tuple[str, str], CollectionJob] = {}
        self._rollouts: dict[tuple[str, str], Rollout] = {}
        self._rollout_by_package: dict[tuple[str, str], str] = {}
        self._rollout_by_sequence: dict[tuple[str, str, int], str] = {}
        self._rollout_by_source_fingerprint: dict[tuple[str, str], str] = {}
        self._sessions: dict[str, UploadSession] = {}
        self._session_by_rollout: dict[tuple[str, str], str] = {}
        self._session_by_package: dict[tuple[str, str], str] = {}
        self._objects: dict[str, UploadObject] = {}
        self._parts: dict[tuple[str, int], UploadPart] = {}
        self._manifest_preflights: dict[str, ManifestPreflightResultV1] = {}
        self._committed: dict[tuple[str, str], RawObjectCommittedV1] = {}
        self._workflow_triggers: dict[str, IngestWorkflowLocator] = {}
        self.raw_media_audit_events: list[RawMediaAccessAuditEvent] = []
        self._lock = RLock()

    def register_upload(
        self,
        job: CollectionJob,
        rollout: Rollout,
        session: UploadSession,
        upload_object: UploadObject,
        preflight: ManifestPreflightResultV1,
    ) -> UploadSession:
        """Atomically register the graph or return the same rollout/SHA session."""

        with self._lock:
            existing_rollout = self.get_rollout_by_package(
                rollout.project_id, rollout.data_package_id
            )
            if existing_rollout is not None:
                if existing_rollout.source_sha256 != rollout.source_sha256:
                    raise problem(
                        status=409,
                        code="ROLLOUT_CONTENT_CONFLICT",
                        title="Rollout content conflict",
                        detail="This rollout id is registered with a different SHA-256 digest.",
                    )
                existing_session = self.find_session_by_package(
                    rollout.project_id, rollout.data_package_id
                )
                if existing_session is not None:
                    return existing_session
            if rollout.source_fingerprint is not None:
                existing_source = self.get_rollout_by_source_fingerprint(
                    rollout.project_id, rollout.source_fingerprint
                )
                if existing_source is not None and existing_source.rollout_id != rollout.rollout_id:
                    raise source_recording_duplicate(existing_source)
            self.save_collection_job(job)
            self.save_rollout(rollout)
            self.save_session(session)
            self.save_upload_object(upload_object)
            self.save_manifest_preflight(session.session_id, preflight)
            return session

    def get_collection_job(
        self,
        project_id: str,
        collection_job_id: str,
    ) -> CollectionJob | None:
        with self._lock:
            return self._jobs.get((project_id, collection_job_id))

    def save_collection_job(self, job: CollectionJob) -> None:
        key = (job.project_id, job.collection_job_id)
        with self._lock:
            existing = self._jobs.get(key)
            if existing is not None and (
                existing.region_code != job.region_code
                or existing.task_id != job.task_id
                or existing.robot_id != job.robot_id
            ):
                raise _identity_conflict("Collection job")
            self._jobs[key] = job

    def get_rollout(self, project_id: str, rollout_id: str) -> Rollout | None:
        with self._lock:
            return self._rollouts.get((project_id, rollout_id))

    def get_rollout_by_package(self, project_id: str, data_package_id: str) -> Rollout | None:
        with self._lock:
            rollout_id = self._rollout_by_package.get((project_id, data_package_id))
            return None if rollout_id is None else self._rollouts[(project_id, rollout_id)]

    def get_rollout_by_source_fingerprint(
        self, project_id: str, source_fingerprint: str
    ) -> Rollout | None:
        with self._lock:
            rollout_id = self._rollout_by_source_fingerprint.get((project_id, source_fingerprint))
            return None if rollout_id is None else self._rollouts[(project_id, rollout_id)]

    def save_rollout(self, rollout: Rollout) -> None:
        key = (rollout.project_id, rollout.rollout_id)
        sequence_key = (rollout.project_id, rollout.collection_job_id, rollout.sequence_no)
        with self._lock:
            existing = self._rollouts.get(key)
            if existing is not None and existing.source_sha256 != rollout.source_sha256:
                raise problem(
                    status=409,
                    code="ROLLOUT_CONTENT_CONFLICT",
                    title="Rollout content conflict",
                    detail="This rollout id is registered with a different SHA-256 digest.",
                )
            if existing is not None and (
                existing.region_code != rollout.region_code
                or existing.collection_job_id != rollout.collection_job_id
                or existing.sequence_no != rollout.sequence_no
                or existing.robot_id != rollout.robot_id
                or existing.collection_session_id != rollout.collection_session_id
                or existing.recording_request_id != rollout.recording_request_id
                or existing.data_package_id != rollout.data_package_id
                or existing.pico_instance_id != rollout.pico_instance_id
                or existing.source_fingerprint != rollout.source_fingerprint
            ):
                raise _identity_conflict("Rollout")
            package_key = (rollout.project_id, rollout.data_package_id)
            package_owner = self._rollout_by_package.get(package_key)
            if package_owner is not None and package_owner != rollout.rollout_id:
                raise problem(
                    status=409,
                    code="DATA_PACKAGE_ID_CONFLICT",
                    title="Data package identity conflict",
                    detail="This data_package_id already identifies a different rollout.",
                )
            sequence_owner = self._rollout_by_sequence.get(sequence_key)
            if sequence_owner is not None and sequence_owner != rollout.rollout_id:
                raise problem(
                    status=409,
                    code="ROLLOUT_SEQUENCE_CONFLICT",
                    title="Rollout sequence conflict",
                    detail="A collection job sequence number identifies exactly one rollout.",
                )
            if rollout.source_fingerprint is not None:
                source_key = (rollout.project_id, rollout.source_fingerprint)
                source_owner = self._rollout_by_source_fingerprint.get(source_key)
                if source_owner is not None and source_owner != rollout.rollout_id:
                    existing_source = self._rollouts[(rollout.project_id, source_owner)]
                    raise source_recording_duplicate(existing_source)
            self._rollouts[key] = rollout
            self._rollout_by_package[package_key] = rollout.rollout_id
            self._rollout_by_sequence[sequence_key] = rollout.rollout_id
            if rollout.source_fingerprint is not None:
                self._rollout_by_source_fingerprint[
                    (rollout.project_id, rollout.source_fingerprint)
                ] = rollout.rollout_id

    def get_session(self, session_id: str) -> UploadSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            return session.model_copy(update={"workflow": self._workflow_triggers.get(session_id)})

    def find_session(self, project_id: str, rollout_id: str) -> UploadSession | None:
        with self._lock:
            session_id = self._session_by_rollout.get((project_id, rollout_id))
            return None if session_id is None else self._sessions[session_id]

    def find_session_by_package(
        self, project_id: str, data_package_id: str
    ) -> UploadSession | None:
        with self._lock:
            session_id = self._session_by_package.get((project_id, data_package_id))
            return None if session_id is None else self._sessions[session_id]

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
        with self._lock:
            rows = [
                item
                for item in self._sessions.values()
                if item.project_id == project_id
                and item.region_code == region_code
                and (status is None or item.status.value == status)
                and (data_package_id is None or item.data_package_id == data_package_id)
                and (after is None or (item.updated_at, item.session_id) < after)
            ]
            return sorted(
                rows,
                key=lambda item: (item.updated_at, item.session_id),
                reverse=True,
            )[:limit]

    def save_session(self, session: UploadSession) -> None:
        session = session.model_copy(update={"workflow": None})
        rollout_key = (session.project_id, session.rollout_id)
        with self._lock:
            package_key = (session.project_id, session.data_package_id)
            existing_package_session = self._session_by_package.get(package_key)
            if (
                existing_package_session is not None
                and existing_package_session != session.session_id
            ):
                raise problem(
                    status=409,
                    code="UPLOAD_SESSION_ALREADY_EXISTS",
                    title="Upload session already exists",
                    detail="A data package has exactly one resumable upload session.",
                )
            existing_id = self._session_by_rollout.get(rollout_key)
            if existing_id is not None and existing_id != session.session_id:
                raise problem(
                    status=409,
                    code="UPLOAD_SESSION_ALREADY_EXISTS",
                    title="Upload session already exists",
                    detail="A rollout has exactly one resumable upload session.",
                )
            existing = self._sessions.get(session.session_id)
            if existing is not None and (
                existing.object_key != session.object_key
                or existing.multipart_upload_id != session.multipart_upload_id
                or existing.source_type != session.source_type
                or existing.data_package_id != session.data_package_id
            ):
                raise _identity_conflict("Upload session")
            self._sessions[session.session_id] = session
            self._session_by_rollout[rollout_key] = session.session_id
            self._session_by_package[package_key] = session.session_id

    def get_upload_object(self, session_id: str) -> UploadObject | None:
        with self._lock:
            return self._objects.get(session_id)

    def save_upload_object(self, upload_object: UploadObject) -> None:
        with self._lock:
            existing = self._objects.get(upload_object.session_id)
            if existing is not None and (
                existing.object_id != upload_object.object_id
                or existing.object_key != upload_object.object_key
            ):
                raise _identity_conflict("Upload object")
            self._objects[upload_object.session_id] = upload_object

    def list_parts(self, session_id: str) -> list[UploadPart]:
        with self._lock:
            return sorted(
                (
                    part
                    for (saved_session, _), part in self._parts.items()
                    if saved_session == session_id
                ),
                key=lambda part: part.part_number,
            )

    def save_part(self, part: UploadPart) -> None:
        with self._lock:
            existing = self._parts.get((part.session_id, part.part_number))
            if existing is not None and (
                existing.project_id != part.project_id or existing.region_code != part.region_code
            ):
                raise _identity_conflict("Upload part")
            self._parts[(part.session_id, part.part_number)] = part

    def save_manifest_preflight(self, session_id: str, result: ManifestPreflightResultV1) -> None:
        with self._lock:
            existing = self._manifest_preflights.get(session_id)
            if existing is not None and (
                existing.manifest_fingerprint != result.manifest_fingerprint
            ):
                raise _identity_conflict("Manifest discovery")
            self._manifest_preflights[session_id] = result

    def get_manifest_preflight(self, session_id: str) -> ManifestPreflightResultV1 | None:
        with self._lock:
            return self._manifest_preflights.get(session_id)

    def get_committed(
        self,
        project_id: str,
        rollout_id: str,
    ) -> RawObjectCommittedV1 | None:
        with self._lock:
            return self._committed.get((project_id, rollout_id))

    def save_committed(self, event: RawObjectCommittedV1) -> None:
        key = (event.project_id, event.rollout_id)
        with self._lock:
            existing = self._committed.get(key)
            if existing is not None and existing.sha256 != event.sha256:
                raise problem(
                    status=409,
                    code="ROLLOUT_CONTENT_CONFLICT",
                    title="Rollout content conflict",
                    detail="This rollout id was already committed with different content.",
                )
            if existing is not None and (
                existing.region_code != event.region_code
                or existing.object_key != event.object_key
                or existing.manifest_key != event.manifest_key
            ):
                raise _identity_conflict("Committed rollout object")
            self._committed[key] = event

    def commit_raw_and_stage_workflow(
        self,
        *,
        session: UploadSession,
        event: RawObjectCommittedV1,
        workflow: IngestWorkflowLocator,
        actor_id: str,
        request_id: str,
    ) -> IngestWorkflowLocator:
        del actor_id, request_id
        with self._lock:
            snapshots = (
                dict(self._committed),
                dict(self._sessions),
                dict(self._objects),
                dict(self._rollouts),
                dict(self._rollout_by_source_fingerprint),
                dict(self._workflow_triggers),
            )
            try:
                existing = self._workflow_triggers.get(session.session_id)
                if existing is not None and (
                    existing.event_id != workflow.event_id
                    or existing.workflow_id != workflow.workflow_id
                ):
                    raise _identity_conflict("Ingest workflow trigger")
                self.save_committed(event.model_copy(update={"workflow": None}))
                now = utc_now()
                self.save_session(
                    session.model_copy(
                        update={"status": UploadStatus.RAW_COMMITTED, "updated_at": now}
                    )
                )
                upload_object = self.get_upload_object(session.session_id)
                if upload_object is None:
                    raise RuntimeError("upload object is missing")
                self.save_upload_object(
                    upload_object.model_copy(
                        update={
                            "status": UploadObjectStatus.COMMITTED,
                            "actual_size": event.file_size,
                            "actual_sha256": event.sha256,
                            "updated_at": now,
                        }
                    )
                )
                rollout = self.get_rollout(session.project_id, session.rollout_id)
                if rollout is not None:
                    self.save_rollout(
                        rollout.model_copy(
                            update={"status": RolloutStatus.RAW_COMMITTED, "updated_at": now}
                        )
                    )
                if existing is None:
                    self._workflow_triggers[session.session_id] = workflow
                    return workflow
                return existing
            except Exception:
                (
                    self._committed,
                    self._sessions,
                    self._objects,
                    self._rollouts,
                    self._rollout_by_source_fingerprint,
                    self._workflow_triggers,
                ) = snapshots
                raise

    def commit_raw_without_workflow(
        self,
        *,
        session: UploadSession,
        event: RawObjectCommittedV1,
        actor_id: str,
        request_id: str,
    ) -> None:
        """Commit an immutable long recording without pretending it is one episode."""

        del actor_id, request_id
        with self._lock:
            snapshots = (
                dict(self._committed),
                dict(self._sessions),
                dict(self._objects),
                dict(self._rollouts),
                dict(self._rollout_by_source_fingerprint),
            )
            try:
                self.save_committed(event.model_copy(update={"workflow": None}))
                now = utc_now()
                self.save_session(
                    session.model_copy(
                        update={"status": UploadStatus.RAW_COMMITTED, "updated_at": now}
                    )
                )
                upload_object = self.get_upload_object(session.session_id)
                if upload_object is None:
                    raise RuntimeError("upload object is missing")
                self.save_upload_object(
                    upload_object.model_copy(
                        update={
                            "status": UploadObjectStatus.COMMITTED,
                            "actual_size": event.file_size,
                            "actual_sha256": event.sha256,
                            "updated_at": now,
                        }
                    )
                )
                rollout = self.get_rollout(session.project_id, session.rollout_id)
                if rollout is not None:
                    self.save_rollout(
                        rollout.model_copy(
                            update={"status": RolloutStatus.RAW_COMMITTED, "updated_at": now}
                        )
                    )
            except Exception:
                (
                    self._committed,
                    self._sessions,
                    self._objects,
                    self._rollouts,
                    self._rollout_by_source_fingerprint,
                ) = snapshots
                raise

    def record_raw_media_access(self, event: RawMediaAccessAuditEvent) -> None:
        with self._lock:
            self.raw_media_audit_events.append(event)

    def get_workflow_trigger(self, session_id: str) -> IngestWorkflowLocator | None:
        with self._lock:
            return self._workflow_triggers.get(session_id)


def _identity_conflict(resource: str) -> Exception:
    return problem(
        status=409,
        code="IMMUTABLE_IDENTITY_CONFLICT",
        title=f"{resource} identity conflict",
        detail=f"The immutable identity fields of this {resource.lower()} cannot be changed.",
    )


def source_recording_duplicate(existing: Rollout) -> Exception:
    return problem(
        status=409,
        code="SOURCE_RECORDING_DUPLICATE",
        title="Source recording already uploaded",
        detail=(
            "This source episode already exists as data package "
            f"{existing.data_package_id}. Select a different episode instead of "
            "uploading another converter version."
        ),
        details={
            "existing_data_package_id": existing.data_package_id,
            "existing_rollout_id": existing.rollout_id,
        },
    )
