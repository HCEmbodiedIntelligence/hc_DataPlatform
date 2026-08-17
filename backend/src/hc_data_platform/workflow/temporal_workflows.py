"""Replay-safe Temporal workflow definitions for the data pipeline."""

from __future__ import annotations

import asyncio
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
    from hc_data_platform.quality.models import QualityStatus
    from hc_data_platform.verification.models import VerificationStatus

    from .models import (
        AlignmentActivityOutput,
        CatalogCommitActivityInput,
        CatalogCommitActivityOutput,
        CatalogReconciliationActivityInput,
        CatalogReconciliationActivityOutput,
        CatalogReconciliationWorkflowInput,
        DatasetWriterWorkflowInput,
        ExportActivityInput,
        ExportActivityOutput,
        ExportWorkflowInput,
        IngestRolloutWorkflowInput,
        JobRecord,
        JobStatus,
        PreviewActivityInput,
        PreviewActivityOutput,
        PreviewWorkflowInput,
        PublishActivityInput,
        PublishActivityOutput,
        PublishDatasetWorkflowInput,
        PublishReconciliationActivityInput,
        PublishReconciliationActivityOutput,
        PublishReconciliationWorkflowInput,
        QualityActivityOutput,
        VerificationActivityOutput,
        workflow_id,
    )
    from .names import (
        ALIGN_FRAGMENT_ACTIVITY,
        CATALOG_RECONCILIATION_WORKFLOW,
        COMMIT_FRAGMENT_ACTIVITY,
        CREATE_PREVIEW_ACTIVITY,
        DATASET_WRITER_WORKFLOW,
        EVALUATE_QUALITY_ACTIVITY,
        EXPORT_DATASET_ACTIVITY,
        EXPORT_WORKFLOW,
        INGEST_ROLLOUT_WORKFLOW,
        PREVIEW_WORKFLOW,
        PUBLISH_DATASET_ACTIVITY,
        PUBLISH_DATASET_WORKFLOW,
        PUBLISH_RECONCILIATION_WORKFLOW,
        RECONCILE_CATALOG_ACTIVITY,
        RECONCILE_PUBLICATION_ACTIVITY,
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
                "stage": "completed" if status is JobStatus.SUCCEEDED else status.value.lower(),
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
    @workflow.run
    async def run(self, request: IngestRolloutWorkflowInput) -> JobRecord:
        self._begin(
            job_type=INGEST_ROLLOUT_WORKFLOW,
            project_id=request.project_id,
            resource_id=request.rollout_id,
        )
        try:
            self._stage("verification")
            verified = await _execute_activity(
                VERIFY_RAW_ACTIVITY,
                request.verification,
                VerificationActivityOutput,
                STANDARD_ACTIVITY,
            )
            if verified.report.status is VerificationStatus.REJECTED:
                return self._finish(
                    JobStatus.QUALITY_REJECTED,
                    result={
                        "verification": verified.report.model_dump(mode="json"),
                        "raw_preserved": True,
                        "training_eligible": False,
                    },
                    error_code="RAW_VERIFICATION_REJECTED",
                )

            self._stage("quality")
            quality = await _execute_activity(
                EVALUATE_QUALITY_ACTIVITY,
                request.quality,
                QualityActivityOutput,
                STANDARD_ACTIVITY,
            )
            common_result = {
                "verification": verified.report.model_dump(mode="json"),
                "quality": quality.report.model_dump(mode="json"),
                "raw_preserved": True,
            }
            if quality.report.status is QualityStatus.REJECT:
                return self._finish(
                    JobStatus.QUALITY_REJECTED,
                    result={**common_result, "training_eligible": False},
                    error_code="QUALITY_REJECTED",
                )
            if quality.report.status is QualityStatus.RISK:
                return self._finish(
                    JobStatus.QUALITY_RISK,
                    result={
                        **common_result,
                        "training_eligible": False,
                        "preview_state": "ISOLATED",
                    },
                )

            self._stage("alignment")
            aligned = await _execute_activity(
                ALIGN_FRAGMENT_ACTIVITY,
                request.alignment,
                result_type=AlignmentActivityOutput,
                policy=LONG_ACTIVITY,
            )
            self._stage("lance_commit")
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
                return self._finish(
                    JobStatus.TECHNICAL_FAILED,
                    result={**common_result, "writer_job": writer.model_dump(mode="json")},
                    error_code=writer.error_code or "DATASET_WRITER_FAILED",
                )
            return self._finish(
                JobStatus.SUCCEEDED,
                result={
                    **common_result,
                    "alignment": aligned.staged_manifest.model_dump(mode="json"),
                    "derived": (writer.result or {}).get("derived_ready"),
                    "dataset_version": (writer.result or {}).get("dataset_version"),
                    "training_eligible": True,
                },
            )
        except (asyncio.CancelledError, CancelledError):
            self._cancelled()
            raise
        except (ActivityError, ChildWorkflowError) as exc:
            return self._technical_failure(exc)

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
                str(preview.annotation_revision),
                preview.camera_id,
                preview.view_mode.value,
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
                PreviewActivityInput(request=preview),
                PreviewActivityOutput,
                LONG_ACTIVITY,
            )
            return self._finish(
                JobStatus.SUCCEEDED,
                result={"preview": result.descriptor.model_dump(mode="json")},
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
        self._begin(
            job_type=EXPORT_WORKFLOW,
            project_id=manifest.project_id,
            resource_id=f"{manifest.dataset_id}/{manifest.dataset_version}/{request.format.value}",
        )
        try:
            self._stage("export")
            result = await _execute_activity(
                EXPORT_DATASET_ACTIVITY,
                ExportActivityInput(manifest=manifest, format=request.format),
                ExportActivityOutput,
                LONG_ACTIVITY,
            )
            return self._finish(
                JobStatus.SUCCEEDED,
                result={"export": result.result.model_dump(mode="json")},
            )
        except (asyncio.CancelledError, CancelledError):
            self._cancelled()
            raise
        except ActivityError as exc:
            return self._technical_failure(exc)

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


ALL_WORKFLOWS = (
    IngestRolloutWorkflow,
    DatasetWriterWorkflow,
    PreviewWorkflow,
    PublishDatasetWorkflow,
    ExportWorkflow,
    CatalogReconciliationWorkflow,
    PublishReconciliationWorkflow,
)
