from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.security.outbox import (
    InMemoryOutboxDeliveryRepository,
    OutboxDispatcher,
)
from hc_data_platform.workflow.ingest_dispatch import IngestOutboxHandler
from hc_data_platform.workflow.models import WorkflowKind, workflow_id

NOW = datetime(2026, 8, 18, tzinfo=timezone.utc)


def _event() -> DomainEventEnvelope:
    locator = workflow_id(WorkflowKind.INGEST_ROLLOUT, "project-a", "cn-hz/rollout-1")
    return DomainEventEnvelope(
        event_id="9aaf2834-c231-5d7b-91f2-4bb1e7babb94",
        event_type=IngestOutboxHandler.EVENT_TYPE,
        aggregate_type="upload_session",
        aggregate_id="session-1",
        organization_id="organization-a",
        project_id="project-a",
        region_code="cn-hz",
        occurred_at=NOW,
        trace_id="request-1",
        payload={
            "session_id": "session-1",
            "rollout_id": "rollout-1",
            "data_package_id": "package-1",
            "workflow_id": locator,
        },
    )


def _project_event() -> DomainEventEnvelope:
    return _event().model_copy(
        update={
            "event_id": "1e022dcf-d873-51cb-9510-c2c403054d5d",
            "event_type": "storage.lifecycle.execution.requested.v1",
            "aggregate_type": "lifecycle_execution",
            "aggregate_id": "execution-1",
            "region_code": None,
            "payload": {
                "execution_id": "execution-1",
                "workflow_id": "storage-lifecycle/project-a/execution-1",
            },
        }
    )


class PersistedPlanResolver:
    def resolve(self, **values: str) -> object:
        return SimpleNamespace(
            project_id=values["project_id"],
            region_code=values["region_code"],
            rollout_id=values["rollout_id"],
        )


class AlreadyStartedLauncher:
    def __init__(self) -> None:
        self.calls = 0
        self.execution_ids: set[str] = set()

    async def start(self, **values: object) -> object:
        self.calls += 1
        self.execution_ids.add(str(values["workflow_id"]))
        return SimpleNamespace(workflow_id=values["workflow_id"])


@pytest.mark.asyncio
async def test_outbox_replay_and_temporal_already_started_converge_to_one_execution() -> None:
    event = _event()
    launcher = AlreadyStartedLauncher()
    handler = IngestOutboxHandler(launcher, PersistedPlanResolver())  # type: ignore[arg-type]

    # A direct redelivery models an acknowledgement loss after Temporal accepted start.
    await handler(event)
    await handler(event)
    assert launcher.calls == 2
    assert launcher.execution_ids == {event.payload["workflow_id"]}

    repository = InMemoryOutboxDeliveryRepository((event,))
    dispatcher = OutboxDispatcher(
        repository,
        {event.event_type: handler},
        worker_id="dispatcher-1",
        clock=lambda: NOW,
    )
    assert await dispatcher.dispatch_one(
        organization_id="organization-a", project_id="project-a", region_code="cn-hz"
    )
    assert not await dispatcher.dispatch_one(
        organization_id="organization-a", project_id="project-a", region_code="cn-hz"
    )
    state = repository.state(event.event_id)
    assert state["published_at"] == NOW
    assert state["attempts"] == 1
    assert repository.audit == [
        {
            "organization_id": "organization-a",
            "project_id": "project-a",
            "region_code": "cn-hz",
            "resource_id": "session-1",
            "workflow_id": event.payload["workflow_id"],
            "action": "workflow.dispatch.accepted",
            "error_code": None,
            "attempt": 1,
        }
    ]


@pytest.mark.asyncio
async def test_dispatch_failure_has_safe_retry_fact_then_recovers() -> None:
    event = _event()
    repository = InMemoryOutboxDeliveryRepository((event,))
    clock = {"now": NOW}
    calls = 0

    class TemporaryFailure(RuntimeError):
        code = "TEMPORAL_UNAVAILABLE"

    async def handler(_: DomainEventEnvelope) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TemporaryFailure("secret endpoint and raw exception must not be persisted")

    dispatcher = OutboxDispatcher(
        repository,
        {event.event_type: handler},
        worker_id="dispatcher-1",
        clock=lambda: clock["now"],
        retry_delay=lambda _: timedelta(seconds=5),
    )
    assert await dispatcher.dispatch_one(
        organization_id="organization-a", project_id="project-a", region_code="cn-hz"
    )
    failed = repository.state(event.event_id)
    assert failed["error_code"] == "TEMPORAL_UNAVAILABLE"
    assert "secret" not in repr(failed)
    assert repository.audit[-1]["action"] == "workflow.dispatch.retry"

    assert not await dispatcher.dispatch_one(
        organization_id="organization-a", project_id="project-a", region_code="cn-hz"
    )
    clock["now"] = NOW + timedelta(seconds=5)
    assert await dispatcher.dispatch_one(
        organization_id="organization-a", project_id="project-a", region_code="cn-hz"
    )
    recovered = repository.state(event.event_id)
    assert recovered["published_at"] == clock["now"]
    assert recovered["attempts"] == 2
    assert recovered["error_code"] is None


def test_region_worker_can_claim_a_project_wide_event_but_not_another_project() -> None:
    event = _project_event()
    repository = InMemoryOutboxDeliveryRepository((event,))

    assert (
        repository.claim_next(
            organization_id="organization-a",
            project_id="other-project",
            region_code="cn-hz",
            worker_id="worker-other",
            now=NOW,
            claimed_until=NOW + timedelta(minutes=1),
        )
        is None
    )
    assert (
        repository.claim_next(
            organization_id="organization-b",
            project_id="project-a",
            region_code="cn-hz",
            worker_id="worker-other-organization",
            now=NOW,
            claimed_until=NOW + timedelta(minutes=1),
        )
        is None
    )
    claimed = repository.claim_next(
        organization_id="organization-a",
        project_id="project-a",
        region_code="cn-hz",
        worker_id="worker-project",
        now=NOW,
        claimed_until=NOW + timedelta(minutes=1),
    )
    assert claimed is not None
    assert claimed.event.region_code is None


def test_expired_claim_is_recoverable_after_dispatcher_kill() -> None:
    event = _event()
    repository = InMemoryOutboxDeliveryRepository((event,))
    first = repository.claim_next(
        organization_id="organization-a",
        project_id="project-a",
        region_code="cn-hz",
        worker_id="dead-worker",
        now=NOW,
        claimed_until=NOW + timedelta(minutes=1),
    )
    assert first is not None
    assert (
        repository.claim_next(
            organization_id="organization-a",
            project_id="project-a",
            region_code="cn-hz",
            worker_id="live-worker",
            now=NOW + timedelta(seconds=30),
            claimed_until=NOW + timedelta(minutes=2),
        )
        is None
    )

    recovered = repository.claim_next(
        organization_id="organization-a",
        project_id="project-a",
        region_code="cn-hz",
        worker_id="live-worker",
        now=NOW + timedelta(minutes=1),
        claimed_until=NOW + timedelta(minutes=2),
    )
    assert recovered is not None
    assert recovered.attempt == 2
    assert recovered.event.event_id == first.event.event_id
