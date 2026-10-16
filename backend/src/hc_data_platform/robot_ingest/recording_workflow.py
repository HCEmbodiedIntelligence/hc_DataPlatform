"""Durable Worker preparation before an unsliced recording enters the workbench."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class RobotRecordingWorkflow:
    @workflow.run
    async def run(self, event: dict) -> None:
        # Keep replay of already scheduled work compatible with the old policy.
        responsive = workflow.patched("recording-heartbeat-recovery-v1")
        try:
            await workflow.execute_activity(
                "robot.recording.prepare",
                event,
                start_to_close_timeout=timedelta(hours=24),
                heartbeat_timeout=timedelta(seconds=30 if responsive else 300),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    initial_interval=timedelta(seconds=2 if responsive else 1),
                    maximum_interval=timedelta(seconds=10) if responsive else None,
                ),
            )
        except ActivityError:
            await workflow.execute_activity(
                "robot.recording.failed", event, start_to_close_timeout=timedelta(minutes=1)
            )
            raise
