from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response

from hc_data_platform.ingest.router import get_service as get_ingest_service
from hc_data_platform.security.http import VerifiedAuth

from .asset_models import (
    AdvanceEpisodeProcessingCommand,
    AuthorizeRecordingAssetPartsCommand,
    CompleteRecordingAssetCommand,
    CreateRecordingUploadCommand,
    EpisodeProcessingEnvelope,
    EpisodeProcessingPage,
    EpisodeVideoSourceEnvelope,
    RecordingAssetPartGrant,
    RecordingUploadEnvelope,
    RecordingUploadGrant,
    RecordingVideoSourceEnvelope,
)
from .asset_repository import InMemoryRecordingAssetRepository
from .models import (
    ContinuousRecordingEnvelope,
    ContinuousRecordingPage,
    FinalizeSliceCommand,
    ProposeModelSlicesCommand,
    RegisterContinuousRecordingCommand,
    SaveSliceDraftCommand,
    SliceRevisionEnvelope,
)
from .repository import InMemoryContinuousRecordingRepository
from .service import ContinuousRecordingService

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/regions/{region_code}/continuous-recordings",
    tags=["continuous-recordings"],
)
_service: ContinuousRecordingService | None = None
_default_repository = InMemoryContinuousRecordingRepository()
_default_asset_repository = InMemoryRecordingAssetRepository()

PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {
        "description": "The continuous-recording request could not be completed.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    }
    for status in (400, 401, 403, 404, 409, 412, 422, 500)
}


def configure_continuous_recordings(service: ContinuousRecordingService | None) -> None:
    global _service
    _service = service


def get_continuous_recording_service() -> ContinuousRecordingService:
    if _service is not None:
        return _service
    ingest = get_ingest_service()
    return ContinuousRecordingService(
        _default_repository,
        ingest,
        _default_asset_repository,
        ingest.storage,
    )


Service = Annotated[ContinuousRecordingService, Depends(get_continuous_recording_service)]
OrganizationHeader = Annotated[str, Header(alias="X-Organization-Id", min_length=1, max_length=128)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=4, max_length=64)]


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) and value else "request-id-unavailable"


def _recording_headers(response: Response, *, etag: str) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["ETag"] = etag


@router.post(
    "/uploads",
    operation_id="beginContinuousRecordingUpload",
    response_model=RecordingUploadGrant,
    status_code=201,
    responses=PROBLEM_RESPONSES,
)
def begin_continuous_recording_upload(
    project_id: str,
    region_code: str,
    command: CreateRecordingUploadCommand,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> RecordingUploadGrant:
    response.headers["Cache-Control"] = "no-store"
    return service.begin_upload(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        command=command,
        actor_id=auth.subject_id,
    )


@router.get(
    "/uploads/{upload_id}",
    operation_id="getContinuousRecordingUpload",
    response_model=RecordingUploadEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_continuous_recording_upload(
    project_id: str,
    region_code: str,
    upload_id: UUID,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> RecordingUploadEnvelope:
    response.headers["Cache-Control"] = "no-store"
    return service.get_upload(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        upload_id=upload_id,
    )


@router.post(
    "/uploads/{upload_id}/assets/{asset_id}:authorize-parts",
    operation_id="authorizeContinuousRecordingAssetParts",
    response_model=RecordingAssetPartGrant,
    responses=PROBLEM_RESPONSES,
)
def authorize_continuous_recording_asset_parts(
    project_id: str,
    region_code: str,
    upload_id: UUID,
    asset_id: UUID,
    command: AuthorizeRecordingAssetPartsCommand,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> RecordingAssetPartGrant:
    response.headers["Cache-Control"] = "no-store"
    return service.authorize_asset_parts(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        upload_id=upload_id,
        asset_id=asset_id,
        command=command,
    )


@router.post(
    "/uploads/{upload_id}/assets/{asset_id}:complete",
    operation_id="completeContinuousRecordingAsset",
    response_model=RecordingUploadEnvelope,
    responses=PROBLEM_RESPONSES,
)
def complete_continuous_recording_asset(
    project_id: str,
    region_code: str,
    upload_id: UUID,
    asset_id: UUID,
    command: CompleteRecordingAssetCommand,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> RecordingUploadEnvelope:
    response.headers["Cache-Control"] = "no-store"
    return service.complete_asset(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        upload_id=upload_id,
        asset_id=asset_id,
        command=command,
    )


@router.post(
    "/uploads/{upload_id}:commit",
    operation_id="commitContinuousRecordingUpload",
    response_model=ContinuousRecordingEnvelope,
    responses=PROBLEM_RESPONSES,
)
def commit_continuous_recording_upload(
    project_id: str,
    region_code: str,
    upload_id: UUID,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> ContinuousRecordingEnvelope:
    result = service.commit_upload(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        upload_id=upload_id,
        actor_id=auth.subject_id,
        request_id=_request_id(request),
    )
    _recording_headers(response, etag=result.data.etag)
    return result


@router.post(
    "",
    operation_id="registerContinuousRecording",
    response_model=ContinuousRecordingEnvelope,
    status_code=201,
    responses=PROBLEM_RESPONSES,
)
def register_continuous_recording(
    project_id: str,
    region_code: str,
    command: RegisterContinuousRecordingCommand,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> ContinuousRecordingEnvelope:
    result = service.register(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        upload_session_id=str(command.upload_session_id),
        actor_id=auth.subject_id,
        request_id=_request_id(request),
    )
    _recording_headers(response, etag=result.data.etag)
    return result


@router.get(
    "",
    operation_id="listContinuousRecordings",
    response_model=ContinuousRecordingPage,
    responses=PROBLEM_RESPONSES,
)
def list_continuous_recordings(
    project_id: str,
    region_code: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> ContinuousRecordingPage:
    response.headers["Cache-Control"] = "no-store"
    return service.list(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
    )


@router.get(
    "/{recording_id}",
    operation_id="getContinuousRecording",
    response_model=ContinuousRecordingEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_continuous_recording(
    project_id: str,
    region_code: str,
    recording_id: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> ContinuousRecordingEnvelope:
    result = service.get(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        recording_id=recording_id,
    )
    _recording_headers(response, etag=result.data.etag)
    return result


@router.put(
    "/{recording_id}/slice-draft",
    operation_id="saveContinuousRecordingSliceDraft",
    response_model=SliceRevisionEnvelope,
    responses=PROBLEM_RESPONSES,
)
def save_continuous_recording_slice_draft(
    project_id: str,
    region_code: str,
    recording_id: str,
    command: SaveSliceDraftCommand,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    if_match: IfMatch,
    service: Service,
) -> SliceRevisionEnvelope:
    result = service.save_draft(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        recording_id=recording_id,
        command=command,
        if_match=if_match,
        actor_id=auth.subject_id,
        request_id=_request_id(request),
    )
    _recording_headers(response, etag=result.recording.etag)
    return result


@router.post(
    "/{recording_id}/slice-proposals",
    operation_id="proposeContinuousRecordingSlices",
    response_model=SliceRevisionEnvelope,
    responses=PROBLEM_RESPONSES,
)
def propose_continuous_recording_slices(
    project_id: str,
    region_code: str,
    recording_id: str,
    command: ProposeModelSlicesCommand,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    if_match: IfMatch,
    service: Service,
) -> SliceRevisionEnvelope:
    result = service.propose_model_slices(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        recording_id=recording_id,
        command=command,
        if_match=if_match,
        actor_id=auth.subject_id,
        request_id=_request_id(request),
    )
    _recording_headers(response, etag=result.recording.etag)
    return result


@router.post(
    "/{recording_id}/slice-draft:finalize",
    operation_id="finalizeContinuousRecordingSlices",
    response_model=SliceRevisionEnvelope,
    responses=PROBLEM_RESPONSES,
)
def finalize_continuous_recording_slices(
    project_id: str,
    region_code: str,
    recording_id: str,
    command: FinalizeSliceCommand,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    if_match: IfMatch,
    service: Service,
) -> SliceRevisionEnvelope:
    result = service.finalize(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        recording_id=recording_id,
        expected_draft_revision=command.expected_draft_revision,
        if_match=if_match,
        actor_id=auth.subject_id,
        request_id=_request_id(request),
    )
    _recording_headers(response, etag=result.recording.etag)
    return result


@router.get(
    "/{recording_id}/episodes",
    operation_id="listContinuousRecordingEpisodes",
    response_model=EpisodeProcessingPage,
    responses=PROBLEM_RESPONSES,
)
def list_continuous_recording_episodes(
    project_id: str,
    region_code: str,
    recording_id: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> EpisodeProcessingPage:
    response.headers["Cache-Control"] = "no-store"
    return service.list_episode_processing(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        recording_id=recording_id,
    )


@router.post(
    "/{recording_id}/episodes/{episode_id}:transition-processing",
    operation_id="transitionContinuousRecordingEpisodeProcessing",
    response_model=EpisodeProcessingEnvelope,
    responses=PROBLEM_RESPONSES,
)
def transition_continuous_recording_episode_processing(
    project_id: str,
    region_code: str,
    recording_id: str,
    episode_id: str,
    command: AdvanceEpisodeProcessingCommand,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> EpisodeProcessingEnvelope:
    response.headers["Cache-Control"] = "no-store"
    return service.advance_episode_processing(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        recording_id=recording_id,
        episode_id=episode_id,
        command=command,
    )


@router.get(
    "/{recording_id}/video-sources",
    operation_id="authorizeContinuousRecordingVideos",
    response_model=RecordingVideoSourceEnvelope,
    responses=PROBLEM_RESPONSES,
)
def authorize_continuous_recording_videos(
    project_id: str,
    region_code: str,
    recording_id: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> RecordingVideoSourceEnvelope:
    response.headers["Cache-Control"] = "no-store"
    return service.authorize_recording_videos(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        recording_id=recording_id,
    )


@router.get(
    "/{recording_id}/episodes/{episode_id}/video-sources",
    operation_id="authorizeContinuousRecordingEpisodeVideos",
    response_model=EpisodeVideoSourceEnvelope,
    responses=PROBLEM_RESPONSES,
)
def authorize_continuous_recording_episode_videos(
    project_id: str,
    region_code: str,
    recording_id: str,
    episode_id: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> EpisodeVideoSourceEnvelope:
    response.headers["Cache-Control"] = "no-store"
    return service.authorize_episode_videos(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        recording_id=recording_id,
        episode_id=episode_id,
    )
