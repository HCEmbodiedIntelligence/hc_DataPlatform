"""E-only unsliced recording target; preserves the previous v1 acceptance Dataset."""
import json
import os
import psycopg

from hc_data_platform.annotation.models import TagSchemaDocument, TagSchemaTarget
from hc_data_platform.core.config import get_settings
from hc_data_platform.core.context import RequestContext, bind_request_context, reset_request_context
from hc_data_platform.quality.models import QualityProfileV1
from hc_data_platform.robot_ingest.models import UpdateRobotIngestIdentity
from hc_data_platform.runtime import build_runtime
from hc_data_platform.security.auth import AuthContext

ORG,PROJECT,REGION='openarm-e-synthetic','openarm-e-project','openarm-e-region'
ROBOT,TASK,DATASET='synthetic-platform-robot','synthetic-task-E-recording-v3','dataset_openarm_e_recording_v3'


def main():
    settings=get_settings()
    assert settings.platform_environment_id=='openarm-e'
    with psycopg.connect(os.environ['HC_POSTGRES_DSN'].replace('postgresql+asyncpg://','postgresql://')) as db:
        db.execute("SELECT set_config('app.platform_admin','true',false)")
        db.execute('''INSERT INTO collection_tasks.collection_tasks
            (collection_task_id,organization_id,project_id,dataset_id,task_code,name,
             task_type,scenario,status,create_fingerprint,upload_region_code)
            VALUES (%s,%s,%s,%s,'93000004','E explicit VR recordings','ROBOT_CAPTURE',
                    'synthetic','ACTIVE',%s,%s) ON CONFLICT DO NOTHING''',
            (TASK,ORG,PROJECT,DATASET,'2'*64,REGION))
    bind_request_context(RequestContext(organization_id=ORG,project_id=PROJECT,region_code=REGION,
        service_identity=True,subject_id='openarm-e-recording-bootstrap'))
    actor=AuthContext(subject_id='openarm-e-recording-bootstrap',organization_ids=frozenset({ORG}),
        project_ids=frozenset({PROJECT}),region_codes=frozenset({REGION}),
        organization_scope_triples=frozenset({(ORG,PROJECT,REGION),(ORG,PROJECT,None)}),
        scope_pairs=frozenset({(PROJECT,REGION),(PROJECT,None)}),
        capabilities=frozenset({'data_schema.publish','data_schema.read'}),
        organization_scoped_capabilities=frozenset({(ORG,PROJECT,'ingest_source.manage'),(ORG,PROJECT,'ingest_source.read')}))
    runtime=build_runtime(settings,include_media=True)
    for identity in runtime.robot_ingest.list_identities(auth=actor,organization_id=ORG,project_id=PROJECT).items:
        if identity.robot_id==ROBOT:
            runtime.robot_ingest.update_identity(auth=actor,organization_id=ORG,project_id=PROJECT,
                ingest_identity_id=identity.ingest_identity_id,command=UpdateRobotIngestIdentity(
                    allowed_transports=identity.allowed_transports,
                    allowed_formats=tuple(sorted(set(identity.allowed_formats)|{'CAPTURE_BUNDLE'})),
                    upload_policy=identity.upload_policy))
            break
    else:raise RuntimeError('E robot identity missing')
    schema_id='openarm-e-recording-raw-outcomes'
    schema_context=bind_request_context(RequestContext(organization_id=ORG,project_id=PROJECT,
        region_code=None,service_identity=True,subject_id=actor.subject_id))
    schemas=runtime.annotation.list_tag_schema_versions(project_id=PROJECT,schema_id=schema_id,actor=actor)
    if not schemas:
        schema=runtime.annotation.create_tag_schema_version(project_id=PROJECT,name='E platform slicing outcomes',
            schema_id=schema_id,actor=actor,
            document=TagSchemaDocument(nodes=tuple({'tag_id':v,'code':v,'display_name':v} for v in ('success','failure'))),
            compatible_targets=(TagSchemaTarget(region_code=REGION,dataset_id=DATASET,
                dataset_schema_snapshot_id='openarm-recording-v2',task_kind='TAGGING'),))
        runtime.annotation.publish_tag_schema_version(project_id=PROJECT,schema_id=schema_id,version=schema.version,actor=actor)
    reset_request_context(schema_context)
    topics=frozenset(['observation.images.'+c+suffix for c in ('camera_left','camera_right','head')
        for suffix in ('','_depth')]+['/observation/state','/action','/openarm/capture_validity','source.timestamp_ns'])
    runtime.quality_repository.put_profile(PROJECT,QualityProfileV1(profile_id='openarm-recording-v3',required_topics=topics))
    print(json.dumps({'task_id':TASK,'dataset_id':DATASET,'robot_id':ROBOT,'formats':['LEROBOT_V3','CAPTURE_BUNDLE']}))


if __name__=='__main__':main()
