from __future__ import annotations

import pytest
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from hc_data_platform.lerobot_imports.orchestration import LeRobotEpisodeTaskV1, build_import_plan
from hc_data_platform.workflow.lerobot_workflow import (
    PREPARE_NATIVE_EPISODE,
    UPDATE_NATIVE_STATE,
    LeRobotImportWorkflow,
    LeRobotImportWorkflowInput,
    NativeStateUpdate,
    PreparedNativeEpisode,
)
from hc_data_platform.workflow.models import (
    JobRecord,
    JobStatus,
    WorkflowJobPersistenceActivityInput,
)
from hc_data_platform.workflow.names import PERSIST_WORKFLOW_JOB_ACTIVITY


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_first", [False, True])
async def test_native_import_continues_and_preserves_partial_failure(fail_first: bool) -> None:
    visited: list[int] = []
    states: list[str] = []
    persisted: list[JobRecord] = []

    @activity.defn(name=PREPARE_NATIVE_EPISODE)
    async def prepare(task: LeRobotEpisodeTaskV1) -> PreparedNativeEpisode:
        visited.append(task.source.episode_index)
        if fail_first and task.source.episode_index == 0:
            raise ApplicationError("bad source", type="LEROBOT_INVALID_SOURCE", non_retryable=True)
        return PreparedNativeEpisode(already_ready=True)

    @activity.defn(name=UPDATE_NATIVE_STATE)
    async def update(value: NativeStateUpdate) -> None:
        states.append(value.status)

    @activity.defn(name=PERSIST_WORKFLOW_JOB_ACTIVITY)
    async def persist(value: WorkflowJobPersistenceActivityInput) -> JobRecord:
        persisted.append(value.job)
        return value.job

    task = build_import_plan(
        organization_id="org-native",
        project_id="project-native",
        region_code="global",
        dataset_id="dataset-native",
        collection_task_id="collection-native",
        robot_id="g1",
        raw_upload_id="a" * 32,
        raw_manifest_key="raw/native/manifest.json",
        episode_count=21,
    ).episode_tasks[0]
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as environment,
        Worker(
            environment.client,
            task_queue="native-import-tests",
            workflows=[LeRobotImportWorkflow],
            activities=[prepare, update, persist],
        ),
    ):
        result = await environment.client.execute_workflow(
            LeRobotImportWorkflow.run,
            LeRobotImportWorkflowInput(task=task, episode_count=21),
            id=f"native-import-{fail_first}",
            task_queue="native-import-tests",
        )
    assert visited == list(range(21))
    assert states.count("RUNNING") == 2  # continue-as-new retains counts after Episode 20
    assert states[-1] == ("PARTIALLY_FAILED" if fail_first else "SUCCEEDED")
    assert result.status is (JobStatus.TECHNICAL_FAILED if fail_first else JobStatus.SUCCEEDED)
    assert result.result is not None
    assert result.result["succeeded"] == (20 if fail_first else 21)
    assert result.result["failed"] == int(fail_first)
    assert persisted[-1] == result
