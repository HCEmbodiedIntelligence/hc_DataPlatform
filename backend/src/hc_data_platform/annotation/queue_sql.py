"""Read current annotation work and its labels from the latest dataset projection."""

# Match the current attempt selection in collection_tasks.captures. Rank before
# filtering missing recording IDs: failed replacements also supersede old runs.
QUEUE_TASKS = """
WITH scope AS (
    SELECT NULLIF(current_setting('app.organization_id', true), '') AS organization_id,
           NULLIF(current_setting('app.project_id', true), '') AS project_id,
           NULLIF(current_setting('app.region_code', true), '') AS region_code
), file_runs AS (
    SELECT recording_id, error, row_number() OVER (
        PARTITION BY raw_source_id, source_path
        ORDER BY (error IS NOT DISTINCT FROM 'MCAP_REPROCESS_CANCELLED'), created_at DESC, processing_id
    ) AS position
    FROM ingest.mcap_processing_files JOIN scope USING (organization_id, project_id, region_code)
), superseded_recordings AS (
    SELECT recording_id FROM file_runs WHERE recording_id IS NOT NULL GROUP BY recording_id
    HAVING NOT bool_or(position = 1 AND error IS DISTINCT FROM 'MCAP_REPROCESS_CANCELLED')
), hidden_tasks AS (
    SELECT annotation_task_id FROM episode_store.qc_retired_versions
    JOIN scope USING (organization_id, project_id, region_code)
    UNION ALL
    SELECT annotation_task_id FROM ingest.recording_episode_processing
    JOIN scope USING (organization_id, project_id, region_code)
    JOIN superseded_recordings USING (recording_id)
)
SELECT {columns}, e.ordinal AS queue_ordinal, COALESCE(et.task_code, dt.task_code) AS queue_task_code,
       COALESCE(et.collection_task_id, dt.collection_task_id) AS collection_task_id
FROM annotation.annotation_tasks a
JOIN scope s USING (organization_id, project_id, region_code)
LEFT JOIN LATERAL (
    SELECT v.version_id FROM dataset_registry.dataset_versions v
    WHERE (v.organization_id,v.project_id,v.region_code,v.dataset_id)
        = (a.organization_id,a.project_id,a.region_code,a.dataset_id)
    ORDER BY v.created_at DESC, v.version_id DESC LIMIT 1
) latest ON true
LEFT JOIN dataset_registry.dataset_version_episodes e
    ON (e.organization_id,e.project_id,e.region_code,e.dataset_id,e.version_id,e.episode_id)
     = (a.organization_id,a.project_id,a.region_code,a.dataset_id,latest.version_id,
        a.data_ref #>> '{{identity,episode_id}}')
LEFT JOIN dataset_registry.datasets d
    ON (d.organization_id,d.project_id,d.region_code,d.dataset_id)
     = (a.organization_id,a.project_id,a.region_code,a.dataset_id)
LEFT JOIN collection_tasks.collection_tasks et
    ON (et.organization_id,et.project_id,et.collection_task_id) = (a.organization_id,a.project_id,e.task)
LEFT JOIN collection_tasks.collection_tasks dt
    ON (dt.organization_id,dt.project_id,dt.collection_task_id) = (a.organization_id,a.project_id,d.collection_task_id)
WHERE {predicate}
  AND NOT EXISTS (SELECT FROM hidden_tasks h WHERE h.annotation_task_id = a.task_id)
ORDER BY a.updated_at DESC, a.task_id DESC
"""
