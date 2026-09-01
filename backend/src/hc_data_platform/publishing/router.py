from __future__ import annotations

import hashlib
import inspect
import re
from collections.abc import Awaitable
from datetime import datetime, timedelta, timezone
from typing import TypeVar

from fastapi import APIRouter, Header, Response

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.security.http import VerifiedAuth, authorize_scope
from hc_data_platform.workflow.models import (
    ExportWorkflowInput,
    JobRecord,
    JobStatus,
    WorkflowKind,
    workflow_id,
)
from hc_data_platform.workflow.names import EXPORT_WORKFLOW
from hc_data_platform.workflow.router import get_launcher
from hc_data_platform.workflow.service import InMemoryWorkflowLauncher, TemporalWorkflowLauncher

from .audit import ExportAuditRecorder, ExportDownloadAuditEvent, InMemoryExportAuditRecorder
from .memory import (
    InMemoryAnnotationSnapshot,
    InMemoryArtifactSink,
    InMemoryCatalogSnapshot,
    InMemoryExportSource,
    InMemoryPublishedManifestRepository,
)
from .models import (
    ExportDatasetRequestV1,
    ExportDownloadAuthorizationV1,
    ExportFormat,
    ExportJobProgressV1,
    ExportJobResultV1,
    ExportJobStatus,
    ExportJobV1,
    ExportResultV1,
    PublishDatasetRequestV1,
    PublishedDatasetManifestV1,
    PublishPreflightReportV1,
)
from .service import DatasetPublisher, ExportCoordinator

router = APIRouter(prefix="/api/v1/datasets", tags=["publishing"])

_artifact_sink = InMemoryArtifactSink()
_publisher = DatasetPublisher(
    catalog=InMemoryCatalogSnapshot(),
    annotations=InMemoryAnnotationSnapshot(),
    repository=InMemoryPublishedManifestRepository(),
    artifact_sink=_artifact_sink,
)
_export_coordinator = ExportCoordinator(
    source=InMemoryExportSource(),
    sink=_artifact_sink,
    exporters=(),
)
_audit_recorder: ExportAuditRecorder = InMemoryExportAuditRecorder()
_T = TypeVar("_T")
_DOWNLOAD_TTL = timedelta(minutes=15)
_READY_LANCE_VERSION = re.compile(r"^version_lance_([1-9][0-9]*)$")
_NO_STORE_HEADERS = {
    "Cache-Control": {
        "description": "Response contains workflow state or a bearer capability.",
        "schema": {"type": "string", "const": "no-store"},
    }
}


def configure_dataset_publisher(publisher: DatasetPublisher) -> None:
    """Application composition hook for catalog, annotation and durable manifest adapters."""

    global _publisher
    _publisher = publisher


def configure_export_coordinator(coordinator: ExportCoordinator) -> None:
    """Application composition hook for native exporter and durable staging adapters."""

    global _export_coordinator
    _export_coordinator = coordinator


def configure_export_audit_recorder(recorder: ExportAuditRecorder) -> None:
    """Install the durable ledger used after each fresh export-download authorization."""

    global _audit_recorder
    _audit_recorder = recorder


async def _resolve(value: _T | Awaitable[_T]) -> _T:
    if inspect.isawaitable(value):
        return await value
    return value


def _selected_attempt(
    manifest: PublishedDatasetManifestV1,
    export_format: ExportFormat,
    idempotency_key: str,
) -> str:
    """Never put a caller-supplied idempotency value in Temporal/resource IDs."""

    return hashlib.sha256(
        f"{manifest.content_hash}:{export_format.value}:{idempotency_key}".encode()
    ).hexdigest()[:32]


def _resource_id(
    manifest: PublishedDatasetManifestV1,
    export_format: ExportFormat,
    attempt_id: str,
) -> str:
    return "/".join(
        (manifest.dataset_id, manifest.dataset_version, export_format.value, attempt_id)
    )


def _resolve_export_manifest(
    *, project_id: str, dataset_id: str, dataset_version: str
) -> PublishedDatasetManifestV1:
    try:
        return _publisher.get(
            project_id=project_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )
    except ProblemException as exc:
        if exc.problem.code != "DATASET_VERSION_NOT_FOUND":
            raise
        match = _READY_LANCE_VERSION.fullmatch(dataset_version)
        if match is None:
            raise
        return _publisher.publish(
            PublishDatasetRequestV1(
                project_id=project_id,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                base_lance_version=match.group(1),
            )
        )


def _export_job_not_found() -> Exception:
    return problem(
        status=404,
        code="EXPORT_JOB_NOT_FOUND",
        title="Export job not found",
        detail="No export job exists for the requested dataset version.",
    )


def _result_from_job(record: JobRecord) -> ExportResultV1 | None:
    payload = record.result
    if not isinstance(payload, dict):
        return None
    raw = payload.get("export")
    if not isinstance(raw, dict):
        return None
    try:
        return ExportResultV1.model_validate(raw)
    except ValueError as exc:
        raise problem(
            status=409,
            code="EXPORT_JOB_RESULT_INVALID",
            title="Export job result is invalid",
            detail="The durable export result cannot be used for download authorization.",
        ) from exc


def _export_job(
    record: JobRecord,
    *,
    dataset_id: str,
    dataset_version: str,
) -> ExportJobV1:
    prefix = f"{dataset_id}/{dataset_version}/"
    if record.job_type != EXPORT_WORKFLOW or not record.resource_id.startswith(prefix):
        raise _export_job_not_found()
    parts = record.resource_id.removeprefix(prefix).split("/", maxsplit=1)
    if len(parts) != 2:
        raise _export_job_not_found()
    try:
        export_format = ExportFormat(parts[0])
    except ValueError as exc:
        raise _export_job_not_found() from exc
    status = {
        JobStatus.PENDING: ExportJobStatus.PENDING,
        JobStatus.RUNNING: ExportJobStatus.RUNNING,
        JobStatus.SUCCEEDED: ExportJobStatus.SUCCEEDED,
        JobStatus.CANCELLED: ExportJobStatus.CANCELLED,
        JobStatus.TECHNICAL_FAILED: ExportJobStatus.FAILED,
        JobStatus.QUALITY_RISK: ExportJobStatus.FAILED,
        JobStatus.QUALITY_REJECTED: ExportJobStatus.FAILED,
    }[record.status]
    result = _result_from_job(record)
    if result is not None and (
        result.project_id != record.project_id
        or result.dataset_id != dataset_id
        or result.dataset_version != dataset_version
        or result.format is not export_format
        or result.attempt_id != parts[1]
    ):
        raise problem(
            status=409,
            code="EXPORT_JOB_RESULT_SCOPE_MISMATCH",
            title="Export job result scope mismatch",
            detail="The durable export result does not match this dataset version.",
        )
    return ExportJobV1(
        job_id=record.job_id,
        project_id=record.project_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        format=export_format,
        attempt_id=parts[1],
        status=status,
        stage=record.stage,
        progress=_export_progress(record),
        cancellation_requested=record.cancellation_requested,
        result=(
            None
            if result is None
            else ExportJobResultV1.model_validate(
                result.model_dump(exclude={"artifact_uri", "download_uri"})
            )
        ),
        error_code=record.error_code,
        error_message=record.error_message,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _export_progress(record: JobRecord) -> ExportJobProgressV1:
    """Project only durable Temporal phases; never invent a byte-level estimate."""

    if record.status is JobStatus.SUCCEEDED:
        return ExportJobProgressV1(phase="completed", completed_phases=3, total_phases=3)
    completed, phase = {
        "pending": (0, "queued"),
        "starting": (0, "queued"),
        "preflight": (0, "preflight"),
        "materializing": (1, "materializing"),
        "verifying_artifact": (2, "verifying_artifact"),
        # Pre-progress historical histories consist of one materialization
        # activity.  Keep their fact truthful rather than forcing a new scale.
        "export": (0, "materializing"),
    }.get(record.stage, (0, record.stage))
    return ExportJobProgressV1(
        phase=phase,
        completed_phases=completed,
        total_phases=3,
    )


async def _launch_export(
    manifest: PublishedDatasetManifestV1,
    *,
    export_format: ExportFormat,
    attempt_id: str,
) -> JobRecord:
    resource_id = _resource_id(manifest, export_format, attempt_id)
    identifier = workflow_id(WorkflowKind.EXPORT, manifest.project_id, resource_id)
    launcher = get_launcher()
    if isinstance(launcher, TemporalWorkflowLauncher):
        return await launcher.start(
            workflow_name=EXPORT_WORKFLOW,
            workflow_input=ExportWorkflowInput(
                manifest=manifest,
                format=export_format,
                attempt_id=attempt_id,
            ),
            workflow_id=identifier,
            job_type=EXPORT_WORKFLOW,
            project_id=manifest.project_id,
            resource_id=resource_id,
        )
    if not isinstance(launcher, InMemoryWorkflowLauncher):
        raise RuntimeError("configured workflow launcher does not support export execution")
    return launcher.start(
        workflow_id=identifier,
        job_type=EXPORT_WORKFLOW,
        project_id=manifest.project_id,
        resource_id=resource_id,
        runner=lambda: {
            "export": _export_coordinator.export(
                manifest,
                format=export_format,
                attempt_id=attempt_id,
            ).model_dump(mode="json")
        },
    )


@router.post("/publication-preflight", response_model=PublishPreflightReportV1)
def publication_preflight(
    request: PublishDatasetRequestV1,
    auth: VerifiedAuth,
) -> PublishPreflightReportV1:
    authorize_scope(auth, request.project_id, "dataset_version.publish")
    return _publisher.preflight(request)


@router.post("/publications", response_model=PublishedDatasetManifestV1, status_code=201)
def publish_dataset(
    request: PublishDatasetRequestV1,
    auth: VerifiedAuth,
) -> PublishedDatasetManifestV1:
    authorize_scope(auth, request.project_id, "dataset_version.publish")
    return _publisher.publish(request)


@router.get(
    "/{dataset_id}/versions/{dataset_version}",
    response_model=PublishedDatasetManifestV1,
)
def get_dataset_version(
    dataset_id: str,
    dataset_version: str,
    project_id: str,
    auth: VerifiedAuth,
) -> PublishedDatasetManifestV1:
    authorize_scope(auth, project_id, "export.read")
    return _publisher.get(
        project_id=project_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )


@router.post(
    "/{dataset_id}/versions/{dataset_version}/exports",
    response_model=ExportJobV1,
    status_code=202,
    responses={
        202: {
            "headers": {
                **_NO_STORE_HEADERS,
                "Location": {
                    "description": "Canonical export-job status resource.",
                    "schema": {"type": "string", "minLength": 1},
                },
            }
        }
    },
)
async def export_dataset_version(
    dataset_id: str,
    dataset_version: str,
    request: ExportDatasetRequestV1,
    auth: VerifiedAuth,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
) -> ExportJobV1:
    authorize_scope(auth, request.project_id, "dataset_version.publish")
    manifest = _resolve_export_manifest(
        project_id=request.project_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )
    attempt_id = _selected_attempt(manifest, request.format, idempotency_key)
    job = await _launch_export(manifest, export_format=request.format, attempt_id=attempt_id)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Location"] = (
        f"/api/v1/datasets/{dataset_id}/versions/{dataset_version}/exports/{job.job_id}"
    )
    return _export_job(job, dataset_id=dataset_id, dataset_version=dataset_version)


@router.get(
    "/{dataset_id}/versions/{dataset_version}/exports/{job_id}",
    response_model=ExportJobV1,
    responses={200: {"headers": _NO_STORE_HEADERS}},
)
async def get_export_job(
    dataset_id: str,
    dataset_version: str,
    job_id: str,
    project_id: str,
    auth: VerifiedAuth,
    response: Response,
) -> ExportJobV1:
    authorize_scope(auth, project_id, "dataset_version.publish")
    job = await _resolve(get_launcher().get(job_id))
    if job.project_id != project_id:
        raise _export_job_not_found()
    response.headers["Cache-Control"] = "no-store"
    return _export_job(job, dataset_id=dataset_id, dataset_version=dataset_version)


@router.post(
    "/{dataset_id}/versions/{dataset_version}/exports/{job_id}:cancel",
    response_model=ExportJobV1,
    responses={200: {"headers": _NO_STORE_HEADERS}},
)
async def cancel_export_job(
    dataset_id: str,
    dataset_version: str,
    job_id: str,
    project_id: str,
    auth: VerifiedAuth,
    response: Response,
) -> ExportJobV1:
    authorize_scope(auth, project_id, "dataset_version.publish")
    existing = await _resolve(get_launcher().get(job_id))
    if existing.project_id != project_id:
        raise _export_job_not_found()
    _export_job(existing, dataset_id=dataset_id, dataset_version=dataset_version)
    cancelled = await _resolve(get_launcher().cancel(job_id))
    response.headers["Cache-Control"] = "no-store"
    return _export_job(cancelled, dataset_id=dataset_id, dataset_version=dataset_version)


@router.post(
    "/{dataset_id}/versions/{dataset_version}/exports/{job_id}:retry",
    response_model=ExportJobV1,
    status_code=202,
    responses={202: {"headers": _NO_STORE_HEADERS}},
)
async def retry_export_job(
    dataset_id: str,
    dataset_version: str,
    job_id: str,
    project_id: str,
    auth: VerifiedAuth,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
) -> ExportJobV1:
    authorize_scope(auth, project_id, "dataset_version.publish")
    existing = await _resolve(get_launcher().get(job_id))
    if existing.project_id != project_id:
        raise _export_job_not_found()
    previous = _export_job(existing, dataset_id=dataset_id, dataset_version=dataset_version)
    if previous.status not in {ExportJobStatus.FAILED, ExportJobStatus.CANCELLED}:
        raise problem(
            status=409,
            code="EXPORT_RETRY_NOT_ALLOWED",
            title="Export retry is not allowed",
            detail="Only failed or cancelled export jobs can be retried.",
        )
    manifest = _publisher.get(
        project_id=project_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )
    retry_attempt = hashlib.sha256(
        f"{manifest.content_hash}:{previous.format.value}:retry:{idempotency_key}".encode()
    ).hexdigest()[:32]
    job = await _launch_export(
        manifest,
        export_format=previous.format,
        attempt_id=retry_attempt,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Location"] = (
        f"/api/v1/datasets/{dataset_id}/versions/{dataset_version}/exports/{job.job_id}"
    )
    return _export_job(job, dataset_id=dataset_id, dataset_version=dataset_version)


@router.get(
    "/{dataset_id}/versions/{dataset_version}/exports/{job_id}/download",
    response_model=ExportDownloadAuthorizationV1,
    responses={200: {"headers": _NO_STORE_HEADERS}},
)
async def authorize_export_download(
    dataset_id: str,
    dataset_version: str,
    job_id: str,
    project_id: str,
    auth: VerifiedAuth,
    response: Response,
) -> ExportDownloadAuthorizationV1:
    authorize_scope(auth, project_id, "dataset_version.publish")
    job = await _resolve(get_launcher().get(job_id))
    if job.project_id != project_id:
        raise _export_job_not_found()
    export_job = _export_job(job, dataset_id=dataset_id, dataset_version=dataset_version)
    if export_job.status is not ExportJobStatus.SUCCEEDED:
        raise problem(
            status=409,
            code="EXPORT_NOT_READY_FOR_DOWNLOAD",
            title="Export is not ready for download",
            detail="Only a successfully completed export can receive a download authorization.",
        )
    internal_result = _result_from_job(job)
    if internal_result is None:
        raise problem(
            status=409,
            code="EXPORT_RESULT_MISSING",
            title="Export result is missing",
            detail="The completed export has no result available for download.",
        )
    _publisher.get(
        project_id=project_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )
    download_url = _export_coordinator.authorize_download(internal_result)
    context = current_request_context()
    _audit_recorder.append_download_authorization(
        ExportDownloadAuditEvent(
            project_id=project_id,
            region_code=context.region_code,
            actor_id=auth.subject_id,
            request_id=context.request_id,
            job_id=job_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            export_format=export_job.format.value,
            artifact_content_hash=internal_result.artifact_content_hash,
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return ExportDownloadAuthorizationV1(
        job_id=job_id,
        format=export_job.format,
        download_url=download_url,
        expires_at=datetime.now(timezone.utc) + _DOWNLOAD_TTL,
        artifact_content_hash=internal_result.artifact_content_hash,
        media_type=internal_result.media_type,
    )
