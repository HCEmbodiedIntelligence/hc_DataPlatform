"""Read only durable E receipts; no processing or state substitution."""
import json
import os
from pathlib import Path
from hc_data_platform.core.config import get_settings
from hc_data_platform.core.context import RequestContext,bind_request_context
from hc_data_platform.runtime import build_runtime

ORG,PROJECT,REGION='openarm-e-synthetic','openarm-e-project','openarm-e-region'
settings=get_settings()
assert settings.platform_environment_id=='openarm-e'
bind_request_context(RequestContext(organization_id=ORG,project_id=PROJECT,region_code=REGION,
    service_identity=True,subject_id='e-native-proof'))
runtime=build_runtime(settings,include_media=True)
evidence={'schema_version':'e-recording-v2-proof/v1','synthetic':True,'hardware_started':False,'uploads':[]}
with runtime.robot_processing.connections() as db:
    for client in ('1221ee52-9cdf-4908-b419-1605a617a339','875892fe-6ac8-4007-9f61-b2d927b2750f'):
        rows=db.execute('SELECT upload_document FROM ingest.robot_ingest_uploads WHERE client_upload_id=%s',(client,)).fetchall()
        assert len(rows)==1
        upload=rows[0][0]
        recording=upload['manifest']['format_metadata']['recording_id']
        count=db.execute('SELECT count(*) FROM ingest.recording_uploads WHERE recording_id=%s',(recording,)).fetchone()[0]
        assert count==1
        episodes=[r[0] for r in db.execute('SELECT episode_document FROM ingest.recording_episode_processing WHERE recording_id=%s ORDER BY episode_id',(recording,))]
        assert len(episodes)==2 and all(e['status']=='READY' and e['aligned_media_camera_count']==3 for e in episodes)
        reports=[db.execute('SELECT report_document FROM ingest.recording_episode_qc_reports WHERE report_id=%s',(e['qc_report_id'],)).fetchone()[0] for e in episodes]
        assert all(r['status']=='PASS' and not r['finding_codes'] for r in reports)
        event=db.execute("SELECT published_at IS NOT NULL,publish_attempts FROM core.outbox_events WHERE event_type='robot.recording.register.requested.v1' AND envelope->>'aggregate_id'=%s",(upload['raw_source_id'],)).fetchone()
        evidence['uploads'].append({'client_upload_id':client,'upload_id':upload['upload_id'],
            'raw_source_id':upload['raw_source_id'],'recording_id':recording,'recording_projection_count':count,
            'episodes':episodes,'qc_reports':reports,'outbox':list(event) if event else None})
versions=runtime.catalog.list_versions('dataset_openarm_e_recording_v2_final',project_id=PROJECT)
assert [v.version for v in versions]==[1,2,3,4]
evidence['dataset_versions']=[v.model_dump(mode='json') for v in versions]
ids=[e['dataset_episode_id'] for u in evidence['uploads'] for e in u['episodes']]
assert len(set(ids))==4
output=Path(os.environ['E_RECORDING_OUTPUT'])
output.mkdir(parents=True,exist_ok=True)
(output/'durable-receipts.json').write_text(json.dumps(evidence,indent=2)+'\n')
print(json.dumps({'uploads':2,'recordings':2,'ready_episodes':4,'distinct_episode_ids':4,'dataset_versions':4}))
