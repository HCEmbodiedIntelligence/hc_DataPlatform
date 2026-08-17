from __future__ import annotations

from threading import RLock
from typing import Protocol

from hc_data_platform.core.errors import problem

from .models import (
    CollectionJob,
    RawObjectCommittedV1,
    Rollout,
    UploadObject,
    UploadPart,
    UploadSession,
)


class IngestPersistencePort(Protocol):
    def register_upload(
        self,
        job: CollectionJob,
        rollout: Rollout,
        session: UploadSession,
        upload_object: UploadObject,
    ) -> UploadSession: ...

    def get_collection_job(
        self,
        project_id: str,
        collection_job_id: str,
    ) -> CollectionJob | None: ...

    def save_collection_job(self, job: CollectionJob) -> None: ...

    def get_rollout(self, project_id: str, rollout_id: str) -> Rollout | None: ...

    def save_rollout(self, rollout: Rollout) -> None: ...

    def get_session(self, session_id: str) -> UploadSession | None: ...

    def find_session(self, project_id: str, rollout_id: str) -> UploadSession | None: ...

    def save_session(self, session: UploadSession) -> None: ...

    def get_upload_object(self, session_id: str) -> UploadObject | None: ...

    def save_upload_object(self, upload_object: UploadObject) -> None: ...

    def list_parts(self, session_id: str) -> list[UploadPart]: ...

    def save_part(self, part: UploadPart) -> None: ...

    def get_committed(
        self,
        project_id: str,
        rollout_id: str,
    ) -> RawObjectCommittedV1 | None: ...

    def save_committed(self, event: RawObjectCommittedV1) -> None: ...


class InMemoryIngestPersistence:
    """Thread-safe fake mirroring the uniqueness constraints in the SQL migration."""

    def __init__(self) -> None:
        self._jobs: dict[tuple[str, str], CollectionJob] = {}
        self._rollouts: dict[tuple[str, str], Rollout] = {}
        self._rollout_by_sequence: dict[tuple[str, str, int], str] = {}
        self._sessions: dict[str, UploadSession] = {}
        self._session_by_rollout: dict[tuple[str, str], str] = {}
        self._objects: dict[str, UploadObject] = {}
        self._parts: dict[tuple[str, int], UploadPart] = {}
        self._committed: dict[tuple[str, str], RawObjectCommittedV1] = {}
        self._lock = RLock()

    def register_upload(
        self,
        job: CollectionJob,
        rollout: Rollout,
        session: UploadSession,
        upload_object: UploadObject,
    ) -> UploadSession:
        """Atomically register the graph or return the same rollout/SHA session."""

        with self._lock:
            existing_rollout = self._rollouts.get((rollout.project_id, rollout.rollout_id))
            if existing_rollout is not None:
                if existing_rollout.source_sha256 != rollout.source_sha256:
                    raise problem(
                        status=409,
                        code="ROLLOUT_CONTENT_CONFLICT",
                        title="Rollout content conflict",
                        detail="This rollout id is registered with a different SHA-256 digest.",
                    )
                existing_session = self.find_session(rollout.project_id, rollout.rollout_id)
                if existing_session is not None:
                    return existing_session
            self.save_collection_job(job)
            self.save_rollout(rollout)
            self.save_session(session)
            self.save_upload_object(upload_object)
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
            ):
                raise _identity_conflict("Rollout")
            sequence_owner = self._rollout_by_sequence.get(sequence_key)
            if sequence_owner is not None and sequence_owner != rollout.rollout_id:
                raise problem(
                    status=409,
                    code="ROLLOUT_SEQUENCE_CONFLICT",
                    title="Rollout sequence conflict",
                    detail="A collection job sequence number identifies exactly one rollout.",
                )
            self._rollouts[key] = rollout
            self._rollout_by_sequence[sequence_key] = rollout.rollout_id

    def get_session(self, session_id: str) -> UploadSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def find_session(self, project_id: str, rollout_id: str) -> UploadSession | None:
        with self._lock:
            session_id = self._session_by_rollout.get((project_id, rollout_id))
            return None if session_id is None else self._sessions[session_id]

    def save_session(self, session: UploadSession) -> None:
        rollout_key = (session.project_id, session.rollout_id)
        with self._lock:
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
            ):
                raise _identity_conflict("Upload session")
            self._sessions[session.session_id] = session
            self._session_by_rollout[rollout_key] = session.session_id

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


def _identity_conflict(resource: str) -> Exception:
    return problem(
        status=409,
        code="IMMUTABLE_IDENTITY_CONFLICT",
        title=f"{resource} identity conflict",
        detail=f"The immutable identity fields of this {resource.lower()} cannot be changed.",
    )
