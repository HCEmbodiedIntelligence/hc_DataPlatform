from __future__ import annotations

from hc_data_platform.workflow.models import PreviewWorkflowInput
from hc_data_platform.workflow.names import PREVIEW_WORKFLOW
from hc_data_platform.workflow.service import TemporalWorkflowLauncher

from .models import PreviewJobV1


class TemporalPreviewJobQueue:
    """Launch one media-queue workflow for each durable preview.jobs row."""

    def __init__(self, launcher: TemporalWorkflowLauncher) -> None:
        self._launcher = launcher

    async def enqueue(self, job: PreviewJobV1) -> None:
        await self._launcher.start(
            workflow_name=PREVIEW_WORKFLOW,
            workflow_input=PreviewWorkflowInput(
                organization_id=job.scope.organization_id,
                region_code=job.scope.region_code,
                job_id=job.job_id,
                request=job.request,
            ),
            workflow_id=f"preview-job/{job.job_id}",
            job_type=PREVIEW_WORKFLOW,
            project_id=job.scope.project_id,
            resource_id=job.artifact_key,
        )
