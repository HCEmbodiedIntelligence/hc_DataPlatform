"""Outbox-to-Temporal dispatch for finalized continuous recording Episodes."""

from __future__ import annotations

from typing import Protocol

from hc_data_platform.core.events import DomainEventEnvelope

from .models import (
    ContinuousEpisodeWorkflowInput,
    WorkflowKind,
    continuous_episode_workflow_id,
    workflow_id,
)
from .names import CONTINUOUS_EPISODE_WORKFLOW
from .service import TemporalWorkflowLauncher


class ContinuousEpisodeWorkflowInputResolver(Protocol):
    def terminal_result(self, **scope: object) -> object | None: ...

    def resolve(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        recording_id: str,
        episode_id: str,
        finalized_revision: int,
        expected_workflow_id: str | None = None,
    ) -> ContinuousEpisodeWorkflowInput: ...


class ContinuousEpisodeOutboxHandler:
    EVENT_TYPE = "continuous-recording.episode.workflow.requested.v1"

    def __init__(
        self,
        launcher: TemporalWorkflowLauncher,
        resolver: ContinuousEpisodeWorkflowInputResolver,
    ) -> None:
        self._launcher = launcher
        self._resolver = resolver

    async def __call__(self, event: DomainEventEnvelope) -> object:
        if event.event_type != self.EVENT_TYPE:
            raise ValueError("unsupported continuous Episode outbox event type")
        if event.organization_id is None or event.region_code is None:
            raise ValueError("continuous Episode events require organization and region scope")
        payload = event.payload
        recording_id = _required(payload, "recording_id")
        episode_id = _required(payload, "episode_id")
        persisted_workflow_id = _required(payload, "workflow_id")
        finalized_revision = payload.get("finalized_revision")
        if not isinstance(finalized_revision, int) or finalized_revision < 1:
            raise ValueError("continuous Episode event has no finalized revision")
        resource_id = f"{recording_id}/{episode_id}"
        legacy_workflow_id = workflow_id(
            WorkflowKind.CONTINUOUS_EPISODE,
            event.project_id,
            f"{event.region_code}/{resource_id}",
        )
        expected_workflow_id = continuous_episode_workflow_id(
            event.organization_id, event.project_id, event.region_code, recording_id, episode_id
        )
        if (
            persisted_workflow_id not in {expected_workflow_id, legacy_workflow_id}
            or event.aggregate_id != resource_id
        ):
            raise ValueError("continuous Episode workflow locator disagrees with event lineage")
        scope = dict(
            organization_id=event.organization_id,
            project_id=event.project_id,
            region_code=event.region_code,
            recording_id=recording_id,
            episode_id=episode_id,
            finalized_revision=finalized_revision,
            expected_workflow_id=persisted_workflow_id,
        )
        terminal = self._resolver.terminal_result(**scope)
        if terminal is not None:
            return terminal
        request = self._resolver.resolve(**scope)
        source = request.projection
        if (
            source.organization_id != event.organization_id
            or source.project_id != event.project_id
            or source.region_code != event.region_code
            or source.recording_id != recording_id
            or source.episode_id != episode_id
        ):
            raise ValueError("resolved continuous Episode input crosses the persisted event scope")
        return await self._launcher.start(
            workflow_name=CONTINUOUS_EPISODE_WORKFLOW,
            workflow_input=request,
            workflow_id=persisted_workflow_id,
            job_type=CONTINUOUS_EPISODE_WORKFLOW,
            project_id=event.project_id,
            resource_id=resource_id,
        )


def _required(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"continuous Episode outbox payload is missing {name}")
    return value
