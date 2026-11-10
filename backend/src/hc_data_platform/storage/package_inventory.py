"""Durable file registrations from the Raw/Episode package pipeline.

The caller binds an exact tenant scope through the normal RLS connection factory.
Only committed prefixes and receipts are listed; provider byte sizes remain the
source of truth, and the producer deduplicates shared keys across registrations.
"""

from typing import Any


def package_inventory_rows(cursor: Any, project_id: str) -> None:
    cursor.execute(
        """
        WITH selected_project AS (SELECT %s::text AS project_id),
        packages AS (
            SELECT v.*,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM annotation.annotation_tasks t
                       WHERE t.organization_id = v.organization_id
                         AND t.project_id = v.project_id AND t.region_code = v.region_code
                         AND t.data_ref->>'data_version_id' = v.data_version_id
                         AND t.status = 'APPROVED'
                   ) THEN 'ANNOTATION_COMPLETE' ELSE 'PENDING_ANNOTATION' END AS category
            FROM episode_store.versions v
            JOIN selected_project p USING (project_id)
            WHERE v.status = 'COMMITTED'
        )
        SELECT 'raw-source:' || raw_source_id AS identity,
               rtrim(storage_prefix, '/') || '/' AS object_key,
               NULL::bigint AS expected_bytes, 'RAW' AS business_category,
               'RAW' AS object_role, committed_at AS observed_at,
               true AS prefix, 65 AS priority, true AS required
        FROM ingest.raw_sources JOIN selected_project USING (project_id)
        WHERE raw_status = 'COMMITTED' AND NOT starts_with(storage_prefix, 'oss://')
        UNION ALL
        SELECT 'raw-manifest:' || raw_source_id, manifest_key, NULL::bigint,
               'RAW', 'MANIFEST', committed_at, false, 66, true
        FROM ingest.raw_sources JOIN selected_project USING (project_id)
        WHERE raw_status = 'COMMITTED'
        UNION ALL
        SELECT 'recording-asset:' || a.asset_id, a.object_key, a.expected_size,
               'RAW', 'RAW', a.committed_at, false, 60,
               NOT EXISTS (SELECT 1 FROM ingest.recording_cache_retirements c
                   WHERE (c.organization_id,c.project_id,c.region_code,c.object_key)
                       = (a.organization_id,a.project_id,a.region_code,a.object_key))
        FROM ingest.recording_upload_assets a JOIN selected_project USING (project_id)
        WHERE a.status = 'COMMITTED' AND NOT starts_with(a.object_key, 'oss://')
        UNION ALL
        SELECT 'episode:' || data_version_id,
               rtrim(receipt_document->'data'->>'package_prefix', '/') || '/',
               NULL::bigint, category, 'REBUILDABLE_DERIVATIVE', committed_at, true, 75, true
        FROM packages
        UNION ALL
        SELECT 'episode-preview:' || m.data_version_id || ':' || feature || ':' || profile_id,
               receipt->>'object_key', (receipt->'file'->>'size')::bigint,
               p.category, 'REBUILDABLE_DERIVATIVE', updated_at, false, 74, true
        FROM episode_store.media_previews m JOIN packages p
          USING (organization_id, project_id, region_code, data_version_id)
        WHERE m.status = 'READY'
        UNION ALL
        SELECT 'annotation-file:' || f.task_id || ':' || f.revision || ':' || f.format_version,
               f.object_key, f.size_bytes,
               CASE WHEN t.status = 'APPROVED' AND t.approved_revision = f.revision
                    THEN 'ANNOTATION_COMPLETE' ELSE 'PENDING_ANNOTATION' END,
               'REBUILDABLE_DERIVATIVE', f.updated_at, false, 80, true
        FROM annotation.revision_files f JOIN selected_project USING (project_id)
        JOIN annotation.annotation_tasks t USING (organization_id, project_id, region_code, task_id)
        WHERE f.status = 'READY'
        UNION ALL
        SELECT 'episode-delivery:' || job_id, artifact_key, size_bytes,
               CASE WHEN EXISTS (
                   SELECT 1 FROM jsonb_array_elements(plan->'members') member
                   WHERE member->'annotation' IS NOT NULL
                     AND member->'annotation' <> 'null'::jsonb
               ) THEN 'ANNOTATION_COMPLETE'
                    ELSE 'PENDING_ANNOTATION' END,
               'REBUILDABLE_DERIVATIVE', updated_at, false, 80, true
        FROM episode_store.delivery_jobs JOIN selected_project USING (project_id)
        WHERE status = 'READY'
        UNION ALL
        SELECT 'annotation-delivery:' || job_id, artifact_key, size_bytes,
               'ANNOTATION_COMPLETE', 'REBUILDABLE_DERIVATIVE', updated_at, false, 80, true
        FROM annotation.delivery_jobs JOIN selected_project USING (project_id)
        WHERE status = 'READY'
        ORDER BY identity
        """,
        (project_id,),
    )
