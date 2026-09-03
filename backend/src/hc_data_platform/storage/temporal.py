from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from threading import RLock
from typing import Protocol, TypeVar

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from .errors import LifecycleExecutionBlocked
from .models import (
    LifecycleBatchCommand,
    LifecycleBatchResult,
    LifecycleExecutionCandidate,
    LifecycleExecutionRequest,
    LifecycleExecutionResult,
    evaluate_execution_protection,
)

STORAGE_LIFECYCLE_WORKFLOW = "StorageLifecycleExecutionWorkflow"
APPLY_LIFECYCLE_BATCH_ACTIVITY = "storage.apply_lifecycle_batch"


class LifecycleBatchExecutor(Protocol):
    def apply_batch(self, command: LifecycleBatchCommand) -> LifecycleBatchResult: ...


class LifecycleExecutorNotConfigured(RuntimeError):
    pass


class DisabledLifecycleBatchExecutor:
    def apply_batch(self, command: LifecycleBatchCommand) -> LifecycleBatchResult:
        del command
        raise LifecycleExecutorNotConfigured(
            "storage lifecycle physical execution is not configured"
        )


class InMemoryLifecycleBatchExecutor:
    """Idempotent sandbox executor used by recovery and replay tests."""

    def __init__(self) -> None:
        self._results: dict[
            tuple[str, int], tuple[LifecycleBatchCommand, LifecycleBatchResult]
        ] = {}
        self._lock = RLock()

    def apply_batch(self, command: LifecycleBatchCommand) -> LifecycleBatchResult:
        key = (command.execution_id, command.batch_index)
        with self._lock:
            existing = self._results.get(key)
            if existing is not None:
                if existing[0] != command:
                    raise ApplicationError(
                        "lifecycle batch identity was reused with different candidates",
                        type="LIFECYCLE_BATCH_CONFLICT",
                        non_retryable=True,
                    )
                return existing[1].model_copy(update={"replayed": True})
            result = LifecycleBatchResult(
                execution_id=command.execution_id,
                batch_index=command.batch_index,
                processed_instance_ids=tuple(
                    candidate.physical_instance_id for candidate in command.candidates
                ),
            )
            self._results[key] = (command, result)
            return result


_executor: LifecycleBatchExecutor = DisabledLifecycleBatchExecutor()


def configure_lifecycle_batch_executor(executor: LifecycleBatchExecutor) -> None:
    global _executor
    _executor = executor


def _execution_request(command: LifecycleBatchCommand) -> LifecycleExecutionRequest:
    return LifecycleExecutionRequest(
        execution_id=command.execution_id,
        organization_id=command.organization_id,
        project_id=command.project_id,
        region_code=command.region_code,
        policy_id=command.policy_id,
        policy_version=command.policy_version,
        action=command.action,
        production=command.production,
        production_execution_approved=command.production_execution_approved,
        approval_id=command.approval_id,
        plan_hash=command.plan_hash,
        candidates=command.candidates,
    )


@activity.defn(name=APPLY_LIFECYCLE_BATCH_ACTIVITY)
async def storage_apply_lifecycle_batch(
    command: LifecycleBatchCommand,
) -> LifecycleBatchResult:
    guard = evaluate_execution_protection(_execution_request(command))
    if guard.status == "BLOCKED":
        raise ApplicationError(
            ";".join(guard.blocked_reasons),
            type="LIFECYCLE_PROTECTION_BLOCKED",
            non_retryable=True,
        )
    try:
        return _executor.apply_batch(command)
    except LifecycleExecutionBlocked as exc:
        raise ApplicationError(
            str(exc),
            type="LIFECYCLE_PROTECTION_BLOCKED",
            non_retryable=True,
        ) from exc
    except LifecycleExecutorNotConfigured as exc:
        raise ApplicationError(
            str(exc),
            type="LIFECYCLE_EXECUTOR_NOT_CONFIGURED",
            non_retryable=True,
        ) from exc


_T = TypeVar("_T")


def _batches(values: Sequence[_T], size: int) -> tuple[tuple[_T, ...], ...]:
    return tuple(tuple(values[index : index + size]) for index in range(0, len(values), size))


@workflow.defn(name=STORAGE_LIFECYCLE_WORKFLOW)
class StorageLifecycleExecutionWorkflow:
    @workflow.run
    async def run(self, request: LifecycleExecutionRequest) -> LifecycleExecutionResult:
        guard = evaluate_execution_protection(request)
        if guard.status == "BLOCKED":
            return guard

        processed: list[str] = []
        replayed_batches = 0
        candidate_batches = _batches(request.candidates, request.batch_size)
        for batch_index, raw_batch in enumerate(candidate_batches):
            batch: tuple[LifecycleExecutionCandidate, ...] = tuple(raw_batch)
            result = await workflow.execute_activity(
                APPLY_LIFECYCLE_BATCH_ACTIVITY,
                LifecycleBatchCommand(
                    execution_id=request.execution_id,
                    organization_id=request.organization_id,
                    project_id=request.project_id,
                    region_code=request.region_code,
                    policy_id=request.policy_id,
                    policy_version=request.policy_version,
                    action=request.action,
                    production=request.production,
                    production_execution_approved=request.production_execution_approved,
                    approval_id=request.approval_id,
                    plan_hash=request.plan_hash,
                    batch_index=batch_index,
                    final_batch=batch_index == len(candidate_batches) - 1,
                    candidates=batch,
                ),
                result_type=LifecycleBatchResult,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(milliseconds=100),
                    backoff_coefficient=2,
                    maximum_interval=timedelta(seconds=5),
                    maximum_attempts=5,
                    non_retryable_error_types=[
                        "LIFECYCLE_PROTECTION_BLOCKED",
                        "LIFECYCLE_EXECUTOR_NOT_CONFIGURED",
                        "LIFECYCLE_BATCH_CONFLICT",
                    ],
                ),
            )
            processed.extend(result.processed_instance_ids)
            replayed_batches += int(result.replayed)
        return LifecycleExecutionResult(
            execution_id=request.execution_id,
            status="COMPLETED",
            processed_instance_ids=tuple(processed),
            replayed_batches=replayed_batches,
        )


ALL_STORAGE_WORKFLOWS = (StorageLifecycleExecutionWorkflow,)
ALL_STORAGE_ACTIVITIES = (storage_apply_lifecycle_batch,)
