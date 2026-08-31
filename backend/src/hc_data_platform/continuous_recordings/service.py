from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import PurePosixPath
from typing import NoReturn
from uuid import UUID, uuid4, uuid5

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.core.errors import problem
from hc_data_platform.ingest.models import (
    ContinuousCaptureSourceV1,
    IngestProcessingMode,
    PartAuthorization,
    UploadStatus,
    utc_now,
)
from hc_data_platform.ingest.ports import ObjectStoragePort
from hc_data_platform.ingest.service import UploadSessionService
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.scope import ScopeGuard

from .asset_models import (
    AuthorizeRecordingAssetPartsCommand,
    CompleteRecordingAssetCommand,
    CreateRecordingUploadCommand,
    EpisodeProcessingPage,
    EpisodeVideoSource,
    EpisodeVideoSourceEnvelope,
    RecordingAsset,
    RecordingAssetPartGrant,
    RecordingAssetRole,
    RecordingAssetStatus,
    RecordingAssetSummary,
    RecordingAssetUploadGrant,
    RecordingUpload,
    RecordingUploadEnvelope,
    RecordingUploadGrant,
    RecordingUploadStatus,
    RecordingVideoSource,
    RecordingVideoSourceEnvelope,
)
from .asset_repository import (
    InMemoryRecordingAssetRepository,
    RecordingAssetRepository,
)
from .models import (
    ContinuousRecording,
    ContinuousRecordingEnvelope,
    ContinuousRecordingPage,
    EpisodeSlice,
    ModelSliceProposalMetadata,
    ProposeModelSlicesCommand,
    RecordingScope,
    RecordingSliceRevision,
    RecordingStatus,
    SaveSliceDraftCommand,
    SliceAuthoringMode,
    SliceRevisionEnvelope,
    SliceRevisionStatus,
    absolute_recording_time,
    recording_duration_ns,
)
from .repository import (
    ContinuousRecordingRepository,
    InMemoryContinuousRecordingRepository,
)


@dataclass(frozen=True, slots=True)
class MutationResult:
    envelope: ContinuousRecordingEnvelope | SliceRevisionEnvelope
    replayed: bool = False


class ContinuousRecordingService:
    def __init__(
        self,
        repository: ContinuousRecordingRepository,
        ingest: UploadSessionService,
        asset_repository: RecordingAssetRepository | None = None,
        storage: ObjectStoragePort | None = None,
        *,
        upload_authorization_ttl_seconds: int = 900,
        preview_authorization_ttl_seconds: int = 900,
    ) -> None:
        if not 1 <= upload_authorization_ttl_seconds <= 3600:
            raise ValueError("upload authorization TTL must be between 1 and 3600 seconds")
        if not 1 <= preview_authorization_ttl_seconds <= 3600:
            raise ValueError("preview authorization TTL must be between 1 and 3600 seconds")
        self._repository = repository
        self._ingest = ingest
        self._assets = asset_repository
        self._storage = storage or ingest.storage
        self._upload_ttl = upload_authorization_ttl_seconds
        self._preview_ttl = preview_authorization_ttl_seconds

    @classmethod
    def in_memory(cls, ingest: UploadSessionService) -> ContinuousRecordingService:
        return cls(
            InMemoryContinuousRecordingRepository(),
            ingest,
            InMemoryRecordingAssetRepository(),
            ingest.storage,
        )

    def begin_upload(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        command: CreateRecordingUploadCommand,
        actor_id: str,
    ) -> RecordingUploadGrant:
        scope = self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            write=True,
        )
        repository = self._require_asset_repository()
        manifest_sha256 = canonical_hash(command.model_dump(mode="json"))
        existing = repository.get_upload_by_recording(scope, command.recording_id)
        if existing is not None:
            if existing.manifest_sha256 != manifest_sha256:
                raise problem(
                    status=409,
                    code="RECORDING_UPLOAD_MANIFEST_CHANGED",
                    title="Recording upload manifest changed",
                    detail="A recording id cannot be reused for different immutable assets.",
                )
            return self._upload_grant(existing, repository.list_assets(scope, existing.upload_id))

        upload_id = uuid4()
        now = utc_now()
        upload = RecordingUpload(
            scope=scope,
            upload_id=upload_id,
            command=command,
            manifest_sha256=manifest_sha256,
            created_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        assets: list[RecordingAsset] = []
        try:
            for manifest in command.assets:
                asset_id = uuid5(upload_id, manifest.path)
                object_key = _recording_asset_key(
                    scope, command.recording_id, asset_id, manifest.path
                )
                if self._storage.head(object_key) is not None:
                    raise problem(
                        status=409,
                        code="RECORDING_ASSET_ALREADY_EXISTS",
                        title="Recording asset already exists",
                        detail="The immutable Raw object key is already occupied.",
                    )
                multipart_upload_id = self._storage.create_multipart(object_key)
                assets.append(
                    RecordingAsset(
                        scope=scope,
                        upload_id=upload_id,
                        asset_id=asset_id,
                        manifest=manifest,
                        object_key=object_key,
                        multipart_upload_id=multipart_upload_id,
                    )
                )
            persisted = repository.create_upload(upload, tuple(assets))
        except Exception:
            for asset in assets:
                self._storage.abort_multipart(asset.object_key, asset.multipart_upload_id)
            raise
        return self._upload_grant(persisted, tuple(assets))

    def get_upload(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        upload_id: UUID,
    ) -> RecordingUploadEnvelope:
        scope = self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            write=False,
        )
        repository = self._require_asset_repository()
        upload = repository.get_upload(scope, upload_id)
        if upload is None:
            self._upload_not_found()
        return RecordingUploadEnvelope(
            upload=upload,
            assets=tuple(
                _asset_summary(asset) for asset in repository.list_assets(scope, upload_id)
            ),
        )

    def authorize_asset_parts(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        upload_id: UUID,
        asset_id: UUID,
        command: AuthorizeRecordingAssetPartsCommand,
    ) -> RecordingAssetPartGrant:
        scope = self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            write=True,
        )
        asset = self._require_asset(scope, upload_id, asset_id)
        if asset.status is not RecordingAssetStatus.UPLOADING:
            raise problem(
                status=409,
                code="RECORDING_ASSET_NOT_UPLOADING",
                title="Recording asset is not uploading",
                detail="Part authorizations are issued only for active uploads.",
            )
        if max(command.part_numbers) > asset.manifest.part_count:
            raise problem(
                status=422,
                code="RECORDING_ASSET_PART_OUT_OF_RANGE",
                title="Recording asset part is out of range",
                detail="A requested part exceeds the declared part_count.",
            )
        return RecordingAssetPartGrant(
            asset_id=asset.asset_id,
            parts=self._part_authorizations(asset, command.part_numbers),
        )

    def complete_asset(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        upload_id: UUID,
        asset_id: UUID,
        command: CompleteRecordingAssetCommand,
    ) -> RecordingUploadEnvelope:
        scope = self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            write=True,
        )
        repository = self._require_asset_repository()
        upload = repository.get_upload(scope, upload_id)
        if upload is None:
            self._upload_not_found()
        asset = self._require_asset(scope, upload_id, asset_id)
        if asset.status is RecordingAssetStatus.COMMITTED:
            return self.get_upload(
                auth=auth,
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
                upload_id=upload_id,
            )
        expected_numbers = tuple(range(1, asset.manifest.part_count + 1))
        supplied_numbers = tuple(part.part_number for part in command.parts)
        if supplied_numbers != expected_numbers:
            raise problem(
                status=409,
                code="RECORDING_ASSET_PART_SET_INCOMPLETE",
                title="Recording asset part set is incomplete",
                detail="Complete the asset with every declared part in ascending order.",
            )
        metadata = self._storage.complete_multipart(
            asset.object_key,
            asset.multipart_upload_id,
            command.parts,
        )
        digest = hashlib.sha256()
        for chunk in self._storage.read_chunks(asset.object_key):
            digest.update(chunk)
        failure_code: str | None = None
        if metadata.size != asset.manifest.size:
            failure_code = "SIZE_MISMATCH"
        elif (
            asset.manifest.crc64 is not None
            and metadata.crc64 is not None
            and metadata.crc64 != asset.manifest.crc64
        ):
            failure_code = "CRC64_MISMATCH"
        elif digest.hexdigest() != asset.manifest.sha256:
            failure_code = "SHA256_MISMATCH"
        now = utc_now()
        if failure_code is not None:
            repository.save_asset(
                asset.model_copy(
                    update={
                        "status": RecordingAssetStatus.FAILED,
                        "object_etag": metadata.etag,
                        "failure_code": failure_code,
                    }
                )
            )
            repository.save_upload(
                upload.model_copy(
                    update={"status": RecordingUploadStatus.FAILED, "updated_at": now}
                )
            )
            raise problem(
                status=422,
                code=f"RECORDING_ASSET_{failure_code}",
                title="Recording asset integrity verification failed",
                detail="The completed OSS object does not match its immutable manifest.",
            )
        repository.save_asset(
            asset.model_copy(
                update={
                    "status": RecordingAssetStatus.COMMITTED,
                    "object_etag": metadata.etag,
                    "committed_at": now,
                }
            )
        )
        assets = repository.list_assets(scope, upload_id)
        if all(item.status is RecordingAssetStatus.COMMITTED for item in assets):
            upload = repository.save_upload(
                upload.model_copy(
                    update={"status": RecordingUploadStatus.READY_TO_COMMIT, "updated_at": now}
                )
            )
        return RecordingUploadEnvelope(
            upload=upload,
            assets=tuple(_asset_summary(item) for item in assets),
        )

    def commit_upload(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        upload_id: UUID,
        actor_id: str,
        request_id: str,
    ) -> ContinuousRecordingEnvelope:
        scope = self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            write=True,
        )
        asset_repository = self._require_asset_repository()
        upload = asset_repository.get_upload(scope, upload_id)
        if upload is None:
            self._upload_not_found()
        existing = self._repository.get(scope, upload.command.recording_id)
        if existing is not None:
            if existing.recording_upload_id != upload_id:
                raise problem(
                    status=409,
                    code="CONTINUOUS_RECORDING_IDENTITY_CONFLICT",
                    title="Continuous recording identity conflict",
                    detail="The recording id is already bound to another source upload.",
                )
            if upload.status is not RecordingUploadStatus.COMMITTED:
                asset_repository.save_upload(
                    upload.model_copy(
                        update={
                            "status": RecordingUploadStatus.COMMITTED,
                            "updated_at": utc_now(),
                        }
                    )
                )
            return self._envelope(existing)
        assets = asset_repository.list_assets(scope, upload_id)
        if upload.status is not RecordingUploadStatus.READY_TO_COMMIT or not all(
            asset.status is RecordingAssetStatus.COMMITTED for asset in assets
        ):
            raise problem(
                status=409,
                code="RECORDING_UPLOAD_ASSETS_NOT_COMMITTED",
                title="Recording upload is incomplete",
                detail="Every video, sensor, and configuration asset must pass verification.",
            )
        command = upload.command
        now = utc_now()
        recording = ContinuousRecording(
            schema_version="continuous-recording/v2",
            scope=scope,
            recording_id=command.recording_id,
            recording_upload_id=upload_id,
            rollout_id=command.rollout_id,
            data_package_id=command.data_package_id,
            collection_task_id=command.collection_task_id,
            collection_job_id=command.collection_job_id,
            robot_id=command.robot_id,
            device_id=command.device_id,
            capture_started_at=command.capture_started_at,
            capture_ended_at=command.capture_ended_at,
            duration_ns=recording_duration_ns(command.capture_started_at, command.capture_ended_at),
            source_sha256=canonical_hash(
                [
                    {"asset_id": str(asset.asset_id), "sha256": asset.manifest.sha256}
                    for asset in assets
                ]
            ),
            manifest_fingerprint=upload.manifest_sha256,
            video_asset_count=sum(
                asset.manifest.role is RecordingAssetRole.RAW_VIDEO for asset in assets
            ),
            etag='"v1"',
            created_at=now,
            updated_at=now,
        )
        persisted = self._repository.create(
            recording,
            actor_id=actor_id,
            request_id=request_id,
        )
        asset_repository.save_upload(
            upload.model_copy(update={"status": RecordingUploadStatus.COMMITTED, "updated_at": now})
        )
        return self._envelope(persisted)

    def register(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        upload_session_id: str,
        actor_id: str,
        request_id: str,
    ) -> ContinuousRecordingEnvelope:
        scope = self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            write=True,
        )
        existing = self._repository.get_by_upload_session(scope, upload_session_id)
        if existing is not None:
            return self._envelope(existing)
        session = self._ingest.get_session(upload_session_id)
        if session.project_id != project_id or session.region_code != region_code:
            self._not_found()
        if session.status is not UploadStatus.RAW_COMMITTED:
            raise problem(
                status=409,
                code="CONTINUOUS_RECORDING_UPLOAD_NOT_COMMITTED",
                title="Capture bundle is not committed",
                detail="Complete and commit the whole capture bundle before registering it.",
            )
        preflight = self._ingest.get_manifest_preflight(upload_session_id)
        manifest = preflight.manifest
        source = manifest.source_recording
        if (
            manifest.processing_mode is not IngestProcessingMode.CONTINUOUS_RECORDING
            or not isinstance(source, ContinuousCaptureSourceV1)
        ):
            raise problem(
                status=409,
                code="UPLOAD_IS_NOT_CONTINUOUS_RECORDING",
                title="Upload is not a continuous recording",
                detail=(
                    "The committed Manifest must use CONTINUOUS_RECORDING mode and a "
                    "CONTINUOUS_CAPTURE source."
                ),
            )
        now = utc_now()
        recording = ContinuousRecording(
            scope=scope,
            recording_id=source.recording_id,
            upload_session_id=session.session_id,
            rollout_id=manifest.rollout_id,
            data_package_id=manifest.data_package_id,
            collection_task_id=manifest.task_id,
            collection_job_id=manifest.collection_job_id,
            robot_id=manifest.robot_id,
            device_id=source.device_id,
            capture_started_at=manifest.start_time,
            capture_ended_at=manifest.end_time,
            duration_ns=recording_duration_ns(manifest.start_time, manifest.end_time),
            source_sha256=manifest.sha256,
            manifest_fingerprint=preflight.manifest_fingerprint,
            etag='"v1"',
            created_at=now,
            updated_at=now,
        )
        persisted = self._repository.create(
            recording,
            actor_id=actor_id,
            request_id=request_id,
        )
        return self._envelope(persisted)

    def list(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
    ) -> ContinuousRecordingPage:
        scope = self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            write=False,
        )
        items = self._repository.list(scope)
        return ContinuousRecordingPage(items=items, total=len(items))

    def get(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        recording_id: str,
    ) -> ContinuousRecordingEnvelope:
        scope = self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            write=False,
        )
        recording = self._require_recording(scope, recording_id)
        return self._envelope(recording)

    def save_draft(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        recording_id: str,
        command: SaveSliceDraftCommand,
        if_match: str,
        actor_id: str,
        request_id: str,
    ) -> SliceRevisionEnvelope:
        scope = self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            write=True,
        )
        recording = self._require_recording(scope, recording_id)
        if recording.status is RecordingStatus.SLICED:
            raise problem(
                status=409,
                code="CONTINUOUS_RECORDING_ALREADY_SLICED",
                title="Recording slices are finalized",
                detail="Finalized episode slices are immutable.",
            )
        self._require_etag(recording, if_match)
        revision_number = recording.current_revision + 1
        revision = self._build_revision(
            recording,
            revision_number=revision_number,
            status=SliceRevisionStatus.DRAFT,
            inputs=command,
            actor_id=actor_id,
        )
        updated = recording.model_copy(
            update={
                "current_revision": revision_number,
                "etag": f'"v{revision_number + 1}"',
                "updated_at": revision.created_at,
            }
        )
        persisted = self._repository.save_revision(
            recording=updated,
            revision=revision,
            expected_version=recording.current_revision,
            actor_id=actor_id,
            request_id=request_id,
        )
        return SliceRevisionEnvelope(recording=persisted, revision=revision)

    def propose_model_slices(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        recording_id: str,
        command: ProposeModelSlicesCommand,
        if_match: str,
        actor_id: str,
        request_id: str,
    ) -> SliceRevisionEnvelope:
        scope = self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            write=True,
        )
        recording = self._require_recording(scope, recording_id)
        if recording.status is RecordingStatus.SLICED:
            raise problem(
                status=409,
                code="CONTINUOUS_RECORDING_ALREADY_SLICED",
                title="Recording slices are finalized",
                detail="A model cannot replace finalized Episode windows.",
            )
        self._require_etag(recording, if_match)
        revision_number = recording.current_revision + 1
        revision = self._build_revision(
            recording,
            revision_number=revision_number,
            status=SliceRevisionStatus.DRAFT,
            inputs=SaveSliceDraftCommand(slices=command.slices),
            actor_id=actor_id,
            authoring_mode=SliceAuthoringMode.MODEL,
            model=command.model,
        )
        updated = recording.model_copy(
            update={
                "current_revision": revision_number,
                "etag": f'"v{revision_number + 1}"',
                "updated_at": revision.created_at,
            }
        )
        persisted = self._repository.save_revision(
            recording=updated,
            revision=revision,
            expected_version=recording.current_revision,
            actor_id=actor_id,
            request_id=request_id,
        )
        return SliceRevisionEnvelope(recording=persisted, revision=revision)

    def finalize(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        recording_id: str,
        expected_draft_revision: int,
        if_match: str,
        actor_id: str,
        request_id: str,
    ) -> SliceRevisionEnvelope:
        scope = self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            write=True,
        )
        recording = self._require_recording(scope, recording_id)
        if recording.status is RecordingStatus.SLICED:
            finalized = self._repository.get_revision(
                scope, recording.recording_id, recording.finalized_revision or 0
            )
            if finalized is None:
                raise RuntimeError("finalized recording revision is missing")
            return SliceRevisionEnvelope(recording=recording, revision=finalized)
        self._require_etag(recording, if_match)
        if expected_draft_revision != recording.current_revision:
            raise problem(
                status=409,
                code="SLICE_DRAFT_REVISION_MISMATCH",
                title="Slice draft revision mismatch",
                detail="Finalize the currently selected draft revision.",
            )
        draft = self._repository.get_revision(scope, recording_id, expected_draft_revision)
        if draft is None or draft.status is not SliceRevisionStatus.DRAFT:
            raise problem(
                status=409,
                code="SLICE_DRAFT_NOT_FOUND",
                title="Slice draft not found",
                detail="Save a slice draft before finalizing episodes.",
            )
        if not draft.slices:
            raise problem(
                status=422,
                code="SLICE_DRAFT_EMPTY",
                title="Slice draft is empty",
                detail="At least one episode interval is required before finalization.",
            )
        revision_number = recording.current_revision + 1
        finalized_slices = tuple(
            item.model_copy(update={"revision": revision_number}) for item in draft.slices
        )
        now = utc_now()
        revision = RecordingSliceRevision(
            scope=scope,
            recording_id=recording_id,
            revision=revision_number,
            status=SliceRevisionStatus.FINALIZED,
            slices=finalized_slices,
            authoring_mode=draft.authoring_mode,
            model=draft.model,
            created_by=actor_id,
            created_at=now,
        )
        updated = recording.model_copy(
            update={
                "status": RecordingStatus.SLICED,
                "current_revision": revision_number,
                "finalized_revision": revision_number,
                "etag": f'"v{revision_number + 1}"',
                "updated_at": now,
            }
        )
        persisted = self._repository.save_revision(
            recording=updated,
            revision=revision,
            expected_version=recording.current_revision,
            actor_id=actor_id,
            request_id=request_id,
        )
        return SliceRevisionEnvelope(recording=persisted, revision=revision)

    def list_episode_processing(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        recording_id: str,
    ) -> EpisodeProcessingPage:
        scope = self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            write=False,
        )
        self._require_recording(scope, recording_id)
        items = self._repository.list_episode_processing(scope, recording_id)
        return EpisodeProcessingPage(items=items, total=len(items))

    def authorize_episode_videos(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        recording_id: str,
        episode_id: str,
    ) -> EpisodeVideoSourceEnvelope:
        scope = self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            write=False,
        )
        recording = self._require_recording(scope, recording_id)
        if recording.recording_upload_id is None or recording.finalized_revision is None:
            raise problem(
                status=409,
                code="ORIGINAL_VIDEO_PREVIEW_UNAVAILABLE",
                title="Original video preview is unavailable",
                detail=(
                    "Direct time-window preview requires a finalized v2 multi-object recording."
                ),
            )
        revision = self._repository.get_revision(scope, recording_id, recording.finalized_revision)
        episode = (
            None
            if revision is None
            else next((item for item in revision.slices if item.episode_id == episode_id), None)
        )
        if episode is None:
            raise problem(
                status=404,
                code="RECORDING_EPISODE_NOT_FOUND",
                title="Recording Episode not found",
                detail="The finalized recording does not contain this Episode.",
            )
        asset_repository = self._require_asset_repository()
        upload = asset_repository.get_upload(scope, recording.recording_upload_id)
        if upload is None:
            self._upload_not_found()
        cameras = {camera.camera_id: camera for camera in upload.command.recording_config.cameras}
        expires_at = utc_now() + timedelta(seconds=self._preview_ttl)
        sources = tuple(
            EpisodeVideoSource(
                recording_id=recording_id,
                episode_id=episode.episode_id,
                asset_id=asset.asset_id,
                camera_id=asset.manifest.camera_id or "missing-camera",
                media_type=asset.manifest.media_type,
                source_url=self._storage.presign_read(asset.object_key, self._preview_ttl),
                start_offset_ns=episode.start_offset_ns,
                end_offset_ns=episode.end_offset_ns,
                fps=cameras[asset.manifest.camera_id or ""].fps,
                codec=cameras[asset.manifest.camera_id or ""].codec,
                expires_at=expires_at,
            )
            for asset in asset_repository.list_assets(scope, recording.recording_upload_id)
            if asset.manifest.role is RecordingAssetRole.RAW_VIDEO
            and asset.status is RecordingAssetStatus.COMMITTED
        )
        if not sources:
            raise problem(
                status=409,
                code="RECORDING_VIDEO_ASSETS_MISSING",
                title="Recording video assets are missing",
                detail="No committed original video can be authorized for this Episode.",
            )
        return EpisodeVideoSourceEnvelope(sources=sources)

    def authorize_recording_videos(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        recording_id: str,
    ) -> RecordingVideoSourceEnvelope:
        """Authorize original videos before Episodes exist; never materializes a preview."""
        scope = self._authorize(
            auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            write=False,
        )
        recording = self._require_recording(scope, recording_id)
        if recording.recording_upload_id is None:
            raise problem(
                status=409,
                code="ORIGINAL_VIDEO_PREVIEW_UNAVAILABLE",
                title="Original video preview is unavailable",
                detail="The slice editor requires a v2 multi-object recording.",
            )
        asset_repository = self._require_asset_repository()
        upload = asset_repository.get_upload(scope, recording.recording_upload_id)
        if upload is None:
            self._upload_not_found()
        cameras = {camera.camera_id: camera for camera in upload.command.recording_config.cameras}
        expires_at = utc_now() + timedelta(seconds=self._preview_ttl)
        sources = tuple(
            RecordingVideoSource(
                recording_id=recording_id,
                asset_id=asset.asset_id,
                camera_id=asset.manifest.camera_id or "missing-camera",
                media_type=asset.manifest.media_type,
                source_url=self._storage.presign_read(asset.object_key, self._preview_ttl),
                duration_ns=recording.duration_ns,
                fps=cameras[asset.manifest.camera_id or ""].fps,
                codec=cameras[asset.manifest.camera_id or ""].codec,
                expires_at=expires_at,
            )
            for asset in asset_repository.list_assets(scope, recording.recording_upload_id)
            if asset.manifest.role is RecordingAssetRole.RAW_VIDEO
            and asset.status is RecordingAssetStatus.COMMITTED
        )
        if not sources:
            raise problem(
                status=409,
                code="RECORDING_VIDEO_ASSETS_MISSING",
                title="Recording video assets are missing",
                detail="No committed original video can be authorized for this recording.",
            )
        return RecordingVideoSourceEnvelope(sources=sources)

    def _build_revision(
        self,
        recording: ContinuousRecording,
        *,
        revision_number: int,
        status: SliceRevisionStatus,
        inputs: SaveSliceDraftCommand,
        actor_id: str,
        authoring_mode: SliceAuthoringMode = SliceAuthoringMode.HUMAN,
        model: ModelSliceProposalMetadata | None = None,
    ) -> RecordingSliceRevision:
        ordered = sorted(
            inputs.slices,
            key=lambda item: (item.start_offset_ns, item.end_offset_ns, item.episode_id),
        )
        if ordered and ordered[-1].end_offset_ns > recording.duration_ns:
            raise problem(
                status=422,
                code="EPISODE_SLICE_OUT_OF_RANGE",
                title="Episode slice is outside the recording",
                detail="Every episode interval must be contained in the uploaded capture range.",
            )
        slices = tuple(
            EpisodeSlice(
                scope=recording.scope,
                recording_id=recording.recording_id,
                revision=revision_number,
                ordinal=ordinal,
                episode_id=item.episode_id,
                start_offset_ns=item.start_offset_ns,
                end_offset_ns=item.end_offset_ns,
                started_at=absolute_recording_time(
                    recording.capture_started_at, item.start_offset_ns
                ),
                ended_at=absolute_recording_time(recording.capture_started_at, item.end_offset_ns),
                title=item.title,
                task_label=item.task_label,
                notes=item.notes,
                source_sha256=recording.source_sha256,
                source_upload_session_id=recording.upload_session_id,
                source_recording_upload_id=recording.recording_upload_id,
            )
            for ordinal, item in enumerate(ordered)
        )
        return RecordingSliceRevision(
            scope=recording.scope,
            recording_id=recording.recording_id,
            revision=revision_number,
            status=status,
            slices=slices,
            authoring_mode=authoring_mode,
            model=model,
            created_by=actor_id,
        )

    def _envelope(self, recording: ContinuousRecording) -> ContinuousRecordingEnvelope:
        revision = (
            None
            if recording.current_revision == 0
            else self._repository.get_revision(
                recording.scope, recording.recording_id, recording.current_revision
            )
        )
        return ContinuousRecordingEnvelope(
            data=recording,
            current_slice_revision=revision,
        )

    def _require_recording(self, scope: RecordingScope, recording_id: str) -> ContinuousRecording:
        recording = self._repository.get(scope, recording_id)
        if recording is None:
            self._not_found()
        return recording

    def _require_asset(
        self, scope: RecordingScope, upload_id: UUID, asset_id: UUID
    ) -> RecordingAsset:
        asset = self._require_asset_repository().get_asset(scope, upload_id, asset_id)
        if asset is None:
            self._upload_not_found()
        return asset

    def _require_asset_repository(self) -> RecordingAssetRepository:
        if self._assets is None:
            raise problem(
                status=503,
                code="RECORDING_VIDEO_UPLOAD_NOT_CONFIGURED",
                title="Recording video upload is not configured",
                detail="The multi-object recording repository is unavailable.",
            )
        return self._assets

    def _upload_grant(
        self, upload: RecordingUpload, assets: tuple[RecordingAsset, ...]
    ) -> RecordingUploadGrant:
        grants = tuple(
            RecordingAssetUploadGrant(
                asset=_asset_summary(asset),
                multipart_upload_id=asset.multipart_upload_id,
                parts=(
                    self._part_authorizations(
                        asset,
                        range(1, min(asset.manifest.part_count, 256) + 1),
                    )
                    if asset.status is RecordingAssetStatus.UPLOADING
                    else ()
                ),
            )
            for asset in assets
        )
        return RecordingUploadGrant(upload=upload, assets=grants)

    def _part_authorizations(
        self, asset: RecordingAsset, part_numbers: Iterable[int]
    ) -> tuple[PartAuthorization, ...]:
        expires_at = utc_now() + timedelta(seconds=self._upload_ttl)
        return tuple(
            PartAuthorization(
                part_number=number,
                url=self._storage.presign_part(
                    asset.object_key,
                    asset.multipart_upload_id,
                    number,
                    self._upload_ttl,
                ),
                expires_at=expires_at,
            )
            for number in part_numbers
        )

    @staticmethod
    def _require_etag(recording: ContinuousRecording, if_match: str) -> None:
        if if_match != recording.etag:
            raise problem(
                status=412,
                code="CONTINUOUS_RECORDING_VERSION_MISMATCH",
                title="Continuous recording changed",
                detail="Reload the recording before editing its slice draft.",
                details={"expected": recording.etag},
            )

    @staticmethod
    def _authorize(
        auth: AuthContext,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        write: bool,
    ) -> RecordingScope:
        ScopeGuard.require(auth, project_id, region_code, organization_id)
        select_request_scope(project_id, region_code, organization_id=organization_id)
        capabilities = auth.effective_capabilities(project_id, organization_id)
        accepted = (
            {"upload.manage", "ingest.upload", "annotation.write", "annotation.edit"}
            if write
            else {"upload.read", "episode.read", "datasets.read"}
        )
        if not auth.is_platform_admin and not capabilities.intersection(accepted):
            raise problem(
                status=403,
                code="CAPABILITY_REQUIRED",
                title="Insufficient capability",
                detail="The current project scope cannot access continuous recordings.",
            )
        return RecordingScope(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )

    @staticmethod
    def _not_found() -> NoReturn:
        raise problem(
            status=404,
            code="CONTINUOUS_RECORDING_NOT_FOUND",
            title="Continuous recording not found",
            detail="The continuous recording does not exist in this scope.",
        )

    @staticmethod
    def _upload_not_found() -> NoReturn:
        raise problem(
            status=404,
            code="RECORDING_UPLOAD_NOT_FOUND",
            title="Recording upload not found",
            detail="The recording upload or asset does not exist in this scope.",
        )


def _asset_summary(asset: RecordingAsset) -> RecordingAssetSummary:
    return RecordingAssetSummary(
        asset_id=asset.asset_id,
        path=asset.manifest.path,
        role=asset.manifest.role,
        camera_id=asset.manifest.camera_id,
        media_type=asset.manifest.media_type,
        size=asset.manifest.size,
        sha256=asset.manifest.sha256,
        status=asset.status,
    )


def _recording_asset_key(
    scope: RecordingScope,
    recording_id: str,
    asset_id: UUID,
    path: str,
) -> str:
    suffix = "".join(PurePosixPath(path).suffixes)[-32:]
    return (
        f"raw/v2/org={scope.organization_id}/project={scope.project_id}/"
        f"region={scope.region_code}/recording={recording_id}/"
        f"asset={asset_id}{suffix}"
    )
