"""Real immutable derivation replay; no replacement processor is allowed to run."""
import json
import hashlib
from pathlib import Path
from hc_data_platform.core.config import get_settings
from hc_data_platform.core.context import RequestContext,bind_request_context
from hc_data_platform.runtime import build_runtime
from hc_data_platform.robot_ingest.models import RobotIngestUpload
from hc_data_platform.robot_ingest.recording_contract import OpenArmRecordingComplete
from hc_data_platform.robot_ingest import raw_recording
ORG,PROJECT,REGION='openarm-e-synthetic','openarm-e-project','openarm-e-region'
UPLOAD='riu-5e0b69f16d50521582ad5733d02621fb'
settings=get_settings()
assert settings.platform_environment_id=='openarm-e'
bind_request_context(RequestContext(organization_id=ORG,project_id=PROJECT,region_code=REGION,
    subject_id='e-raw-receipt-verifier',service_identity=True))
runtime=build_runtime(settings,include_media=True)
pipeline=runtime.activities.lerobot_pipeline
with pipeline.connections() as db:
    row=db.execute('SELECT upload_document FROM ingest.robot_ingest_uploads WHERE organization_id=%s AND project_id=%s AND region_code=%s AND upload_id=%s',(ORG,PROJECT,REGION,UPLOAD)).fetchone()
upload=RobotIngestUpload.model_validate(row[0])
assets={a.path:a for a in upload.assets}
marker=json.loads(b''.join(pipeline.storage.read_chunks(assets['recording-complete.json'].object_key)))
command=OpenArmRecordingComplete.model_validate(marker).recording_upload
before=runtime.catalog.current_version('dataset_openarm_e_recording_v3',project_id=PROJECT).version

def forbidden(*a,**kw):raise AssertionError('A durable receipt must prevent reprocessing')
raw_recording.prepare=forbidden
recovered,derived=raw_recording.materialize(pipeline.storage,upload,marker,command,assets)
again,other=raw_recording.materialize(pipeline.storage,upload,marker,command,assets)
assert recovered==again and derived==other
assert runtime.catalog.current_version('dataset_openarm_e_recording_v3',project_id=PROJECT).version==before
assert recovered.recording_id==command.recording_id
assert all(a.object_key==other[p].object_key for p,a in derived.items())
result={'status':'PASS','upload_id':UPLOAD,'recording_id':recovered.recording_id,'source_sha256':marker['content_sha256'],
    'two_receipt_replays':True,'reprocessing_forbidden':True,'lance_version_unchanged':before,
    'original_count':len(command.assets),'verified_derived_and_original_count':len(derived)}
Path('/tmp/e-raw-v3/receipt-replay.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
