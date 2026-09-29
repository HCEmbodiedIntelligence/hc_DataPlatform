from __future__ import annotations

import asyncio

import pytest
from temporalio import activity, workflow
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
    IngestRolloutWorkflowInput,
    JobRecord,
    JobStatus,
    WorkflowJobPersistenceActivityInput,
)
from hc_data_platform.workflow.names import INGEST_ROLLOUT_WORKFLOW, PERSIST_WORKFLOW_JOB_ACTIVITY


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_first", [False, True])
@pytest.mark.parametrize("only_episode", [None, 20])
async def test_native_import_continues_and_preserves_partial_failure(
    fail_first: bool,
    only_episode: int | None,
) -> None:
    visited: list[int] = []
    states: list[str] = []
    persisted: list[JobRecord] = []
    active = peak = 0

    @activity.defn(name=PREPARE_NATIVE_EPISODE)
    async def prepare(task: LeRobotEpisodeTaskV1) -> PreparedNativeEpisode:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.03)
        active -= 1
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
            LeRobotImportWorkflowInput(task=task, episode_count=21, only_episode=only_episode),
            id=f"native-import-{fail_first}",
            task_queue="native-import-tests",
        )
    if only_episode is not None:
        assert visited == [20]
        assert states.count("RUNNING") == 1
        assert result.status is JobStatus.SUCCEEDED
        assert result.result["episode_count"] == 1
        return
    assert sorted(visited) == list(range(21))
    assert 2 <= peak <= 4
    assert states.count("RUNNING") == 2  # continue-as-new retains counts after Episode 20
    assert states[-1] == ("PARTIALLY_FAILED" if fail_first else "SUCCEEDED")
    assert result.status is (JobStatus.TECHNICAL_FAILED if fail_first else JobStatus.SUCCEEDED)
    assert result.result is not None
    assert result.result["succeeded"] == (20 if fail_first else 21)
    assert result.result["failed"] == int(fail_first)
    assert persisted[-1] == result


@workflow.defn(name=INGEST_ROLLOUT_WORKFLOW, sandboxed=False)
class EpisodeFailureWorkflow:
    @workflow.run
    async def run(self, request: IngestRolloutWorkflowInput) -> JobRecord:
        if request.rollout_id == "r1" and request.automatic_qc_run_id == "child_exception":
            raise ApplicationError("failed episode", type="EPISODE_FAILED", non_retryable=True)
        failed = request.rollout_id == "r1"
        return JobRecord(
            job_id=str(workflow.uuid4()),
            workflow_id=workflow.info().workflow_id,
            project_id=request.project_id,
            resource_id=request.rollout_id,
            job_type=INGEST_ROLLOUT_WORKFLOW,
            status=JobStatus.TECHNICAL_FAILED if failed else JobStatus.SUCCEEDED,
            error_code="ALIGNMENT_ATTEMPT_IMMUTABLE" if failed else None,
            created_at=workflow.now(),
            updated_at=workflow.now(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cache", "child_result", "child_exception"])
async def test_failed_episode_is_recorded_once_and_later_episodes_finish(failure: str) -> None:
    from hc_data_platform.lerobot_imports.cache import SourceCacheLimitExceeded
    from hc_data_platform.workflow.activities import _invoke
    from tests.workflow.test_temporal_workflows import _ingest_input

    visited = []
    states = []

    @activity.defn(name=PREPARE_NATIVE_EPISODE)
    async def prepare(task: LeRobotEpisodeTaskV1) -> PreparedNativeEpisode:
        index = task.source.episode_index
        visited.append(index)
        if failure == "cache" and index == 1:

            def fail():
                raise SourceCacheLimitExceeded("active source files exceed cache limit")

            return await _invoke("native_episode_prepare", fail)
        payload = _ingest_input().model_dump(mode="json")

        def rename(value):
            if isinstance(value, dict):
                return {
                    key: f"r{index}" if key == "rollout_id" else rename(item)
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [rename(item) for item in value]
            return value

        payload = rename(payload)
        payload["automatic_qc_run_id"] = failure
        return PreparedNativeEpisode(request=IngestRolloutWorkflowInput.model_validate(payload))

    @activity.defn(name=UPDATE_NATIVE_STATE)
    async def update(value: NativeStateUpdate) -> None:
        states.append(value)

    @activity.defn(name=PERSIST_WORKFLOW_JOB_ACTIVITY)
    async def persist(value: WorkflowJobPersistenceActivityInput) -> JobRecord:
        return value.job

    task = build_import_plan(
        organization_id="organization-a",
        project_id="p1",
        region_code="cn",
        dataset_id="d1",
        collection_task_id="collection-native",
        robot_id="g1",
        raw_upload_id="a" * 32,
        raw_manifest_key="raw/native/manifest.json",
        episode_count=3,
    ).episode_tasks[0]
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as environment,
        Worker(
            environment.client,
            task_queue="native-skip-tests",
            workflows=[LeRobotImportWorkflow, EpisodeFailureWorkflow],
            activities=[prepare, update, persist],
        ),
    ):
        result = await environment.client.execute_workflow(
            LeRobotImportWorkflow.run,
            LeRobotImportWorkflowInput(task=task, episode_count=3),
            id=f"skip-failed-{failure}",
            task_queue="native-skip-tests",
        )
    assert sorted(visited) == [0, 1, 2]
    assert result.result["succeeded"] == 2
    assert result.result["failed"] == 1
    assert states[-1].status == "PARTIALLY_FAILED"
    episodes = sorted(
        [state for state in states if state.status.startswith("EPISODE_")],
        key=lambda state: state.task.source.episode_index,
    )
    assert [state.task.source.episode_index for state in episodes] == [0, 1, 2]
    assert episodes[-1].episode_job.status is JobStatus.SUCCEEDED
    if failure == "cache":
        assert episodes[1].error_code == "SOURCE_CACHE_LIMIT_EXCEEDED"
