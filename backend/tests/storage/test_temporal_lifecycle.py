from __future__ import annotations

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from hc_data_platform.storage.models import (
    LifecycleBatchCommand,
    LifecycleBatchResult,
    LifecycleExecutionCandidate,
    LifecycleExecutionRequest,
    LifecyclePolicyAction,
    ObjectRole,
)
from hc_data_platform.storage.temporal import (
    ALL_STORAGE_ACTIVITIES,
    ALL_STORAGE_WORKFLOWS,
    InMemoryLifecycleBatchExecutor,
    StorageLifecycleExecutionWorkflow,
    configure_lifecycle_batch_executor,
)


def sandbox_request(
    *,
    execution_id: str = "sandbox-execution",
) -> LifecycleExecutionRequest:
    return LifecycleExecutionRequest(
        execution_id=execution_id,
        project_id="project-a",
        policy_id="cache-cleanup-policy",
        policy_version=1,
        action=LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE,
        production=False,
        batch_size=1,
        candidates=(
            LifecycleExecutionCandidate(
                physical_instance_id="cache-1",
                logical_object_id="logical-cache-1",
                object_role=ObjectRole.REBUILDABLE_DERIVATIVE,
                rebuild_source_id="raw-source-1",
                protection_verified=True,
            ),
            LifecycleExecutionCandidate(
                physical_instance_id="cache-2",
                logical_object_id="logical-cache-2",
                object_role=ObjectRole.REBUILDABLE_DERIVATIVE,
                rebuild_source_id="raw-source-2",
                protection_verified=True,
            ),
        ),
    )


class CrashAfterFirstDurableBatch(InMemoryLifecycleBatchExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def apply_batch(self, command: LifecycleBatchCommand) -> LifecycleBatchResult:
        self.calls += 1
        result = super().apply_batch(command)
        if self.calls == 1:
            raise RuntimeError("connection lost after durable lifecycle checkpoint")
        return result


@pytest.mark.asyncio
async def test_lifecycle_worker_recovers_idempotently_and_replays_history() -> None:
    environment = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    )
    executor = CrashAfterFirstDurableBatch()
    configure_lifecycle_batch_executor(executor)
    try:
        async with Worker(
            environment.client,
            task_queue="storage-lifecycle-tests",
            workflows=list(ALL_STORAGE_WORKFLOWS),
            activities=list(ALL_STORAGE_ACTIVITIES),
        ):
            handle = await environment.client.start_workflow(
                StorageLifecycleExecutionWorkflow.run,
                sandbox_request(),
                id="storage-lifecycle-recovery",
                task_queue="storage-lifecycle-tests",
            )
            result = await handle.result()
            assert result.status == "COMPLETED"
            assert result.processed_instance_ids == ("cache-1", "cache-2")
            assert result.replayed_batches == 1
            assert executor.calls == 3

            history = await handle.fetch_history()
            await Replayer(
                workflows=list(ALL_STORAGE_WORKFLOWS),
                data_converter=pydantic_data_converter,
            ).replay_workflow(history)
    finally:
        await environment.shutdown()


@pytest.mark.asyncio
async def test_lifecycle_worker_blocks_production_before_any_activity() -> None:
    environment = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    )
    executor = InMemoryLifecycleBatchExecutor()
    configure_lifecycle_batch_executor(executor)
    request = sandbox_request(execution_id="production-blocked").model_copy(
        update={"production": True, "production_execution_approved": True}
    )
    try:
        async with Worker(
            environment.client,
            task_queue="storage-lifecycle-production-gate",
            workflows=list(ALL_STORAGE_WORKFLOWS),
            activities=list(ALL_STORAGE_ACTIVITIES),
        ):
            handle = await environment.client.start_workflow(
                StorageLifecycleExecutionWorkflow.run,
                request,
                id="storage-lifecycle-production-blocked",
                task_queue="storage-lifecycle-production-gate",
            )
            result = await handle.result()
            assert result.status == "BLOCKED"
            assert result.processed_instance_ids == ()
            assert result.blocked_reasons == ("OPEN_10_PRODUCTION_EXECUTION_DISABLED",)

            history = await handle.fetch_history()
            await Replayer(
                workflows=list(ALL_STORAGE_WORKFLOWS),
                data_converter=pydantic_data_converter,
            ).replay_workflow(history)
    finally:
        await environment.shutdown()
