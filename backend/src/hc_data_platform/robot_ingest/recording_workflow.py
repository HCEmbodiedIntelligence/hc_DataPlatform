"""Durable Worker preparation before an unsliced recording enters the workbench."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class RobotRecordingWorkflow:
    @workflow.run
    async def run(self, event: dict) -> None:
        try:
            await workflow.execute_activity(
                "robot.recording.prepare",
                event,
                start_to_close_timeout=timedelta(hours=24),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        except ActivityError:
            await workflow.execute_activity(
                "robot.recording.failed", event, start_to_close_timeout=timedelta(minutes=1)
            )
            raise
