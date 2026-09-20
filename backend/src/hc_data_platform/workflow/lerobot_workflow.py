"""Durable native LeRobot dispatch; process episodes sequentially per import."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, CancelledError, ChildWorkflowError

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel, ConfigDict, Field

    from hc_data_platform.lerobot_imports.orchestration import LeRobotEpisodeTaskV1

    from .models import (
        IngestRolloutWorkflowInput,
        JobRecord,
        JobStatus,
        WorkflowJobPersistenceActivityInput,
        workflow_id,
    )
    from .names import INGEST_ROLLOUT_WORKFLOW, PERSIST_WORKFLOW_JOB_ACTIVITY

from .temporal_workflows import _JobLifecycle

LEROBOT_IMPORT_WORKFLOW = "LeRobotImportWorkflow"
PREPARE_NATIVE_EPISODE = "workflow.prepare_lerobot_episode"
UPDATE_NATIVE_STATE = "workflow.update_lerobot_state"


class LeRobotImportWorkflowInput(BaseModel):
    model_config = ConfigDict(frozen=True)
    task: LeRobotEpisodeTaskV1
    episode_count: int = Field(ge=1, le=10_000)
    next_episode: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    last_error_code: str | None = None


class PreparedNativeEpisode(BaseModel):
    request: IngestRolloutWorkflowInput | None = None
    already_ready: bool = False


class NativeStateUpdate(BaseModel):
    task: LeRobotEpisodeTaskV1
    status: str
    episode_job: JobRecord | None = None
    error_code: str | None = None


@activity.defn(name=PREPARE_NATIVE_EPISODE)
async def prepare_lerobot_episode(task: LeRobotEpisodeTaskV1) -> PreparedNativeEpisode:
    from . import activities

    with activities._worker_scope(
        task.project_id,
        task.region_code,
        task.source.raw_upload_id,
        organization_id=task.organization_id,
    ):
        pipeline = activities._require(
            activities._dependencies.lerobot_pipeline, "lerobot_pipeline"
        )

        def prepare() -> PreparedNativeEpisode:
            if pipeline.episode_ready(task):
                return PreparedNativeEpisode(already_ready=True)
            return PreparedNativeEpisode(request=pipeline.prepare(task))

        return await activities._invoke("native_episode_prepare", prepare)


@activity.defn(name=UPDATE_NATIVE_STATE)
async def update_lerobot_state(update: NativeStateUpdate) -> None:
    from . import activities

    task = update.task
    with activities._worker_scope(
        task.project_id,
        task.region_code,
        task.source.raw_upload_id,
        organization_id=task.organization_id,
    ):
        pipeline = activities._require(
            activities._dependencies.lerobot_pipeline, "lerobot_pipeline"
        )
        await activities._invoke("native_episode_state", lambda: pipeline.update_state(update))


@workflow.defn(name=LEROBOT_IMPORT_WORKFLOW)
class LeRobotImportWorkflow(_JobLifecycle):
    async def _call(self, name: str, argument: Any, result_type: Any = None) -> Any:
        return await workflow.execute_activity(
            name,
            argument,
            result_type=result_type,
            start_to_close_timeout=timedelta(hours=1),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2)),
        )

    async def _persist(self, task: LeRobotEpisodeTaskV1) -> None:
        await workflow.execute_local_activity(
            PERSIST_WORKFLOW_JOB_ACTIVITY,
            WorkflowJobPersistenceActivityInput(
                organization_id=task.organization_id,
                region_code=task.region_code,
                job=self._record(),
            ),
            result_type=JobRecord,
            start_to_close_timeout=timedelta(minutes=1),
        )

    @workflow.run
    async def run(self, request: LeRobotImportWorkflowInput) -> JobRecord:
        task = request.task
        self._begin(
            job_type=LEROBOT_IMPORT_WORKFLOW,
            project_id=task.project_id,
            resource_id=task.source.raw_upload_id,
        )
        self._job_persistence_enabled = True
        succeeded, failed = request.succeeded, request.failed
        last_error = request.last_error_code
        await self._call(UPDATE_NATIVE_STATE, NativeStateUpdate(task=task, status="RUNNING"))
        try:
            for index in range(request.next_episode, request.episode_count):
                episode_task = task.model_copy(
                    update={
                        "task_id": f"lerobot:{task.source.raw_upload_id}:episode:{index}",
                        "source": task.source.model_copy(update={"episode_index": index}),
                    }
                )
                self._stage(f"episode_{index + 1}_of_{request.episode_count}")
                await self._persist(task)
                try:
                    prepared = await self._call(
                        PREPARE_NATIVE_EPISODE, episode_task, PreparedNativeEpisode
                    )
                    if prepared.already_ready:
                        succeeded += 1
                    else:
                        episode_input = prepared.request
                        assert episode_input is not None
                        job = await workflow.execute_child_workflow(
                            INGEST_ROLLOUT_WORKFLOW,
                            episode_input,
                            id=workflow_id(
                                "ingest-rollout",
                                task.project_id,
                                f"{task.region_code}/{episode_input.rollout_id}/{workflow.info().workflow_id}",
                            ),
                            result_type=JobRecord,
                        )
                        if job.status is JobStatus.SUCCEEDED:
                            succeeded += 1
                        else:
                            failed += 1
                            last_error = job.error_code or job.status.value
                        await self._call(
                            UPDATE_NATIVE_STATE,
                            NativeStateUpdate(
                                task=episode_task,
                                status="EPISODE_DONE",
                                episode_job=job,
                                error_code=last_error,
                            ),
                        )
                except (ActivityError, ChildWorkflowError) as exc:
                    from .temporal_workflows import _error_code

                    failed += 1
                    last_error = _error_code(exc)
                    await self._call(
                        UPDATE_NATIVE_STATE,
                        NativeStateUpdate(
                            task=episode_task, status="EPISODE_FAILED", error_code=last_error
                        ),
                    )
                if (index + 1) % 20 == 0 and index + 1 < request.episode_count:
                    workflow.continue_as_new(
                        request.model_copy(
                            update={
                                "next_episode": index + 1,
                                "succeeded": succeeded,
                                "failed": failed,
                                "last_error_code": last_error,
                            }
                        )
                    )
            status = "SUCCEEDED" if failed == 0 else "PARTIALLY_FAILED" if succeeded else "FAILED"
            await self._call(
                UPDATE_NATIVE_STATE,
                NativeStateUpdate(task=task, status=status, error_code=last_error),
            )
            job = self._finish(
                JobStatus.SUCCEEDED if failed == 0 else JobStatus.TECHNICAL_FAILED,
                result={
                    "episode_count": request.episode_count,
                    "succeeded": succeeded,
                    "failed": failed,
                    "dataset_id": task.dataset_id,
                    "raw_preserved": True,
                },
                error_code=last_error,
            )
            await self._persist(task)
            return job
        except (asyncio.CancelledError, CancelledError):
            self._cancelled()
            await self._persist(task)
            await self._call(UPDATE_NATIVE_STATE, NativeStateUpdate(task=task, status="CANCELLED"))
            raise
