"""Retire only the immutable Episode package bound to a manual report."""
from hc_data_platform.core.errors import problem


def retire_reported_version(cursor, record):
    scope = (record.scope.organization_id, record.scope.project_id, record.scope.region_code)
    cursor.execute("""
        SELECT v.data_version_id, stream#>>'{package_binding,data,identity,rollout_id}'
        FROM dataset_registry.dataset_version_episode_revisions r
        CROSS JOIN LATERAL jsonb_array_elements(r.revision_document->'streams') stream
        JOIN episode_store.versions v ON v.data_version_id=stream#>>'{package_binding,data,data_version_id}'
            AND v.receipt_document->'data'=stream#>'{package_binding,data}'
        WHERE (r.organization_id,r.project_id,r.region_code,r.dataset_id,r.version_id,r.episode_id,r.revision_id)
            =(%s,%s,%s,%s,%s,%s,%s) AND stream->>'episode_stream_id'=%s
          AND (v.organization_id,v.project_id,v.region_code,v.dataset_id,v.episode_id)=
              (r.organization_id,r.project_id,r.region_code,r.dataset_id,r.episode_id)
          AND v.status='COMMITTED' FOR UPDATE OF v
    """, (*scope, record.dataset_id, record.origin_dataset_version_id, record.episode_id,
          record.episode_revision_id, record.episode_stream_id))
    binding = cursor.fetchall()
    if len(binding) != 1:
        raise problem(status=409, code="MANUAL_ISSUE_PACKAGE_UNBOUND", title="无法丢弃数据包",
                      detail="该上报未关联唯一的已入库数据包。")
    version, rollout = binding[0]
    cursor.execute("""
        UPDATE ingest.recording_episode_processing SET status='DISCARDED',updated_at=now()
        WHERE (organization_id,project_id,region_code,dataset_id)=(%s,%s,%s,%s)
          AND data_ref->>'data_version_id'=%s AND status='READY'
        RETURNING recording_id, annotation_task_id
    """, (*scope,record.dataset_id,version))
    processed = cursor.fetchone()
    cursor.execute("""
        UPDATE ingest.raw_source_episodes SET status='DISCARDED',updated_at=now()
        WHERE (organization_id,project_id,region_code)=(%s,%s,%s)
          AND data_ref->>'data_version_id'=%s AND status='READY'
    """, (*scope,version))
    cursor.execute("""
        INSERT INTO episode_store.qc_retired_versions
        (organization_id,project_id,region_code,dataset_id,episode_id,data_version_id,
         recording_id,annotation_task_id,report_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
    """, (*scope, record.dataset_id, record.episode_id, version,
          processed[0] if processed else rollout, processed[1] if processed else record.annotation_task_id, record.id))
    return version

