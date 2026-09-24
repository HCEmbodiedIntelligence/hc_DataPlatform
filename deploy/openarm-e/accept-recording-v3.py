"""Production E services: preview, platform EDL and real workflow evidence."""
import json
import os
from pathlib import Path
import sys
import hashlib
import io
import zipfile
from dataclasses import replace
from urllib.request import Request,urlopen

from hc_data_platform.core.config import get_settings
from hc_data_platform.core.context import RequestContext,bind_request_context
from hc_data_platform.continuous_recordings.models import SaveSliceDraftCommand
from hc_data_platform.runtime import build_runtime
from hc_data_platform.security.auth import AuthContext

ORG,PROJECT,REGION='openarm-e-synthetic','openarm-e-project','openarm-e-region'
RECORDING=os.environ.get('E_RECORDING_ID','recording-33572ab6a1375622a46922cf8db08372')
DATASET='dataset_openarm_e_recording_v3'


def main():
    settings=get_settings()
    assert settings.platform_environment_id=='openarm-e'
    actor=AuthContext(subject_id='openarm-e-recording-operator',organization_ids=frozenset({ORG}),
        project_ids=frozenset({PROJECT}),region_codes=frozenset({REGION}),
        organization_scope_triples=frozenset({(ORG,PROJECT,REGION),(ORG,PROJECT,None)}),
        scope_pairs=frozenset({(PROJECT,REGION),(PROJECT,None)}),
        capabilities=frozenset({'upload.read','upload.manage','annotation_task.claim','annotation.read',
            'annotation.write','annotation.save','annotation.submit','annotation.review','dataset_version.publish'}))
    bind_request_context(RequestContext(organization_id=ORG,project_id=PROJECT,region_code=REGION,
        subject_id=actor.subject_id,request_id='openarm-e-recording-v2'))
    runtime=build_runtime(settings,include_media=True)
    service=runtime.continuous_recordings
    arguments=dict(auth=actor,organization_id=ORG,project_id=PROJECT,region_code=REGION,recording_id=RECORDING)
    envelope=service.get(**arguments)
    recording=envelope.data
    if sys.argv[1]=='resolve':
        from hc_data_platform.continuous_recordings.processing import PostgresContinuousEpisodeWorkflowInputResolver
        resolver=PostgresContinuousEpisodeWorkflowInputResolver(runtime.robot_processing.connections,runtime.catalog)
        request=resolver.resolve(organization_id=ORG,project_id=PROJECT,region_code=REGION,
            recording_id=RECORDING,episode_id='episode_0001',finalized_revision=recording.finalized_revision)
        print('resolved',request.dataset_id)
    elif sys.argv[1]=='slice':
        videos=service.authorize_recording_videos(**arguments)
        assert len(videos.sources)==6
        for source in videos.sources:
            with urlopen(Request(source.source_url,headers={'Range':'bytes=0-31'})) as response:
                assert response.status==206 and len(response.read())==32
        state=service.read_recording_sensor_window(**arguments,topic=None,start_offset_ns=0,
            end_offset_ns=recording.duration_ns,maximum_samples=1000)
        assert state.topic=='/observation/state' and len(state.samples)==21
        assert all(len(s.value['values'])==16 for s in state.samples)
        assert state.samples[0].value['units']==['rad']*7+['m']+['rad']*7+['m']
        if recording.status.value!='SLICED':
            assert service.list_episode_processing(**arguments).items==()
            draft=service.save_draft(**arguments,if_match=recording.etag,actor_id=actor.subject_id,
                request_id='e-native-draft',command=SaveSliceDraftCommand(slices=(
                    {'episode_id':'episode_0001','start_offset_ns':0,'end_offset_ns':200_000_000,'task_label':'success'},
                    {'episode_id':'episode_0002','start_offset_ns':300_000_000,'end_offset_ns':500_000_000,'task_label':'failure'})))
            finalized=service.finalize(**arguments,expected_draft_revision=draft.revision.revision,
                if_match=draft.recording.etag,actor_id=actor.subject_id,request_id='e-native-finalize')
            assert finalized.recording.status.value=='SLICED'
        evidence={'recording_id':RECORDING,'range_preview_cameras':len(videos.sources),
            'sensor_samples':len(state.samples),'axis_names':state.samples[0].value['names'],
            'axis_units':state.samples[0].value['units'],'slice_windows_ns':[[0,200_000_000],[300_000_000,500_000_000]]}
        destination=Path(os.environ['E_RECORDING_OUTPUT'])
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'preview-and-slice.json').write_text(json.dumps(evidence,indent=2)+'\n')
        print(json.dumps(evidence))
    elif sys.argv[1]=='identity':
        episodes=service.list_episode_processing(**arguments).items
        assert len(episodes)==2 and all(e.status.value=='READY' for e in episodes)
        first=service.list_episode_processing(**{**arguments,'recording_id':'recording-33572ab6a1375622a46922cf8db08372'}).items
        assert set(e.episode_id for e in episodes)==set(e.episode_id for e in first)
        assert not set(e.dataset_episode_id for e in episodes).intersection(e.dataset_episode_id for e in first)
        assert all(e.dataset_episode_id for e in episodes)
        result={'recording_id':RECORDING,'global_episode_ids':[e.dataset_episode_id for e in episodes],
                'previous_global_episode_ids':[e.dataset_episode_id for e in first],
                'dataset_versions':[e.dataset_version for e in episodes],'reused_local_ids_are_distinct':True}
        Path(os.environ['E_RECORDING_OUTPUT']).mkdir(parents=True,exist_ok=True)
        Path(os.environ['E_RECORDING_OUTPUT'],'identity.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
    elif sys.argv[1]=='vectors':
        from hc_data_platform.publishing.exporters import _lerobot_value
        for ep in service.list_episode_processing(**arguments).items:
            steps=runtime.catalog.read_steps(DATASET,RECORDING+':'+ep.episode_id,0,6,project_id=PROJECT,version=ep.dataset_version)
            for step in steps.steps:
                print(json.dumps({'episode':ep.episode_id,'step':step.step_index,'values':step.modalities},default=str))
    elif sys.argv[1]=='finish':
        from hc_data_platform.annotation.models import AnnotationStatus,AnnotationTag,ReviewDecision
        from hc_data_platform.publishing.models import ExportFormat,PublishDatasetRequestV1
        episodes=service.list_episode_processing(**arguments).items
        assert len(episodes)==2 and all(e.status.value=='READY' for e in episodes)
        evidence=[]
        with runtime.robot_processing.connections() as db:
            for index,episode in enumerate(episodes):
                rollout_id=RECORDING+':'+episode.episode_id
                rows=runtime.catalog.read_steps(DATASET,rollout_id,0,100,project_id=PROJECT,version=episode.dataset_version)
                assert len(rows.steps)==6
                assert all(row.sample_valid for row in rows.steps)
                assert all(row.modalities.get('/action') is not None and row.modalities.get('/observation/state') is not None for row in rows.steps)
                report=db.execute('SELECT report_document FROM ingest.recording_episode_qc_reports WHERE report_id=%s',
                    (episode.qc_report_id,)).fetchone()[0]
                assert report['status']=='PASS' and report['finding_codes']==[]
                assert episode.aligned_media_camera_count==6
                task=runtime.annotation.get_task(episode.annotation_task_id)
                if task.status is not AnnotationStatus.APPROVED:
                    task=runtime.annotation.claim(task.task_id,actor)
                    outcome=('success','failure')[index]
                    revision=runtime.annotation.save_draft(task.task_id,actor,[],
                        tags=[AnnotationTag(annotation_id='outcome',tag_id=outcome,path=(outcome,),start_step=0,end_step=6)],
                        expected_revision=task.current_revision,if_match=task.etag,client_mutation_id='native-outcome-'+episode.episode_id)
                    submitted=runtime.annotation.submit(task.task_id,actor,expected_revision=revision.revision,
                        if_match=runtime.annotation.get_task(task.task_id).etag)
                    runtime.annotation.review(task.task_id,replace(actor,subject_id='openarm-e-recording-reviewer'),
                        ReviewDecision.APPROVE,revision=revision.revision,if_match=submitted.etag)
                evidence.append({'episode_id':episode.episode_id,'rollout_id':rollout_id,'frames':len(rows.steps),
                    'qc_report':report,'dataset_version':episode.dataset_version,'lance_version':episode.lance_version,
                    'annotation_task_id':episode.annotation_task_id,'dataset_episode_id':episode.dataset_episode_id})
        publish_request=PublishDatasetRequestV1(project_id=PROJECT,dataset_id=DATASET,
            dataset_version='openarm-e-recording-v2',base_lance_version=str(max(e.dataset_version for e in episodes)))
        print(runtime.publisher.preflight(publish_request).model_dump_json())
        published=runtime.publisher.publish(publish_request)
        assert len(published.rollouts)==2
        exported=runtime.exporter.export(published,format=ExportFormat.LEROBOT_V3)
        with urlopen(runtime.exporter.authorize_download(exported)) as response:content=response.read()
        assert hashlib.sha256(content).hexdigest()==exported.artifact_content_hash
        destination=Path(os.environ['E_RECORDING_OUTPUT'])
        destination.mkdir(parents=True,exist_ok=True)
        prior=destination/'export.zip'
        if prior.exists() and hashlib.sha256(prior.read_bytes()).hexdigest()!=exported.artifact_content_hash:
            previous=destination/('previous-'+hashlib.sha256(prior.read_bytes()).hexdigest()[:12])
            previous.mkdir()
            for name in ('export.zip','export','roundtrip.json'):
                if (destination/name).exists():(destination/name).rename(previous/name)
        (destination/'export.zip').write_bytes(content)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:archive.extractall(destination/'export')
        result={'recording_id':RECORDING,'dataset_id':DATASET,'episodes':evidence,'export_sha256':exported.artifact_content_hash}
        (destination/'roundtrip.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'recording_id':RECORDING,'episodes':len(episodes),'frames':12,'export_sha256':exported.artifact_content_hash}))
    else:
        print(service.list_episode_processing(**arguments).model_dump_json())


if __name__=='__main__':main()
