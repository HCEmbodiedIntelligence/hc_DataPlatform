"""Outbox-to-Temporal adapter for the single ingest rollout workflow."""

from __future__ import annotations

import asyncio
from typing import Protocol

from hc_data_platform.core.events import DomainEventEnvelope

from .models import IngestRolloutWorkflowInput, WorkflowKind, workflow_id
from .names import INGEST_ROLLOUT_WORKFLOW
from .service import TemporalWorkflowLauncher


class IngestWorkflowPlanBlocked(RuntimeError):
    code = "INGEST_WORKFLOW_PLAN_BLOCKED"


class IngestWorkflowInputResolver(Protocol):
    """Resolve immutable, persisted processing inputs; never infer them from UI fixtures."""

    def resolve(
        self,
        *,
        project_id: str,
        region_code: str,
        session_id: str,
        rollout_id: str,
        data_package_id: str,
    ) -> IngestRolloutWorkflowInput: ...


class IngestDispatchGuard(Protocol):
    def reserve(self, request: IngestRolloutWorkflowInput, workflow_id: str) -> None: ...


class BlockingIngestWorkflowInputResolver:
    """Fail visibly until deployment injects a persisted ingest-plan resolver."""

    def resolve(
        self,
        *,
        project_id: str,
        region_code: str,
        session_id: str,
        rollout_id: str,
        data_package_id: str,
    ) -> IngestRolloutWorkflowInput:
        del project_id, region_code, session_id, rollout_id, data_package_id
        raise IngestWorkflowPlanBlocked(
            "no persisted dataset/QC/alignment plan is bound to this collection task"
        )


class IngestOutboxHandler:
    EVENT_TYPE = "ingest.workflow.requested.v1"

    def __init__(
        self,
        launcher: TemporalWorkflowLauncher,
        resolver: IngestWorkflowInputResolver,
        guard: IngestDispatchGuard | None = None,
    ) -> None:
        self._launcher = launcher
        self._resolver = resolver
        self._guard = guard

    async def __call__(self, event: DomainEventEnvelope) -> object:
        if event.event_type != self.EVENT_TYPE:
            raise ValueError("unsupported outbox event type")
        payload = event.payload
        region_code = event.region_code
        session_id = _required(payload, "session_id")
        rollout_id = _required(payload, "rollout_id")
        data_package_id = _required(payload, "data_package_id")
        persisted_workflow_id = _required(payload, "workflow_id")
        if region_code is None:
            raise ValueError("ingest workflow events require an exact region")
        expected_workflow_id = workflow_id(
            WorkflowKind.INGEST_ROLLOUT,
            event.project_id,
            f"{region_code}/{rollout_id}",
        )
        if persisted_workflow_id != expected_workflow_id:
            raise ValueError("ingest workflow locator does not match event lineage")
        request = await asyncio.to_thread(
            self._resolver.resolve,
            project_id=event.project_id,
            region_code=region_code,
            session_id=session_id,
            rollout_id=rollout_id,
            data_package_id=data_package_id,
        )
        if (
            request.project_id != event.project_id
            or request.region_code != region_code
            or request.rollout_id != rollout_id
        ):
            raise ValueError("resolved ingest input crosses the persisted event scope")
        if self._guard is not None:
            await asyncio.to_thread(self._guard.reserve, request, persisted_workflow_id)
        return await self._launcher.start(
            workflow_name=INGEST_ROLLOUT_WORKFLOW,
            workflow_input=request,
            workflow_id=persisted_workflow_id,
            job_type=INGEST_ROLLOUT_WORKFLOW,
            project_id=event.project_id,
            resource_id=rollout_id,
        )


def _required(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"ingest outbox payload is missing {name}")
    return value
