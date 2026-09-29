-- Reuse delivery_jobs and its scoped outbox. Collection sizes are unknown until built.
ALTER TABLE episode_store.delivery_jobs ALTER COLUMN total_files DROP NOT NULL;
ALTER TABLE episode_store.delivery_jobs ALTER COLUMN total_bytes DROP NOT NULL;
DO $$
DECLARE constraint_row record;
BEGIN
    FOR constraint_row IN SELECT conname FROM pg_constraint
        WHERE conrelid='episode_store.delivery_jobs'::regclass AND contype='c'
          AND (pg_get_constraintdef(oid) LIKE '%total_files%'
            OR pg_get_constraintdef(oid) LIKE '%total_bytes%'
            OR pg_get_constraintdef(oid) LIKE '%phase%') LOOP
        EXECUTE format('ALTER TABLE episode_store.delivery_jobs DROP CONSTRAINT %I', constraint_row.conname);
    END LOOP;
END $$;
ALTER TABLE episode_store.delivery_jobs
    ADD CONSTRAINT delivery_archive_totals CHECK (
        (total_files IS NULL AND total_bytes IS NULL AND
         plan->>'format'='hc-collection-delivery-plan/1' AND
         processed_files=0 AND processed_bytes=0 AND artifact_key IS NULL AND status<>'READY') OR
        (total_files IS NOT NULL AND total_bytes IS NOT NULL AND total_files>0 AND total_bytes>0 AND
         processed_files BETWEEN 0 AND total_files AND processed_bytes BETWEEN 0 AND total_bytes)),
    ADD CONSTRAINT delivery_ready_archive CHECK (status<>'READY' OR
        (artifact_sha256 IS NOT NULL AND total_files IS NOT NULL AND total_bytes IS NOT NULL AND
         processed_files=total_files AND processed_bytes=total_bytes AND uploaded_bytes=size_bytes)),
    ADD CONSTRAINT delivery_status_phase CHECK (
        (status='PENDING' AND phase='queued') OR
        (status='RUNNING' AND phase IN ('verifying','projecting','archiving','uploading')) OR
        (status='READY' AND phase='completed') OR (status='FAILED' AND phase='failed') OR
        (status='CANCELLED' AND phase='cancelled'));

CREATE OR REPLACE FUNCTION episode_store.protect_delivery_identity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE member jsonb;
DECLARE parent episode_store.versions%ROWTYPE;
DECLARE selection jsonb;
BEGIN
    IF NEW.organization_id IS DISTINCT FROM NULLIF(current_setting('app.organization_id',true),'')
       OR NEW.project_id IS DISTINCT FROM NULLIF(current_setting('app.project_id',true),'')
       OR NEW.region_code IS DISTINCT FROM NULLIF(current_setting('app.region_code',true),'') THEN
        RAISE EXCEPTION 'package delivery scope mismatch' USING ERRCODE = '42501';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.status<>'PENDING' OR NEW.generation<>1 OR NEW.attempts<>0 OR NEW.artifact_key IS NOT NULL
           OR NEW.processed_bytes<>0 OR NEW.processed_files<>0
           OR NEW.plan->>'format' IS NULL OR NEW.plan->>'format' NOT IN
              ('hc-episode-delivery-plan/1','hc-collection-delivery-plan/1')
           OR jsonb_typeof(NEW.plan->'members') IS DISTINCT FROM 'array'
           OR jsonb_array_length(NEW.plan->'members') NOT BETWEEN 1 AND 100 THEN
            RAISE EXCEPTION 'invalid initial package delivery' USING ERRCODE = '23514';
        END IF;
        IF NEW.plan->>'format'='hc-episode-delivery-plan/1' THEN
            IF NEW.plan->>'modalities' IS DISTINCT FROM 'all_collected'
               OR NEW.plan->>'annotation_selection' IS DISTINCT FROM 'package_contents_only'
               OR NEW.total_files IS NULL OR NEW.total_bytes IS NULL THEN
                RAISE EXCEPTION 'invalid independent package selection' USING ERRCODE = '23514';
            END IF;
        ELSE
            IF NEW.plan->>'modalities' IS NULL OR NEW.plan->>'modalities' NOT IN ('rgb','rgbd')
               OR NEW.plan->>'archive_format' IS DISTINCT FROM 'lerobot_collection_zip/1'
               OR NEW.plan->>'annotation_selection' IS DISTINCT FROM 'per_episode_fixed_selection'
               OR NEW.total_files IS NOT NULL OR NEW.total_bytes IS NOT NULL THEN
                RAISE EXCEPTION 'invalid collection selection' USING ERRCODE = '23514';
            END IF;
        END IF;
        FOR member IN SELECT value FROM jsonb_array_elements(NEW.plan->'members') LOOP
            SELECT * INTO parent FROM episode_store.versions WHERE organization_id=NEW.organization_id
              AND project_id=NEW.project_id AND region_code=NEW.region_code AND status='COMMITTED'
              AND data_version_id=member->'data'->>'data_version_id';
            IF NOT FOUND OR parent.receipt_document->'data' IS DISTINCT FROM member->'data'
               OR parent.receipt_document->>'manifest_sha256' IS DISTINCT FROM member->>'manifest_sha256' THEN
                RAISE EXCEPTION 'delivery requires exact committed package receipts' USING ERRCODE = '23514';
            END IF;
            selection := member->'annotation';
            IF NEW.plan->>'format'='hc-collection-delivery-plan/1' AND selection IS DISTINCT FROM 'null'::jsonb THEN
                IF selection IS NULL OR selection->'binding'->'data' IS DISTINCT FROM member->'data'
                   OR selection->>'parent_manifest_sha256' IS DISTINCT FROM member->>'manifest_sha256'
                   OR NOT EXISTS (
                     SELECT 1 FROM annotation.revision_files f JOIN annotation.annotation_tasks t USING(task_id)
                     WHERE f.organization_id=NEW.organization_id AND f.project_id=NEW.project_id
                       AND f.region_code=NEW.region_code AND f.status='READY'
                       AND f.task_id=selection->'binding'->>'task_id'
                       AND f.revision=(selection->'binding'->>'revision')::bigint
                       AND f.revision_content_hash=selection->'binding'->>'revision_content_hash'
                       AND f.content_sha256=selection->'binding'->>'annotation_file_sha256'
                       AND f.size_bytes=(selection->>'annotation_size')::bigint AND t.data_ref=member->'data'
                   ) THEN
                    RAISE EXCEPTION 'collection annotation requires exact ready revision' USING ERRCODE = '23514';
                END IF;
            END IF;
        END LOOP;
    ELSE
        IF ROW(NEW.organization_id,NEW.project_id,NEW.region_code,NEW.job_id,NEW.plan,NEW.actor_id,NEW.created_at)
           IS DISTINCT FROM ROW(OLD.organization_id,OLD.project_id,OLD.region_code,OLD.job_id,OLD.plan,OLD.actor_id,OLD.created_at)
           OR (OLD.artifact_sha256 IS NOT NULL AND ROW(NEW.artifact_key,NEW.artifact_sha256,NEW.size_bytes)
               IS DISTINCT FROM ROW(OLD.artifact_key,OLD.artifact_sha256,OLD.size_bytes)) THEN
            RAISE EXCEPTION 'package delivery identity/content is immutable' USING ERRCODE = '23514';
        END IF;
        IF ROW(NEW.total_files,NEW.total_bytes) IS DISTINCT FROM ROW(OLD.total_files,OLD.total_bytes) AND
           NOT (OLD.total_files IS NULL AND NEW.total_files IS NOT NULL AND NEW.total_bytes IS NOT NULL
             AND OLD.status='RUNNING' AND NEW.status='RUNNING' AND OLD.lease_token=NEW.lease_token
             AND OLD.lease_expires_at>clock_timestamp() AND NEW.generation=OLD.generation
             AND NEW.plan->>'format'='hc-collection-delivery-plan/1') THEN
            RAISE EXCEPTION 'archive totals are immutable once generated' USING ERRCODE = '23514';
        END IF;
        IF NEW.generation<>OLD.generation AND (NEW.generation<>OLD.generation+1 OR NEW.status<>'PENDING'
           OR OLD.status NOT IN ('FAILED','CANCELLED')) THEN
            RAISE EXCEPTION 'invalid package delivery retry' USING ERRCODE = '23514';
        END IF;
        IF OLD.status='READY' AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'ready package delivery is immutable' USING ERRCODE = '23514';
        END IF;
        IF NEW.status='READY' AND OLD.status NOT IN ('RUNNING','READY') THEN
            RAISE EXCEPTION 'ready package delivery requires a worker lease' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
