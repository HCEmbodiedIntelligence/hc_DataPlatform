"""Configuration rejection is durable, not an endlessly retried transport failure."""

import os
from unittest.mock import Mock
from uuid import uuid4

import pytest

from hc_data_platform.core.errors import problem
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.security.outbox import InMemoryOutboxDeliveryRepository, OutboxDispatcher
from hc_data_platform.workflow.continuous_episode_dispatch import ContinuousEpisodeOutboxHandler
from hc_data_platform.workflow.ingest_dispatch import IngestOutboxHandler
from hc_data_platform.workflow.models import (
    continuous_episode_workflow_id,
    ingest_rollout_workflow_id,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("continuous", [False, True])
async def test_capture_configuration_is_acknowledged_after_persisting_failure(continuous):
    scope = dict(organization_id="org", project_id="project", region_code="global")
    if continuous:
        handler_type = ContinuousEpisodeOutboxHandler
        identity = continuous_episode_workflow_id(
            "org", "project", "global", "recording", "episode"
        )
        payload = {
            "recording_id": "recording",
            "episode_id": "episode",
            "workflow_id": identity,
            "finalized_revision": 1,
        }
        aggregate = "recording/episode"
    else:
        handler_type = IngestOutboxHandler
        identity = ingest_rollout_workflow_id("org", "project", "global", "rollout")
        payload = {
            "session_id": "session",
            "rollout_id": "rollout",
            "data_package_id": "package",
            "workflow_id": identity,
        }
        aggregate = "rollout"
    event = DomainEventEnvelope(
        **scope,
        event_type=handler_type.EVENT_TYPE,
        aggregate_type="capture",
        aggregate_id=aggregate,
        payload=payload,
    )
    resolver, launcher = Mock(), Mock()
    resolver.terminal_result.return_value = None
    resolver.resolve.side_effect = problem(
        status=409,
        code="CAPTURE_PLAN_REQUIRED",
        title="capture plan required",
        detail="no server-owned snapshot",
        retryable=False,
    )
    resolver.record_plan_failure.return_value = {
        "status": "FAILED",
        "error": "CAPTURE_PLAN_REQUIRED",
    }
    repository = InMemoryOutboxDeliveryRepository((event,))
    dispatcher = OutboxDispatcher(
        repository, {event.event_type: handler_type(launcher, resolver)}, worker_id="capture-test"
    )
    assert await dispatcher.dispatch_one(**scope)
    resolver.record_plan_failure.assert_called_once()
    launcher.start.assert_not_called()
    assert repository.state(event.event_id)["published_at"] is not None
    assert not await dispatcher.dispatch_one(**scope)


@pytest.mark.integration
def test_ingest_configuration_failure_is_durable_and_cannot_replace_success(monkeypatch):
    if os.getenv("HC_CAPTURE_PROCESSING_INFRA") != "1":
        pytest.skip("explicit isolated infrastructure required")
    from hc_data_platform.core.context import (
        RequestContext,
        bind_request_context,
        reset_request_context,
    )
    from hc_data_platform.core.dbapi import psycopg_connection_factory
    from hc_data_platform.workflow.ingest_plan import PostgresIngestWorkflowInputResolver

    suffix = uuid4().hex
    scope = dict(organization_id="processing-" + suffix, project_id="p", region_code="global")
    connect = psycopg_connection_factory(os.environ["HC_TEST_POSTGRES_DSN"])
    resolver = PostgresIngestWorkflowInputResolver(connect, Mock(), Mock())
    # Simulate the terminal check occurring before a concurrent workflow commit.
    monkeypatch.setattr(resolver, "terminal_result", lambda event: None)
    event = DomainEventEnvelope(
        **scope,
        event_type=IngestOutboxHandler.EVENT_TYPE,
        aggregate_type="capture",
        aggregate_id="rollout",
        payload={"workflow_id": "rejected-" + suffix, "rollout_id": "rollout"},
    )
    failure = problem(
        status=409,
        code="CAPTURE_PLAN_REQUIRED",
        title="plan required",
        detail="missing frozen snapshot",
        retryable=False,
    )
    token = bind_request_context(RequestContext(**scope, service_identity=True))
    try:
        with connect() as db:
            db.execute(
                "INSERT INTO registry.organization_projects VALUES (%s,%s,'capture test')",
                (scope["organization_id"], scope["project_id"]),
            )
        resolver.record_plan_failure(event, failure)
        with connect() as db:
            record = db.execute(
                "SELECT status,error_code,result FROM workflow.jobs WHERE workflow_id=%s",
                (event.payload["workflow_id"],),
            ).fetchone()
            assert record == (
                "TECHNICAL_FAILED",
                "CAPTURE_PLAN_REQUIRED",
                {"retryable": False, "data_readable": False},
            )
            db.execute(
                "UPDATE workflow.jobs SET status='SUCCEEDED',error_code=NULL,"
                " result='{\"data_readable\":true}'::jsonb WHERE workflow_id=%s",
                (event.payload["workflow_id"],),
            )
        resolver.record_plan_failure(event, failure)
        with connect() as db:
            assert db.execute(
                "SELECT status,error_code,result FROM workflow.jobs WHERE workflow_id=%s",
                (event.payload["workflow_id"],),
            ).fetchone() == ("SUCCEEDED", None, {"data_readable": True})
    finally:
        reset_request_context(token)
