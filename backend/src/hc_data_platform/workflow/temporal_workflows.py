"""Replay-safe Temporal workflow definitions for the data pipeline."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar, cast

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    CancelledError,
    ChildWorkflowError,
)
from temporalio.workflow import ActivityCancellationType

with workflow.unsafe.imports_passed_through():
    from hc_data_platform.aligned_media.models import (
        AlignedMediaArtifactV1,
        AlignedMediaGenerationRequestV1,
        AlignedMediaMp4SourceV1,
        AlignmentStagingArtifactV1,
    )
    from hc_data_platform.annotation.models import AnnotationSubmission
    from hc_data_platform.annotation.validation import (
        TagValidationIssue,
        revision_content_hash,
        validate_tag_revision,
    )
    from hc_data_platform.continuous_recordings.asset_models import (
        EpisodeProcessingStatus,
        RecordingAssetRole,
    )
    from hc_data_platform.quality.models import QualityStatus
    from hc_data_platform.verification.models import VerificationStatus

    from .models import (
        AlignedBundleCommitActivityInput,
        AlignedBundleCommitActivityOutput,
        AlignedMediaActivityInput,
        AlignedMediaActivityOutput,
        AlignedMediaCleanupActivityInput,
        AlignedMediaCleanupActivityOutput,
        AlignmentActivityOutput,
        AlignmentStagingCleanupActivityInput,
        AlignmentStagingCleanupActivityOutput,
        AnnotationReviewPreparationWorkflowInput,
        AutomaticAnnotationActivityInput,
        AutomaticAnnotationActivityOutput,
        CatalogCommitActivityOutput,
        CatalogReconciliationActivityInput,
        CatalogReconciliationActivityOutput,
        CatalogReconciliationWorkflowInput,
        ContinuousEpisodeAlignmentActivityOutput,
        ContinuousEpisodeBundleCommitActivityInput,
        ContinuousEpisodeQcActivityOutput,
        ContinuousEpisodeStateActivityInput,
        ContinuousEpisodeStateActivityOutput,
        ContinuousEpisodeWorkflowInput,
        DatasetWriterWorkflowInput,
        ExportActivityInput,
        ExportActivityOutput,
        ExportArtifactVerificationActivityInput,
        ExportArtifactVerificationActivityOutput,
        ExportPreflightActivityInput,
        ExportPreflightActivityOutput,
        ExportWorkflowInput,
        FrameSelectionManifestRefV1,
        IngestRolloutWorkflowInput,
        IngestProjectionSourceV1,
        IngestSourceProcessingActivityInput,
        IngestSourceProcessingActivityOutput,
        JobRecord,
        JobStatus,
        ManifestActivityOutput,
        LegacyAutomaticAnnotationActivityInput,
        LegacyExportActivityInput,
        ProjectionCleanupActivityInput,
        ProjectionCleanupActivityOutput,
        ProjectionMaterializationActivityInput,
        ProjectionMaterializationActivityOutput,
        PublishActivityInput,
        PublishActivityOutput,
        PublishDatasetWorkflowInput,
        PublishReconciliationActivityInput,
        PublishReconciliationActivityOutput,
        PublishReconciliationWorkflowInput,
        QualityActivityOutput,
        VerificationActivityOutput,
        WorkflowJobPersistenceActivityInput,
    )
    from .names import (
        ALIGN_CONTINUOUS_EPISODE_ACTIVITY,
        ALIGN_FRAGMENT_ACTIVITY,
        ANNOTATION_REVIEW_PREPARATION_WORKFLOW,
        CATALOG_RECONCILIATION_WORKFLOW,
        CLEANUP_ALIGNMENT_STAGING_ACTIVITY,
        CLEANUP_INGEST_PROJECTION_ACTIVITY,
        CLEANUP_UNCOMMITTED_ALIGNED_MEDIA_ACTIVITY,
        COMMIT_ALIGNED_BUNDLE_ACTIVITY,
        COMMIT_CONTINUOUS_EPISODE_BUNDLE_ACTIVITY,
        COMMIT_FRAGMENT_ACTIVITY,
        CONTINUOUS_EPISODE_WORKFLOW,
        CREATE_ALIGNED_MEDIA_ACTIVITY,
        CREATE_ANNOTATION_TASK_ACTIVITY,
        DATASET_WRITER_WORKFLOW,
        EXPORT_DATASET_ACTIVITY,
        EXPORT_WORKFLOW,
        EVALUATE_QUALITY_ACTIVITY,
        INGEST_ROLLOUT_WORKFLOW,
        MATERIALIZE_INGEST_PROJECTION_ACTIVITY,
        PARSE_MANIFEST_ACTIVITY,
        PERSIST_WORKFLOW_JOB_ACTIVITY,
        PREFLIGHT_EXPORT_ACTIVITY,
        PROCESS_INGEST_SOURCE_ACTIVITY,
        PUBLISH_DATASET_ACTIVITY,
        PUBLISH_DATASET_WORKFLOW,
        PUBLISH_RECONCILIATION_WORKFLOW,
        QC_CONTINUOUS_EPISODE_ACTIVITY,
        RECONCILE_CATALOG_ACTIVITY,
        RECONCILE_PUBLICATION_ACTIVITY,
        UPDATE_CONTINUOUS_EPISODE_STATE_ACTIVITY,
        VERIFY_EXPORT_ARTIFACT_ACTIVITY,
        VERIFY_RAW_ACTIVITY,
    )

_ResultT = TypeVar("_ResultT")
NON_RETRYABLE_ERROR_TYPES = (
    "WORKFLOW_PORT_NOT_CONFIGURED",
    "VALIDATION_FAILED",
    "RAW_VERIFICATION_REJECTED",
    "LANCE_CATALOG_CONFLICT",
    "LANCE_SCHEMA_INCOMPATIBLE",
    "NO_ELIGIBLE_ROLLOUTS",
    "DATASET_VERSION_IMMUTABLE",
    "EXPORT_FORMAT_DISABLED",
    "EXPORT_SOURCE_INCOMPLETE",
)

ACTIVITY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=8,
    non_retryable_error_types=NON_RETRYABLE_ERROR_TYPES,
)


@dataclass(frozen=True, slots=True)
class ActivityPolicy:
    start_to_close: timedelta
    schedule_to_close: timedelta
    heartbeat: timedelta


STANDARD_ACTIVITY = ActivityPolicy(
    start_to_close=timedelta(minutes=10),
    schedule_to_close=timedelta(minutes=30),
    heartbeat=timedelta(seconds=30),
)
LONG_ACTIVITY = ActivityPolicy(
    start_to_close=timedelta(hours=1),
    schedule_to_close=timedelta(hours=6),
    heartbeat=timedelta(seconds=30),
)
COMMIT_ACTIVITY = ActivityPolicy(
    start_to_close=timedelta(minutes=5),
    schedule_to_close=timedelta(minutes=30),
    heartbeat=timedelta(seconds=15),
)


async def _execute_activity(
    name: str,
    argument: Any,
    result_type: type[_ResultT],
    policy: ActivityPolicy,
    *,
    task_queue: str | None = None,
    cancellation_type: ActivityCancellationType = ActivityCancellationType.TRY_CANCEL,
) -> _ResultT:
    result = await workflow.execute_activity(
        name,
        argument,
        result_type=result_type,
        start_to_close_timeout=policy.start_to_close,
        schedule_to_close_timeout=policy.schedule_to_close,
        heartbeat_timeout=policy.heartbeat,
        retry_policy=ACTIVITY_RETRY_POLICY,
        cancellation_type=cancellation_type,
        task_queue=task_queue,
    )
    return cast(_ResultT, result)


def _error_code(error: BaseException) -> str:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, ApplicationError) and current.type:
            return current.type
        cause = current.__cause__
        current = cause if isinstance(cause, BaseException) else None
    return type(error).__name__


def _datetime_ns(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = value.astimezone(timezone.utc) - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


class _JobLifecycle:
    _job: JobRecord | None

    def __init__(self) -> None:
        self._job = None

    def _begin(self, *, job_type: str, project_id: str, resource_id: str) -> None:
        info = workflow.info()
        now = workflow.now()
        self._job = JobRecord(
            job_id=info.workflow_id,
            workflow_id=info.workflow_id,
            workflow_run_id=info.run_id,
            job_type=job_type,
            project_id=project_id,
            resource_id=resource_id,
            status=JobStatus.RUNNING,
            stage="starting",
            attempt=info.attempt,
            created_at=info.workflow_start_time,
            updated_at=now,
        )

    def _record(self) -> JobRecord:
        if self._job is None:
            raise RuntimeError("workflow job state is not initialized")
        return self._job

    def _stage(self, stage: str) -> None:
        self._job = self._record().model_copy(update={"stage": stage, "updated_at": workflow.now()})

    def _finish(
        self,
        status: JobStatus,
        *,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> JobRecord:
        self._job = self._record().model_copy(
            update={
                "status": status,
                "stage": ("completed" if status is JobStatus.SUCCEEDED else self._record().stage),
                "result": result,
                "error_code": error_code,
                "error_message": error_message,
                "updated_at": workflow.now(),
            }
        )
        return self._job

    def _technical_failure(self, error: BaseException) -> JobRecord:
        return self._finish(
            JobStatus.TECHNICAL_FAILED,
            error_code=_error_code(error),
            error_message=str(error),
        )

    def _cancelled(self) -> None:
        self._job = self._record().model_copy(
            update={
                "status": JobStatus.CANCELLED,
                "stage": "cancelled",
                "cancellation_requested": True,
                "updated_at": workflow.now(),
            }
        )


@workflow.defn(name=INGEST_ROLLOUT_WORKFLOW)
class IngestRolloutWorkflow(_JobLifecycle):
    async def _persist_job(self, request: IngestRolloutWorkflowInput) -> None:
        if not self._job_persistence_enabled:
            return
        if request.organization_id is None:
            raise ApplicationError(
                "ingest workflow has no verified organization scope for job persistence",
                type="ORGANIZATION_SCOPE_MISSING",
                non_retryable=True,
            )
        await workflow.execute_local_activity(
            PERSIST_WORKFLOW_JOB_ACTIVITY,
            WorkflowJobPersistenceActivityInput(
                organization_id=request.organization_id,
                region_code=request.region_code,
                job=self._record(),
            ),
            result_type=JobRecord,
            start_to_close_timeout=COMMIT_ACTIVITY.start_to_close,
            retry_policy=ACTIVITY_RETRY_POLICY,
            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )

    async def _finish_and_persist(
        self,
        request: IngestRolloutWorkflowInput,
        status: JobStatus,
        *,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> JobRecord:
        job = self._finish(
            status,
            result=result,
            error_code=error_code,
            error_message=error_message,
        )
        await self._persist_job(request)
        return job

    @workflow.run
    async def run(self, request: IngestRolloutWorkflowInput) -> JobRecord:
        self._begin(
            job_type=INGEST_ROLLOUT_WORKFLOW,
            project_id=request.project_id,
            resource_id=request.rollout_id,
        )
        self._job_persistence_enabled = workflow.patched("persist-ingest-workflow-job-v1")
        materialized_source: IngestProjectionSourceV1 | None = None
        alignment_staging: AlignmentStagingArtifactV1 | None = None
        frame_selection: FrameSelectionManifestRefV1 | None = None
        media_artifacts: list[AlignedMediaArtifactV1] = []
        bundle_committed = False
        try:
            self._stage("manifest")
            await self._persist_job(request)
            parsed = await _execute_activity(
                PARSE_MANIFEST_ACTIVITY,
                request.manifest,
                ManifestActivityOutput,
                STANDARD_ACTIVITY,
            )
            manifest = parsed.preflight.manifest
            verification_request = request.verification.model_copy(
                update={
                    "required_topics": frozenset(manifest.expected_topics),
                    "known_optional_topics": frozenset(
                        set(manifest.actual_topics) - set(manifest.expected_topics)
                    ),
                }
            )
            aligned = None
            alignment_request = request.alignment
            uses_local_ingest_processing = workflow.patched(
                "object-store-free-ingest-processing-v1"
            )  # noqa: E501
            if uses_local_ingest_processing:
                self._stage("ingest_processing")
                await self._persist_job(request)
                processed = await _execute_activity(
                    PROCESS_INGEST_SOURCE_ACTIVITY,
                    IngestSourceProcessingActivityInput(
                        verification=verification_request,
                        quality=request.quality,
                        alignment=request.alignment,
                    ),
                    IngestSourceProcessingActivityOutput,
                    LONG_ACTIVITY,
                )
                verified = processed.verification
                quality = processed.quality
                aligned = processed.alignment
                frame_selection = processed.frame_selection
            else:
                # Replay-only compatibility for histories that already scheduled
                # the former Projection Arrow activity sequence.
                self._stage("verification")
                await self._persist_job(request)
                verified = await _execute_activity(
                    VERIFY_RAW_ACTIVITY,
                    verification_request,
                    VerificationActivityOutput,
                    STANDARD_ACTIVITY,
                )
                if verified.report.status is VerificationStatus.REJECTED:
                    return await self._finish_and_persist(
                        request,
                        JobStatus.QUALITY_REJECTED,
                        result={
                            "manifest": parsed.preflight.model_dump(mode="json"),
                            "verification": verified.report.model_dump(mode="json"),
                            "raw_preserved": True,
                            "training_eligible": False,
                        },
                        error_code="RAW_VERIFICATION_REJECTED",
                    )
                quality_request = request.quality
                if (
                    workflow.patched("single-pass-ingest-projection-v1")
                    and request.quality.source is not None
                ):
                    self._stage("projection_materialization")
                    await self._persist_job(request)
                    projected = await _execute_activity(
                        MATERIALIZE_INGEST_PROJECTION_ACTIVITY,
                        ProjectionMaterializationActivityInput(source=request.quality.source),
                        ProjectionMaterializationActivityOutput,
                        LONG_ACTIVITY,
                    )
                    materialized_source = projected.source
                    quality_request = request.quality.model_copy(
                        update={"source": projected.source}
                    )
                    alignment_request = request.alignment.model_copy(
                        update={"source": projected.source}
                    )
                    if projected.source.materialization is not None:
                        frame_selection = projected.source.materialization.frame_selection
                self._stage("quality")
                await self._persist_job(request)
                quality = await _execute_activity(
                    EVALUATE_QUALITY_ACTIVITY,
                    quality_request,
                    QualityActivityOutput,
                    STANDARD_ACTIVITY,
                )
            if verified.report.status is VerificationStatus.REJECTED:
                return await self._finish_and_persist(
                    request,
                    JobStatus.QUALITY_REJECTED,
                    result={
                        "manifest": parsed.preflight.model_dump(mode="json"),
                        "verification": verified.report.model_dump(mode="json"),
                        "raw_preserved": True,
                        "training_eligible": False,
                    },
                    error_code="RAW_VERIFICATION_REJECTED",
                )
            if quality is None:
                raise ApplicationError(
                    "verified ingest processing did not return a QC report",
                    type="INGEST_PROCESSING_INCOMPLETE",
                    non_retryable=True,
                )
            common_result = {
                "manifest": parsed.preflight.model_dump(mode="json"),
                "verification": verified.report.model_dump(mode="json"),
                "quality": quality.report.model_dump(mode="json"),
                "quality_decision_source": request.quality.decision_source,
                "automatic_qc_run_id": request.automatic_qc_run_id,
                "raw_preserved": True,
                "frame_selection": (
                    None if frame_selection is None else frame_selection.model_dump(mode="json")
                ),
            }
            if quality.report.status is QualityStatus.REJECT:
                return await self._finish_and_persist(
                    request,
                    JobStatus.QUALITY_REJECTED,
                    result={**common_result, "training_eligible": False},
                    error_code="QUALITY_REJECTED",
                )
            if quality.report.status is QualityStatus.RISK:
                return await self._finish_and_persist(
                    request,
                    JobStatus.QUALITY_RISK,
                    result={
                        **common_result,
                        "training_eligible": False,
                        "media_state": "NOT_PRODUCED",
                    },
                )

            if aligned is None and not uses_local_ingest_processing:
                self._stage("alignment")
                await self._persist_job(request)
                aligned = await _execute_activity(
                    ALIGN_FRAGMENT_ACTIVITY,
                    alignment_request,
                    result_type=AlignmentActivityOutput,
                    policy=LONG_ACTIVITY,
                )
            if aligned is None:
                raise ApplicationError(
                    "passing ingest processing did not return alignment staging",
                    type="INGEST_PROCESSING_INCOMPLETE",
                    non_retryable=True,
                )
            if aligned.alignment_staging is None or aligned.expected_dataset_version is None:
                raise ApplicationError(
                    "alignment did not publish bounded cross-worker staging",
                    type="ALIGNMENT_STAGING_MISSING",
                    non_retryable=True,
                )
            alignment_staging = aligned.alignment_staging
            if aligned.staged_manifest.row_count < 1:
                raise ApplicationError(
                    "canonical media requires at least one aligned step",
                    type="EMPTY_ALIGNED_ROLLOUT",
                    non_retryable=True,
                )
            self._stage("aligned_media")
            await self._persist_job(request)
            if request.organization_id is None:
                raise ApplicationError(
                    "aligned media generation requires an organization scope",
                    type="ORGANIZATION_SCOPE_MISSING",
                    non_retryable=True,
                )
            media_inputs = tuple(
                AlignedMediaActivityInput(
                    organization_id=request.organization_id,
                    region_code=request.region_code,
                    request=AlignedMediaGenerationRequestV1(
                        project_id=request.project_id,
                        dataset_id=request.dataset_id,
                        rollout_id=request.rollout_id,
                        expected_dataset_version=aligned.expected_dataset_version,
                        camera_id=camera.topic,
                        source_sha256=request.verification.source_sha256,
                        alignment=aligned.alignment_staging,
                    ),
                )
                for camera in manifest.cameras
            )
            for offset in range(0, len(media_inputs), 2):
                batch = media_inputs[offset : offset + 2]
                results = await asyncio.gather(
                    *(
                        _execute_activity(
                            CREATE_ALIGNED_MEDIA_ACTIVITY,
                            item,
                            AlignedMediaActivityOutput,
                            LONG_ACTIVITY,
                            task_queue=request.media_task_queue,
                        )
                        for item in batch
                    ),
                    return_exceptions=True,
                )
                media_artifacts.extend(
                    item.artifact
                    for item in results
                    if isinstance(item, AlignedMediaActivityOutput)
                )
                batch_failure = next(
                    (item for item in results if isinstance(item, BaseException)),
                    None,
                )
                if batch_failure is not None:
                    raise batch_failure
            expected_camera_ids = tuple(camera.topic for camera in manifest.cameras)
            if (
                len(expected_camera_ids) != len(set(expected_camera_ids))
                or {artifact.camera_id for artifact in media_artifacts} != set(expected_camera_ids)
                or len(media_artifacts) != len(expected_camera_ids)
                or any(
                    artifact.status.value != "READY"
                    or artifact.frame_count != aligned.staged_manifest.row_count
                    or artifact.fps != 30
                    or artifact.timeline is None
                    or artifact.timeline.frame_count != aligned.staged_manifest.row_count
                    or artifact.timeline.first_step != 0
                    or artifact.timeline.pts_time_base_numerator != 1
                    or artifact.timeline.pts_time_base_denominator != 30
                    or abs(artifact.duration_seconds - aligned.staged_manifest.row_count / 30)
                    > 1 / 30
                    for artifact in media_artifacts
                )
            ):
                raise ApplicationError(
                    "all camera MP4 receipts must be READY and timeline-identical",
                    type="ALIGNED_MEDIA_BUNDLE_INCOMPLETE",
                    non_retryable=True,
                )
            self._stage("lance_commit")
            await self._persist_job(request)
            committed = await _execute_activity(
                COMMIT_ALIGNED_BUNDLE_ACTIVITY,
                AlignedBundleCommitActivityInput(
                    alignment=alignment_request,
                    staged_manifest=aligned.staged_manifest,
                    alignment_staging=aligned.alignment_staging,
                    expected_dataset_version=aligned.expected_dataset_version,
                    expected_camera_ids=expected_camera_ids,
                    media_artifacts=tuple(media_artifacts),
                ),
                AlignedBundleCommitActivityOutput,
                LONG_ACTIVITY,
                cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
            bundle_committed = True
            derived = committed.derived_ready
            writer_result = {
                "dataset_version": committed.version.model_dump(mode="json"),
                "derived_ready": derived.model_dump(mode="json"),
                "viewer_target": committed.viewer_target.model_dump(mode="json"),
            }
            if (
                derived.project_id != request.project_id
                or derived.dataset_id != request.dataset_id
                or derived.rollout_id != request.rollout_id
            ):
                raise ApplicationError(
                    "DerivedReady lineage does not match ingest workflow input",
                    type="VALIDATION_FAILED",
                    non_retryable=True,
                )
            self._stage("annotation_task")
            await self._persist_job(request)
            uses_organization_scope = workflow.patched("annotation-organization-scope-v1")
            if uses_organization_scope and request.organization_id is None:
                raise ApplicationError(
                    "ingest workflow has no verified organization scope",
                    type="ORGANIZATION_SCOPE_MISSING",
                    non_retryable=True,
                )
            annotation_input = (
                AutomaticAnnotationActivityInput(
                    organization_id=request.organization_id,
                    project_id=request.project_id,
                    region_code=request.region_code,
                    rollout_id=request.rollout_id,
                    dataset_id=request.dataset_id,
                    dataset_version=derived.dataset_version,
                    lance_version=derived.lance_version,
                    dataset_schema_snapshot_id=request.alignment.schema_snapshot_id,
                    base_step_count=derived.step_count,
                    source_workflow_id=workflow.info().workflow_id,
                    frame_selection=(
                        None
                        if frame_selection is None
                        else {
                            **frame_selection.model_dump(mode="json"),
                            "source_sha256": request.verification.source_sha256,
                        }
                    ),
                )
                if uses_organization_scope
                else LegacyAutomaticAnnotationActivityInput(
                    project_id=request.project_id,
                    region_code=request.region_code,
                    rollout_id=request.rollout_id,
                    dataset_id=request.dataset_id,
                    dataset_version=derived.dataset_version,
                    lance_version=derived.lance_version,
                    dataset_schema_snapshot_id=request.alignment.schema_snapshot_id,
                    base_step_count=derived.step_count,
                    source_workflow_id=workflow.info().workflow_id,
                )
            )
            annotation_task = await _execute_activity(
                CREATE_ANNOTATION_TASK_ACTIVITY,
                annotation_input,
                AutomaticAnnotationActivityOutput,
                STANDARD_ACTIVITY,
            )
            return await self._finish_and_persist(
                request,
                JobStatus.SUCCEEDED,
                result={
                    **common_result,
                    "alignment": aligned.staged_manifest.model_dump(mode="json"),
                    "derived": writer_result.get("derived_ready"),
                    "dataset_version": writer_result.get("dataset_version"),
                    "viewer_target": writer_result.get("viewer_target"),
                    "annotation_task": annotation_task.model_dump(mode="json"),
                    "aligned_media_count": len(media_artifacts),
                    "training_eligible": True,
                },
            )
        except (asyncio.CancelledError, CancelledError):
            self._cancelled()
            await self._persist_job(request)
            raise
        except ActivityError as exc:
            if "cancel" in str(exc).lower():
                self._cancelled()
                await self._persist_job(request)
                raise
            failed_job = self._technical_failure(exc)
            await self._persist_job(request)
            return failed_job
        except (ChildWorkflowError, ApplicationError) as exc:
            failed_job = self._technical_failure(exc)
            await self._persist_job(request)
            return failed_job
        finally:
            if (
                workflow.patched("cleanup-uncommitted-aligned-media-v1")
                and media_artifacts
                and not bundle_committed
                and request.organization_id is not None
            ):
                with suppress(ActivityError, asyncio.CancelledError, CancelledError):
                    await asyncio.shield(
                        _execute_activity(
                            CLEANUP_UNCOMMITTED_ALIGNED_MEDIA_ACTIVITY,
                            AlignedMediaCleanupActivityInput(
                                organization_id=request.organization_id,
                                project_id=request.project_id,
                                region_code=request.region_code,
                                artifacts=tuple(media_artifacts),
                            ),
                            AlignedMediaCleanupActivityOutput,
                            STANDARD_ACTIVITY,
                            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                        )
                    )
            if alignment_staging is not None and request.organization_id is not None:
                with suppress(ActivityError, asyncio.CancelledError, CancelledError):
                    await asyncio.shield(
                        _execute_activity(
                            CLEANUP_ALIGNMENT_STAGING_ACTIVITY,
                            AlignmentStagingCleanupActivityInput(
                                organization_id=request.organization_id,
                                project_id=request.project_id,
                                region_code=request.region_code,
                                staging=alignment_staging,
                            ),
                            AlignmentStagingCleanupActivityOutput,
                            STANDARD_ACTIVITY,
                            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                        )
                    )
            if materialized_source is not None:
                cleanup_projection = workflow.patched("cleanup-ingest-projection-v1")
                if cleanup_projection:
                    with suppress(ActivityError, asyncio.CancelledError, CancelledError):
                        await asyncio.shield(
                            _execute_activity(
                                CLEANUP_INGEST_PROJECTION_ACTIVITY,
                                ProjectionCleanupActivityInput(source=materialized_source),
                                ProjectionCleanupActivityOutput,
                                STANDARD_ACTIVITY,
                            )
                        )
    @workflow.query(name="job")
    def job(self) -> JobRecord:
        return self._record()


@workflow.defn(name=CONTINUOUS_EPISODE_WORKFLOW)
class ContinuousRecordingEpisodeWorkflow(_JobLifecycle):
    """Finalize one immutable soft slice without putting media or samples in history."""

    async def _persist_job(self, request: ContinuousEpisodeWorkflowInput) -> None:
        source = request.projection
        await workflow.execute_local_activity(
            PERSIST_WORKFLOW_JOB_ACTIVITY,
            WorkflowJobPersistenceActivityInput(
                organization_id=source.organization_id,
                region_code=source.region_code,
                job=self._record(),
            ),
            result_type=JobRecord,
            start_to_close_timeout=COMMIT_ACTIVITY.start_to_close,
            retry_policy=ACTIVITY_RETRY_POLICY,
            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )

    async def _state(
        self,
        request: ContinuousEpisodeWorkflowInput,
        *,
        expected: EpisodeProcessingStatus,
        new: EpisodeProcessingStatus,
        qc_report_id: str | None = None,
        alignment_attempt_id: str | None = None,
        dataset_id: str | None = None,
        dataset_version: int | None = None,
        lance_version: int | None = None,
        annotation_task_id: str | None = None,
        aligned_media_camera_count: int | None = None,
        failure_code: str | None = None,
        failure_stage: str | None = None,
    ) -> None:
        source = request.projection
        await _execute_activity(
            UPDATE_CONTINUOUS_EPISODE_STATE_ACTIVITY,
            ContinuousEpisodeStateActivityInput(
                organization_id=source.organization_id,
                project_id=source.project_id,
                region_code=source.region_code,
                recording_id=source.recording_id,
                episode_id=source.episode_id,
                expected_status=expected,
                new_status=new,
                qc_report_id=qc_report_id,
                alignment_attempt_id=alignment_attempt_id,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                lance_version=lance_version,
                annotation_task_id=annotation_task_id,
                aligned_media_camera_count=aligned_media_camera_count,
                failure_code=failure_code,
                failure_stage=failure_stage,
            ),
            ContinuousEpisodeStateActivityOutput,
            COMMIT_ACTIVITY,
            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )

    @workflow.run
    async def run(self, request: ContinuousEpisodeWorkflowInput) -> JobRecord:
        source = request.projection
        self._begin(
            job_type=CONTINUOUS_EPISODE_WORKFLOW,
            project_id=source.project_id,
            resource_id=f"{source.recording_id}/{source.episode_id}",
        )
        ledger_status = EpisodeProcessingStatus.PENDING_QC
        alignment_staging: AlignmentStagingArtifactV1 | None = None
        media_artifacts: list[AlignedMediaArtifactV1] = []
        bundle_committed = False
        try:
            self._stage("qc")
            await self._persist_job(request)
            await self._state(
                request,
                expected=ledger_status,
                new=EpisodeProcessingStatus.QC_RUNNING,
            )
            ledger_status = EpisodeProcessingStatus.QC_RUNNING
            qc = await _execute_activity(
                QC_CONTINUOUS_EPISODE_ACTIVITY,
                request,
                ContinuousEpisodeQcActivityOutput,
                LONG_ACTIVITY,
            )
            if qc.status == "REJECT":
                await self._state(
                    request,
                    expected=ledger_status,
                    new=EpisodeProcessingStatus.QC_FAILED,
                    qc_report_id=qc.report_id,
                    failure_code="QC_REJECTED",
                    failure_stage="qc",
                )
                ledger_status = EpisodeProcessingStatus.QC_FAILED
                result = self._finish(
                    JobStatus.QUALITY_REJECTED,
                    result={
                        "qc_report_id": qc.report_id,
                        "finding_codes": list(qc.finding_codes),
                        "training_eligible": False,
                        "raw_preserved": True,
                    },
                    error_code="QC_REJECTED",
                )
                await self._persist_job(request)
                return result
            await self._state(
                request,
                expected=ledger_status,
                new=EpisodeProcessingStatus.PENDING_ALIGNMENT,
                qc_report_id=qc.report_id,
            )
            ledger_status = EpisodeProcessingStatus.PENDING_ALIGNMENT

            self._stage("alignment")
            await self._persist_job(request)
            alignment_attempt_id = f"continuous-{source.source_sha256[:24]}"
            await self._state(
                request,
                expected=ledger_status,
                new=EpisodeProcessingStatus.ALIGNING,
                alignment_attempt_id=alignment_attempt_id,
            )
            ledger_status = EpisodeProcessingStatus.ALIGNING
            aligned = await _execute_activity(
                ALIGN_CONTINUOUS_EPISODE_ACTIVITY,
                request,
                ContinuousEpisodeAlignmentActivityOutput,
                LONG_ACTIVITY,
            )
            alignment_staging = aligned.alignment_staging
            if aligned.staged_manifest.row_count < 1:
                raise ApplicationError(
                    "continuous Episode alignment produced no canonical steps",
                    type="EMPTY_ALIGNED_ROLLOUT",
                    non_retryable=True,
                )

            self._stage("aligned_media")
            await self._persist_job(request)
            video_assets = {
                asset.camera_id: asset
                for asset in request.assets
                if asset.role is RecordingAssetRole.RAW_VIDEO
            }
            raw_capture_start_ns = _datetime_ns(source.started_at) - request.start_offset_ns
            media_inputs: list[AlignedMediaActivityInput] = []
            for camera in request.recording_config.cameras:
                asset = video_assets.get(camera.camera_id)
                source_start_ns = request.start_offset_ns - camera.capture_start_offset_ns
                source_end_ns = request.end_offset_ns - camera.capture_start_offset_ns
                if asset is None or source_start_ns < 0 or source_end_ns <= source_start_ns:
                    raise ApplicationError(
                        "camera MP4 does not cover the finalized Episode window",
                        type="VIDEO_EPISODE_WINDOW_MISSING",
                        non_retryable=True,
                    )
                media_inputs.append(
                    AlignedMediaActivityInput(
                        organization_id=source.organization_id,
                        region_code=source.region_code,
                        request=AlignedMediaGenerationRequestV1(
                            project_id=source.project_id,
                            dataset_id=request.dataset_id,
                            rollout_id=source.rollout_id,
                            expected_dataset_version=aligned.expected_dataset_version,
                            camera_id=camera.modality_key,
                            source_sha256=source.source_sha256,
                            alignment=aligned.alignment_staging,
                            mp4_source=AlignedMediaMp4SourceV1(
                                object_key=asset.object_key,
                                size_bytes=asset.size_bytes,
                                content_sha256=asset.content_sha256,
                                start_offset_ns=source_start_ns,
                                end_offset_ns=source_end_ns,
                                capture_start_timestamp_ns=(
                                    raw_capture_start_ns + camera.capture_start_offset_ns
                                ),
                                configured_fps=camera.fps,
                                configured_codec=camera.codec,
                                configured_time_base_numerator=camera.time_base_numerator,
                                configured_time_base_denominator=camera.time_base_denominator,
                            ),
                        ),
                    )
                )
            for offset in range(0, len(media_inputs), 2):
                results = await asyncio.gather(
                    *(
                        _execute_activity(
                            CREATE_ALIGNED_MEDIA_ACTIVITY,
                            item,
                            AlignedMediaActivityOutput,
                            LONG_ACTIVITY,
                            task_queue=request.media_task_queue,
                        )
                        for item in media_inputs[offset : offset + 2]
                    ),
                    return_exceptions=True,
                )
                media_artifacts.extend(
                    item.artifact
                    for item in results
                    if isinstance(item, AlignedMediaActivityOutput)
                )
                failure = next((item for item in results if isinstance(item, BaseException)), None)
                if failure is not None:
                    raise failure
            expected_camera_ids = tuple(
                camera.modality_key for camera in request.recording_config.cameras
            )
            if (
                {artifact.camera_id for artifact in media_artifacts} != set(expected_camera_ids)
                or len(media_artifacts) != len(expected_camera_ids)
                or any(
                    artifact.status.value != "READY"
                    or artifact.frame_count != aligned.staged_manifest.row_count
                    or artifact.fps != 30
                    or artifact.timeline is None
                    or artifact.timeline.frame_count != aligned.staged_manifest.row_count
                    or artifact.timeline.first_step != 0
                    or artifact.timeline.pts_time_base_numerator != 1
                    or artifact.timeline.pts_time_base_denominator != 30
                    or abs(artifact.duration_seconds - aligned.staged_manifest.row_count / 30)
                    > 1 / 30
                    for artifact in media_artifacts
                )
            ):
                raise ApplicationError(
                    "all camera MP4 receipts must be READY and timeline-identical",
                    type="ALIGNED_MEDIA_BUNDLE_INCOMPLETE",
                    non_retryable=True,
                )

            self._stage("lance_commit")
            await self._persist_job(request)
            committed = await _execute_activity(
                COMMIT_CONTINUOUS_EPISODE_BUNDLE_ACTIVITY,
                ContinuousEpisodeBundleCommitActivityInput(
                    workflow_input=request,
                    alignment=aligned.alignment,
                    staged_manifest=aligned.staged_manifest,
                    alignment_staging=aligned.alignment_staging,
                    expected_dataset_version=aligned.expected_dataset_version,
                    expected_camera_ids=expected_camera_ids,
                    media_artifacts=tuple(media_artifacts),
                ),
                AlignedBundleCommitActivityOutput,
                LONG_ACTIVITY,
                cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
            bundle_committed = True
            derived = committed.derived_ready
            if (
                derived.project_id != source.project_id
                or derived.dataset_id != request.dataset_id
                or derived.rollout_id != source.rollout_id
            ):
                raise ApplicationError(
                    "DerivedReady lineage does not match the continuous Episode",
                    type="VALIDATION_FAILED",
                    non_retryable=True,
                )

            self._stage("annotation_task")
            await self._persist_job(request)
            annotation = await _execute_activity(
                CREATE_ANNOTATION_TASK_ACTIVITY,
                AutomaticAnnotationActivityInput(
                    organization_id=source.organization_id,
                    project_id=source.project_id,
                    region_code=source.region_code,
                    rollout_id=source.rollout_id,
                    dataset_id=request.dataset_id,
                    dataset_version=derived.dataset_version,
                    lance_version=derived.lance_version,
                    dataset_schema_snapshot_id=request.schema_snapshot_id,
                    base_step_count=derived.step_count,
                    source_workflow_id=workflow.info().workflow_id,
                ),
                AutomaticAnnotationActivityOutput,
                STANDARD_ACTIVITY,
            )
            await self._state(
                request,
                expected=ledger_status,
                new=EpisodeProcessingStatus.READY,
                dataset_id=request.dataset_id,
                dataset_version=derived.dataset_version,
                lance_version=derived.lance_version,
                annotation_task_id=annotation.task_id,
                aligned_media_camera_count=len(media_artifacts),
            )
            ledger_status = EpisodeProcessingStatus.READY
            result = self._finish(
                JobStatus.SUCCEEDED,
                result={
                    "qc_report_id": qc.report_id,
                    "dataset_version": committed.version.model_dump(mode="json"),
                    "derived_ready": derived.model_dump(mode="json"),
                    "viewer_target": committed.viewer_target.model_dump(mode="json"),
                    "annotation_task_id": annotation.task_id,
                    "aligned_media_count": len(media_artifacts),
                    "training_eligible": True,
                },
            )
            await self._persist_job(request)
            return result
        except (asyncio.CancelledError, CancelledError):
            self._cancelled()
            if ledger_status not in {
                EpisodeProcessingStatus.READY,
                EpisodeProcessingStatus.QC_FAILED,
                EpisodeProcessingStatus.FAILED,
            }:
                with suppress(ActivityError, asyncio.CancelledError, CancelledError):
                    await asyncio.shield(
                        self._state(
                            request,
                            expected=ledger_status,
                            new=EpisodeProcessingStatus.FAILED,
                            failure_code="WORKFLOW_CANCELLED",
                            failure_stage=self._record().stage,
                        )
                    )
            await self._persist_job(request)
            raise
        except (ActivityError, ApplicationError) as exc:
            failure_stage = self._record().stage
            failure_code = {
                "qc": "QC_PROCESSING_FAILED",
                "alignment": "ALIGNMENT_FAILED",
                "aligned_media": "ALIGNED_MEDIA_FAILED",
                "lance_commit": "LANCE_COMMIT_FAILED",
                "annotation_task": "ANNOTATION_TASK_FAILED",
            }.get(failure_stage, "CONTINUOUS_EPISODE_FAILED")
            if ledger_status not in {
                EpisodeProcessingStatus.READY,
                EpisodeProcessingStatus.QC_FAILED,
                EpisodeProcessingStatus.FAILED,
            }:
                with suppress(ActivityError, asyncio.CancelledError, CancelledError):
                    await self._state(
                        request,
                        expected=ledger_status,
                        new=EpisodeProcessingStatus.FAILED,
                        failure_code=failure_code,
                        failure_stage=failure_stage,
                    )
            result = self._technical_failure(exc)
            await self._persist_job(request)
            return result
        finally:
            if media_artifacts and not bundle_committed:
                with suppress(ActivityError, asyncio.CancelledError, CancelledError):
                    await asyncio.shield(
                        _execute_activity(
                            CLEANUP_UNCOMMITTED_ALIGNED_MEDIA_ACTIVITY,
                            AlignedMediaCleanupActivityInput(
                                organization_id=source.organization_id,
                                project_id=source.project_id,
                                region_code=source.region_code,
                                artifacts=tuple(media_artifacts),
                            ),
                            AlignedMediaCleanupActivityOutput,
                            STANDARD_ACTIVITY,
                            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                        )
                    )
            if alignment_staging is not None:
                with suppress(ActivityError, asyncio.CancelledError, CancelledError):
                    await asyncio.shield(
                        _execute_activity(
                            CLEANUP_ALIGNMENT_STAGING_ACTIVITY,
                            AlignmentStagingCleanupActivityInput(
                                organization_id=source.organization_id,
                                project_id=source.project_id,
                                region_code=source.region_code,
                                staging=alignment_staging,
                            ),
                            AlignmentStagingCleanupActivityOutput,
                            STANDARD_ACTIVITY,
                            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                        )
                    )

    @workflow.query(name="job")
    def job(self) -> JobRecord:
        return self._record()


@workflow.defn(name=DATASET_WRITER_WORKFLOW)
class DatasetWriterWorkflow(_JobLifecycle):
    @workflow.run
    async def run(self, request: DatasetWriterWorkflowInput) -> JobRecord:
        self._begin(
            job_type=DATASET_WRITER_WORKFLOW,
            project_id=request.project_id,
            resource_id=request.resource_id,
        )
        try:
            self._stage("lance_commit")
            committed = await _execute_activity(
                COMMIT_FRAGMENT_ACTIVITY,
                request.commit,
                CatalogCommitActivityOutput,
                COMMIT_ACTIVITY,
            )
            return self._finish(
                JobStatus.SUCCEEDED,
                result={
                    "dataset_id": request.dataset_id,
                    "dataset_version": committed.version.model_dump(mode="json"),
                    "derived_ready": committed.derived_ready.model_dump(mode="json"),
                },
            )
        except (asyncio.CancelledError, CancelledError):
            self._cancelled()
            raise
        except ActivityError as exc:
            return self._technical_failure(exc)

    @workflow.query(name="job")
    def job(self) -> JobRecord:
        return self._record()


@workflow.defn(name=PUBLISH_DATASET_WORKFLOW)
class PublishDatasetWorkflow(_JobLifecycle):
    @workflow.run
    async def run(self, request: PublishDatasetWorkflowInput) -> JobRecord:
        publication = request.request
        self._begin(
            job_type=PUBLISH_DATASET_WORKFLOW,
            project_id=publication.project_id,
            resource_id=f"{publication.dataset_id}/{publication.dataset_version}",
        )
        try:
            self._stage("publishing")
            result = await _execute_activity(
                PUBLISH_DATASET_ACTIVITY,
                PublishActivityInput(request=publication),
                PublishActivityOutput,
                STANDARD_ACTIVITY,
            )
            return self._finish(
                JobStatus.SUCCEEDED,
                result={"manifest": result.manifest.model_dump(mode="json")},
            )
        except (asyncio.CancelledError, CancelledError):
            self._cancelled()
            raise
        except ActivityError as exc:
            return self._technical_failure(exc)

    @workflow.query(name="job")
    def job(self) -> JobRecord:
        return self._record()


@workflow.defn(name=EXPORT_WORKFLOW)
class ExportWorkflow(_JobLifecycle):
    @workflow.run
    async def run(self, request: ExportWorkflowInput) -> JobRecord:
        manifest = request.manifest
        uses_attempt_identity = workflow.patched("export-attempt-identity-v1")
        uses_phase_progress = workflow.patched("export-phase-progress-v1")
        self._begin(
            job_type=EXPORT_WORKFLOW,
            project_id=manifest.project_id,
            resource_id=(
                (
                    f"{manifest.dataset_id}/{manifest.dataset_version}/"
                    f"{request.format.value}/{request.attempt_id}"
                )
                if uses_attempt_identity
                else f"{manifest.dataset_id}/{manifest.dataset_version}/{request.format.value}"
            ),
        )
        try:
            if uses_phase_progress:
                self._stage("preflight")
                await _execute_activity(
                    PREFLIGHT_EXPORT_ACTIVITY,
                    ExportPreflightActivityInput(
                        manifest=manifest,
                        format=request.format,
                    ),
                    ExportPreflightActivityOutput,
                    STANDARD_ACTIVITY,
                )
            self._stage("materializing" if uses_phase_progress else "export")
            activity_input = (
                ExportActivityInput(
                    manifest=manifest,
                    format=request.format,
                    attempt_id=request.attempt_id,
                )
                if uses_attempt_identity
                else LegacyExportActivityInput(manifest=manifest, format=request.format)
            )
            result = await _execute_activity(
                EXPORT_DATASET_ACTIVITY,
                activity_input,
                ExportActivityOutput,
                LONG_ACTIVITY,
            )
            if uses_phase_progress:
                self._stage("verifying_artifact")
                await _execute_activity(
                    VERIFY_EXPORT_ARTIFACT_ACTIVITY,
                    ExportArtifactVerificationActivityInput(result=result.result),
                    ExportArtifactVerificationActivityOutput,
                    STANDARD_ACTIVITY,
                )
            return self._finish(
                JobStatus.SUCCEEDED,
                result={"export": result.result.model_dump(mode="json")},
            )
        except (asyncio.CancelledError, CancelledError):
            self._cancelled()
            raise
        except ActivityError as exc:
            return self._finish(
                JobStatus.TECHNICAL_FAILED,
                error_code=_error_code(exc),
                error_message=(
                    "The export workflow failed. Retry after resolving the reported code."
                ),
            )

    @workflow.query(name="job")
    def job(self) -> JobRecord:
        return self._record()


@workflow.defn(name=CATALOG_RECONCILIATION_WORKFLOW)
class CatalogReconciliationWorkflow(_JobLifecycle):
    @workflow.run
    async def run(self, request: CatalogReconciliationWorkflowInput) -> JobRecord:
        self._begin(
            job_type=CATALOG_RECONCILIATION_WORKFLOW,
            project_id=request.project_id,
            resource_id=request.dataset_id,
        )
        try:
            self._stage("catalog_reconciliation")
            result = await _execute_activity(
                RECONCILE_CATALOG_ACTIVITY,
                CatalogReconciliationActivityInput(
                    project_id=request.project_id,
                    dataset_id=request.dataset_id,
                ),
                CatalogReconciliationActivityOutput,
                STANDARD_ACTIVITY,
            )
            return self._finish(
                JobStatus.SUCCEEDED,
                result={
                    "repaired_versions": [
                        item.model_dump(mode="json") for item in result.repaired_versions
                    ]
                },
            )
        except (asyncio.CancelledError, CancelledError):
            self._cancelled()
            raise
        except ActivityError as exc:
            return self._technical_failure(exc)

    @workflow.query(name="job")
    def job(self) -> JobRecord:
        return self._record()


@workflow.defn(name=PUBLISH_RECONCILIATION_WORKFLOW)
class PublishReconciliationWorkflow(_JobLifecycle):
    @workflow.run
    async def run(self, request: PublishReconciliationWorkflowInput) -> JobRecord:
        publication = request.request
        self._begin(
            job_type=PUBLISH_RECONCILIATION_WORKFLOW,
            project_id=publication.project_id,
            resource_id=f"{publication.dataset_id}/{publication.dataset_version}",
        )
        try:
            self._stage("publication_reconciliation")
            result = await _execute_activity(
                RECONCILE_PUBLICATION_ACTIVITY,
                PublishReconciliationActivityInput(request=publication),
                PublishReconciliationActivityOutput,
                STANDARD_ACTIVITY,
            )
            return self._finish(
                JobStatus.SUCCEEDED,
                result={"manifest": result.manifest.model_dump(mode="json")},
            )
        except (asyncio.CancelledError, CancelledError):
            self._cancelled()
            raise
        except ActivityError as exc:
            return self._technical_failure(exc)

    @workflow.query(name="job")
    def job(self) -> JobRecord:
        return self._record()


@workflow.defn(name=ANNOTATION_REVIEW_PREPARATION_WORKFLOW)
class AnnotationReviewPreparationWorkflow(_JobLifecycle):
    """Generate an immutable reviewable version from fully pinned annotation input."""

    @workflow.run
    async def run(self, request: AnnotationReviewPreparationWorkflowInput) -> JobRecord:
        revision = request.revision
        self._begin(
            job_type=ANNOTATION_REVIEW_PREPARATION_WORKFLOW,
            project_id=request.project_id,
            resource_id=f"{revision.task_id}/{revision.revision}",
        )
        self._stage("review_checks")
        try:
            checks = validate_tag_revision(
                schema=request.tag_schema,
                base_step_count=request.base_step_count,
                tags=revision.tags,
                operations=request.cumulative_operations,
            )
        except TagValidationIssue as exc:
            raise ApplicationError(
                exc.message,
                type="VALIDATION_FAILED",
                non_retryable=True,
            ) from exc
        expected_hash = revision_content_hash(
            base_lance_version=revision.base_lance_version,
            tag_schema_id=revision.tag_schema_id,
            tag_schema_version=revision.tag_schema_version,
            tags=revision.tags,
            operations=request.cumulative_operations,
        )
        if revision.content_hash != expected_hash:
            raise ApplicationError(
                "annotation revision content hash does not match the workflow snapshot",
                type="VALIDATION_FAILED",
                non_retryable=True,
            )
        # Normalize at the sandbox module boundary because replay may load the same
        # frozen Pydantic model under distinct module identities.
        submission = AnnotationSubmission.model_validate(
            {
                "submission_id": workflow.info().workflow_id,
                "task_id": revision.task_id,
                "revision": revision.revision,
                "submitted_by": request.submitted_by,
                "base_lance_version": revision.base_lance_version,
                "tag_schema_id": request.tag_schema.schema_id,
                "tag_schema_version": request.tag_schema.version,
                "tag_schema_hash": request.tag_schema.content_hash,
                "revision_content_hash": revision.content_hash,
                "checks": [check.model_dump(mode="json") for check in checks],
                "created_at": workflow.now(),
            }
        )
        return self._finish(
            JobStatus.SUCCEEDED,
            result={"submission": submission.model_dump(mode="json")},
        )

    @workflow.query(name="job")
    def job(self) -> JobRecord:
        return self._record()


ALL_WORKFLOWS = (
    IngestRolloutWorkflow,
    ContinuousRecordingEpisodeWorkflow,
    DatasetWriterWorkflow,
    PublishDatasetWorkflow,
    ExportWorkflow,
    CatalogReconciliationWorkflow,
    PublishReconciliationWorkflow,
    AnnotationReviewPreparationWorkflow,
)
