"""Temporal orchestration. Persist each episode independently and replay by stable IDs."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from .processing_store import WORKFLOW_NAME, ProcessingTask


@workflow.defn(name=WORKFLOW_NAME)
class RobotIngestProcessingWorkflow:
    async def call(self, name: str, arg: dict[str, Any], *, processing: bool = False) -> Any:
        return await workflow.execute_activity(
            f"robot.processing.{name}",
            arg,
            start_to_close_timeout=timedelta(hours=1) if processing else timedelta(minutes=5),
            heartbeat_timeout=timedelta(seconds=60) if processing else None,
            retry_policy=RetryPolicy(
                maximum_attempts=0 if name in {"fail", "finish"} else 3,
                initial_interval=timedelta(seconds=1),
            ),
        )

    async def failure(self, task: dict[str, Any], error: ActivityError, index: int | None) -> None:
        cause = error.cause
        code, retryable = "ROBOT_PROCESSING_TECHNICAL_FAILURE", True
        if isinstance(cause, ApplicationError):
            code = cause.type or code
            if cause.details:
                retryable = bool(cause.details[0].get("retryable", True))
        await self.call(
            "fail", {"task": task, "index": index, "error_code": code, "retryable": retryable}
        )

    @workflow.run
    async def run(self, request: dict[str, Any]) -> None:
        task = ProcessingTask.model_validate(request)
        try:
            indexes = await self.call("discover", {"task": request})
        except ActivityError as exc:
            await self.failure(request, exc, None)
            return
        for position, index in enumerate(indexes):
            try:
                await self.call("episode", {"task": request, "index": index}, processing=True)
            except ActivityError as exc:
                await self.failure(request, exc, index)
            # Limit Temporal history. Discovery reuses the durable identity set,
            # and returns only remaining PENDING/PROCESSING episodes.
            if position == 19 and len(indexes) > 20:
                workflow.continue_as_new(task.model_dump())
        await self.call("finish", {"task": request})
