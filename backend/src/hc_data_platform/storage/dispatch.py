"""Durable outbox adapter for approval-bound lifecycle Temporal executions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.security.auth import AuthContext, Role
from hc_data_platform.workflow.service import TemporalWorkflowLauncher

from .models import LifecycleExecutionCandidate, LifecycleExecutionRequest
from .ports import StorageRepository
from .service import StorageGovernanceService
from .temporal import STORAGE_LIFECYCLE_WORKFLOW


class LifecycleExecutionPlanUnavailable(RuntimeError):
    code = "LIFECYCLE_EXECUTION_PLAN_UNAVAILABLE"


class StorageExecutionInputResolver(Protocol):
    def resolve(self, *, project_id: str, execution_id: str) -> LifecycleExecutionRequest: ...


class RepositoryStorageExecutionInputResolver:
    def __init__(self, repository: StorageRepository) -> None:
        self._repository = repository

    def resolve(self, *, project_id: str, execution_id: str) -> LifecycleExecutionRequest:
        execution = self._repository.get_execution(project_id=project_id, execution_id=execution_id)
        if (
            execution is None
            or execution.status.value != "QUEUED"
            or execution.approval_id is None
            or execution.approved_by is None
        ):
            raise LifecycleExecutionPlanUnavailable(
                "the lifecycle execution is not queued with an independent approval"
            )
        candidates: list[LifecycleExecutionCandidate] = []
        for item in execution.items:
            if item.status != "PENDING":
                continue
            record = self._repository.get_managed_object(
                project_id=project_id, object_id=item.object_id
            )
            if record is None:
                raise LifecycleExecutionPlanUnavailable(
                    "a lifecycle execution candidate is no longer registered"
                )
            candidates.append(
                LifecycleExecutionCandidate(
                    physical_instance_id=record.object_id,
                    logical_object_id=record.object_id,
                    object_role=record.object_role,
                    rebuild_source_id=record.rebuild_source_id,
                    active_reference_count=record.active_reference_count,
                    protection_verified=True,
                    retention_active=(
                        record.retention_until is not None
                        and record.retention_until > execution.updated_at
                    ),
                    legal_hold=record.legal_hold,
                    governance_hold=record.governance_hold,
                )
            )
        context = current_request_context()
        return LifecycleExecutionRequest(
            execution_id=execution.execution_id,
            organization_id=context.organization_id,
            project_id=execution.project_id,
            region_code=context.region_code,
            policy_id=execution.policy_id,
            policy_version=execution.policy_version,
            action=execution.action,
            production=True,
            production_execution_approved=True,
            approval_id=execution.approval_id,
            plan_hash=execution.plan_hash,
            candidates=tuple(candidates),
        )


class StorageLifecycleOutboxHandler:
    EVENT_TYPE = "storage.lifecycle.execution.requested.v1"

    def __init__(
        self,
        launcher: TemporalWorkflowLauncher,
        resolver: StorageExecutionInputResolver,
    ) -> None:
        self._launcher = launcher
        self._resolver = resolver

    async def __call__(self, event: DomainEventEnvelope) -> object:
        if event.event_type != self.EVENT_TYPE:
            raise ValueError("unsupported outbox event type")
        execution_id = _required(event.payload, "execution_id")
        persisted_workflow_id = _required(event.payload, "workflow_id")
        expected_workflow_id = f"storage-lifecycle/{event.project_id}/{execution_id}"
        if event.aggregate_id != execution_id or persisted_workflow_id != expected_workflow_id:
            raise ValueError("storage lifecycle outbox identity does not match its lineage")
        request = self._resolver.resolve(
            project_id=event.project_id,
            execution_id=execution_id,
        )
        return await self._launcher.start(
            workflow_name=STORAGE_LIFECYCLE_WORKFLOW,
            workflow_input=request,
            workflow_id=persisted_workflow_id,
            job_type=STORAGE_LIFECYCLE_WORKFLOW,
            project_id=event.project_id,
            resource_id=execution_id,
        )


class StorageScheduleEnqueuer(Protocol):
    def enqueue(self, *, project_id: str, region_code: str, limit: int) -> int: ...


class RepositoryStorageScheduleEnqueuer:
    def __init__(
        self,
        repository: StorageRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._clock = clock

    def enqueue(self, *, project_id: str, region_code: str, limit: int) -> int:
        return self._repository.enqueue_due_schedules(
            project_id=project_id,
            region_code=region_code,
            now=self._clock(),
            limit=limit,
        )


class StorageLifecycleScheduleOutboxHandler:
    EVENT_TYPE = "storage.lifecycle.schedule.due.v1"

    def __init__(self, service: StorageGovernanceService) -> None:
        self._service = service

    def __call__(self, event: DomainEventEnvelope) -> object:
        if event.event_type != self.EVENT_TYPE:
            raise ValueError("unsupported storage schedule outbox event type")
        schedule_id = _required(event.payload, "schedule_id")
        if event.aggregate_id != schedule_id:
            raise ValueError("storage schedule outbox identity does not match its lineage")
        scheduled_for = datetime.fromisoformat(_required(event.payload, "scheduled_for"))
        if scheduled_for.tzinfo is None:
            raise ValueError("storage schedule occurrence must include a timezone")
        actor = AuthContext(
            subject_id="storage-schedule-dispatcher",
            project_ids=frozenset({event.project_id}),
            region_codes=(
                frozenset({event.region_code}) if event.region_code is not None else frozenset()
            ),
            roles=frozenset({Role.ADMIN.value}),
            service_identity=True,
            scope_pairs=frozenset({(event.project_id, event.region_code)}),
            organization_ids=(
                frozenset({event.organization_id})
                if event.organization_id is not None
                else frozenset()
            ),
            organization_scope_triples=(
                frozenset({(event.organization_id, event.project_id, event.region_code)})
                if event.organization_id is not None
                else frozenset()
            ),
        )
        return self._service.run_due_lifecycle_schedule(
            project_id=event.project_id,
            schedule_id=schedule_id,
            scheduled_for=scheduled_for,
            actor=actor,
            request_id=event.trace_id or event.event_id,
        )


def _required(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"storage lifecycle outbox payload is missing {name}")
    return value
