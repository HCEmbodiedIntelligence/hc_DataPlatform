"""E-only replay at the real failed commit, using Temporal's frozen activity input.

This never writes READY or synthesizes a processing receipt. The real Worker
verifies durable Lance and media commits and completes projection and annotation.
"""
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4
from temporalio.client import Client
from temporalio.api.common.v1 import WorkflowExecution
from temporalio.api.workflowservice.v1 import ResetWorkflowExecutionRequest
from hc_data_platform.core.config import get_settings
from hc_data_platform.core.context import RequestContext, bind_request_context
from hc_data_platform.runtime import build_runtime
from hc_data_platform.continuous_recordings.models import RecordingScope
from hc_data_platform.continuous_recordings.asset_models import EpisodeProcessingStatus
from hc_data_platform.workflow.models import ContinuousEpisodeBundleCommitActivityInput

async def main():
    settings=get_settings()
    assert settings.platform_environment_id=='openarm-e'
    episode=sys.argv[1]
    assert episode in ('episode_0001','episode_0002')
    recording='recording-b723306c8d6c5aefb35b3e9560ca7620'
    scope=RecordingScope(organization_id='openarm-e-synthetic',project_id='openarm-e-project',region_code='openarm-e-region')
    bind_request_context(RequestContext(**scope.model_dump(),subject_id='e-recording-recovery',service_identity=True))
    runtime=build_runtime(settings,include_media=True)
    client=await Client.connect('temporal:7233')
    workflow='continuous-episode:v1:openarm-e-project:openarm-e-region%2F'+recording+'%2F'+episode
    handle=client.get_workflow_handle(workflow)
    history=await handle.fetch_history()
    activity=next(e.activity_task_scheduled_event_attributes for e in history.events
        if e.HasField('activity_task_scheduled_event_attributes') and e.activity_task_scheduled_event_attributes.activity_type.name=='workflow.commit_continuous_episode_bundle')
    frozen=json.loads(activity.input.payloads[0].data)
    request=ContinuousEpisodeBundleCommitActivityInput.model_validate(frozen)
    source=request.workflow_input.projection
    assert source.recording_id==recording and source.episode_id==episode
    lineage=runtime.catalog.lineage(request.workflow_input.dataset_id,source.rollout_id,
        project_id=scope.project_id,version=request.expected_dataset_version)
    assert lineage.source_sha256==source.source_sha256
    # A failed workflow released its reservation; recovery starts only after
    # confirming the exact historical physical commit in this E scope.
    repo=runtime.activities.continuous_episode_processing._repository
    current=repo.get_episode_processing(scope,recording,episode)
    if current.status is EpisodeProcessingStatus.READY:
        before=runtime.catalog.current_version(request.workflow_input.dataset_id,project_id=scope.project_id)
        result=runtime.activities.continuous_episode_processing.commit_bundle(**{
            key:getattr(request,key) for key in ('workflow_input','alignment','staged_manifest','alignment_staging',
                'expected_dataset_version','expected_camera_ids','media_artifacts')})
        after=runtime.catalog.current_version(request.workflow_input.dataset_id,project_id=scope.project_id)
        assert before==after and result.version.version==current.dataset_version
        assert result.viewer_target.episode_id==current.dataset_episode_id
        print(json.dumps({'replayed_receipt':True,'dataset_version':result.version.version,
                          'current_dataset_version':after.version,'no_new_commit':True}))
        return
    assert current.status is EpisodeProcessingStatus.FAILED and current.failure_stage=='lance_commit'
    repo.save_episode_processing(current.model_copy(update={'status':EpisodeProcessingStatus.ALIGNING,
        'failure_code':None,'failure_stage':None}),expected_status=EpisodeProcessingStatus.FAILED)
    description=await handle.describe()
    reset=await client.workflow_service.reset_workflow_execution(ResetWorkflowExecutionRequest(
        namespace='default',workflow_execution=WorkflowExecution(workflow_id=workflow,run_id=description.run_id),
        reason='E: repair shared source provenance; recover existing physical commit',
        workflow_task_finish_event_id=activity.workflow_task_completed_event_id,request_id=str(uuid4())))
    print(json.dumps({'workflow':workflow,'prior_run':description.run_id,'recovery_run':reset.run_id,
                      'replayed_from_event':activity.workflow_task_completed_event_id,'dataset_version':request.expected_dataset_version}))

asyncio.run(main())
