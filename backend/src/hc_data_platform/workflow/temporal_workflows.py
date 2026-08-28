"""Replay-safe Temporal workflow definitions for the data pipeline."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, TypeVar, cast

from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    CancelledError,
    ChildWorkflowError,
)
from temporalio.workflow import ActivityCancellationType

with workflow.unsafe.imports_passed_through():
    from hc_data_platform.annotation.models import AnnotationSubmission
    from hc_data_platform.annotation.validation import (
        TagValidationIssue,
        revision_content_hash,
        validate_tag_revision,
    )
    from hc_data_platform.preview.models import PreviewRequestV1
    from hc_data_platform.quality.models import QualityStatus
    from hc_data_platform.verification.models import VerificationStatus

    from .models import (
        AlignmentActivityOutput,
        AnnotationReviewPreparationWorkflowInput,
        AutomaticAnnotationActivityInput,
        AutomaticAnnotationActivityOutput,
        CatalogCommitActivityInput,
        CatalogCommitActivityOutput,
        CatalogReconciliationActivityInput,
        CatalogReconciliationActivityOutput,
        CatalogReconciliationWorkflowInput,
        DatasetWriterWorkflowInput,
        ExportActivityInput,
        ExportActivityOutput,
        ExportArtifactVerificationActivityInput,
        ExportArtifactVerificationActivityOutput,
        ExportPreflightActivityInput,
        ExportPreflightActivityOutput,
        ExportWorkflowInput,
        IngestProjectionSourceV1,
        IngestRolloutWorkflowInput,
        JobRecord,
        JobStatus,
        LegacyAutomaticAnnotationActivityInput,
        LegacyExportActivityInput,
        ManifestActivityOutput,
        PreviewActivityInput,
        PreviewActivityOutput,
        PreviewWorkflowInput,
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
        workflow_id,
    )
    from .names import (
        ALIGN_FRAGMENT_ACTIVITY,
        ANNOTATION_REVIEW_PREPARATION_WORKFLOW,
        CATALOG_RECONCILIATION_WORKFLOW,
        CLEANUP_INGEST_PROJECTION_ACTIVITY,
        COMMIT_FRAGMENT_ACTIVITY,
        CREATE_ANNOTATION_TASK_ACTIVITY,
        CREATE_PREVIEW_ACTIVITY,
        DATASET_WRITER_WORKFLOW,
        EVALUATE_QUALITY_ACTIVITY,
        EXPORT_DATASET_ACTIVITY,
        EXPORT_WORKFLOW,
        INGEST_ROLLOUT_WORKFLOW,
        MATERIALIZE_INGEST_PROJECTION_ACTIVITY,
        PARSE_MANIFEST_ACTIVITY,
        PERSIST_WORKFLOW_JOB_ACTIVITY,
        PREFLIGHT_EXPORT_ACTIVITY,
        PREVIEW_WORKFLOW,
        PUBLISH_DATASET_ACTIVITY,
        PUBLISH_DATASET_WORKFLOW,
        PUBLISH_RECONCILIATION_WORKFLOW,
        RECONCILE_CATALOG_ACTIVITY,
        RECONCILE_PUBLICATION_ACTIVITY,
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
) -> _ResultT:
    result = await workflow.execute_activity(
        name,
        argument,
        result_type=result_type,
        start_to_close_timeout=policy.start_to_close,
        schedule_to_close_timeout=policy.schedule_to_close,
        heartbeat_timeout=policy.heartbeat,
        retry_policy=ACTIVITY_RETRY_POLICY,
        cancellation_type=ActivityCancellationType.TRY_CANCEL,
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
            self._stage("verification")
            await self._persist_job(request)
            verification_request = request.verification.model_copy(
                update={
                    "required_topics": frozenset(manifest.expected_topics),
                    "known_optional_topics": frozenset(
                        set(manifest.actual_topics) - set(manifest.expected_topics)
                    ),
                }
            )
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
            alignment_request = request.alignment
            materialized_projection = None
            if (
                workflow.patched("single-pass-ingest-projection-v1")
                and request.quality.source is not None
            ):
                self._stage("projection_materialization")
                await self._persist_job(request)
                projected = await _execute_activity(
                    MATERIALIZE_INGEST_PROJECTION_ACTIVITY,
                    ProjectionMaterializationActivityInput(
                        source=request.quality.source
                    ),
                    ProjectionMaterializationActivityOutput,
                    LONG_ACTIVITY,
                )
                materialized_projection = projected.source.materialization
                materialized_source = projected.source
                quality_request = request.quality.model_copy(
                    update={"source": projected.source}
                )
                alignment_request = request.alignment.model_copy(
                    update={"source": projected.source}
                )

            self._stage("quality")
            await self._persist_job(request)
            quality = await _execute_activity(
                EVALUATE_QUALITY_ACTIVITY,
                quality_request,
                QualityActivityOutput,
                STANDARD_ACTIVITY,
            )
            common_result = {
                "manifest": parsed.preflight.model_dump(mode="json"),
                "verification": verified.report.model_dump(mode="json"),
                "quality": quality.report.model_dump(mode="json"),
                "quality_decision_source": request.quality.decision_source,
                "automatic_qc_run_id": request.automatic_qc_run_id,
                "raw_preserved": True,
                "frame_selection": (
                    None
                    if materialized_projection is None
                    else materialized_projection.frame_selection.model_dump(mode="json")
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
                        "preview_state": "ISOLATED",
                    },
                )

            self._stage("alignment")
            await self._persist_job(request)
            aligned = await _execute_activity(
                ALIGN_FRAGMENT_ACTIVITY,
                alignment_request,
                result_type=AlignmentActivityOutput,
                policy=LONG_ACTIVITY,
            )
            self._stage("lance_commit")
            await self._persist_job(request)
            if aligned.dataset_version is not None and aligned.derived_ready is not None:
                writer_result = {
                    "dataset_version": aligned.dataset_version.model_dump(mode="json"),
                    "derived_ready": aligned.derived_ready.model_dump(mode="json"),
                    "viewer_target": (
                        None
                        if aligned.viewer_target is None
                        else aligned.viewer_target.model_dump(mode="json")
                    ),
                }
                derived = aligned.derived_ready
            else:
                # Replay compatibility for histories produced before the alignment
                # activity committed Lance inline and returned only compact facts.
                commit_request = CatalogCommitActivityInput(fragment=aligned.catalog_fragment)
                writer_resource = "/".join(
                    (
                        request.dataset_id,
                        request.rollout_id,
                        aligned.catalog_fragment.manifest.content_hash,
                    )
                )
                writer = await workflow.execute_child_workflow(
                    DATASET_WRITER_WORKFLOW,
                    DatasetWriterWorkflowInput(
                        project_id=request.project_id,
                        dataset_id=request.dataset_id,
                        resource_id=writer_resource,
                        commit=commit_request,
                    ),
                    id=workflow_id(
                        "dataset-writer",
                        request.project_id,
                        writer_resource,
                    ),
                    result_type=JobRecord,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                )
                if writer.status is not JobStatus.SUCCEEDED:
                    return await self._finish_and_persist(
                        request,
                        JobStatus.TECHNICAL_FAILED,
                        result={**common_result, "writer_job": writer.model_dump(mode="json")},
                        error_code=writer.error_code or "DATASET_WRITER_FAILED",
                    )
                writer_result = writer.result or {}
                derived_payload = writer_result.get("derived_ready")
                if not isinstance(derived_payload, dict):
                    raise ApplicationError(
                        "dataset writer did not return an immutable DerivedReady event",
                        type="VALIDATION_FAILED",
                        non_retryable=True,
                    )
                derived = CatalogCommitActivityOutput.model_validate(
                    {
                        "version": writer_result.get("dataset_version"),
                        "derived_ready": derived_payload,
                    }
                ).derived_ready
            if alignment_request.source is not None and aligned.viewer_target is None:
                raise ApplicationError(
                    "ingest alignment did not publish a Dataset viewer target",
                    type="DATASET_VIEWER_PROJECTION_MISSING",
                    non_retryable=True,
                )
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
            preview_count = 0
            if (
                workflow.patched("prewarm-original-camera-previews-v1")
                and manifest.cameras
                and derived.step_count > 0
            ):
                # Camera media is an immutable derived asset. Materialize it before
                # publishing the annotation task so opening a task never starts a
                # foreground transcode. Cameras are deliberately processed in
                # sequence to keep one rollout from saturating the worker.
                self._stage("preview_prewarm")
                await self._persist_job(request)
                if request.organization_id is None:
                    raise ApplicationError(
                        "preview prewarm requires an exact organization scope",
                        type="ORGANIZATION_SCOPE_MISSING",
                        non_retryable=True,
                    )
                for camera in manifest.cameras:
                    await _execute_activity(
                        CREATE_PREVIEW_ACTIVITY,
                        PreviewActivityInput(
                            organization_id=request.organization_id,
                            region_code=request.region_code,
                            request=PreviewRequestV1(
                                project_id=request.project_id,
                                dataset_id=request.dataset_id,
                                rollout_id=request.rollout_id,
                                lance_version=str(derived.lance_version),
                                camera_id=camera.topic,
                                frequency_hz=aligned.staged_manifest.frequency_hz,
                                start_step=0,
                                end_step=derived.step_count,
                            )
                        ),
                        PreviewActivityOutput,
                        LONG_ACTIVITY,
                        task_queue=request.media_task_queue,
                    )
                    preview_count += 1
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
                        if materialized_projection is None
                        else {
                            **materialized_projection.frame_selection.model_dump(mode="json"),
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
                    "preview_count": preview_count,
                    "training_eligible": True,
                },
            )
        except (asyncio.CancelledError, CancelledError):
            self._cancelled()
            await self._persist_job(request)
            raise
        except (ActivityError, ChildWorkflowError, ApplicationError) as exc:
            failure = self._technical_failure(exc)
            await self._persist_job(request)
            return failure
        finally:
            if (
                materialized_source is not None
                and workflow.patched("cleanup-ingest-projection-v1")
            ):
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


@workflow.defn(name=PREVIEW_WORKFLOW)
class PreviewWorkflow(_JobLifecycle):
    @workflow.run
    async def run(self, request: PreviewWorkflowInput) -> JobRecord:
        preview = request.request
        resource_id = "/".join(
            (
                preview.dataset_id,
                preview.rollout_id,
                preview.lance_version,
                preview.camera_id,
                preview.profile_id,
            )
        )
        self._begin(
            job_type=PREVIEW_WORKFLOW,
            project_id=preview.project_id,
            resource_id=resource_id,
        )
        try:
            self._stage("preview")
            result = await _execute_activity(
                CREATE_PREVIEW_ACTIVITY,
                PreviewActivityInput(
                    organization_id=request.organization_id,
                    region_code=request.region_code,
                    job_id=request.job_id,
                    request=preview,
                ),
                PreviewActivityOutput,
                LONG_ACTIVITY,
            )
            return self._finish(
                JobStatus.SUCCEEDED,
                result={"preview": result.artifact.model_dump(mode="json")},
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
    DatasetWriterWorkflow,
    PreviewWorkflow,
    PublishDatasetWorkflow,
    ExportWorkflow,
    CatalogReconciliationWorkflow,
    PublishReconciliationWorkflow,
    AnnotationReviewPreparationWorkflow,
)
