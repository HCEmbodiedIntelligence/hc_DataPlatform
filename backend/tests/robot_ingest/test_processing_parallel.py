from __future__ import annotations

import asyncio

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from hc_data_platform.robot_ingest.processing_workflow import RobotIngestProcessingWorkflow


@pytest.mark.asyncio
async def test_robot_episode_parallelism_is_bounded_and_preserves_failures_and_continuations():
    done, failures = set(), []
    active = peak = 0
    finished = False

    @activity.defn(name="robot.processing.discover")
    async def discover(request: dict) -> list[int]:
        return [i for i in range(21) if i not in done]

    @activity.defn(name="robot.processing.episode")
    async def episode(request: dict) -> None:
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        try:
            await asyncio.sleep(0.05)
            if request["index"] == 1:
                raise ApplicationError(
                    "bad episode", type="TEST_FAILED_EPISODE", non_retryable=True
                )
            done.add(request["index"])
        finally:
            active -= 1

    @activity.defn(name="robot.processing.fail")
    async def fail(request: dict) -> None:
        failures.append(request["index"])
        done.add(request["index"])

    @activity.defn(name="robot.processing.finish")
    async def finish(request: dict) -> None:
        nonlocal finished
        assert active == 0 and done == set(range(21))
        finished = True

    task = dict(
        organization_id="org",
        project_id="p",
        region_code="r",
        upload_id="upload",
        raw_source_id="raw",
        generation=0,
        workflow_id="parallel-robot",
        max_concurrent_episodes=3,
    )
    async with await WorkflowEnvironment.start_time_skipping() as environment, Worker(
        environment.client,
        task_queue="robot-parallel-tests",
        workflows=[RobotIngestProcessingWorkflow],
        activities=[discover, episode, fail, finish],
    ):
        handle = await environment.client.start_workflow(
            RobotIngestProcessingWorkflow.run,
            task,
            id=task["workflow_id"],
            task_queue="robot-parallel-tests",
        )
        await asyncio.wait_for(handle.result(), 30)
        await Replayer(workflows=[RobotIngestProcessingWorkflow]).replay_workflow(
            await handle.fetch_history()
        )
    assert 2 <= peak <= 3
    assert failures == [1]
    assert finished
