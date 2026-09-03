"""Temporal worker build-ID compatibility routing for adjacent releases."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkerVersioningPlanV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal["hc-temporal-worker-versioning-plan/v1"] = (
        "hc-temporal-worker-versioning-plan/v1"
    )
    task_queues: tuple[str, ...] = Field(min_length=1)
    source_build_id: str = Field(min_length=1, max_length=127)
    target_build_id: str = Field(min_length=1, max_length=127)

    @model_validator(mode="after")
    def validate_plan(self) -> WorkerVersioningPlanV1:
        if self.source_build_id == self.target_build_id:
            raise ValueError("source and target Temporal build IDs must differ")
        if len(set(self.task_queues)) != len(self.task_queues):
            raise ValueError("Temporal task queues must be unique")
        return self


class WorkerBuildIdCompatibilityClient(Protocol):
    def update_worker_build_id_compatibility(
        self, task_queue: str, operation: Any
    ) -> Awaitable[None]: ...


async def install_compatible_build_routing(
    client: WorkerBuildIdCompatibilityClient,
    plan: WorkerVersioningPlanV1,
    *,
    operation_factory: Callable[[str, str], Any] | None = None,
) -> tuple[str, ...]:
    """Make the target compatible/default while retaining the source build set."""

    if operation_factory is None:
        from temporalio.client import BuildIdOpAddNewCompatible

        def build_operation(target: str, source: str) -> Any:
            return BuildIdOpAddNewCompatible(
                target,
                existing_compatible_build_id=source,
                promote_set=True,
            )

        operation_factory = build_operation
    updated: list[str] = []
    for queue in plan.task_queues:
        await client.update_worker_build_id_compatibility(
            queue,
            operation_factory(plan.target_build_id, plan.source_build_id),
        )
        updated.append(queue)
    return tuple(updated)


__all__ = ["WorkerVersioningPlanV1", "install_compatible_build_routing"]
