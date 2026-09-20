"""Launch native imports from committed Raw facts, never browser-supplied workflows."""

from __future__ import annotations

from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.ingest.raw_sources import RawSourceRepositoryPort
from hc_data_platform.workflow.lerobot_workflow import (
    LEROBOT_IMPORT_WORKFLOW,
    LeRobotImportWorkflowInput,
)
from hc_data_platform.workflow.service import TemporalWorkflowLauncher

from .orchestration import LeRobotEpisodeSourceRefV1, LeRobotEpisodeTaskV1


class LeRobotImportOutboxHandler:
    EVENT_TYPE = "lerobot.import.requested.v1"

    def __init__(
        self, launcher: TemporalWorkflowLauncher, repository: RawSourceRepositoryPort
    ) -> None:
        self.launcher, self.repository = launcher, repository

    async def __call__(self, event: DomainEventEnvelope) -> object:
        if (
            event.event_type != self.EVENT_TYPE
            or not event.organization_id
            or not event.region_code
        ):
            raise ValueError("invalid native import event scope")
        scope = dict(
            organization_id=event.organization_id,
            project_id=event.project_id,
            region_code=event.region_code,
            raw_source_id=str(event.payload["raw_source_id"]),
        )
        raw = self.repository.get_source(**scope)
        job = self.repository.get_job(**scope)
        if (
            raw is None
            or job is None
            or not raw.dataset_id
            or not raw.collection_task_id
            or not raw.robot_id
            or job.workflow_id != event.payload.get("workflow_id")
        ):
            raise ValueError("native dispatch does not match committed Raw source")
        assert job.workflow_id is not None
        episodes = self.repository.list_episodes(**scope)
        task = LeRobotEpisodeTaskV1(
            task_id=f"lerobot:{raw.raw_source_id}:episode:0",
            organization_id=raw.organization_id,
            project_id=raw.project_id,
            region_code=raw.region_code,
            dataset_id=raw.dataset_id,
            collection_task_id=raw.collection_task_id,
            robot_id=raw.robot_id,
            source=LeRobotEpisodeSourceRefV1(
                raw_upload_id=raw.raw_source_id, raw_manifest_key=raw.manifest_key, episode_index=0
            ),
        )
        return await self.launcher.start(
            workflow_name=LEROBOT_IMPORT_WORKFLOW,
            workflow_input=LeRobotImportWorkflowInput(task=task, episode_count=len(episodes)),
            workflow_id=job.workflow_id,
            job_type=LEROBOT_IMPORT_WORKFLOW,
            project_id=event.project_id,
            resource_id=raw.raw_source_id,
        )
