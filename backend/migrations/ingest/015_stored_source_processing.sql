-- Permit the initial processing binding of stored LeRobot data without changing Raw.
CREATE OR REPLACE FUNCTION ingest.protect_raw_source_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.organization_id IS DISTINCT FROM OLD.organization_id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.region_code IS DISTINCT FROM OLD.region_code
       OR NEW.raw_source_id IS DISTINCT FROM OLD.raw_source_id
       OR NEW.upload_id IS DISTINCT FROM OLD.upload_id
       OR NEW.dataset_id IS DISTINCT FROM OLD.dataset_id
       OR NEW.source_format IS DISTINCT FROM OLD.source_format
       OR NEW.source_format_version IS DISTINCT FROM OLD.source_format_version
       OR NEW.manifest_key IS DISTINCT FROM OLD.manifest_key
       OR NEW.storage_prefix IS DISTINCT FROM OLD.storage_prefix
       OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
       OR NEW.file_count IS DISTINCT FROM OLD.file_count
       OR NEW.total_bytes IS DISTINCT FROM OLD.total_bytes
       OR NEW.raw_status IS DISTINCT FROM OLD.raw_status
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.committed_at IS DISTINCT FROM OLD.committed_at
    THEN
        RAISE EXCEPTION 'Raw source immutable identity cannot be changed';
    END IF;
    -- A stored source can acquire its processing binding exactly once. Its
    -- bytes, owner, dataset and original capture identity remain immutable.
    IF (NEW.collection_task_id IS DISTINCT FROM OLD.collection_task_id
        OR NEW.robot_id IS DISTINCT FROM OLD.robot_id)
       AND NOT (
           OLD.processing_status = 'NOT_REQUESTED'
           AND NEW.processing_status = 'PENDING'
           AND OLD.source_format = 'LEROBOT_V3'
           AND OLD.collection_task_id IS NULL AND OLD.robot_id IS NULL
           AND NEW.collection_task_id IS NOT NULL AND NEW.robot_id IS NOT NULL
       )
    THEN
        RAISE EXCEPTION 'Raw source processing binding cannot be changed';
    END IF;
    RETURN NEW;
END
$function$;

