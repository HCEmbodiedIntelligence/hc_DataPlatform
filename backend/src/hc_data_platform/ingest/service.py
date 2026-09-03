from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime, timedelta
from threading import RLock
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.core.pagination import CursorCodec
from hc_data_platform.security.idempotency import IdempotencyStore, InMemoryIdempotencyStore
from hc_data_platform.workflow.models import WorkflowKind, workflow_id

from .manifest import ObjectStorageManifestParser, preflight_manifest
from .models import (
    CollectionJob,
    CollectionJobStatus,
    CompletedPart,
    FailedPartV1,
    IdempotencyOutcome,
    IngestProcessingMode,
    IngestTriggerStatus,
    IngestWorkflowLocator,
    ManifestDiscoveryV1,
    ManifestPreflightResultV1,
    PartAuthorization,
    RawMediaAccessAuditEvent,
    RawMediaSourceV1,
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
    UploadSessionListV1,
    UploadSourceType,
    UploadStatus,
    manifest_object_key,
    raw_object_key,
    utc_now,
)
from .persistence import (
    IngestPersistencePort,
    InMemoryIngestPersistence,
    source_recording_duplicate,
)
from .ports import ObjectMetadata, ObjectStoragePort, crc64_ecma, normalize_etag
from .raw_sources import (
    CommittedRawSourceGraph,
    InMemoryRawSourceRepository,
    RawIngestJob,
    RawIngestJobType,
    RawSource,
    RawSourceEpisode,
    RawSourceFormat,
    RawSourceRepositoryPort,
)

_SESSION_CURSOR_VERSION = 1


class AlternateManifestDiscoveryPort(Protocol):
    """Read-only discovery for non-upload rollout sources such as soft Episodes."""

    def find(
        self, *, project_id: str, region_code: str, rollout_id: str
    ) -> ManifestDiscoveryV1 | None: ...


class UploadSessionService:
    def __init__(
        self,
        storage: ObjectStoragePort,
        persistence: IngestPersistencePort | None = None,
        idempotency: IdempotencyStore | None = None,
        *,
        raw_sources: RawSourceRepositoryPort | None = None,
        authorization_ttl_seconds: int = 900,
        raw_media_authorization_ttl_seconds: int = 900,
        max_authorizations_per_request: int = 256,
        max_part_retries: int = 5,
        cursor_secret: str = "ingest-session-local-cursor-secret",
        alternate_manifest_discovery: AlternateManifestDiscoveryPort | None = None,
    ) -> None:
        if not 1 <= authorization_ttl_seconds <= 3600:
            raise ValueError("part authorization TTL must be between 1 and 3600 seconds")
        if not 1 <= raw_media_authorization_ttl_seconds <= 3600:
            raise ValueError("raw media authorization TTL must be between 1 and 3600 seconds")
        if not 1 <= max_authorizations_per_request <= 1000:
            raise ValueError("authorization batch size must be between 1 and 1000")
        if not 1 <= max_part_retries <= 10:
            raise ValueError("part retry limit must be between 1 and 10")
        self.storage = storage
        self.persistence = persistence or InMemoryIngestPersistence()
        self.raw_sources = raw_sources or InMemoryRawSourceRepository()
        persistence_idempotency = getattr(self.persistence, "idempotency_store", None)
        self.idempotency: IdempotencyStore = (
            idempotency or persistence_idempotency or InMemoryIdempotencyStore()
        )
        self.authorization_ttl_seconds = authorization_ttl_seconds
        self.raw_media_authorization_ttl_seconds = raw_media_authorization_ttl_seconds
        self.max_authorizations_per_request = max_authorizations_per_request
        self.max_part_retries = max_part_retries
        self._cursor = CursorCodec(cursor_secret)
        self._alternate_manifest_discovery = alternate_manifest_discovery
        self._lock = RLock()

    def preflight_upload_manifest(self, manifest: RolloutManifestV1) -> ManifestPreflightResultV1:
        result = preflight_manifest(manifest)
        self._assert_source_recording_available(manifest, result)
        return result

    def create_session(
        self,
        *,
        manifest: RolloutManifestV1,
        region_code: str,
        idempotency_key: str,
    ) -> UploadSession:
        def create() -> UploadSession:
            with self._lock:
                preflight = self.preflight_upload_manifest(manifest)
                existing_rollout = self.persistence.get_rollout_by_package(
                    manifest.project_id,
                    manifest.data_package_id,
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
                    existing_session = self.persistence.find_session_by_package(
                        manifest.project_id,
                        manifest.data_package_id,
                    )
                    if existing_session is not None:
                        if existing_session.source_type is not UploadSourceType.BROWSER_MULTIPART:
                            raise problem(
                                status=409,
                                code="UPLOAD_SOURCE_CONFLICT",
                                title="Upload source conflict",
                                detail="This data package was registered from object storage.",
                            )
                        if existing_session.manifest_fingerprint != preflight.manifest_fingerprint:
                            raise _manifest_changed()
                        return existing_session
                existing_rollout_id = self.persistence.get_rollout(
                    manifest.project_id, manifest.rollout_id
                )
                if (
                    existing_rollout_id is not None
                    and existing_rollout_id.data_package_id != manifest.data_package_id
                ):
                    raise problem(
                        status=409,
                        code="ROLLOUT_ID_CONFLICT",
                        title="Rollout identity conflict",
                        detail="This rollout_id already identifies another data package.",
                    )

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
                    data_package_id=manifest.data_package_id,
                    object_key=key,
                    source_type=UploadSourceType.BROWSER_MULTIPART,
                    multipart_upload_id=upload_id,
                    expected_sha256=manifest.sha256,
                    expected_size=manifest.file_size,
                    expected_crc64=manifest.crc64,
                    manifest_fingerprint=preflight.manifest_fingerprint,
                    status=UploadStatus.UPLOADING,
                )
                try:
                    persisted = self._persist_new_upload(session, manifest, preflight, region_code)
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

    def register_object_storage_session(
        self,
        *,
        manifest: RolloutManifestV1,
        region_code: str,
        object_storage_uri: str,
        idempotency_key: str,
    ) -> UploadSession:
        """Register an existing canonical Raw object using server-held storage authority."""

        def register() -> UploadSession:
            with self._lock:
                preflight = self.preflight_upload_manifest(manifest)
                key = raw_object_key(manifest)
                metadata = self.storage.authorize_existing_object(object_storage_uri, key)
                if metadata.size != manifest.file_size:
                    raise _integrity_problem(
                        "OBJECT_SIZE_MISMATCH",
                        "Object size mismatch",
                        "The authorized object size differs from the manifest.",
                    )
                if metadata.crc64 is not None and metadata.crc64 != manifest.crc64:
                    raise _integrity_problem(
                        "CRC64_MISMATCH",
                        "CRC64 mismatch",
                        "The authorized object CRC64 differs from the manifest.",
                    )
                existing_rollout = self.persistence.get_rollout_by_package(
                    manifest.project_id,
                    manifest.data_package_id,
                )
                if existing_rollout is not None:
                    if existing_rollout.source_sha256 != manifest.sha256:
                        raise _rollout_content_conflict()
                    if existing_rollout.region_code != region_code:
                        raise problem(
                            status=409,
                            code="ROLLOUT_SCOPE_CONFLICT",
                            title="Rollout scope conflict",
                            detail="This data package is registered in a different region.",
                        )
                existing = self.persistence.find_session_by_package(
                    manifest.project_id, manifest.data_package_id
                )
                if existing is not None:
                    if existing.source_type is not UploadSourceType.OBJECT_STORAGE_REFERENCE:
                        raise problem(
                            status=409,
                            code="UPLOAD_SOURCE_CONFLICT",
                            title="Upload source conflict",
                            detail=(
                                "This data package was registered as a browser multipart upload."
                            ),
                        )
                    if (
                        existing.expected_sha256 != manifest.sha256
                        or existing.manifest_fingerprint != preflight.manifest_fingerprint
                    ):
                        raise _rollout_content_conflict()
                    return existing
                existing_rollout_id = self.persistence.get_rollout(
                    manifest.project_id, manifest.rollout_id
                )
                if (
                    existing_rollout_id is not None
                    and existing_rollout_id.data_package_id != manifest.data_package_id
                ):
                    raise problem(
                        status=409,
                        code="ROLLOUT_ID_CONFLICT",
                        title="Rollout identity conflict",
                        detail="This rollout_id already identifies another data package.",
                    )
                now = utc_now()
                session = UploadSession(
                    project_id=manifest.project_id,
                    region_code=region_code,
                    rollout_id=manifest.rollout_id,
                    data_package_id=manifest.data_package_id,
                    object_key=key,
                    source_type=UploadSourceType.OBJECT_STORAGE_REFERENCE,
                    multipart_upload_id=None,
                    expected_sha256=manifest.sha256,
                    expected_size=manifest.file_size,
                    expected_crc64=manifest.crc64,
                    manifest_fingerprint=preflight.manifest_fingerprint,
                    status=UploadStatus.MULTIPART_COMPLETED,
                    etag=metadata.etag,
                    completed_at=now,
                    updated_at=now,
                )
                return self._persist_new_upload(
                    session,
                    manifest,
                    preflight,
                    region_code,
                    object_metadata=metadata,
                )

        result = self.idempotency.execute(
            scope=manifest.project_id,
            key=idempotency_key,
            payload={
                "manifest": manifest.model_dump(mode="json"),
                "region_code": region_code,
                "object_storage_uri_fingerprint": hashlib.sha256(
                    object_storage_uri.encode("utf-8")
                ).hexdigest(),
            },
            action=register,
        ).value
        return self.get_session(result.session_id)

    def create_upload(
        self,
        *,
        manifest: RolloutManifestV1,
        region_code: str,
        idempotency_key: str,
        part_numbers: Sequence[int] = (),
        object_storage_uri: str | None = None,
    ) -> UploadSessionGrant:
        if object_storage_uri is not None and part_numbers:
            raise problem(
                status=422,
                code="UPLOAD_SOURCE_CONFLICT",
                title="Upload source conflict",
                detail="Part authorizations cannot be requested for an object storage reference.",
            )
        previous = self.persistence.find_session_by_package(
            manifest.project_id, manifest.data_package_id
        )
        session = (
            self.register_object_storage_session(
                manifest=manifest,
                region_code=region_code,
                object_storage_uri=object_storage_uri,
                idempotency_key=idempotency_key,
            )
            if object_storage_uri is not None
            else self.create_session(
                manifest=manifest,
                region_code=region_code,
                idempotency_key=idempotency_key,
            )
        )
        parts = (
            self.renew_part_authorizations(session.session_id, part_numbers) if part_numbers else []
        )
        if previous is None:
            outcome = IdempotencyOutcome.CREATED
        elif session.status is UploadStatus.RAW_COMMITTED:
            outcome = IdempotencyOutcome.ALREADY_COMMITTED
        else:
            outcome = IdempotencyOutcome.RESUMED
        return UploadSessionGrant(
            session=session,
            parts=parts,
            idempotency_outcome=outcome,
        )

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
        if len(numbers) > self.max_authorizations_per_request:
            raise problem(
                status=422,
                code="PART_AUTHORIZATION_BATCH_TOO_LARGE",
                title="Part authorization batch is too large",
                detail=(
                    f"Request at most {self.max_authorizations_per_request} part "
                    "authorizations at a time."
                ),
            )
        if session.multipart_upload_id is None:
            raise _invalid_state(session, "issue multipart authorizations")
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
                    retry_count=0 if existing is None else existing.retry_count,
                    failure_code=None if existing is None else existing.failure_code,
                    authorization_expires_at=expires_at,
                )
            )
        return authorizations

    def list_parts(self, session_id: str) -> list[UploadPart]:
        session = self.get_session(session_id)
        if session.multipart_upload_id is None:
            return self.persistence.list_parts(session_id)
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
                    retry_count=0 if previous is None else previous.retry_count,
                    failure_code=None,
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
        if session.status in {
            UploadStatus.MULTIPART_COMPLETED,
            UploadStatus.RAW_COMMITTED,
        }:
            return session
        self._require_status(session, UploadStatus.UPLOADING)
        if session.multipart_upload_id is None:
            raise _invalid_state(session, "complete multipart upload")
        numbers = [part.part_number for part in parts]
        if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
            raise problem(
                status=422,
                code="PARTS_NOT_SORTED",
                title="Multipart completion parts are not sorted",
                detail="Parts must be unique and sorted by ascending part_number.",
            )
        self.list_parts(session_id)
        metadata = self.storage.head(session.object_key)
        if metadata is None:
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
        return UploadSessionGrant(
            session=resumed,
            parts=parts,
            idempotency_outcome=IdempotencyOutcome.RESUMED,
        )

    def cancel_upload(self, session_id: str) -> UploadSession:
        session = self.get_session(session_id)
        if session.status in {UploadStatus.RAW_COMMITTED, UploadStatus.CANCELLED}:
            if session.status is UploadStatus.CANCELLED:
                return session
            raise _invalid_state(session, "cancel")
        if (
            session.status is not UploadStatus.MULTIPART_COMPLETED
            and session.multipart_upload_id is not None
        ):
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

    def retry_failed_parts(
        self,
        session_id: str,
        failures: Sequence[FailedPartV1],
    ) -> list[PartAuthorization]:
        session = self.get_session(session_id)
        self._require_status(session, UploadStatus.UPLOADING)
        if not failures or len(failures) > self.max_authorizations_per_request:
            raise problem(
                status=422,
                code="FAILED_PARTS_INVALID",
                title="Failed parts are invalid",
                detail=(
                    "Provide a non-empty failed-part list no larger than the authorization "
                    "batch limit."
                ),
            )
        numbers = [item.part_number for item in failures]
        if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
            raise problem(
                status=422,
                code="FAILED_PARTS_INVALID",
                title="Failed parts are invalid",
                detail="Failed parts must be unique and sorted by part_number.",
            )
        known = {part.part_number: part for part in self.persistence.list_parts(session_id)}
        now = utc_now()
        for failure in failures:
            previous = known.get(failure.part_number)
            retries = 1 if previous is None else previous.retry_count + 1
            if retries > self.max_part_retries:
                raise problem(
                    status=409,
                    code="PART_RETRY_LIMIT_EXCEEDED",
                    title="Part retry limit exceeded",
                    detail="This failed part has exhausted its retry budget.",
                    details={"part_number": failure.part_number},
                )
            self.persistence.save_part(
                UploadPart(
                    session_id=session_id,
                    project_id=session.project_id,
                    region_code=session.region_code,
                    part_number=failure.part_number,
                    status=UploadPartStatus.FAILED,
                    etag=None if previous is None else previous.etag,
                    size=None if previous is None else previous.size,
                    crc64=None if previous is None else previous.crc64,
                    retry_count=retries,
                    failure_code=failure.failure_code,
                    authorization_expires_at=None,
                    updated_at=now,
                )
            )
        return self.renew_part_authorizations(session_id, numbers)

    def commit_manifest(
        self,
        *,
        organization_id: str,
        session_id: str,
        manifest: RolloutManifestV1,
        actor_id: str = "system",
        request_id: str = "ingest-service",
    ) -> RawObjectCommittedV1:
        if not organization_id:
            raise ValueError("organization_id must not be empty")
        session = self.get_session(session_id)
        if (
            session.project_id != manifest.project_id
            or session.rollout_id != manifest.rollout_id
            or session.data_package_id != manifest.data_package_id
        ):
            raise problem(
                status=409,
                code="MANIFEST_SESSION_MISMATCH",
                title="Manifest does not match upload session",
                detail="Project and rollout identities are immutable after session creation.",
            )
        existing = self.persistence.get_committed(manifest.project_id, manifest.rollout_id)
        if existing is not None:
            if (
                existing.sha256 != manifest.sha256
                or existing.data_package_id != manifest.data_package_id
                or existing.object_key != session.object_key
            ):
                raise _rollout_content_conflict()
            if manifest.processing_mode is IngestProcessingMode.CONTINUOUS_RECORDING:
                source = manifest.source_recording
                recording_id = getattr(source, "recording_id", None)
                event = existing.model_copy(
                    update={
                        "processing_mode": manifest.processing_mode,
                        "continuous_recording_id": recording_id,
                        "workflow": None,
                    }
                )
                self.persistence.commit_raw_without_workflow(
                    session=session,
                    event=event,
                    actor_id=actor_id,
                    request_id=request_id,
                )
                self._register_raw_source(
                    organization_id=organization_id,
                    session=session,
                    manifest=manifest,
                    event=event,
                )
                return event
            workflow = self._stage_workflow_trigger(
                session=session,
                event=existing,
                actor_id=actor_id,
                request_id=request_id,
            )
            event = existing.model_copy(update={"workflow": workflow})
            self._register_raw_source(
                organization_id=organization_id,
                session=session,
                manifest=manifest,
                event=event,
            )
            return event
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

        event = RawObjectCommittedV1(
            project_id=manifest.project_id,
            region_code=session.region_code,
            rollout_id=manifest.rollout_id,
            data_package_id=manifest.data_package_id,
            object_key=session.object_key,
            manifest_key=manifest_object_key(session.object_key),
            sha256=manifest.sha256,
            file_size=manifest.file_size,
            processing_mode=manifest.processing_mode,
            continuous_recording_id=getattr(manifest.source_recording, "recording_id", None),
        )
        self._write_or_reconcile_manifest(event.manifest_key, manifest)
        if manifest.processing_mode is IngestProcessingMode.CONTINUOUS_RECORDING:
            self.persistence.commit_raw_without_workflow(
                session=session,
                event=event,
                actor_id=actor_id,
                request_id=request_id,
            )
            self._register_raw_source(
                organization_id=organization_id,
                session=session,
                manifest=manifest,
                event=event,
            )
            return event
        workflow = self._stage_workflow_trigger(
            session=session,
            event=event,
            actor_id=actor_id,
            request_id=request_id,
        )
        event = event.model_copy(update={"workflow": workflow})
        self._register_raw_source(
            organization_id=organization_id,
            session=session,
            manifest=manifest,
            event=event,
        )
        return event

    def _register_raw_source(
        self,
        *,
        organization_id: str,
        session: UploadSession,
        manifest: RolloutManifestV1,
        event: RawObjectCommittedV1,
    ) -> None:
        """Register MCAP/capture Raw using the same graph as native LeRobot."""

        raw_source_id = f"upload-{session.session_id.replace('-', '')}"
        is_continuous = manifest.processing_mode is IngestProcessingMode.CONTINUOUS_RECORDING
        source_format = RawSourceFormat.CAPTURE_BUNDLE if is_continuous else RawSourceFormat.MCAP
        job_type = (
            RawIngestJobType.CONTINUOUS_RECORDING_DISCOVERY
            if is_continuous
            else RawIngestJobType.DIRECT_EPISODE_INGEST
        )
        adapter_name = "capture_bundle" if is_continuous else "mcap"
        now = event.committed_at
        episodes = (
            ()
            if is_continuous
            else (
                RawSourceEpisode(
                    organization_id=organization_id,
                    project_id=session.project_id,
                    region_code=session.region_code,
                    raw_source_id=raw_source_id,
                    episode_id=session.rollout_id,
                    source_episode_index=0,
                    created_at=now,
                    updated_at=now,
                ),
            )
        )
        self.raw_sources.register_committed(
            CommittedRawSourceGraph(
                source=RawSource(
                    raw_source_id=raw_source_id,
                    organization_id=organization_id,
                    project_id=session.project_id,
                    region_code=session.region_code,
                    upload_id=session.session_id,
                    collection_task_id=manifest.task_id,
                    robot_id=manifest.robot_id,
                    source_format=source_format,
                    source_format_version="v1" if is_continuous else "1.0",
                    manifest_key=event.manifest_key,
                    storage_prefix=(
                        event.object_key.rsplit("/", 1)[0]
                        if "/" in event.object_key
                        else event.object_key
                    ),
                    content_hash=event.sha256,
                    file_count=1,
                    total_bytes=event.file_size,
                    created_at=session.created_at,
                    committed_at=now,
                    updated_at=now,
                ),
                episodes=episodes,
                job=RawIngestJob(
                    organization_id=organization_id,
                    project_id=session.project_id,
                    region_code=session.region_code,
                    job_id=f"raw-job-{session.session_id.replace('-', '')}",
                    raw_source_id=raw_source_id,
                    workflow_id=(None if event.workflow is None else event.workflow.workflow_id),
                    job_type=job_type,
                    adapter_name=adapter_name,
                    created_at=session.created_at,
                    updated_at=now,
                ),
            )
        )

    def _stage_workflow_trigger(
        self,
        *,
        session: UploadSession,
        event: RawObjectCommittedV1,
        actor_id: str,
        request_id: str,
    ) -> IngestWorkflowLocator:
        locator = workflow_id(
            WorkflowKind.INGEST_ROLLOUT,
            session.project_id,
            f"{session.region_code}/{session.rollout_id}",
        )
        trigger = IngestWorkflowLocator(
            event_id=str(uuid5(NAMESPACE_URL, f"hc-data-platform:{locator}")),
            workflow_id=locator,
            status=IngestTriggerStatus.PENDING,
        )
        return self.persistence.commit_raw_and_stage_workflow(
            session=session,
            event=event,
            workflow=trigger,
            actor_id=actor_id,
            request_id=request_id,
        )

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

    def get_manifest_preflight(self, session_id: str) -> ManifestPreflightResultV1:
        self.get_session(session_id)
        result = self.persistence.get_manifest_preflight(session_id)
        if result is None:
            raise problem(
                status=404,
                code="MANIFEST_PREFLIGHT_NOT_FOUND",
                title="Manifest preflight not found",
                detail="No persisted Manifest discovery exists for this upload session.",
            )
        return result

    def get_manifest_discovery_for_rollout(
        self,
        *,
        project_id: str,
        region_code: str,
        rollout_id: str,
    ) -> ManifestDiscoveryV1:
        """Expose persisted Manifest discovery without disclosing upload-session details."""

        session = self.persistence.find_session(project_id, rollout_id)
        if session is None:
            alternate = self._alternate_manifest_discovery
            discovery = (
                None
                if alternate is None
                else alternate.find(
                    project_id=project_id,
                    region_code=region_code,
                    rollout_id=rollout_id,
                )
            )
            if discovery is not None:
                return discovery
            raise problem(
                status=404,
                code="UPLOAD_SESSION_NOT_FOUND",
                title="Upload session not found",
                detail="No upload session exists for this rollout in the selected Region.",
            )
        if session.region_code != region_code:
            raise problem(
                status=404,
                code="UPLOAD_SESSION_NOT_FOUND",
                title="Upload session not found",
                detail="No upload session exists for this rollout in the selected Region.",
            )
        preflight = self.get_manifest_preflight(session.session_id)
        return preflight.discovery.model_copy(update={"robot_id": preflight.identifiers.robot_id})

    def authorize_raw_media(
        self,
        *,
        session_id: str,
        actor_id: str,
        request_id: str,
    ) -> RawMediaSourceV1:
        """Issue an audited, short-lived read handle only for a committed Raw capture."""

        session = self.get_session(session_id)
        committed = self.persistence.get_committed(session.project_id, session.rollout_id)
        if (
            session.status is not UploadStatus.RAW_COMMITTED
            or committed is None
            or committed.region_code != session.region_code
            or committed.object_key != session.object_key
            or committed.sha256 != session.expected_sha256
        ):
            raise problem(
                status=409,
                code="RAW_MEDIA_NOT_COMMITTED",
                title="Raw media is not committed",
                detail="The Raw capture must be committed before it can be inspected.",
            )
        metadata = self.storage.head(session.object_key)
        if metadata is None or metadata.size != committed.file_size:
            raise problem(
                status=409,
                code="RAW_MEDIA_NOT_AVAILABLE",
                title="Raw media is unavailable",
                detail="The committed Raw capture is not available from object storage.",
            )
        expires_at = utc_now() + timedelta(seconds=self.raw_media_authorization_ttl_seconds)
        download_url = self.storage.presign_read(
            session.object_key,
            self.raw_media_authorization_ttl_seconds,
        )
        self.persistence.record_raw_media_access(
            RawMediaAccessAuditEvent(
                project_id=session.project_id,
                region_code=session.region_code,
                actor_id=actor_id,
                request_id=request_id,
                session_id=session.session_id,
                rollout_id=session.rollout_id,
                byte_length=committed.file_size,
                occurred_at=utc_now(),
            )
        )
        preflight = self.get_manifest_preflight(session_id)
        continuous = preflight.manifest.processing_mode is IngestProcessingMode.CONTINUOUS_RECORDING
        primary_role = "CAPTURE_BUNDLE" if continuous else "RAW_MCAP"
        primary = next(item for item in preflight.files if item.role == primary_role)
        return RawMediaSourceV1(
            format="CAPTURE_BUNDLE" if continuous else "MCAP",
            media_type=primary.media_type if continuous else "application/x-mcap",
            download_url=download_url,
            expires_at=expires_at,
            byte_length=committed.file_size,
            sha256=committed.sha256,
        )

    def list_sessions(
        self,
        project_id: str,
        region_code: str,
        *,
        status: UploadStatus | None = None,
        data_package_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> UploadSessionListV1:
        after = self._decode_session_cursor(
            cursor,
            project_id=project_id,
            region_code=region_code,
            status=status,
            data_package_id=data_package_id,
        )
        rows = self.persistence.list_sessions(
            project_id,
            region_code,
            status=None if status is None else status.value,
            data_package_id=data_package_id,
            after=after,
            limit=limit + 1,
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = self._cursor.encode(
                {
                    "v": _SESSION_CURSOR_VERSION,
                    "project_id": project_id,
                    "region_code": region_code,
                    "status": None if status is None else status.value,
                    "data_package_id": data_package_id,
                    "updated_at": last.updated_at.isoformat(),
                    "session_id": last.session_id,
                }
            )
        return UploadSessionListV1(items=tuple(page), total=len(page), next_cursor=next_cursor)

    def _decode_session_cursor(
        self,
        cursor: str | None,
        *,
        project_id: str,
        region_code: str,
        status: UploadStatus | None,
        data_package_id: str | None,
    ) -> tuple[datetime, str] | None:
        if cursor is None:
            return None
        payload = self._cursor.decode(cursor)
        if (
            payload.get("v") != _SESSION_CURSOR_VERSION
            or payload.get("project_id") != project_id
            or payload.get("region_code") != region_code
            or payload.get("status") != (None if status is None else status.value)
            or payload.get("data_package_id") != data_package_id
        ):
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid pagination cursor",
                detail="The cursor does not match this upload-session query.",
            )
        updated_at = payload.get("updated_at")
        session_id = payload.get("session_id")
        if not isinstance(updated_at, str) or not isinstance(session_id, str):
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid pagination cursor",
                detail="The cursor does not contain an upload-session position.",
            )
        try:
            parsed_updated_at = datetime.fromisoformat(updated_at)
        except ValueError as exc:
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid pagination cursor",
                detail="The cursor does not contain a valid upload-session timestamp.",
            ) from exc
        if parsed_updated_at.tzinfo is None or not session_id:
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid pagination cursor",
                detail="The cursor does not contain a valid upload-session position.",
            )
        return parsed_updated_at, session_id

    def _persist_new_upload(
        self,
        session: UploadSession,
        manifest: RolloutManifestV1,
        preflight: ManifestPreflightResultV1,
        region_code: str,
        *,
        object_metadata: ObjectMetadata | None = None,
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
            collection_session_id=manifest.collection_session_id,
            recording_request_id=manifest.recording_request_id,
            data_package_id=manifest.data_package_id,
            pico_instance_id=manifest.pico_instance_id,
            sequence_no=manifest.sequence_no,
            robot_id=manifest.robot_id,
            source_sha256=manifest.sha256,
            source_fingerprint=preflight.source_fingerprint,
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
        if object_metadata is not None:
            upload_object = upload_object.model_copy(
                update={
                    "status": UploadObjectStatus.MULTIPART_COMPLETED,
                    "actual_size": object_metadata.size,
                    "actual_crc64": object_metadata.crc64,
                    "etag": object_metadata.etag,
                    "updated_at": utc_now(),
                }
            )
        return self.persistence.register_upload(job, rollout, session, upload_object, preflight)

    def _assert_source_recording_available(
        self,
        manifest: RolloutManifestV1,
        preflight: ManifestPreflightResultV1,
    ) -> None:
        if preflight.source_fingerprint is None:
            return
        existing = self.persistence.get_rollout_by_source_fingerprint(
            manifest.project_id, preflight.source_fingerprint
        )
        if existing is not None and existing.data_package_id != manifest.data_package_id:
            raise source_recording_duplicate(existing)

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
        try:
            existing = ObjectStorageManifestParser(self.storage).parse(key).manifest
        except (ProblemException, ValueError, TypeError) as exc:
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
    return preflight_manifest(manifest).manifest_fingerprint


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
