ALTER TABLE storage.lifecycle_policies DROP CONSTRAINT lifecycle_policies_action_check;
ALTER TABLE storage.lifecycle_policies ADD CONSTRAINT lifecycle_policies_action_check
CHECK (action IN ('RETAIN', 'REVIEW_EXPIRATION', 'ARCHIVE', 'TRANSITION_TO_COLD',
 'CLEAN_REBUILDABLE_CACHE', 'CLEAN_PROCESSING_CACHE', 'CLEAN_RECORDING_PREVIEWS',
 'CLEAN_EPISODE_PREVIEWS', 'CLEAN_EXPORT_ARCHIVES', 'CLEAN_PROCESSING_STAGING',
 'CLEAN_MEDIA_STAGING', 'CLEAN_OSS_TEST_COPIES', 'CLEAN_INVENTORY_DETAILS', 'CLEAN_INVENTORY_HISTORY'));
ALTER TABLE storage.lifecycle_policies ADD CONSTRAINT inventory_retention_target_check
CHECK (action NOT IN ('CLEAN_INVENTORY_DETAILS', 'CLEAN_INVENTORY_HISTORY') OR
 (object_role = 'REBUILDABLE_DERIVATIVE' AND business_category = 'PENDING_ANNOTATION'
  AND (action = 'CLEAN_INVENTORY_DETAILS' OR minimum_age_days > 0)));
ALTER TABLE storage.inventory_snapshots ADD COLUMN summary jsonb;
ALTER TABLE storage.inventory_snapshots ADD COLUMN details_expired_at timestamptz;
ALTER TABLE storage.inventory_snapshots ADD CONSTRAINT inventory_summary_identity_check
CHECK (summary IS NULL OR (jsonb_typeof(summary) = 'object'
 AND summary->>'snapshot_id' = snapshot_id AND summary->>'project_id' = project_id
 AND (summary->>'physical_total_bytes')::numeric = physical_total_bytes
 AND (summary->>'candidate_business_total_bytes')::numeric = candidate_business_total_bytes
 AND (summary->>'physical_instance_count')::bigint = physical_instance_count
 AND (summary->>'candidate_logical_object_count')::bigint = candidate_logical_object_count));
ALTER TABLE storage.inventory_snapshots ADD CONSTRAINT inventory_details_require_summary
CHECK (details_expired_at IS NULL OR summary IS NOT NULL);

CREATE FUNCTION storage.inventory_expiration_allowed(p_project text, p_snapshot text,
 p_observed timestamptz, p_action text) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS (
   SELECT 1 FROM storage.lifecycle_policies p
   WHERE p.project_id = p_project AND p.action = p_action AND p.state = 'ENABLED'
   HAVING count(*) > 0 AND p_observed < statement_timestamp() - max(p.minimum_age_days) * interval '1 day'
 ) AND p_snapshot <> (
   SELECT snapshot_id FROM storage.inventory_snapshots WHERE project_id = p_project
   ORDER BY observed_at DESC, snapshot_id DESC LIMIT 1
 );
$$;

CREATE OR REPLACE FUNCTION storage.protect_inventory_snapshot() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN
   IF OLD.details_expired_at IS NOT NULL AND storage.inventory_expiration_allowed(
     OLD.project_id, OLD.snapshot_id, OLD.observed_at, 'CLEAN_INVENTORY_HISTORY') THEN
     RETURN OLD;
   END IF;
   RAISE EXCEPTION 'storage inventory summary is retained by lifecycle policy';
 END IF;
 IF OLD.sealed THEN
   IF (to_jsonb(NEW) - ARRAY['summary','details_expired_at']) =
      (to_jsonb(OLD) - ARRAY['summary','details_expired_at']) THEN
     IF OLD.summary IS NULL AND NEW.summary IS NOT NULL
        AND NEW.details_expired_at IS NOT DISTINCT FROM OLD.details_expired_at THEN
       RETURN NEW;
     END IF;
     IF NEW.summary = OLD.summary AND OLD.details_expired_at IS NULL
        AND NEW.details_expired_at IS NOT NULL AND storage.inventory_expiration_allowed(
          OLD.project_id, OLD.snapshot_id, OLD.observed_at, 'CLEAN_INVENTORY_DETAILS') THEN
       RETURN NEW;
     END IF;
   END IF;
   RAISE EXCEPTION 'sealed storage inventory snapshots are immutable';
 END IF;
 IF NOT NEW.sealed OR (to_jsonb(NEW) - ARRAY['sealed','summary']) <>
                       (to_jsonb(OLD) - ARRAY['sealed','summary']) THEN
   RAISE EXCEPTION 'only the one-way storage inventory seal transition is allowed';
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION storage.protect_inventory_fact() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'DELETE' AND EXISTS (
   SELECT 1 FROM storage.inventory_snapshots s WHERE s.project_id = OLD.project_id
   AND s.snapshot_id = OLD.snapshot_id AND s.details_expired_at IS NOT NULL
 ) THEN RETURN OLD;
 END IF;
 IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'storage inventory facts are immutable'; END IF;
 IF EXISTS (SELECT 1 FROM storage.inventory_snapshots s WHERE s.project_id = NEW.project_id
   AND s.snapshot_id = NEW.snapshot_id AND s.sealed) THEN
   RAISE EXCEPTION 'sealed storage inventory snapshots reject new facts';
 END IF;
 RETURN NEW;
END $$;

-- All fact reads use project + snapshot + physical ID; the primary key covers them.
DROP INDEX storage.storage_inventory_facts_business_idx;
