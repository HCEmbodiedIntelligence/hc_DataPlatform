DROP INDEX storage.storage_lifecycle_enabled_target_unique_idx;
CREATE UNIQUE INDEX storage_lifecycle_enabled_target_unique_idx ON storage.lifecycle_policies (
    project_id, business_category, object_role, priority,
    (CASE WHEN action LIKE 'CLEAN_%' AND action <> 'CLEAN_REBUILDABLE_CACHE' THEN action ELSE '' END)
) WHERE state = 'ENABLED';
