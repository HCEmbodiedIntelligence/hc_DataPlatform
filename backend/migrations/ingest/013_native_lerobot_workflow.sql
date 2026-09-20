-- Preserve per-Episode native receipts when aggregate workflow progress is persisted.
CREATE OR REPLACE FUNCTION ingest.sync_raw_source_workflow_job()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    mapped_job_status text;
    mapped_processing_status text;
    mapped_episode_status text;
    receipt_dataset_version integer;
    receipt_lance_version integer;
BEGIN
    -- A native import owns multiple child episode receipts. Its aggregate job cannot
    -- be projected onto every Episode by the legacy single-Rollout trigger.
    IF NEW.job_type = 'LeRobotImportWorkflow' THEN
        RETURN NEW;
    END IF;
    IF NEW.status = 'SUCCEEDED' THEN
        IF (NEW.result #>> '{dataset_version,version}') ~ '^[1-9][0-9]*$'
           AND (NEW.result #>> '{derived,lance_version}') ~ '^[1-9][0-9]*$'
        THEN
            mapped_job_status := 'SUCCEEDED';
            mapped_processing_status := 'READY';
            mapped_episode_status := 'READY';
            receipt_dataset_version := (NEW.result #>> '{dataset_version,version}')::integer;
            receipt_lance_version := (NEW.result #>> '{derived,lance_version}')::integer;
        ELSE
            mapped_job_status := 'PARTIALLY_FAILED';
            mapped_processing_status := 'PARTIALLY_FAILED';
            mapped_episode_status := 'FAILED';
        END IF;
    ELSIF NEW.status IN ('QUALITY_RISK', 'QUALITY_REJECTED') THEN
        mapped_job_status := 'PARTIALLY_FAILED';
        mapped_processing_status := 'PARTIALLY_FAILED';
        mapped_episode_status := 'FAILED';
    ELSIF NEW.status IN ('TECHNICAL_FAILED', 'CANCELLED') THEN
        mapped_job_status := CASE WHEN NEW.status = 'CANCELLED' THEN 'CANCELLED' ELSE 'FAILED' END;
        mapped_processing_status := 'FAILED';
        mapped_episode_status := 'FAILED';
    ELSIF NEW.status = 'RUNNING' THEN
        mapped_job_status := 'RUNNING';
        mapped_processing_status := 'PROCESSING';
        mapped_episode_status := 'PROCESSING';
    ELSE
        mapped_job_status := 'PENDING';
        mapped_processing_status := 'PENDING';
        mapped_episode_status := 'PENDING';
    END IF;

    UPDATE ingest.raw_ingest_jobs job
       SET status = mapped_job_status,
           attempts = NEW.attempt,
           last_error_code = COALESCE(
               NEW.error_code,
               CASE
                   WHEN NEW.status = 'SUCCEEDED' AND mapped_job_status <> 'SUCCEEDED'
                       THEN 'RAW_SOURCE_RECEIPT_MISSING'
                   ELSE NULL
               END
           ),
           updated_at = NEW.updated_at
     WHERE job.organization_id = NEW.organization_id
       AND job.project_id = NEW.project_id
       AND job.workflow_id = NEW.workflow_id;

    IF FOUND THEN
        UPDATE ingest.raw_sources source
           SET processing_status = mapped_processing_status,
               updated_at = NEW.updated_at
         WHERE source.organization_id = NEW.organization_id
           AND source.project_id = NEW.project_id
           AND EXISTS (
               SELECT 1
                 FROM ingest.raw_ingest_jobs job
                WHERE job.organization_id = source.organization_id
                  AND job.project_id = source.project_id
                  AND job.region_code = source.region_code
                  AND job.raw_source_id = source.raw_source_id
                  AND job.workflow_id = NEW.workflow_id
           );

        UPDATE ingest.raw_source_episodes episode
           SET status = mapped_episode_status,
               dataset_version = CASE
                   WHEN mapped_episode_status = 'READY' THEN receipt_dataset_version
                   ELSE NULL
               END,
               lance_version = CASE
                   WHEN mapped_episode_status = 'READY' THEN receipt_lance_version
                   ELSE NULL
               END,
               updated_at = NEW.updated_at
         WHERE episode.organization_id = NEW.organization_id
           AND episode.project_id = NEW.project_id
           AND EXISTS (
               SELECT 1
                 FROM ingest.raw_ingest_jobs job
                WHERE job.organization_id = episode.organization_id
                  AND job.project_id = episode.project_id
                  AND job.region_code = episode.region_code
                  AND job.raw_source_id = episode.raw_source_id
                  AND job.workflow_id = NEW.workflow_id
           );
    END IF;
    RETURN NEW;
END
$function$;

