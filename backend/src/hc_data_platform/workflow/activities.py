"""Temporal activities that adapt orchestration contracts to stage-owned ports."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from typing import Any, Protocol, TypeVar
from urllib.parse import quote, unquote, urlparse

from pydantic import ValidationError
from temporalio import activity
from temporalio.exceptions import ApplicationError

from hc_data_platform.aligned_media.models import (
    AlignedMediaArtifactV1,
    AlignedMediaGenerationRequestV1,
    AlignedMediaScopeV1,
    AlignmentCameraStagingArtifactV1,
    AlignmentStagingArtifactV1,
)
from hc_data_platform.aligned_media.ports import (
    AlignedMediaArtifactStorePort,
    AlignedMediaRepositoryPort,
)
from hc_data_platform.alignment.models import (
    AlignedFragmentManifestV1,
    AlignmentInputV1,
    ModalityKind,
    TimedSampleV1,
)
from hc_data_platform.alignment.ports import AlignmentPort, FragmentWriterPort
from hc_data_platform.annotation.automation import (
    AutomaticAnnotationBlocked,
    AutomaticAnnotationRequest,
)
from hc_data_platform.annotation.models import AnnotationTask
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.observability import (
    LANCE_COMMITS,
    QC_OUTCOMES,
    WORKFLOW_FAILURES,
    locator_workflow_id,
)
from hc_data_platform.core.structured_logging import (
    LogCorrelation,
    bind_log_correlation,
    log_event,
    reset_log_correlation,
)
from hc_data_platform.dataset_registry.models import DatasetIngestViewerTarget
from hc_data_platform.ingest.manifest import ManifestParserPort
from hc_data_platform.lance_catalog.models import (
    AlignedFragmentManifestV1 as CatalogFragmentManifestV1,
)
from hc_data_platform.lance_catalog.models import DatasetVersionRef, DerivedReadyV1, StepRecord
from hc_data_platform.lance_catalog.ports import LanceCatalogPort
from hc_data_platform.lance_catalog.service import (
    CatalogConflictError,
    SchemaIncompatibleError,
)
from hc_data_platform.platform_control.maintenance_contract import MaintenanceContractError
from hc_data_platform.platform_ops.maintenance import (
    EnvironmentId,
    MaintenanceWriteGate,
    SafeActorId,
    writer_permit_scope,
)
from hc_data_platform.publishing.models import (
    ExportFormat,
    ExportResultV1,
    PublishDatasetRequestV1,
    PublishedDatasetManifestV1,
)
from hc_data_platform.quality.models import (
    QcReportV1,
    QualityInputV1,
    QualityStatus,
    QualityStreamObservationV1,
)
from hc_data_platform.quality.ports import QualityEvaluationPort
from hc_data_platform.storage.temporal import LifecycleBatchExecutor
from hc_data_platform.verification.models import RawVerificationReportV1, VerificationStatus
from hc_data_platform.verification.ports import (
    RawStreamVerificationPort,
    RawVerificationPort,
    ReadableBinaryStream,
)
from hc_data_platform.workflow.projection_store import ProjectionArtifactStorePort

from .models import (
    AlignedBundleCommitActivityInput,
    AlignedBundleCommitActivityOutput,
    AlignedMediaActivityInput,
    AlignedMediaActivityOutput,
    AlignedMediaCleanupActivityInput,
    AlignedMediaCleanupActivityOutput,
    AlignmentActivityInput,
    AlignmentActivityOutput,
    AlignmentStagingCleanupActivityInput,
    AlignmentStagingCleanupActivityOutput,
    AutomaticAnnotationActivityInput,
    AutomaticAnnotationActivityOutput,
    CatalogCommitActivityInput,
    CatalogCommitActivityOutput,
    CatalogFragmentPayloadV1,
    CatalogReconciliationActivityInput,
    CatalogReconciliationActivityOutput,
    ContinuousEpisodeAlignmentActivityOutput,
    ContinuousEpisodeBundleCommitActivityInput,
    ContinuousEpisodeQcActivityOutput,
    ContinuousEpisodeStateActivityInput,
    ContinuousEpisodeStateActivityOutput,
    ContinuousEpisodeWorkflowInput,
    ExportActivityInput,
    ExportActivityOutput,
    ExportArtifactVerificationActivityInput,
    ExportArtifactVerificationActivityOutput,
    ExportPreflightActivityInput,
    ExportPreflightActivityOutput,
    FrameSelectionManifestRefV1,
    IngestProjectionSourceV1,
    IngestSourceProcessingActivityInput,
    IngestSourceProcessingActivityOutput,
    JobRecord,
    ManifestActivityInput,
    ManifestActivityOutput,
    ProjectionCleanupActivityInput,
    ProjectionCleanupActivityOutput,
    ProjectionMaterializationActivityInput,
    ProjectionMaterializationActivityOutput,
    PublishActivityInput,
    PublishActivityOutput,
    PublishReconciliationActivityInput,
    PublishReconciliationActivityOutput,
    QualityActivityInput,
    QualityActivityOutput,
    VerificationActivityInput,
    VerificationActivityOutput,
    WorkflowJobPersistenceActivityInput,
)
from .names import (
    ALIGN_CONTINUOUS_EPISODE_ACTIVITY,
    ALIGN_FRAGMENT_ACTIVITY,
    CLEANUP_ALIGNMENT_STAGING_ACTIVITY,
    CLEANUP_INGEST_PROJECTION_ACTIVITY,
    CLEANUP_UNCOMMITTED_ALIGNED_MEDIA_ACTIVITY,
    COMMIT_ALIGNED_BUNDLE_ACTIVITY,
    COMMIT_CONTINUOUS_EPISODE_BUNDLE_ACTIVITY,
    COMMIT_FRAGMENT_ACTIVITY,
    CREATE_ALIGNED_MEDIA_ACTIVITY,
    CREATE_ANNOTATION_TASK_ACTIVITY,
    EVALUATE_QUALITY_ACTIVITY,
    EXPORT_DATASET_ACTIVITY,
    MATERIALIZE_INGEST_PROJECTION_ACTIVITY,
    PARSE_MANIFEST_ACTIVITY,
    PERSIST_WORKFLOW_JOB_ACTIVITY,
    PREFLIGHT_EXPORT_ACTIVITY,
    PROCESS_INGEST_SOURCE_ACTIVITY,
    PUBLISH_DATASET_ACTIVITY,
    QC_CONTINUOUS_EPISODE_ACTIVITY,
    RECONCILE_CATALOG_ACTIVITY,
    RECONCILE_PUBLICATION_ACTIVITY,
    UPDATE_CONTINUOUS_EPISODE_STATE_ACTIVITY,
    VERIFY_EXPORT_ARTIFACT_ACTIVITY,
    VERIFY_RAW_ACTIVITY,
)

_HEARTBEAT_INTERVAL_SECONDS = 5.0
_T = TypeVar("_T")
logger = logging.getLogger(__name__)


class FragmentWriterFactoryPort(Protocol):
    def create(self, request: AlignmentActivityInput) -> FragmentWriterPort: ...


class LocalIngestProjectionSessionPort(Protocol):
    quality_data: QualityInputV1
    alignment_data: AlignmentInputV1

    def open_reader(self) -> ReadableBinaryStream: ...

    def quality_observations(self) -> Iterable[QualityStreamObservationV1]: ...

    def alignment_samples(self) -> Iterable[tuple[str, TimedSampleV1]]: ...

    def frame_selection(self) -> FrameSelectionManifestRefV1: ...


class IngestProjectionPort(Protocol):
    """Reload bounded raw observations inside an activity, outside workflow history."""

    def materialize(self, source: IngestProjectionSourceV1) -> IngestProjectionSourceV1: ...

    def cleanup(self, source: IngestProjectionSourceV1) -> None: ...

    def open_local_session(
        self, source: IngestProjectionSourceV1
    ) -> AbstractContextManager[LocalIngestProjectionSessionPort]: ...

    def project_alignment_metadata(self, source: IngestProjectionSourceV1) -> AlignmentInputV1: ...

    def project_quality(self, source: IngestProjectionSourceV1) -> QualityInputV1: ...

    def project_quality_stream(
        self, source: IngestProjectionSourceV1
    ) -> tuple[QualityInputV1, Iterable[QualityStreamObservationV1]]: ...

    def project_alignment(self, source: IngestProjectionSourceV1) -> AlignmentInputV1: ...

    def project_alignment_stream(
        self, source: IngestProjectionSourceV1
    ) -> tuple[
        AlignmentInputV1,
        Iterable[tuple[str, TimedSampleV1]],
    ]: ...


class DatasetIngestProjectionPort(Protocol):
    """Publish safe P05/P06 facts only after the immutable Lance commit exists."""

    def project(
        self,
        *,
        source: IngestProjectionSourceV1,
        alignment: AlignmentInputV1,
        schema_snapshot_id: str,
        frequency_hz: float,
        version: DatasetVersionRef,
        ready: DerivedReadyV1,
        media_artifacts: Sequence[AlignedMediaArtifactV1],
    ) -> DatasetIngestViewerTarget: ...


class CatalogFragmentAdapterPort(Protocol):
    """Translate staged alignment metadata without reimplementing alignment."""

    def prepare(
        self,
        request: AlignmentActivityInput,
        manifest: AlignedFragmentManifestV1,
    ) -> CatalogFragmentPayloadV1: ...

    def prepare_streaming(
        self,
        request: AlignmentActivityInput,
        manifest: AlignedFragmentManifestV1,
        media_artifacts: Sequence[AlignedMediaArtifactV1] = (),
    ) -> tuple[CatalogFragmentManifestV1, Sequence[StepRecord]]: ...


class AlignedMediaGenerationPort(Protocol):
    def generate(
        self,
        scope: AlignedMediaScopeV1,
        request: AlignedMediaGenerationRequestV1,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> AlignedMediaArtifactV1: ...


class DatasetPublishingPort(Protocol):
    def publish(self, request: PublishDatasetRequestV1) -> PublishedDatasetManifestV1: ...


class DatasetExportPort(Protocol):
    def preflight(
        self,
        manifest: PublishedDatasetManifestV1,
        *,
        format: ExportFormat,
    ) -> int: ...

    def export(
        self,
        manifest: PublishedDatasetManifestV1,
        *,
        format: ExportFormat,
        attempt_id: str,
    ) -> ExportResultV1: ...

    def verify_artifact(self, result: ExportResultV1) -> None: ...


class CatalogReconciliationPort(Protocol):
    def reconcile(
        self,
        dataset_id: str,
        *,
        project_id: str | None = None,
    ) -> tuple[DatasetVersionRef, ...]: ...


class PublicationReconciliationPort(Protocol):
    """Finish registration of already-generated immutable publication assets."""

    def reconcile(self, request: PublishDatasetRequestV1) -> PublishedDatasetManifestV1: ...


class VerificationReportPersistencePort(Protocol):
    def put_report(
        self, *, project_id: str, region_code: str, report: RawVerificationReportV1
    ) -> None: ...


class QualityReportPersistencePort(Protocol):
    def put_report(self, *, project_id: str, region_code: str, report: QcReportV1) -> None: ...


class AlignmentManifestPersistencePort(Protocol):
    def put_ready_manifest(
        self,
        *,
        project_id: str,
        region_code: str,
        manifest: AlignedFragmentManifestV1,
    ) -> None: ...


class AutomaticAnnotationTaskPort(Protocol):
    def ensure_task(self, request: AutomaticAnnotationRequest) -> AnnotationTask: ...


class WorkflowJobPersistencePort(Protocol):
    """Persist the latest workflow-owned job state for read-side projections."""

    def put_job(self, request: WorkflowJobPersistenceActivityInput) -> None: ...


class ContinuousEpisodeProcessingPort(Protocol):
    """Worker-only implementation for finalized continuous recording Episodes."""

    def update_state(
        self, request: ContinuousEpisodeStateActivityInput
    ) -> ContinuousEpisodeStateActivityOutput: ...

    def qc(self, request: ContinuousEpisodeWorkflowInput) -> ContinuousEpisodeQcActivityOutput: ...

    def align(
        self, request: ContinuousEpisodeWorkflowInput
    ) -> ContinuousEpisodeAlignmentActivityOutput: ...

    def commit_bundle(
        self,
        *,
        workflow_input: ContinuousEpisodeWorkflowInput,
        alignment: AlignmentActivityInput,
        staged_manifest: Any,
        alignment_staging: AlignmentStagingArtifactV1,
        expected_dataset_version: int,
        expected_camera_ids: Sequence[str],
        media_artifacts: Sequence[AlignedMediaArtifactV1],
    ) -> AlignedBundleCommitActivityOutput: ...


@dataclass(frozen=True, slots=True)
class ActivityDependencies:
    manifest_parser: ManifestParserPort | None = None
    verifier: RawVerificationPort | None = None
    quality: QualityEvaluationPort | None = None
    alignment: AlignmentPort | None = None
    ingest_projection: IngestProjectionPort | None = None
    dataset_ingest_projection: DatasetIngestProjectionPort | None = None
    fragment_writers: FragmentWriterFactoryPort | None = None
    alignment_staging: ProjectionArtifactStorePort | None = None
    alignment_staging_ttl: timedelta = timedelta(hours=24)
    catalog_fragments: CatalogFragmentAdapterPort | None = None
    catalog: LanceCatalogPort | None = None
    aligned_media: AlignedMediaGenerationPort | None = None
    aligned_media_repository: AlignedMediaRepositoryPort | None = None
    aligned_media_store: AlignedMediaArtifactStorePort | None = None
    publisher: DatasetPublishingPort | None = None
    exporter: DatasetExportPort | None = None
    catalog_reconciler: CatalogReconciliationPort | None = None
    publication_reconciler: PublicationReconciliationPort | None = None
    verification_reports: VerificationReportPersistencePort | None = None
    quality_reports: QualityReportPersistencePort | None = None
    alignment_manifests: AlignmentManifestPersistencePort | None = None
    annotation_tasks: AutomaticAnnotationTaskPort | None = None
    storage_lifecycle: LifecycleBatchExecutor | None = None
    workflow_jobs: WorkflowJobPersistencePort | None = None
    continuous_episode_processing: ContinuousEpisodeProcessingPort | None = None
    lerobot_pipeline: Any | None = None


class WorkflowPortNotConfigured(RuntimeError):
    code = "WORKFLOW_PORT_NOT_CONFIGURED"


_dependencies = ActivityDependencies()
_maintenance_gate: MaintenanceWriteGate | None = None
_maintenance_environment_id: EnvironmentId = "local"
_maintenance_instance_id: SafeActorId = "unconfigured-worker"

# MP4 encoding is CPU-heavy. Temporal may have hundreds of ingest workflows
# queued, but a worker must only transcode a small, fixed number at once. Tasks
# waiting here remain async and heartbeat instead of occupying executor threads.
_MEDIA_ACTIVITY_CONCURRENCY = max(1, int(os.getenv("HC_MEDIA_MAX_CONCURRENT_GENERATIONS", "2")))
_MEDIA_ACTIVITY_SLOTS = asyncio.Semaphore(_MEDIA_ACTIVITY_CONCURRENCY)


def configure_activity_dependencies(dependencies: ActivityDependencies) -> None:
    """Configure worker-local port adapters before polling the task queue."""

    global _dependencies
    _dependencies = dependencies
    if dependencies.storage_lifecycle is not None:
        from hc_data_platform.storage.temporal import configure_lifecycle_batch_executor

        configure_lifecycle_batch_executor(dependencies.storage_lifecycle)


def current_activity_dependencies() -> ActivityDependencies:
    return _dependencies


def configure_activity_maintenance(
    gate: MaintenanceWriteGate | None,
    *,
    environment_id: EnvironmentId = "local",
    instance_id: SafeActorId = "unconfigured-worker",
) -> None:
    """Bind the process-global worker fence before Temporal begins polling."""

    global _maintenance_gate, _maintenance_environment_id, _maintenance_instance_id
    _maintenance_gate = gate
    _maintenance_environment_id = environment_id
    _maintenance_instance_id = instance_id


def _require(value: _T | None, name: str) -> _T:
    if value is None:
        raise WorkflowPortNotConfigured(f"workflow activity port {name!r} is not configured")
    return value


@contextmanager
def _worker_scope(
    project_id: str | None,
    region_code: str | None,
    resource_id: str,
    *,
    organization_id: str | None = None,
) -> Iterator[None]:
    resource_digest = hashlib.sha256(resource_id.encode()).hexdigest()[:24]
    writer_id = f"activity:{_maintenance_instance_id}:{resource_digest}"
    try:
        workflow_id = activity.info().workflow_id
    except RuntimeError:
        workflow_id = None
    request_id = (
        locator_workflow_id("activity", project_id, resource_id) if project_id is not None else None
    )
    correlation_token = bind_log_correlation(
        LogCorrelation(
            request_id=request_id,
            operation_id=workflow_id,
            workflow_id=workflow_id,
        )
    )
    try:
        with writer_permit_scope(
            _maintenance_gate,
            environment_id=_maintenance_environment_id,
            writer_id=writer_id,
            writer_kind="temporal_activity",
        ):
            if project_id is None:
                yield
                return
            token = bind_request_context(
                RequestContext(
                    organization_id=organization_id,
                    project_id=project_id,
                    region_code=region_code,
                    subject_id="hc-data-worker",
                    request_id=request_id or "activity",
                    service_identity=True,
                )
            )
            try:
                yield
            finally:
                reset_request_context(token)
    finally:
        reset_log_correlation(correlation_token)


@contextmanager
def _media_attempt_scope(resource_id: str) -> Iterator[None]:
    resource_digest = hashlib.sha256(resource_id.encode()).hexdigest()[:24]
    with writer_permit_scope(
        _maintenance_gate,
        environment_id=_maintenance_environment_id,
        writer_id=f"aligned-media:{_maintenance_instance_id}:{resource_digest}",
        writer_kind="aligned_media_attempt",
    ):
        yield


def _heartbeat(stage: str, state: str) -> None:
    activity.heartbeat({"stage": stage, "state": state})


async def _with_heartbeats(
    stage: str,
    operation: Callable[[], _T],
    *,
    on_cancel: Callable[[], None] | None = None,
) -> _T:
    """Run a synchronous port outside the event loop and heartbeat until it returns."""

    _heartbeat(stage, "started")
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=_HEARTBEAT_INTERVAL_SECONDS)
            if task in done:
                result = task.result()
                if activity.is_cancelled():
                    raise asyncio.CancelledError
                _heartbeat(stage, "completed")
                return result
            _heartbeat(stage, "running")
    except asyncio.CancelledError:
        if on_cancel is not None:
            on_cancel()
        # Give the synchronous adapter a bounded interval to kill FFmpeg and
        # remove its keyed staging directory before acknowledging cancellation.
        with suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        raise


async def _invoke(
    stage: str,
    operation: Callable[[], _T],
    *,
    on_cancel: Callable[[], None] | None = None,
) -> _T:
    try:
        return await _with_heartbeats(stage, operation, on_cancel=on_cancel)
    except ApplicationError:
        WORKFLOW_FAILURES.labels(
            workflow_kind=stage,
            error_code="APPLICATION_ERROR",
        ).inc()
        raise
    except ProblemException as exc:
        details = exc.problem
        raise ApplicationError(
            details.detail,
            details.model_dump(mode="json"),
            type=details.code,
            non_retryable=not details.retryable,
        ) from exc

    except WorkflowPortNotConfigured as exc:
        raise ApplicationError(str(exc), type=exc.code, non_retryable=True) from exc
    except AutomaticAnnotationBlocked as exc:
        raise ApplicationError(
            str(exc),
            type=exc.code,
            non_retryable=False,
        ) from exc
    except MaintenanceContractError as exc:
        raise ApplicationError(
            "The environment is read-only for maintenance.",
            type=exc.code,
            non_retryable=False,
        ) from exc
    except (CatalogConflictError, SchemaIncompatibleError) as exc:
        raise ApplicationError(
            str(exc),
            type=exc.code,
            non_retryable=True,
        ) from exc
    except (KeyError, TypeError, ValidationError, ValueError) as exc:
        raise ApplicationError(
            str(exc),
            type="VALIDATION_FAILED",
            non_retryable=True,
        ) from exc


@activity.defn(name=PERSIST_WORKFLOW_JOB_ACTIVITY)
async def persist_workflow_job(
    request: WorkflowJobPersistenceActivityInput,
) -> JobRecord:
    job = request.job
    with _worker_scope(
        job.project_id,
        request.region_code,
        job.resource_id,
        organization_id=request.organization_id,
    ):
        try:
            await asyncio.to_thread(
                lambda: _require(
                    _dependencies.workflow_jobs,
                    "workflow.WorkflowJobPersistencePort",
                ).put_job(request)
            )
        except WorkflowPortNotConfigured as exc:
            raise ApplicationError(str(exc), type=exc.code, non_retryable=True) from exc
        except (TypeError, ValueError) as exc:
            raise ApplicationError(
                str(exc),
                type="VALIDATION_FAILED",
                non_retryable=True,
            ) from exc
    return job


@activity.defn(name=UPDATE_CONTINUOUS_EPISODE_STATE_ACTIVITY)
async def update_continuous_episode_state(
    request: ContinuousEpisodeStateActivityInput,
) -> ContinuousEpisodeStateActivityOutput:
    with _worker_scope(
        request.project_id,
        request.region_code,
        f"{request.recording_id}/{request.episode_id}",
        organization_id=request.organization_id,
    ):
        return await _invoke(
            "continuous_episode_state",
            lambda: _require(
                _dependencies.continuous_episode_processing,
                "continuous_recordings.ContinuousEpisodeProcessingService",
            ).update_state(request),
        )


@activity.defn(name=QC_CONTINUOUS_EPISODE_ACTIVITY)
async def qc_continuous_episode(
    request: ContinuousEpisodeWorkflowInput,
) -> ContinuousEpisodeQcActivityOutput:
    source = request.projection
    with _worker_scope(
        source.project_id,
        source.region_code,
        source.rollout_id,
        organization_id=source.organization_id,
    ):
        return await _invoke(
            "continuous_episode_qc",
            lambda: _require(
                _dependencies.continuous_episode_processing,
                "continuous_recordings.ContinuousEpisodeProcessingService",
            ).qc(request),
        )


@activity.defn(name=ALIGN_CONTINUOUS_EPISODE_ACTIVITY)
async def align_continuous_episode(
    request: ContinuousEpisodeWorkflowInput,
) -> ContinuousEpisodeAlignmentActivityOutput:
    source = request.projection
    with _worker_scope(
        source.project_id,
        source.region_code,
        source.rollout_id,
        organization_id=source.organization_id,
    ):
        return await _invoke(
            "continuous_episode_alignment",
            lambda: _require(
                _dependencies.continuous_episode_processing,
                "continuous_recordings.ContinuousEpisodeProcessingService",
            ).align(request),
        )


@activity.defn(name=COMMIT_CONTINUOUS_EPISODE_BUNDLE_ACTIVITY)
async def commit_continuous_episode_bundle(
    request: ContinuousEpisodeBundleCommitActivityInput,
) -> AlignedBundleCommitActivityOutput:
    source = request.workflow_input.projection
    with _worker_scope(
        source.project_id,
        source.region_code,
        source.rollout_id,
        organization_id=source.organization_id,
    ):
        return await _invoke(
            "continuous_episode_commit",
            lambda: _require(
                _dependencies.continuous_episode_processing,
                "continuous_recordings.ContinuousEpisodeProcessingService",
            ).commit_bundle(
                workflow_input=request.workflow_input,
                alignment=request.alignment,
                staged_manifest=request.staged_manifest,
                alignment_staging=request.alignment_staging,
                expected_dataset_version=request.expected_dataset_version,
                expected_camera_ids=request.expected_camera_ids,
                media_artifacts=request.media_artifacts,
            ),
        )


@activity.defn(name=PARSE_MANIFEST_ACTIVITY)
async def parse_manifest(request: ManifestActivityInput) -> ManifestActivityOutput:
    def parse_and_validate() -> ManifestActivityOutput:
        result = _require(
            _dependencies.manifest_parser,
            "ingest.ManifestParserPort",
        ).parse(request.manifest_key)
        manifest = result.manifest
        if (
            manifest.project_id != request.project_id
            or manifest.rollout_id != request.rollout_id
            or manifest.data_package_id != request.data_package_id
            or manifest.sha256 != request.source_sha256
            or result.manifest_fingerprint != request.manifest_fingerprint
        ):
            raise ValueError("Manifest immutable identity does not match workflow input")
        return ManifestActivityOutput(preflight=result)

    with _worker_scope(request.project_id, request.region_code, request.data_package_id):
        return await _invoke("manifest", parse_and_validate)


@activity.defn(name=VERIFY_RAW_ACTIVITY)
async def verify_raw(request: VerificationActivityInput) -> VerificationActivityOutput:
    def verify_and_persist() -> RawVerificationReportV1:
        report = _require(_dependencies.verifier, "verification.RawVerificationPort").verify(
            rollout_id=request.rollout_id,
            object_key=request.object_key,
            source_sha256=request.source_sha256,
            required_topics=set(request.required_topics),
            known_optional_topics=set(request.known_optional_topics),
        )
        if (
            report.rollout_id != request.rollout_id
            or report.object_key != request.object_key
            or report.source_sha256 != request.source_sha256
        ):
            raise ValueError("Raw verification report lineage does not match activity input")
        if _dependencies.verification_reports is not None:
            _dependencies.verification_reports.put_report(
                project_id=request.project_id,
                region_code=request.region_code,
                report=report,
            )
        return report

    with _worker_scope(
        request.project_id,
        request.region_code,
        request.rollout_id,
        organization_id=request.organization_id,
    ):
        report = await _invoke("verification", verify_and_persist)
    return VerificationActivityOutput(report=report)


@activity.defn(name=PROCESS_INGEST_SOURCE_ACTIVITY)
async def process_ingest_source(
    request: IngestSourceProcessingActivityInput,
) -> IngestSourceProcessingActivityOutput:
    """Use one OSS Raw download and no object-store Projection Arrow."""

    source = _require(request.quality.source, "workflow.IngestProjectionSourceV1")
    cancel_event = Event()

    def cancelled() -> bool:
        return cancel_event.is_set() or activity.is_cancelled()

    def stop_if_cancelled() -> None:
        # A short event wait closes the race between an accepted Temporal
        # cancellation and completion of the current synchronous callback.
        if cancel_event.wait(0.2) or cancelled():
            raise asyncio.CancelledError

    def process() -> IngestSourceProcessingActivityOutput:
        projection = _require(
            _dependencies.ingest_projection,
            "workflow.IngestProjectionPort",
        )
        verifier = _require(_dependencies.verifier, "verification.RawVerificationPort")
        if not isinstance(verifier, RawStreamVerificationPort):
            raise WorkflowPortNotConfigured(
                "combined ingest processing requires verification.RawStreamVerificationPort"
            )
        with ExitStack() as sessions:
            if source.lerobot is not None:
                pipeline = _require(_dependencies.lerobot_pipeline, "lerobot_pipeline")
                native_session = sessions.enter_context(pipeline.open_session(source))
                verification_report = native_session.verification_report
                session = native_session
            else:
                session = sessions.enter_context(projection.open_local_session(source))
                verification_report = verifier.verify_stream(
                    rollout_id=request.verification.rollout_id,
                    object_key=request.verification.object_key,
                    source_sha256=request.verification.source_sha256,
                    required_topics=set(request.verification.required_topics),
                    known_optional_topics=set(request.verification.known_optional_topics),
                    stream=session.open_reader(),
                )
            stop_if_cancelled()
            if (
                verification_report.rollout_id != request.verification.rollout_id
                or verification_report.object_key != request.verification.object_key
                or verification_report.source_sha256 != request.verification.source_sha256
            ):
                raise ValueError("Raw verification report lineage does not match activity input")
            if _dependencies.verification_reports is not None:
                _dependencies.verification_reports.put_report(
                    project_id=request.verification.project_id,
                    region_code=request.verification.region_code,
                    report=verification_report,
                )
            verification_output = VerificationActivityOutput(report=verification_report)
            if verification_report.status is VerificationStatus.REJECTED:
                return IngestSourceProcessingActivityOutput(
                    verification=verification_output,
                )

            quality_data = session.quality_data
            quality_report = _require(
                _dependencies.quality,
                "quality.QualityEvaluationPort",
            ).evaluate_stream(
                quality_data,
                session.quality_observations(),
                request.quality.profile,
            )
            stop_if_cancelled()
            if (
                quality_report.rollout_id != quality_data.rollout_id
                or quality_report.source_sha256 != quality_data.source_sha256
                or quality_report.profile_id != request.quality.profile.profile_id
                or quality_report.profile_version != request.quality.profile.profile_version
                or quality_report.profile_sha256 != request.quality.profile.content_sha256()
                or quality_report.engine_version != request.quality.profile.engine_version
                or quality_report.start_ns != quality_data.start_ns
                or quality_report.end_ns != quality_data.end_ns
            ):
                raise ValueError("Quality report lineage does not match activity input")
            if _dependencies.quality_reports is not None:
                if request.quality.project_id is None or request.quality.region_code is None:
                    raise ValueError("quality persistence requires project_id and region_code")
                _dependencies.quality_reports.put_report(
                    project_id=request.quality.project_id,
                    region_code=request.quality.region_code,
                    report=quality_report,
                )
            quality_output = QualityActivityOutput(report=quality_report)
            if quality_report.status is not QualityStatus.PASS:
                return IngestSourceProcessingActivityOutput(
                    verification=verification_output,
                    quality=quality_output,
                )

            alignment_data = session.alignment_data
            effective_request = request.alignment.model_copy(
                update={"data": alignment_data, "source": None}
            )
            alignment = _require(_dependencies.alignment, "alignment.AlignmentPort")
            writer = _require(
                _dependencies.fragment_writers,
                "alignment.FragmentWriterFactoryPort",
            ).create(effective_request)
            staged_manifest = alignment.align_stream_to_writer(
                rollout_id=alignment_data.rollout_id,
                source_sha256=alignment_data.source_sha256,
                attempt_id=alignment_data.attempt_id,
                start_ns=alignment_data.start_ns,
                end_ns=alignment_data.end_ns,
                stream_kinds={name: stream.kind for name, stream in alignment_data.streams.items()},
                samples=session.alignment_samples(),
                profile=request.alignment.profile,
                writer=writer,
            )
            stop_if_cancelled()
            if (
                staged_manifest.rollout_id != alignment_data.rollout_id
                or staged_manifest.source_sha256 != alignment_data.source_sha256
                or staged_manifest.attempt_id != alignment_data.attempt_id
                or staged_manifest.frequency_hz != request.alignment.profile.frequency_hz
            ):
                raise ValueError("Alignment manifest lineage does not match activity input")
            if _dependencies.alignment_manifests is not None:
                if request.alignment.region_code is None:
                    raise ValueError("alignment persistence requires region_code")
                _dependencies.alignment_manifests.put_ready_manifest(
                    project_id=request.alignment.project_id,
                    region_code=request.alignment.region_code,
                    manifest=staged_manifest,
                )
            original_videos = getattr(session, "original_videos", {})
            staging = _publish_alignment_staging(
                request.alignment,
                staged_manifest,
                camera_ids=tuple(
                    name
                    for name, stream in alignment_data.streams.items()
                    if stream.kind is ModalityKind.IMAGE and name not in original_videos
                ),
            )
            if original_videos:
                staging = staging.model_copy(update={"original_videos": original_videos})
            catalog = _require(_dependencies.catalog, "lance_catalog.LanceCatalogPort")
            current = catalog.current_version(
                request.alignment.dataset_id,
                project_id=request.alignment.project_id,
            )
            expected_version = 1 if current is None else current.version + 1
            alignment_output = AlignmentActivityOutput(
                staged_manifest=staged_manifest,
                alignment_staging=staging,
                expected_dataset_version=expected_version,
            )
            return IngestSourceProcessingActivityOutput(
                verification=verification_output,
                quality=quality_output,
                alignment=alignment_output,
                frame_selection=session.frame_selection(),
            )

    with _worker_scope(
        source.project_id,
        source.region_code,
        source.rollout_id,
        organization_id=source.organization_id,
    ):
        result = await _invoke(
            "ingest_source_processing",
            process,
            on_cancel=cancel_event.set,
        )
    if result.quality is not None:
        QC_OUTCOMES.labels(
            outcome=result.quality.report.status.value,
            profile_id=result.quality.report.profile_id,
        ).inc()
    return result


@activity.defn(name=MATERIALIZE_INGEST_PROJECTION_ACTIVITY)
async def materialize_ingest_projection(
    request: ProjectionMaterializationActivityInput,
) -> ProjectionMaterializationActivityOutput:
    source = request.source
    with _worker_scope(
        source.project_id,
        source.region_code,
        source.rollout_id,
        organization_id=source.organization_id,
    ):
        materialized = await _invoke(
            "projection_materialization",
            lambda: _require(
                _dependencies.ingest_projection,
                "workflow.IngestProjectionPort",
            ).materialize(source),
        )
    if (
        materialized.project_id != source.project_id
        or materialized.region_code != source.region_code
        or materialized.rollout_id != source.rollout_id
        or materialized.source_sha256 != source.source_sha256
        or materialized.materialization is None
    ):
        raise ValueError("materialized projection lineage does not match its source")
    return ProjectionMaterializationActivityOutput(source=materialized)


@activity.defn(name=CLEANUP_INGEST_PROJECTION_ACTIVITY)
async def cleanup_ingest_projection(
    request: ProjectionCleanupActivityInput,
) -> ProjectionCleanupActivityOutput:
    source = request.source
    if source.materialization is None:
        return ProjectionCleanupActivityOutput()
    with _worker_scope(
        source.project_id,
        source.region_code,
        source.rollout_id,
        organization_id=source.organization_id,
    ):
        await _invoke(
            "projection_cleanup",
            lambda: _require(
                _dependencies.ingest_projection,
                "workflow.IngestProjectionPort",
            ).cleanup(source),
        )
    return ProjectionCleanupActivityOutput()


@activity.defn(name=EVALUATE_QUALITY_ACTIVITY)
async def evaluate_quality(request: QualityActivityInput) -> QualityActivityOutput:
    def evaluate_and_persist() -> QcReportV1:
        data = request.data
        observations: Iterable[QualityStreamObservationV1] | None = None
        if data is None:
            source = _require(request.source, "workflow.IngestProjectionSourceV1")
            projection = _require(
                _dependencies.ingest_projection,
                "workflow.IngestProjectionPort",
            )
            if source.materialization is None:
                data = projection.project_quality(source)
            else:
                data, observations = projection.project_quality_stream(source)
            if data.rollout_id != source.rollout_id or data.source_sha256 != source.source_sha256:
                raise ValueError("projected quality data lineage does not match source")
        evaluator = _require(_dependencies.quality, "quality.QualityEvaluationPort")
        report = (
            evaluator.evaluate(data, request.profile)
            if observations is None
            else evaluator.evaluate_stream(data, observations, request.profile)
        )
        if (
            report.rollout_id != data.rollout_id
            or report.source_sha256 != data.source_sha256
            or report.profile_id != request.profile.profile_id
            or report.profile_version != request.profile.profile_version
            or report.profile_sha256 != request.profile.content_sha256()
            or report.engine_version != request.profile.engine_version
            or report.start_ns != data.start_ns
            or report.end_ns != data.end_ns
        ):
            raise ValueError("Quality report lineage does not match activity input")
        if _dependencies.quality_reports is not None:
            _dependencies.quality_reports.put_report(
                project_id=request.project_id,
                region_code=request.region_code,
                report=report,
            )
        return report

    lineage = request.data or request.source
    if lineage is None:
        raise ValueError("quality activity lineage is missing")
    with _worker_scope(
        request.project_id,
        request.region_code,
        lineage.rollout_id,
        organization_id=request.organization_id,
    ):
        report = await _invoke("quality", evaluate_and_persist)
    workflow_id = locator_workflow_id("ingest-rollout", request.project_id, lineage.rollout_id)
    QC_OUTCOMES.labels(
        outcome=report.status.value,
        profile_id=report.profile_id,
    ).inc()
    log_event(
        logger,
        logging.INFO,
        "ACTIVITY.QUALITY_COMPLETED",
        workflow_id=workflow_id,
    )
    return QualityActivityOutput(report=report)


@activity.defn(name=ALIGN_FRAGMENT_ACTIVITY)
async def align_fragment(request: AlignmentActivityInput) -> AlignmentActivityOutput:
    def align_and_stage() -> AlignmentActivityOutput:
        data = request.data
        effective_request = request
        stream_samples: Iterable[tuple[str, TimedSampleV1]] | None = None
        if data is None:
            source = _require(request.source, "workflow.IngestProjectionSourceV1")
            projection = _require(
                _dependencies.ingest_projection,
                "workflow.IngestProjectionPort",
            )
            if source.materialization is None:
                data = projection.project_alignment(source)
            else:
                data, stream_samples = projection.project_alignment_stream(source)
            if data.rollout_id != source.rollout_id or data.source_sha256 != source.source_sha256:
                raise ValueError("projected alignment data lineage does not match source")
            effective_request = request.model_copy(update={"data": data, "source": None})
        alignment = _require(_dependencies.alignment, "alignment.AlignmentPort")
        writers = _require(_dependencies.fragment_writers, "alignment.FragmentWriterFactoryPort")
        catalog = _require(_dependencies.catalog, "lance_catalog.LanceCatalogPort")
        writer = writers.create(effective_request)
        manifest = (
            alignment.align_to_writer(data, request.profile, writer)
            if stream_samples is None
            else alignment.align_stream_to_writer(
                rollout_id=data.rollout_id,
                source_sha256=data.source_sha256,
                attempt_id=data.attempt_id,
                start_ns=data.start_ns,
                end_ns=data.end_ns,
                stream_kinds={name: stream.kind for name, stream in data.streams.items()},
                samples=stream_samples,
                profile=request.profile,
                writer=writer,
            )
        )
        if (
            manifest.rollout_id != data.rollout_id
            or manifest.source_sha256 != data.source_sha256
            or manifest.attempt_id != data.attempt_id
            or manifest.frequency_hz != request.profile.frequency_hz
        ):
            raise ValueError("Alignment manifest lineage does not match activity input")
        if _dependencies.alignment_manifests is not None:
            _dependencies.alignment_manifests.put_ready_manifest(
                project_id=request.project_id,
                region_code=request.region_code,
                manifest=manifest,
            )
        staging = _publish_alignment_staging(
            request,
            manifest,
            camera_ids=tuple(
                name for name, stream in data.streams.items() if stream.kind is ModalityKind.IMAGE
            ),
        )
        current = catalog.current_version(request.dataset_id, project_id=request.project_id)
        expected_dataset_version = 1 if current is None else current.version + 1
        return AlignmentActivityOutput(
            staged_manifest=manifest,
            alignment_staging=staging,
            expected_dataset_version=expected_dataset_version,
        )

    lineage = request.data or request.source
    if lineage is None:
        raise ValueError("alignment activity lineage is missing")
    organization_id = request.source.organization_id if request.source is not None else None
    with _worker_scope(
        request.project_id,
        request.region_code,
        lineage.rollout_id,
        organization_id=organization_id,
    ):
        return await _invoke("alignment", align_and_stage)


def _publish_alignment_staging(
    request: AlignmentActivityInput,
    manifest: AlignedFragmentManifestV1,
    *,
    camera_ids: Sequence[str] = (),
) -> AlignmentStagingArtifactV1:
    store = _require(
        _dependencies.alignment_staging,
        "workflow.ProjectionArtifactStorePort",
    )
    parsed = urlparse(manifest.staging_uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ValueError("alignment writer must commit a local Arrow file")
    path = Path(unquote(parsed.path)).resolve()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    file_sha256 = digest.hexdigest()
    object_key = "/".join(
        (
            "staging/alignment",
            quote(request.organization_id, safe="-._~"),
            quote(request.project_id, safe="-._~"),
            quote(request.dataset_id, safe="-._~"),
            quote(manifest.rollout_id, safe="-._~"),
            quote(manifest.attempt_id, safe="-._~"),
            f"{file_sha256}.arrow",
        )
    )
    published_keys: list[str] = []
    camera_shards: dict[str, AlignmentCameraStagingArtifactV1] = {}
    try:
        size = store.publish_file(object_key, path, sha256=file_sha256)
        published_keys.append(object_key)
        shard_paths: dict[str, Path] = {}
        try:
            for camera_id in tuple(dict.fromkeys(camera_ids)):
                descriptor, shard_name = tempfile.mkstemp(
                    prefix="aligned-camera-",
                    suffix=".arrow.part",
                    dir=path.parent,
                )
                os.close(descriptor)
                shard_paths[camera_id] = Path(shard_name)
            _write_alignment_camera_shards(
                source_path=path,
                destinations=shard_paths,
                rollout_id=manifest.rollout_id,
                expected_rows=manifest.row_count,
            )
            for camera_id, shard_path in shard_paths.items():
                shard_sha256 = _file_sha256(shard_path)
                camera_component = hashlib.sha256(camera_id.encode()).hexdigest()[:24]
                shard_key = f"{object_key.removesuffix('.arrow')}/cameras/{camera_component}.arrow"
                shard_size = store.publish_file(
                    shard_key,
                    shard_path,
                    sha256=shard_sha256,
                )
                published_keys.append(shard_key)
                camera_shards[camera_id] = AlignmentCameraStagingArtifactV1(
                    object_key=shard_key,
                    content_sha256=shard_sha256,
                    size_bytes=shard_size,
                    row_count=manifest.row_count,
                )
        finally:
            for shard_path in shard_paths.values():
                with suppress(OSError):
                    shard_path.unlink()
    except Exception:
        for published_key in reversed(published_keys):
            with suppress(Exception):
                store.delete(published_key)
        raise
    finally:
        with suppress(OSError):
            path.unlink()
        with suppress(OSError):
            path.parent.rmdir()
    now = datetime.now(timezone.utc)
    return AlignmentStagingArtifactV1(
        object_key=object_key,
        content_sha256=file_sha256,
        size_bytes=size,
        row_count=manifest.row_count,
        alignment_version=f"{manifest.converter_version}:{manifest.profile_id}",
        camera_shards=camera_shards,
        created_at=now,
        expires_at=now + _dependencies.alignment_staging_ttl,
    )


def _write_alignment_camera_shards(
    *,
    source_path: Path,
    destinations: Mapping[str, Path],
    rollout_id: str,
    expected_rows: int,
) -> None:
    """Scan alignment Arrow once and fan out bounded camera-only shards."""

    try:
        import pyarrow as pa
        import pyarrow.ipc as ipc
    except ImportError as exc:
        raise RuntimeError("alignment camera sharding requires PyArrow") from exc
    from hc_data_platform.alignment.canonical import denormalize_from_json
    from hc_data_platform.alignment.models import AlignedValueV1

    if not destinations:
        return
    states: dict[str, dict[str, Any]] = {}
    for camera_id, destination_path in destinations.items():
        if not camera_id:
            raise ValueError("camera shard identity must be non-empty")
        schema = pa.schema(
            [
                pa.field("rollout_id", pa.string(), nullable=False),
                pa.field("step_index", pa.int64(), nullable=False),
                pa.field("timestamp_ns", pa.int64(), nullable=False),
                pa.field("image", pa.binary()),
                pa.field("valid", pa.bool_(), nullable=False),
                pa.field("repeated", pa.bool_(), nullable=False),
            ],
            metadata={
                b"hc.schema": b"aligned-camera/v1",
                b"hc.camera_id": camera_id.encode(),
            },
        )
        sink = pa.OSFile(str(destination_path), "wb")
        states[camera_id] = {
            "schema": schema,
            "sink": sink,
            "writer": ipc.new_file(sink, schema),
            "buffers": {field.name: [] for field in schema},
        }

    buffered_bytes = 0
    buffered_rows = 0
    observed = 0

    def flush() -> None:
        nonlocal buffered_bytes, buffered_rows
        if buffered_rows == 0:
            return
        for state in states.values():
            schema = state["schema"]
            buffers = state["buffers"]
            batch = pa.record_batch(
                [buffers[field.name] for field in schema],
                schema=schema,
            )
            state["writer"].write_batch(batch)
            del batch
            state["buffers"] = {field.name: [] for field in schema}
        buffered_bytes = 0
        buffered_rows = 0
        pa.default_memory_pool().release_unused()

    try:
        with pa.memory_map(str(source_path), "r") as source:
            reader = ipc.open_file(source)
            for batch_index in range(reader.num_record_batches):
                batch = reader.get_batch(batch_index)
                rollouts = batch.column("rollout_id")
                indexes = batch.column("step_index")
                timestamps = batch.column("timestamp_ns")
                modalities = batch.column("modalities_json")
                for row_index in range(batch.num_rows):
                    row_rollout = str(rollouts[row_index].as_py())
                    step_index = int(indexes[row_index].as_py())
                    if row_rollout != rollout_id or step_index != observed:
                        raise ValueError("aligned staging rows are not contiguous for camera shard")
                    encoded = modalities[row_index].as_py()
                    payload = denormalize_from_json(json.loads(bytes(encoded).decode("utf-8")))
                    if not isinstance(payload, dict):
                        raise ValueError("aligned staging modalities must be an object")
                    projected: dict[str, tuple[AlignedValueV1, bytes | None]] = {}
                    row_bytes = 0
                    for camera_id in states:
                        if camera_id not in payload:
                            raise ValueError(f"camera {camera_id!r} is absent from aligned staging")
                        aligned = AlignedValueV1.model_validate(payload[camera_id])
                        image = aligned.value if isinstance(aligned.value, bytes) else None
                        projected[camera_id] = aligned, image
                        row_bytes += 0 if image is None else len(image)
                    if buffered_rows and buffered_bytes + row_bytes > 16 * 1024 * 1024:
                        flush()
                    timestamp_ns = int(timestamps[row_index].as_py())
                    for camera_id, (aligned, image) in projected.items():
                        buffers = states[camera_id]["buffers"]
                        buffers["rollout_id"].append(row_rollout)
                        buffers["step_index"].append(step_index)
                        buffers["timestamp_ns"].append(timestamp_ns)
                        buffers["image"].append(image)
                        buffers["valid"].append(aligned.valid and image is not None)
                        buffers["repeated"].append(aligned.repeated)
                    buffered_bytes += row_bytes
                    buffered_rows += 1
                    observed += 1
                    if buffered_rows >= 2_048 or buffered_bytes >= 16 * 1024 * 1024:
                        flush()
                del batch
        if observed != expected_rows:
            raise ValueError("camera shard row count does not match alignment manifest")
        flush()
        for state in states.values():
            state["writer"].close()
            state["sink"].close()
    except Exception:
        for state in states.values():
            with suppress(Exception):
                state["writer"].close()
            with suppress(Exception):
                state["sink"].close()
        raise


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@activity.defn(name=COMMIT_ALIGNED_BUNDLE_ACTIVITY)
async def commit_aligned_bundle(
    request: AlignedBundleCommitActivityInput,
) -> AlignedBundleCommitActivityOutput:
    def commit() -> AlignedBundleCommitActivityOutput:
        alignment_request = request.alignment
        source = _require(alignment_request.source, "workflow.IngestProjectionSourceV1")
        if any(
            artifact.status.value != "READY"
            or artifact.dataset_id != alignment_request.dataset_id
            or artifact.rollout_id != source.rollout_id
            or artifact.dataset_version != request.expected_dataset_version
            or artifact.frame_count != request.staged_manifest.row_count
            or artifact.alignment_version != request.alignment_staging.alignment_version
            for artifact in request.media_artifacts
        ):
            raise ValueError("aligned media bundle is incomplete or has mismatched lineage")
        camera_ids = [artifact.camera_id for artifact in request.media_artifacts]
        if len(camera_ids) != len(set(camera_ids)):
            raise ValueError("aligned media bundle contains duplicate cameras")
        if request.expected_camera_ids and (
            len(request.expected_camera_ids) != len(set(request.expected_camera_ids))
            or set(camera_ids) != set(request.expected_camera_ids)
        ):
            raise ValueError("aligned media bundle does not cover the manifest cameras")
        catalog = _require(_dependencies.catalog, "lance_catalog.LanceCatalogPort")
        current = catalog.current_version(
            alignment_request.dataset_id,
            project_id=alignment_request.project_id,
        )
        actual_next = 1 if current is None else current.version + 1
        recovering_same_rollout = (
            current is not None
            and current.version == request.expected_dataset_version
            and source.rollout_id in current.committed_rollouts
        )
        if actual_next != request.expected_dataset_version and not recovering_same_rollout:
            raise ApplicationError(
                "reserved Dataset version changed before aligned media commit",
                type="ALIGNED_MEDIA_VERSION_CONFLICT",
                non_retryable=True,
            )
        scope = AlignedMediaScopeV1(
            organization_id=_require(
                source.organization_id,
                "workflow.IngestProjectionSourceV1.organization_id",
            ),
            project_id=alignment_request.project_id,
            region_code=_require(alignment_request.region_code, "alignment.region_code"),
        )
        artifact_ids = tuple(artifact.artifact_id for artifact in request.media_artifacts)
        repository = _require(
            _dependencies.aligned_media_repository,
            "aligned_media.AlignedMediaRepositoryPort",
        )
        now = datetime.now(timezone.utc)
        repository.begin_dataset_commit(
            scope=scope,
            artifact_ids=artifact_ids,
            lease_expires_at=now + timedelta(hours=6),
            now=now,
        )
        store = _require(
            _dependencies.alignment_staging,
            "workflow.ProjectionArtifactStorePort",
        )
        adapter = _require(
            _dependencies.catalog_fragments,
            "workflow.CatalogFragmentAdapterPort",
        )
        with store.local_file(
            request.alignment_staging.object_key,
            expected_sha256=request.alignment_staging.content_sha256,
            expected_size=request.alignment_staging.size_bytes,
        ) as path:
            local_manifest = request.staged_manifest.model_copy(
                update={"staging_uri": path.resolve().as_uri()}
            )
            catalog_manifest, steps = adapter.prepare_streaming(
                alignment_request,
                local_manifest,
                request.media_artifacts,
            )
            version, ready = catalog.commit_fragment(catalog_manifest, steps)
        if version.version != request.expected_dataset_version:
            raise RuntimeError("Lance committed a Dataset version different from its media key")
        repository.mark_dataset_committed(
            scope=scope,
            artifact_ids=artifact_ids,
            now=datetime.now(timezone.utc),
        )
        projection = _require(
            _dependencies.ingest_projection,
            "workflow.IngestProjectionPort",
        )
        alignment = (
            _require(_dependencies.lerobot_pipeline, "lerobot_pipeline").alignment_metadata(
                source,
                dataset_id=ready.dataset_id,
                dataset_version=ready.dataset_version,
            )
            if source.lerobot is not None
            else projection.project_alignment_metadata(source)
        )
        viewer_target = _require(
            _dependencies.dataset_ingest_projection,
            "workflow.DatasetIngestProjectionPort",
        ).project(
            source=source,
            alignment=alignment,
            schema_snapshot_id=alignment_request.schema_snapshot_id,
            frequency_hz=alignment_request.profile.frequency_hz,
            version=version,
            ready=ready,
            media_artifacts=request.media_artifacts,
        )
        LANCE_COMMITS.labels(outcome="success").inc()
        return AlignedBundleCommitActivityOutput(
            version=version,
            derived_ready=ready,
            viewer_target=viewer_target,
        )

    source = _require(request.alignment.source, "workflow.IngestProjectionSourceV1")
    with _worker_scope(
        request.alignment.project_id,
        request.alignment.region_code,
        source.rollout_id,
        organization_id=source.organization_id,
    ):
        return await _invoke("aligned_bundle_commit", commit)


@activity.defn(name=COMMIT_FRAGMENT_ACTIVITY)
async def commit_fragment(request: CatalogCommitActivityInput) -> CatalogCommitActivityOutput:
    def commit() -> CatalogCommitActivityOutput:
        catalog = _require(_dependencies.catalog, "lance_catalog.LanceCatalogPort")
        version, ready = catalog.commit_fragment(
            request.fragment.manifest,
            request.fragment.steps,
        )
        LANCE_COMMITS.labels(
            outcome="success",
        ).inc()
        return CatalogCommitActivityOutput(version=version, derived_ready=ready)

    manifest = request.fragment.manifest
    with _worker_scope(manifest.project_id, None, manifest.dataset_id):
        return await _invoke("lance_commit", commit)


@activity.defn(name=CREATE_ANNOTATION_TASK_ACTIVITY)
async def create_annotation_task(
    request: AutomaticAnnotationActivityInput,
) -> AutomaticAnnotationActivityOutput:
    if request.organization_id is None:
        raise ApplicationError(
            "automatic annotation activity requires an organization scope",
            type="ORGANIZATION_SCOPE_MISSING",
            non_retryable=True,
        )

    def create() -> AutomaticAnnotationActivityOutput:
        task = _require(
            _dependencies.annotation_tasks,
            "annotation.AutomaticAnnotationTaskService",
        ).ensure_task(
            AutomaticAnnotationRequest(
                project_id=request.project_id,
                region_code=request.region_code,
                rollout_id=request.rollout_id,
                dataset_id=request.dataset_id,
                dataset_version=request.dataset_version,
                lance_version=request.lance_version,
                dataset_schema_snapshot_id=request.dataset_schema_snapshot_id,
                base_step_count=request.base_step_count,
                source_workflow_id=request.source_workflow_id,
                task_kind=request.task_kind,
                frame_selection=request.frame_selection,
            )
        )
        return AutomaticAnnotationActivityOutput(task_id=task.task_id)

    with _worker_scope(
        request.project_id,
        request.region_code,
        request.rollout_id,
        organization_id=request.organization_id,
    ):
        return await _invoke("annotation_task", create)


@activity.defn(name=CREATE_ALIGNED_MEDIA_ACTIVITY)
async def create_aligned_media(
    request: AlignedMediaActivityInput,
) -> AlignedMediaActivityOutput:
    media_request = request.request
    with _worker_scope(
        media_request.project_id,
        request.region_code,
        f"{media_request.dataset_id}/{media_request.rollout_id}/{media_request.camera_id}",
        organization_id=request.organization_id,
    ):
        while True:
            try:
                await asyncio.wait_for(_MEDIA_ACTIVITY_SLOTS.acquire(), timeout=10)
                break
            except TimeoutError:
                _heartbeat("aligned_media", "queued")
        try:
            cancel_event = Event()
            with _media_attempt_scope(
                f"{media_request.dataset_id}/{media_request.rollout_id}/{media_request.camera_id}"
            ):
                artifact = await _invoke(
                    "aligned_media",
                    lambda: _require(
                        _dependencies.aligned_media,
                        "aligned_media.AlignedMediaGenerationService",
                    ).generate(
                        AlignedMediaScopeV1(
                            organization_id=request.organization_id,
                            project_id=media_request.project_id,
                            region_code=request.region_code,
                        ),
                        media_request,
                        cancelled=cancel_event.is_set,
                    ),
                    on_cancel=cancel_event.set,
                )
        finally:
            _MEDIA_ACTIVITY_SLOTS.release()
    return AlignedMediaActivityOutput(artifact=artifact)


@activity.defn(name=CLEANUP_UNCOMMITTED_ALIGNED_MEDIA_ACTIVITY)
async def cleanup_uncommitted_aligned_media(
    request: AlignedMediaCleanupActivityInput,
) -> AlignedMediaCleanupActivityOutput:
    scope = AlignedMediaScopeV1(
        organization_id=request.organization_id,
        project_id=request.project_id,
        region_code=request.region_code,
    )
    artifact_ids = tuple(artifact.artifact_id for artifact in request.artifacts)

    def cleanup() -> AlignedMediaCleanupActivityOutput:
        repository = _require(
            _dependencies.aligned_media_repository,
            "aligned_media.AlignedMediaRepositoryPort",
        )
        store = _require(
            _dependencies.aligned_media_store,
            "aligned_media.AlignedMediaArtifactStorePort",
        )
        identities = {
            (artifact.dataset_id, artifact.dataset_version, artifact.rollout_id)
            for artifact in request.artifacts
        }
        if len(identities) != 1:
            raise ValueError("aligned media cleanup requires one Dataset-version rollout")
        dataset_id, dataset_version, rollout_id = identities.pop()
        current = _require(
            _dependencies.catalog,
            "lance_catalog.LanceCatalogPort",
        ).current_version(dataset_id, project_id=request.project_id)
        if (
            current is not None
            and current.version >= dataset_version
            and rollout_id in current.committed_rollouts
        ):
            # The cross-system commit won the race. Repair the PostgreSQL marker
            # instead of deleting MP4s already referenced by the visible Lance version.
            repository.mark_dataset_committed(
                scope=scope,
                artifact_ids=artifact_ids,
                now=datetime.now(timezone.utc),
            )
            return AlignedMediaCleanupActivityOutput(deleted_bytes=0)
        objects = repository.begin_abandon_uncommitted(
            scope=scope,
            artifact_ids=artifact_ids,
            error_code="ALIGNED_MEDIA_BUNDLE_ABORTED",
            now=datetime.now(timezone.utc),
        )
        deleted = store.delete_exact(objects)
        repository.complete_abandon_uncommitted(
            scope=scope,
            artifact_ids=artifact_ids,
            now=datetime.now(timezone.utc),
        )
        return AlignedMediaCleanupActivityOutput(deleted_bytes=deleted)

    with _worker_scope(
        request.project_id,
        request.region_code,
        "aligned-media-bundle-cleanup",
        organization_id=request.organization_id,
    ):
        return await _invoke("aligned_media_bundle_cleanup", cleanup)


@activity.defn(name=CLEANUP_ALIGNMENT_STAGING_ACTIVITY)
async def cleanup_alignment_staging(
    request: AlignmentStagingCleanupActivityInput,
) -> AlignmentStagingCleanupActivityOutput:
    def cleanup() -> AlignmentStagingCleanupActivityOutput:
        store = _require(
            _dependencies.alignment_staging,
            "workflow.ProjectionArtifactStorePort",
        )
        for shard in request.staging.camera_shards.values():
            store.delete(shard.object_key)
        store.delete(request.staging.object_key)
        return AlignmentStagingCleanupActivityOutput()

    with _worker_scope(
        request.project_id,
        request.region_code,
        request.staging.object_key,
        organization_id=request.organization_id,
    ):
        return await _invoke("alignment_staging_cleanup", cleanup)


@activity.defn(name=PUBLISH_DATASET_ACTIVITY)
async def publish_dataset(request: PublishActivityInput) -> PublishActivityOutput:
    publish_request = request.request
    with _worker_scope(
        publish_request.project_id,
        None,
        f"{publish_request.dataset_id}/{publish_request.dataset_version}",
    ):
        manifest = await _invoke(
            "publishing",
            lambda: _require(_dependencies.publisher, "publishing.DatasetPublisher").publish(
                publish_request
            ),
        )
    return PublishActivityOutput(manifest=manifest)


@activity.defn(name=EXPORT_DATASET_ACTIVITY)
async def export_dataset(request: ExportActivityInput) -> ExportActivityOutput:
    manifest = request.manifest
    with _worker_scope(
        manifest.project_id,
        None,
        f"{manifest.dataset_id}/{manifest.dataset_version}",
    ):
        result = await _invoke(
            "export",
            lambda: _require(_dependencies.exporter, "publishing.ExportCoordinator").export(
                manifest,
                format=request.format,
                attempt_id=request.attempt_id,
            ),
        )
    return ExportActivityOutput(result=result)


@activity.defn(name=PREFLIGHT_EXPORT_ACTIVITY)
async def preflight_export(
    request: ExportPreflightActivityInput,
) -> ExportPreflightActivityOutput:
    manifest = request.manifest
    with _worker_scope(
        manifest.project_id,
        None,
        f"{manifest.dataset_id}/{manifest.dataset_version}",
    ):
        row_count = await _invoke(
            "export_preflight",
            lambda: _require(_dependencies.exporter, "publishing.ExportCoordinator").preflight(
                manifest, format=request.format
            ),
        )
    return ExportPreflightActivityOutput(row_count=row_count)


@activity.defn(name=VERIFY_EXPORT_ARTIFACT_ACTIVITY)
async def verify_export_artifact(
    request: ExportArtifactVerificationActivityInput,
) -> ExportArtifactVerificationActivityOutput:
    result = request.result
    with _worker_scope(
        result.project_id,
        None,
        f"{result.dataset_id}/{result.dataset_version}",
    ):
        await _invoke(
            "export_artifact_verification",
            lambda: _require(
                _dependencies.exporter, "publishing.ExportCoordinator"
            ).verify_artifact(result),
        )
    return ExportArtifactVerificationActivityOutput()


@activity.defn(name=RECONCILE_CATALOG_ACTIVITY)
async def reconcile_catalog(
    request: CatalogReconciliationActivityInput,
) -> CatalogReconciliationActivityOutput:
    with _worker_scope(request.project_id, None, request.dataset_id):
        versions = await _invoke(
            "catalog_reconciliation",
            lambda: _require(_dependencies.catalog_reconciler, "lance_catalog.reconcile").reconcile(
                request.dataset_id, project_id=request.project_id
            ),
        )
    return CatalogReconciliationActivityOutput(
        repaired_versions=tuple(versions),
    )


@activity.defn(name=RECONCILE_PUBLICATION_ACTIVITY)
async def reconcile_publication(
    request: PublishReconciliationActivityInput,
) -> PublishReconciliationActivityOutput:
    reconciler = _dependencies.publication_reconciler
    publisher = _dependencies.publisher
    if reconciler is None and publisher is None:
        raise ApplicationError(
            "neither the publication reconciler nor DatasetPublisher is configured",
            type="WORKFLOW_PORT_NOT_CONFIGURED",
            non_retryable=True,
        )
    publish_request = request.request
    with _worker_scope(
        publish_request.project_id,
        None,
        f"{publish_request.dataset_id}/{publish_request.dataset_version}",
    ):
        manifest = await _invoke(
            "publication_reconciliation",
            lambda: (
                reconciler.reconcile(publish_request)
                if reconciler is not None
                else _require(publisher, "publishing.DatasetPublisher").publish(publish_request)
            ),
        )
    return PublishReconciliationActivityOutput(manifest=manifest)


ALL_ACTIVITIES = (
    persist_workflow_job,
    update_continuous_episode_state,
    qc_continuous_episode,
    align_continuous_episode,
    commit_continuous_episode_bundle,
    parse_manifest,
    verify_raw,
    process_ingest_source,
    materialize_ingest_projection,
    cleanup_ingest_projection,
    evaluate_quality,
    align_fragment,
    commit_fragment,
    commit_aligned_bundle,
    create_annotation_task,
    create_aligned_media,
    cleanup_uncommitted_aligned_media,
    cleanup_alignment_staging,
    publish_dataset,
    preflight_export,
    export_dataset,
    verify_export_artifact,
    reconcile_catalog,
    reconcile_publication,
)
