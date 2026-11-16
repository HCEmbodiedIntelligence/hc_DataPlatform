"""Submit each selection through the existing idempotent import service."""

import asyncio
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, CancelledError

with workflow.unsafe.imports_passed_through():
    from hc_data_platform.lerobot_imports.oss_batch import BATCH_WORKFLOW, BatchInput, BatchItem
    from .models import JobRecord, JobStatus, WorkflowJobPersistenceActivityInput
    from .names import PERSIST_WORKFLOW_JOB_ACTIVITY, control_task_queue

from .temporal_workflows import _JobLifecycle, _error_code


@activity.defn(name="oss.commit_batch_item")
async def commit_oss_batch_item(item: BatchItem) -> None:
    from hc_data_platform.lerobot_imports.oss_imports import CommitOssImport, commit_source
    from hc_data_platform.security.auth import AuthContext
    from . import activities

    org, project, region = item.organization_id, item.project_id, item.region_code
    auth = AuthContext(
        subject_id=item.actor_id,
        project_ids=frozenset([project]),
        region_codes=frozenset([region]),
        organization_ids=frozenset([org]),
        organization_scope_triples=frozenset([(org, project, region)]),
        organization_scoped_capabilities=frozenset([(org, project, "upload.manage")]),
    )
    command = CommitOssImport.model_validate(item.command)
    with activities._worker_scope(project, region, command.dataset_id, organization_id=org):
        service = activities._require(
            activities.current_activity_dependencies().oss_uploads, "oss_uploads"
        )
        receipt = await activities._invoke(
            "oss_batch_commit",
            lambda: commit_source(
                service,
                auth=auth,
                organization_id=org,
                project_id=project,
                region_code=region,
                command=command,
            ),
        )

        if item.batch_id:
            from hc_data_platform.ingest.upload_batches import register_batch

            await activities._invoke("oss_batch_membership", lambda: register_batch(
                (org, project, region), "oss", item.batch_id, receipt.import_id))


@workflow.defn(name=BATCH_WORKFLOW)
class OssBatchImportWorkflow(_JobLifecycle):
    def __init__(self):
        super().__init__()
        self._metadata_registration = False
        self._waiting_item = None
        self._has_selection = False

    @workflow.signal(name="resume_mcap_registration")
    def resume_mcap_registration(self):
        """Move pending legacy selections onto the short-activity queue."""
        if self._has_selection:
            self._metadata_registration = True
            if self._waiting_item is not None:
                self._waiting_item.cancel()

    async def _register(self, item):
        options = dict(
            start_to_close_timeout=timedelta(hours=2),
            heartbeat_timeout=timedelta(seconds=45),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        if not self._metadata_registration:
            self._waiting_item = workflow.start_activity(commit_oss_batch_item, item, **options)
            try:
                await self._waiting_item
                return
            except (ActivityError, asyncio.CancelledError) as exc:
                cancelled = isinstance(exc, asyncio.CancelledError) or isinstance(exc.cause, CancelledError)
                if not (cancelled and self._metadata_registration):
                    raise
            finally:
                self._waiting_item = None
        await workflow.execute_activity(
            commit_oss_batch_item, item, task_queue=control_task_queue(workflow.info().task_queue),
            **options,
        )

    @workflow.query(name="job")
    def job(self) -> JobRecord:
        return self._record()

    async def persist(self, batch: BatchInput) -> None:
        await workflow.execute_local_activity(
            PERSIST_WORKFLOW_JOB_ACTIVITY,
            WorkflowJobPersistenceActivityInput(
                organization_id=batch.organization_id,
                region_code=batch.region_code,
                job=self._record(),
            ),
            result_type=JobRecord,
            start_to_close_timeout=timedelta(minutes=1),
        )

    @workflow.run
    async def run(self, batch: BatchInput) -> JobRecord:
        command = batch.command
        paths = command.recording_paths or [None]
        self._has_selection = bool(command.recording_paths)
        self._begin(
            job_type=BATCH_WORKFLOW,
            project_id=batch.project_id,
            resource_id=str(command.request_id),
        )
        counts = dict(
            region_code=batch.region_code,
            total=len(paths),
            submitted=batch.offset - batch.failed,
            failed=batch.failed,
            first_failure=batch.first_failure,
            source_name=command.source_uri.rstrip("/").rsplit("/", 1)[-1],
        )
        self._job = self._record().model_copy(update={"result": counts})
        self._stage("submitting")
        await self.persist(batch)
        base = command.model_dump(mode="json", exclude={"request_id", "recording_paths"})
        try:
            for index in range(batch.offset, len(paths)):
                path = paths[index]
                item = BatchItem(
                    organization_id=batch.organization_id,
                    project_id=batch.project_id,
                    region_code=batch.region_code,
                    actor_id=batch.actor_id,
                    batch_id=workflow.info().workflow_id,
                    command={**base, "recording_path": path},
                )
                try:
                    await self._register(item)
                    counts["submitted"] += 1
                except ActivityError as exc:
                    if isinstance(exc.cause, CancelledError):
                        raise asyncio.CancelledError() from exc
                    counts["failed"] += 1
                    if not counts["first_failure"]:
                        counts["first_failure"] = (
                            f"{path or counts['source_name']} · {_error_code(exc)}"
                        )
                self._job = self._record().model_copy(
                    update={"result": dict(counts), "updated_at": workflow.now()}
                )
                if index + 1 < len(paths):
                    await self.persist(batch)
                    if (index + 1) % 100 == 0:
                        workflow.continue_as_new(
                            batch.model_copy(
                                update={
                                    "offset": index + 1,
                                    "failed": counts["failed"],
                                    "first_failure": counts["first_failure"],
                                }
                            )
                        )
            status = JobStatus.TECHNICAL_FAILED if counts["failed"] else JobStatus.SUCCEEDED
            self._finish(status, result=counts, error_message=counts["first_failure"])
        except (asyncio.CancelledError, CancelledError):
            self._cancelled()
            raise
        finally:
            if self._record().status != JobStatus.RUNNING:
                await self.persist(batch)
        return self._record()
