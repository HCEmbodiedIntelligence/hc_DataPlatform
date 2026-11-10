from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

with workflow.unsafe.imports_passed_through():
    from hc_data_platform.workflow.names import (
        COMMIT_CONTINUOUS_EPISODE_BUNDLE_ACTIVITY,
        CONTINUOUS_CONTROL_ACTIVITIES,
        CONTINUOUS_EPISODE_WORKFLOW,
        CREATE_ANNOTATION_TASK_ACTIVITY,
        UPDATE_CONTINUOUS_EPISODE_STATE_ACTIVITY,
        control_task_queue,
    )
    from hc_data_platform.workflow.temporal_workflows import STANDARD_ACTIVITY, _execute_activity


class ProbeLifecycle:
    def __init__(self):
        self.completed = 0
        self.advance_requested = False

    @workflow.signal
    def advance(self):
        self.advance_requested = True

    @workflow.query
    def progress(self) -> int:
        return self.completed


@workflow.defn(name=CONTINUOUS_EPISODE_WORKFLOW)
class ControlProbe(ProbeLifecycle):
    @workflow.run
    async def run(self, names: str | list[str]) -> str:
        for index, name in enumerate([names] if isinstance(names, str) else names):
            if index:
                await workflow.wait_condition(lambda: self.advance_requested)
            result = await _execute_activity(name, "ready", str, STANDARD_ACTIVITY)
            self.completed += 1
        return result


@workflow.defn(name=CONTINUOUS_EPISODE_WORKFLOW)
class LegacyControlProbe(ProbeLifecycle):
    @workflow.run
    async def run(self, names: str | list[str]) -> str:
        # Exact pre-change helper behavior, before the new patch marker.
        from hc_data_platform.workflow.temporal_workflows import ACTIVITY_RETRY_POLICY

        for index, name in enumerate([names] if isinstance(names, str) else names):
            if index:
                await workflow.wait_condition(lambda: self.advance_requested)
            result = await workflow.execute_activity(
                name,
                "ready",
                result_type=str,
                start_to_close_timeout=STANDARD_ACTIVITY.start_to_close,
                schedule_to_close_timeout=STANDARD_ACTIVITY.schedule_to_close,
                heartbeat_timeout=STANDARD_ACTIVITY.heartbeat,
                retry_policy=ACTIVITY_RETRY_POLICY,
            )
            self.completed += 1
        return result


@activity.defn(name=UPDATE_CONTINUOUS_EPISODE_STATE_ACTIVITY)
async def state(value: str) -> str:
    return value


@activity.defn(name=CREATE_ANNOTATION_TASK_ACTIVITY)
async def annotation(value: str) -> str:
    return value


@pytest.mark.asyncio
async def test_state_finishes_while_main_activity_capacity_is_full():
    environment = await WorkflowEnvironment.start_time_skipping()
    started, release = asyncio.Event(), asyncio.Event()

    @activity.defn(name=COMMIT_CONTINUOUS_EPISODE_BUNDLE_ACTIVITY)
    async def heavy(value: str) -> str:
        started.set()
        await release.wait()
        return value

    queue = "control-capacity-" + uuid4().hex
    try:
        async with (
            Worker(
                environment.client,
                task_queue=queue,
                workflows=[ControlProbe],
                activities=[heavy, state],
                max_concurrent_activities=1,
            ),
            Worker(
                environment.client,
                task_queue=control_task_queue(queue),
                activities=[state],
                max_concurrent_activities=1,
            ),
        ):
            handle = await environment.client.start_workflow(
                ControlProbe.run,
                COMMIT_CONTINUOUS_EPISODE_BUNDLE_ACTIVITY,
                id=uuid4().hex,
                task_queue=queue,
            )
            try:
                await asyncio.wait_for(started.wait(), timeout=10)
                fast = await environment.client.start_workflow(
                    ControlProbe.run,
                    UPDATE_CONTINUOUS_EPISODE_STATE_ACTIVITY,
                    id=uuid4().hex,
                    task_queue=queue,
                )
                assert await asyncio.wait_for(fast.result(), timeout=5) == "ready"
                assert not release.is_set()
                history = await fast.fetch_history()
                scheduled = [
                    e.activity_task_scheduled_event_attributes
                    for e in history.events
                    if e.HasField("activity_task_scheduled_event_attributes")
                ]
                assert [a.task_queue.name for a in scheduled] == [control_task_queue(queue)]
                await Replayer(workflows=[ControlProbe]).replay_workflow(history)
            finally:
                release.set()
                await asyncio.wait_for(handle.result(), timeout=10)
    finally:
        await environment.shutdown()


@pytest.mark.asyncio
async def test_legacy_control_history_replays_without_changing_scheduled_queue():
    environment = await WorkflowEnvironment.start_time_skipping()
    queue = "control-legacy-" + uuid4().hex
    try:
        async with Worker(
            environment.client, task_queue=queue, workflows=[LegacyControlProbe], activities=[state]
        ):
            handle = await environment.client.start_workflow(
                LegacyControlProbe.run,
                UPDATE_CONTINUOUS_EPISODE_STATE_ACTIVITY,
                id=uuid4().hex,
                task_queue=queue,
            )
            assert await handle.result() == "ready"
            history = await handle.fetch_history()
        await Replayer(workflows=[ControlProbe]).replay_workflow(history)
    finally:
        await environment.shutdown()


def test_control_worker_registers_only_short_activities_and_main_retains_legacy_routes():
    from hc_data_platform.workflow.worker import discover_temporal_registrations

    workflows, controls = discover_temporal_registrations("control")
    _, main = discover_temporal_registrations("main")
    names = {activity._Definition.must_from_callable(item).name for item in controls}
    assert names == CONTINUOUS_CONTROL_ACTIVITIES
    assert not workflows
    assert set(controls) <= set(main)


@pytest.mark.asyncio
async def test_inflight_legacy_execution_routes_its_future_control_step_after_upgrade():
    environment = await WorkflowEnvironment.start_time_skipping()
    queue = "control-upgrade-" + uuid4().hex
    try:
        async with Worker(
            environment.client,
            task_queue=queue,
            workflows=[LegacyControlProbe],
            activities=[state, annotation],
            max_cached_workflows=0,
        ):
            handle = await environment.client.start_workflow(
                LegacyControlProbe.run,
                [UPDATE_CONTINUOUS_EPISODE_STATE_ACTIVITY, CREATE_ANNOTATION_TASK_ACTIVITY],
                id=uuid4().hex,
                task_queue=queue,
            )

            async def first_step_finished():
                while await handle.query("progress") != 1:
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(first_step_finished(), timeout=10)
        async with (
            Worker(
                environment.client,
                task_queue=queue,
                workflows=[ControlProbe],
                activities=[state, annotation],
            ),
            Worker(
                environment.client,
                task_queue=control_task_queue(queue),
                activities=[state, annotation],
            ),
        ):
            await handle.signal("advance")
            assert await asyncio.wait_for(handle.result(), timeout=30) == "ready"
            history = await handle.fetch_history()
            scheduled = [
                e.activity_task_scheduled_event_attributes.task_queue.name
                for e in history.events
                if e.HasField("activity_task_scheduled_event_attributes")
            ]
            assert scheduled == [queue, control_task_queue(queue)]
        await Replayer(workflows=[ControlProbe]).replay_workflow(history)
    finally:
        await environment.shutdown()
