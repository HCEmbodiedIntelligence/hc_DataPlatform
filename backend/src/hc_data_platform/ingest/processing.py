from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable
from typing import Protocol, TypeVar, cast

from pydantic import BaseModel, ConfigDict, ValidationError

from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.dataset_registry.models import DatasetIngestViewerTarget
from hc_data_platform.lance_catalog.models import DerivedReadyV1
from hc_data_platform.workflow.models import (
    AutomaticAnnotationActivityOutput,
    JobRecord,
    JobStatus,
)
from hc_data_platform.workflow.names import INGEST_ROLLOUT_WORKFLOW

from .models import (
    IngestTriggerStatus,
    UploadPreviewTargetV1,
    UploadProcessingState,
    UploadProcessingStatusV1,
    UploadSession,
)

_T = TypeVar("_T")
_SAFE_ERROR_CODE = re.compile(r"^[A-Z0-9_]{1,128}$")


class UploadJobStatusPort(Protocol):
    def get(self, job_id: str) -> JobRecord | Awaitable[JobRecord]: ...


class _AlignmentSummary(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    frequency_hz: float


class _SuccessfulIngestResult(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    alignment: _AlignmentSummary
    derived: DerivedReadyV1
    annotation_task: AutomaticAnnotationActivityOutput
    viewer_target: DatasetIngestViewerTarget


async def _resolve(value: _T | Awaitable[_T]) -> _T:
    if inspect.isawaitable(value):
        return await cast(Awaitable[_T], value)
    return value


def _pending_status(session: UploadSession) -> UploadProcessingStatusV1:
    locator = session.workflow
    if locator is None:
        raise _processing_not_started()
    stage = {
        IngestTriggerStatus.PENDING: "dispatch_pending",
        IngestTriggerStatus.RETRY_WAIT: "dispatch_retry_wait",
        IngestTriggerStatus.DISPATCHED: "workflow_starting",
    }[locator.status]
    return UploadProcessingStatusV1(
        schema_version="upload-processing-status/v1",
        session_id=session.session_id,
        rollout_id=session.rollout_id,
        workflow_id=locator.workflow_id,
        status=UploadProcessingState.PENDING,
        stage=stage,
        attempt=locator.attempts,
        preview=None,
        viewer=None,
        error_code=_safe_error_code(locator.last_error_code),
        updated_at=locator.updated_at,
    )


def _successful_targets(
    session: UploadSession,
    job: JobRecord,
) -> tuple[UploadPreviewTargetV1 | None, DatasetIngestViewerTarget | None]:
    if job.status is not JobStatus.SUCCEEDED:
        return None, None
    try:
        result = _SuccessfulIngestResult.model_validate(job.result)
    except ValidationError as exc:
        raise problem(
            status=500,
            code="UPLOAD_PROCESSING_RESULT_INVALID",
            title="Upload processing result is invalid",
            detail="The completed upload does not expose a valid preview target.",
        ) from exc
    derived = result.derived
    if (
        derived.project_id != session.project_id
        or derived.rollout_id != session.rollout_id
        or derived.source_sha256 != session.expected_sha256
        or result.viewer_target.dataset_id != derived.dataset_id
        or result.viewer_target.version_id != f"version_lance_{derived.dataset_version}"
    ):
        raise problem(
            status=500,
            code="UPLOAD_PROCESSING_LINEAGE_INVALID",
            title="Upload processing lineage is invalid",
            detail="The completed upload preview does not match the committed Raw capture.",
        )
    return (
        UploadPreviewTargetV1(
            schema_version="upload-preview-target/v1",
            project_id=derived.project_id,
            dataset_id=derived.dataset_id,
            rollout_id=derived.rollout_id,
            dataset_version=derived.dataset_version,
            lance_version=derived.lance_version,
            annotation_task_id=result.annotation_task.task_id,
            frequency_hz=result.alignment.frequency_hz,
            start_step=0,
            end_step=derived.step_count,
        ),
        result.viewer_target,
    )


def project_upload_processing_status(
    session: UploadSession,
    job: JobRecord,
) -> UploadProcessingStatusV1:
    locator = session.workflow
    if locator is None:
        raise _processing_not_started()
    if (
        job.workflow_id != locator.workflow_id
        or job.job_type != INGEST_ROLLOUT_WORKFLOW
        or job.project_id != session.project_id
        or job.resource_id != session.rollout_id
    ):
        raise problem(
            status=500,
            code="UPLOAD_PROCESSING_IDENTITY_INVALID",
            title="Upload processing identity is invalid",
            detail="The workflow result does not match the selected upload session.",
        )
    preview, viewer = _successful_targets(session, job)
    return UploadProcessingStatusV1(
        schema_version="upload-processing-status/v1",
        session_id=session.session_id,
        rollout_id=session.rollout_id,
        workflow_id=locator.workflow_id,
        status=UploadProcessingState(job.status.value),
        stage=job.stage,
        attempt=job.attempt,
        preview=preview,
        viewer=viewer,
        error_code=_safe_error_code(job.error_code),
        updated_at=job.updated_at,
    )


async def read_upload_processing_status(
    session: UploadSession,
    status_port: UploadJobStatusPort,
) -> UploadProcessingStatusV1:
    locator = session.workflow
    if locator is None:
        raise _processing_not_started()
    try:
        job = await _resolve(status_port.get(locator.workflow_id))
    except ProblemException as exc:
        if exc.problem.status == 404 and exc.problem.code == "JOB_NOT_FOUND":
            return _pending_status(session)
        raise
    return project_upload_processing_status(session, job)


def _processing_not_started() -> ProblemException:
    return problem(
        status=409,
        code="UPLOAD_PROCESSING_NOT_STARTED",
        title="Upload processing has not started",
        detail="Commit the upload Manifest before reading its processing status.",
    )


def _safe_error_code(value: str | None) -> str | None:
    if value is None:
        return None
    if _SAFE_ERROR_CODE.fullmatch(value):
        return value
    return "WORKFLOW_EXECUTION_FAILED"
