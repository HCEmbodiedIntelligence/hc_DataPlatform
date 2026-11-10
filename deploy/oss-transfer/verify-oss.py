"""Check restored business indexes and OSS manifests with the platform's encrypted config."""
import hashlib
import json
from hc_data_platform.core.context import RequestContext, bind_request_context
from hc_data_platform.core.config import get_settings
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.platform_ops.object_store_config import load_persisted_object_store_settings
from hc_data_platform.runtime import _oss

bind_request_context(RequestContext(organization_id='01',project_id='01',region_code='global',
                                    service_identity=True,platform_admin=True))
settings,_ = load_persisted_object_store_settings(get_settings())
_,_,storage = _oss(settings.model_copy(update={'readiness_timeout_seconds':60}))
with psycopg_connection_factory(settings.postgres_dsn)() as db:
    db.execute('SET TRANSACTION READ ONLY')
    q = """SELECT t.task_code,c.document,c.content_hash FROM episode_store.collection_heads h
    JOIN episode_store.collections c USING(organization_id,project_id,region_code,dataset_id,collection_version_id)
    JOIN collection_tasks.collection_tasks t USING(organization_id,project_id,dataset_id)
    WHERE h.organization_id='01' AND h.project_id='01' AND h.region_code='global' ORDER BY 1"""
    from hc_data_platform.episode_store.models import CollectionSnapshot
    rows=db.execute(q).fetchall()
    assert rows, "No active project collections"
    for code, doc, digest in rows:
        snapshot=CollectionSnapshot.model_validate(doc)
        read=lambda k:b''.join(storage.read_chunks(k))
        assert hashlib.sha256(read(snapshot.manifest_key)).hexdigest()==digest
        for episode in snapshot.episodes:
            key=episode.package_prefix+'/hc/manifest.json'
            manifest=read(key)
            assert hashlib.sha256(manifest).hexdigest()==episode.content_hash,key
            assert json.loads(manifest)['frame_count']==episode.frame_count
        print('OSS verified: task',code,'episodes',len(snapshot.episodes),flush=True)
