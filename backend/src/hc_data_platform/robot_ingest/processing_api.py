"""Robot-owned processing reads/retries; E includes this router beside robot_ingest.router."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import Field

from hc_data_platform.core.errors import problem

from .models import StrictModel
from .processing_contract import ProcessingResult, processing_result
from .processing_store import ProcessingStore, eligible
from .router import RobotToken, Service

router = APIRouter(prefix="/api/v1/robot-ingest", tags=["robot-ingest"])
_store: ProcessingStore | None = None


def configure_processing(store: ProcessingStore) -> None:
    global _store
    _store = store


def get_processing_store() -> ProcessingStore:
    if _store is None:
        raise problem(
            status=503,
            code="ROBOT_PROCESSING_NOT_CONFIGURED",
            title="Processing unavailable",
            detail="Processing persistence is not configured.",
        )
    return _store


Store = Annotated[ProcessingStore, Depends(get_processing_store)]


def owned(service: Service, token: str, upload_id: str):
    upload = service.get_upload(token=token, upload_id=upload_id).data
    if upload.state.value != "COMMITTED" or upload.raw_source_id is None:
        raise problem(
            status=409,
            code="ROBOT_UPLOAD_NOT_COMMITTED",
            title="Raw is not committed",
            detail="Complete and commit the upload before polling processing.",
        )
    if not eligible(upload):
        raise problem(
            status=409,
            code="ROBOT_PROCESSING_INCOMPATIBLE",
            title="Source cannot be processed",
            detail="This processing API requires a complete LeRobot v3 robot upload.",
        )
    return upload


@router.get(
    "/uploads/{upload_id}/processing",
    response_model=ProcessingResult,
    operation_id="getRobotIngestProcessing",
)
def get_processing(
    upload_id: str, response: Response, token: RobotToken, service: Service, store: Store
) -> ProcessingResult:
    response.headers["Cache-Control"] = "no-store"
    upload = owned(service, token, upload_id)
    task, document = store.read(upload)
    if task is None:
        raise problem(
            status=409,
            code="ROBOT_PROCESSING_NOT_SCHEDULED",
            title="Historical Raw needs explicit scheduling",
            detail="Use retry-processing with a persisted request_id; do not upload bytes again.",
        )
    return processing_result(upload, document)


class ProcessingDiagnostics(StrictModel):
    upload_id: str
    workflow_id: str | None
    generation: int | None
    error_code: str | None
    retryable: bool
    next_action: str


@router.get(
    "/uploads/{upload_id}/processing/diagnostics",
    response_model=ProcessingDiagnostics,
    operation_id="getRobotIngestProcessingDiagnostics",
)
def get_diagnostics(
    upload_id: str, response: Response, token: RobotToken, service: Service, store: Store
) -> ProcessingDiagnostics:
    """Pre-discovery failures have no source episode. Keep the G0 schema unchanged."""
    response.headers["Cache-Control"] = "no-store"
    upload = owned(service, token, upload_id)
    task, doc = store.read(upload)
    can_retry = doc.phase == "DONE" and (doc.retryable or any(ep.retryable for ep in doc.episodes))
    return ProcessingDiagnostics(
        upload_id=upload_id,
        workflow_id=task.workflow_id if task else None,
        generation=task.generation if task else None,
        error_code=doc.error_code,
        retryable=can_retry,
        next_action="retry_processing"
        if can_retry
        else "repair_export"
        if doc.error_code
        else "wait"
        if doc.phase != "DONE"
        else "inspect_episodes",
    )


class RetryProcessing(StrictModel):
    request_id: UUID


class RecordingResult(StrictModel):
    upload_id: str
    raw_source_id: str | None
    recording_id: str | None
    status: Literal["REGISTERING", "AWAITING_SLICE", "SLICED", "FAILED"]
    page_path: str | None
    terminal: bool
    poll_after_seconds: int = Field(ge=0, le=60)
    error_code: str | None = None
    registration_attempts: int = Field(default=0, ge=0)


@router.get(
    "/uploads/{upload_id}/recording",
    response_model=RecordingResult,
    operation_id="getRobotIngestRecording",
)
def get_recording(
    upload_id: str, response: Response, token: RobotToken, service: Service, store: Store
) -> RecordingResult:
    from . import recording_bridge

    upload = service.get_upload(token=token, upload_id=upload_id).data
    if not recording_bridge.eligible(upload):
        raise problem(
            status=409,
            code="ROBOT_RECORDING_INCOMPATIBLE",
            title="Recording unavailable",
            detail="This endpoint requires a committed OpenArm recording bundle.",
        )
    response.headers["Cache-Control"] = "no-store"
    return RecordingResult.model_validate(recording_bridge.result(store.connections, upload))


@router.post(
    "/uploads/{upload_id}/recording:retry",
    response_model=RecordingResult,
    operation_id="retryRobotRecordingPreparation",
)
def retry_recording(
    upload_id: str,
    command: RetryProcessing,
    response: Response,
    token: RobotToken,
    service: Service,
    store: Store,
) -> RecordingResult:
    from . import recording_bridge

    # Same Bearer ownership check as GET; this never allocates another Raw.
    get_recording(upload_id, response, token, service, store)
    upload = service.get_upload(token=token, upload_id=upload_id).data
    recording_bridge.retry(store.connections, upload, str(command.request_id))
    return get_recording(upload_id, response, token, service, store)


@router.post(
    "/uploads/{upload_id}:retry-processing",
    response_model=ProcessingResult,
    operation_id="retryRobotIngestProcessing",
)
def retry_processing(
    upload_id: str,
    command: RetryProcessing,
    response: Response,
    token: RobotToken,
    service: Service,
    store: Store,
) -> ProcessingResult:
    upload = owned(service, token, upload_id)
    store.retry(upload, str(command.request_id))
    return get_processing(upload_id, response, token, service, store)
