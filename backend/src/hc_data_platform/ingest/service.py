from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import timedelta
from threading import RLock

from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.security.idempotency import IdempotencyStore, InMemoryIdempotencyStore

from .models import (
    CollectionJob,
    CollectionJobStatus,
    CompletedPart,
    PartAuthorization,
    RawObjectCommittedV1,
    Rollout,
    RolloutManifestV1,
    RolloutStatus,
    UploadObject,
    UploadObjectStatus,
    UploadPart,
    UploadPartStatus,
    UploadSession,
    UploadSessionGrant,
    UploadStatus,
    manifest_object_key,
    raw_object_key,
    utc_now,
)
from .persistence import IngestPersistencePort, InMemoryIngestPersistence
from .ports import ObjectStoragePort, crc64_ecma, normalize_etag


class UploadSessionService:
    def __init__(
        self,
        storage: ObjectStoragePort,
        persistence: IngestPersistencePort | None = None,
        idempotency: IdempotencyStore | None = None,
        *,
        authorization_ttl_seconds: int = 900,
    ) -> None:
        if not 1 <= authorization_ttl_seconds <= 3600:
            raise ValueError("part authorization TTL must be between 1 and 3600 seconds")
        self.storage = storage
        self.persistence = persistence or InMemoryIngestPersistence()
        persistence_idempotency = getattr(self.persistence, "idempotency_store", None)
        self.idempotency: IdempotencyStore = (
            idempotency or persistence_idempotency or InMemoryIdempotencyStore()
        )
        self.authorization_ttl_seconds = authorization_ttl_seconds
        self._lock = RLock()

    def create_session(
        self,
        *,
        manifest: RolloutManifestV1,
        region_code: str,
        idempotency_key: str,
    ) -> UploadSession:
        def create() -> UploadSession:
            with self._lock:
                existing_rollout = self.persistence.get_rollout(
                    manifest.project_id,
                    manifest.rollout_id,
                )
                if existing_rollout is not None:
                    if existing_rollout.source_sha256 != manifest.sha256:
                        raise _rollout_content_conflict()
                    if existing_rollout.region_code != region_code:
                        raise problem(
                            status=409,
                            code="ROLLOUT_SCOPE_CONFLICT",
                            title="Rollout scope conflict",
                            detail="This rollout id is registered in a different region.",
                        )
                    existing_session = self.persistence.find_session(
                        manifest.project_id,
                        manifest.rollout_id,
                    )
                    if existing_session is not None:
                        return existing_session

                key = raw_object_key(manifest)
                if self.storage.head(key) is not None:
                    raise problem(
                        status=409,
                        code="OBJECT_ALREADY_EXISTS",
                        title="Immutable object already exists",
                        detail="The raw key already exists without a matching persisted rollout.",
                    )
                upload_id = self.storage.create_multipart(key)
                session = UploadSession(
                    project_id=manifest.project_id,
                    region_code=region_code,
                    rollout_id=manifest.rollout_id,
                    object_key=key,
                    multipart_upload_id=upload_id,
                    expected_sha256=manifest.sha256,
                    expected_size=manifest.file_size,
                    expected_crc64=manifest.crc64,
                    manifest_fingerprint=_manifest_fingerprint(manifest),
                    status=UploadStatus.UPLOADING,
                )
                try:
                    persisted = self._persist_new_upload(session, manifest, region_code)
                except Exception:
                    self.storage.abort_multipart(key, upload_id)
                    raise
                if persisted.session_id != session.session_id:
                    self.storage.abort_multipart(key, upload_id)
                return persisted

        result = self.idempotency.execute(
            scope=manifest.project_id,
            key=idempotency_key,
            payload={"manifest": manifest.model_dump(mode="json"), "region_code": region_code},
            action=create,
        ).value
        return self.get_session(result.session_id)

    def create_upload(
        self,
        *,
        manifest: RolloutManifestV1,
        region_code: str,
        idempotency_key: str,
        part_numbers: Sequence[int] = (),
    ) -> UploadSessionGrant:
        session = self.create_session(
            manifest=manifest,
            region_code=region_code,
            idempotency_key=idempotency_key,
        )
        parts = (
            self.renew_part_authorizations(session.session_id, part_numbers) if part_numbers else []
        )
        return UploadSessionGrant(session=session, parts=parts)

    def renew_part_authorizations(
        self,
        session_id: str,
        part_numbers: Sequence[int],
    ) -> list[PartAuthorization]:
        session = self.get_session(session_id)
        self._require_status(session, UploadStatus.UPLOADING)
        numbers = list(part_numbers)
        if not numbers or numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
            raise problem(
                status=422,
                code="PART_NUMBERS_INVALID",
                title="Part numbers are invalid",
                detail="Part numbers must be a non-empty, unique, ascending list.",
            )
        expires_at = utc_now() + timedelta(seconds=self.authorization_ttl_seconds)
        authorizations: list[PartAuthorization] = []
        known = {part.part_number: part for part in self.persistence.list_parts(session_id)}
        for number in numbers:
            if not 1 <= number <= 10_000:
                raise problem(
                    status=422,
                    code="PART_NUMBER_INVALID",
                    title="Part number is invalid",
                    detail="Part numbers must be between 1 and 10000.",
                )
            url = self.storage.presign_part(
                session.object_key,
                session.multipart_upload_id,
                number,
                self.authorization_ttl_seconds,
            )
            authorizations.append(
                PartAuthorization(part_number=number, url=url, expires_at=expires_at)
            )
            existing = known.get(number)
            self.persistence.save_part(
                UploadPart(
                    session_id=session_id,
                    project_id=session.project_id,
                    region_code=session.region_code,
                    part_number=number,
                    status=(
                        UploadPartStatus.UPLOADED
                        if existing is not None and existing.status is UploadPartStatus.UPLOADED
                        else UploadPartStatus.AUTHORIZED
                    ),
                    etag=None if existing is None else existing.etag,
                    size=None if existing is None else existing.size,
                    crc64=None if existing is None else existing.crc64,
                    authorization_expires_at=expires_at,
                )
            )
        return authorizations

    def list_parts(self, session_id: str) -> list[UploadPart]:
        session = self.get_session(session_id)
        if session.status in {
            UploadStatus.MULTIPART_COMPLETED,
            UploadStatus.RAW_COMMITTED,
            UploadStatus.CANCELLED,
            UploadStatus.FAILED,
        }:
            return self.persistence.list_parts(session_id)
        known = {part.part_number: part for part in self.persistence.list_parts(session_id)}
        for uploaded in self.storage.list_parts(
            session.object_key,
            session.multipart_upload_id,
        ):
            previous = known.get(uploaded.part_number)
            self.persistence.save_part(
                UploadPart(
                    session_id=session_id,
                    project_id=session.project_id,
                    region_code=session.region_code,
                    part_number=uploaded.part_number,
                    status=UploadPartStatus.UPLOADED,
                    etag=normalize_etag(uploaded.etag),
                    size=uploaded.size,
                    crc64=uploaded.crc64,
                    authorization_expires_at=(
                        None if previous is None else previous.authorization_expires_at
                    ),
                )
            )
        return self.persistence.list_parts(session_id)

    def complete_upload(
        self,
        session_id: str,
        parts: Sequence[CompletedPart],
    ) -> UploadSession:
        session = self.get_session(session_id)
        self._require_status(session, UploadStatus.UPLOADING)
        numbers = [part.part_number for part in parts]
        if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
            raise problem(
                status=422,
                code="PARTS_NOT_SORTED",
                title="Multipart completion parts are not sorted",
                detail="Parts must be unique and sorted by ascending part_number.",
            )
        self.list_parts(session_id)
        metadata = self.storage.complete_multipart(
            session.object_key,
            session.multipart_upload_id,
            parts,
        )
        now = utc_now()
        updated = session.model_copy(
            update={
                "status": UploadStatus.MULTIPART_COMPLETED,
                "etag": metadata.etag,
                "updated_at": now,
                "completed_at": now,
            }
        )
        self.persistence.save_session(updated)
        upload_object = self._get_upload_object(session_id)
        self.persistence.save_upload_object(
            upload_object.model_copy(
                update={
                    "status": UploadObjectStatus.MULTIPART_COMPLETED,
                    "actual_size": metadata.size,
                    "actual_crc64": metadata.crc64,
                    "etag": metadata.etag,
                    "updated_at": now,
                }
            )
        )
        return updated

    def pause_upload(self, session_id: str) -> UploadSession:
        session = self.get_session(session_id)
        self._require_status(session, UploadStatus.UPLOADING)
        return self._set_session_status(session, UploadStatus.PAUSED)

    def resume_upload(
        self,
        session_id: str,
        part_numbers: Sequence[int] = (),
    ) -> UploadSessionGrant:
        session = self.get_session(session_id)
        self._require_status(session, UploadStatus.PAUSED)
        resumed = self._set_session_status(session, UploadStatus.UPLOADING)
        parts = self.renew_part_authorizations(session_id, part_numbers) if part_numbers else []
        return UploadSessionGrant(session=resumed, parts=parts)

    def cancel_upload(self, session_id: str) -> UploadSession:
        session = self.get_session(session_id)
        if session.status in {UploadStatus.RAW_COMMITTED, UploadStatus.CANCELLED}:
            if session.status is UploadStatus.CANCELLED:
                return session
            raise _invalid_state(session, "cancel")
        if session.status is not UploadStatus.MULTIPART_COMPLETED:
            self.storage.abort_multipart(session.object_key, session.multipart_upload_id)
        updated = self._set_session_status(session, UploadStatus.CANCELLED)
        upload_object = self._get_upload_object(session_id)
        self.persistence.save_upload_object(
            upload_object.model_copy(
                update={"status": UploadObjectStatus.CANCELLED, "updated_at": utc_now()}
            )
        )
        rollout = self.persistence.get_rollout(session.project_id, session.rollout_id)
        if rollout is not None:
            self.persistence.save_rollout(
                rollout.model_copy(
                    update={"status": RolloutStatus.CANCELLED, "updated_at": utc_now()}
                )
            )
        return updated

    def commit_manifest(
        self,
        *,
        session_id: str,
        manifest: RolloutManifestV1,
    ) -> RawObjectCommittedV1:
        session = self.get_session(session_id)
        if session.project_id != manifest.project_id or session.rollout_id != manifest.rollout_id:
            raise problem(
                status=409,
                code="MANIFEST_SESSION_MISMATCH",
                title="Manifest does not match upload session",
                detail="Project and rollout identities are immutable after session creation.",
            )
        existing = self.persistence.get_committed(manifest.project_id, manifest.rollout_id)
        if existing is not None:
            if existing.sha256 != manifest.sha256:
                raise _rollout_content_conflict()
            return existing
        if session.expected_sha256 != manifest.sha256:
            raise _rollout_content_conflict()
        if session.manifest_fingerprint != _manifest_fingerprint(manifest):
            raise _manifest_changed()
        if session.object_key != raw_object_key(manifest):
            raise _manifest_changed()
        if session.status not in {
            UploadStatus.MULTIPART_COMPLETED,
            UploadStatus.RAW_COMMITTED,
        }:
            raise problem(
                status=409,
                code="RAW_OBJECT_INCOMPLETE",
                title="Raw object is incomplete",
                detail="Complete the multipart object before committing its manifest.",
            )

        metadata = self.storage.head(session.object_key)
        if metadata is None:
            raise problem(
                status=409,
                code="RAW_OBJECT_INCOMPLETE",
                title="Raw object is incomplete",
                detail="Complete the multipart object before committing its manifest.",
            )
        if metadata.size != manifest.file_size:
            self._fail(session, "OBJECT_SIZE_MISMATCH")
            raise _integrity_problem(
                "OBJECT_SIZE_MISMATCH",
                "Object size mismatch",
                "The object size differs from the manifest.",
            )
        if metadata.crc64 is not None and metadata.crc64 != manifest.crc64:
            self._fail(session, "CRC64_MISMATCH")
            raise _integrity_problem(
                "CRC64_MISMATCH",
                "CRC64 mismatch",
                "The storage service CRC64 differs from the manifest.",
            )

        digest = hashlib.sha256()
        streamed_crc64 = 0
        streamed_size = 0
        for chunk in self.storage.read_chunks(session.object_key):
            streamed_size += len(chunk)
            digest.update(chunk)
            streamed_crc64 = crc64_ecma(chunk, streamed_crc64)
        if streamed_size != manifest.file_size:
            self._fail(session, "OBJECT_SIZE_MISMATCH")
            raise _integrity_problem(
                "OBJECT_SIZE_MISMATCH",
                "Object size mismatch",
                "The streamed object size differs from the manifest.",
            )
        if streamed_crc64 != manifest.crc64:
            self._fail(session, "CRC64_MISMATCH")
            raise _integrity_problem(
                "CRC64_MISMATCH",
                "CRC64 mismatch",
                "The server-streamed CRC64 differs from the manifest.",
            )
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != manifest.sha256:
            self._fail(session, "SHA256_MISMATCH")
            raise _integrity_problem(
                "SHA256_MISMATCH",
                "SHA-256 mismatch",
                "The server-computed content digest differs from the manifest.",
            )

        upload_object = self._get_upload_object(session_id)
        self.persistence.save_upload_object(
            upload_object.model_copy(
                update={
                    "status": UploadObjectStatus.VERIFIED,
                    "actual_size": streamed_size,
                    "actual_crc64": streamed_crc64,
                    "actual_sha256": actual_sha256,
                    "updated_at": utc_now(),
                }
            )
        )
        event = RawObjectCommittedV1(
            project_id=manifest.project_id,
            region_code=session.region_code,
            rollout_id=manifest.rollout_id,
            object_key=session.object_key,
            manifest_key=manifest_object_key(session.object_key),
            sha256=manifest.sha256,
            file_size=manifest.file_size,
        )
        self._write_or_reconcile_manifest(event.manifest_key, manifest)
        self._mark_committed(session, event)
        return event

    def get_session(self, session_id: str) -> UploadSession:
        session = self.persistence.get_session(session_id)
        if session is None:
            raise problem(
                status=404,
                code="UPLOAD_SESSION_NOT_FOUND",
                title="Upload session not found",
                detail="The upload session does not exist.",
            )
        return session

    def _persist_new_upload(
        self,
        session: UploadSession,
        manifest: RolloutManifestV1,
        region_code: str,
    ) -> UploadSession:
        job = CollectionJob(
            project_id=manifest.project_id,
            region_code=region_code,
            task_id=manifest.task_id,
            collection_job_id=manifest.collection_job_id,
            robot_id=manifest.robot_id,
            status=CollectionJobStatus.COLLECTING,
        )
        rollout = Rollout(
            project_id=manifest.project_id,
            region_code=region_code,
            collection_job_id=manifest.collection_job_id,
            rollout_id=manifest.rollout_id,
            sequence_no=manifest.sequence_no,
            robot_id=manifest.robot_id,
            source_sha256=manifest.sha256,
            status=RolloutStatus.UPLOADING,
        )
        upload_object = UploadObject(
            session_id=session.session_id,
            project_id=session.project_id,
            region_code=session.region_code,
            rollout_id=session.rollout_id,
            object_key=session.object_key,
            expected_size=session.expected_size,
            expected_sha256=session.expected_sha256,
            expected_crc64=session.expected_crc64,
        )
        return self.persistence.register_upload(job, rollout, session, upload_object)

    def _write_or_reconcile_manifest(
        self,
        key: str,
        manifest: RolloutManifestV1,
    ) -> None:
        if self.storage.head(key) is not None:
            self._assert_existing_manifest(key, manifest)
            return
        try:
            self.storage.put_json(key, manifest.model_dump(mode="json"), if_none_match=True)
        except ProblemException as exc:
            if exc.problem.code != "OBJECT_ALREADY_EXISTS":
                raise
            self._assert_existing_manifest(key, manifest)

    def _assert_existing_manifest(self, key: str, manifest: RolloutManifestV1) -> None:
        encoded = b"".join(self.storage.read_chunks(key, chunk_size=64 * 1024))
        try:
            existing = RolloutManifestV1.model_validate(json.loads(encoded))
        except (ValueError, TypeError) as exc:
            raise problem(
                status=409,
                code="MANIFEST_COMMIT_CONFLICT",
                title="Manifest commit marker conflict",
                detail="The existing commit marker is invalid or belongs to different content.",
            ) from exc
        if _manifest_fingerprint(existing) != _manifest_fingerprint(manifest):
            raise problem(
                status=409,
                code="MANIFEST_COMMIT_CONFLICT",
                title="Manifest commit marker conflict",
                detail="The existing commit marker belongs to different content.",
            )

    def _mark_committed(self, session: UploadSession, event: RawObjectCommittedV1) -> None:
        self.persistence.save_committed(event)
        self.persistence.save_session(
            session.model_copy(
                update={"status": UploadStatus.RAW_COMMITTED, "updated_at": utc_now()}
            )
        )
        upload_object = self._get_upload_object(session.session_id)
        self.persistence.save_upload_object(
            upload_object.model_copy(
                update={"status": UploadObjectStatus.COMMITTED, "updated_at": utc_now()}
            )
        )
        rollout = self.persistence.get_rollout(session.project_id, session.rollout_id)
        if rollout is not None:
            self.persistence.save_rollout(
                rollout.model_copy(
                    update={"status": RolloutStatus.RAW_COMMITTED, "updated_at": utc_now()}
                )
            )

    def _get_upload_object(self, session_id: str) -> UploadObject:
        upload_object = self.persistence.get_upload_object(session_id)
        if upload_object is None:
            raise RuntimeError(f"upload object missing for session {session_id}")
        return upload_object

    def _set_session_status(
        self,
        session: UploadSession,
        status: UploadStatus,
    ) -> UploadSession:
        updated = session.model_copy(update={"status": status, "updated_at": utc_now()})
        self.persistence.save_session(updated)
        return updated

    @staticmethod
    def _require_status(session: UploadSession, *allowed: UploadStatus) -> None:
        if session.status not in allowed:
            raise _invalid_state(session, "perform this operation")

    def _fail(self, session: UploadSession, code: str) -> None:
        now = utc_now()
        self.persistence.save_session(
            session.model_copy(
                update={"status": UploadStatus.FAILED, "failure_code": code, "updated_at": now}
            )
        )
        upload_object = self._get_upload_object(session.session_id)
        self.persistence.save_upload_object(
            upload_object.model_copy(
                update={"status": UploadObjectStatus.FAILED, "updated_at": now}
            )
        )
        rollout = self.persistence.get_rollout(session.project_id, session.rollout_id)
        if rollout is not None:
            self.persistence.save_rollout(
                rollout.model_copy(update={"status": RolloutStatus.FAILED, "updated_at": now})
            )


def _manifest_fingerprint(manifest: RolloutManifestV1) -> str:
    payload = json.dumps(
        manifest.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _manifest_changed() -> ProblemException:
    return problem(
        status=409,
        code="MANIFEST_CHANGED",
        title="Manifest changed after upload registration",
        detail="The complete manifest must match the value used to create the upload session.",
    )


def _rollout_content_conflict() -> ProblemException:
    return problem(
        status=409,
        code="ROLLOUT_CONTENT_CONFLICT",
        title="Rollout content conflict",
        detail="This rollout id was already registered with different content.",
    )


def _invalid_state(session: UploadSession, operation: str) -> ProblemException:
    return problem(
        status=409,
        code="UPLOAD_SESSION_STATE_CONFLICT",
        title="Upload session state conflict",
        detail=f"Cannot {operation} while the session is {session.status.value}.",
        details={"status": session.status.value},
    )


def _integrity_problem(code: str, title: str, detail: str) -> ProblemException:
    return problem(status=422, code=code, title=title, detail=detail)
