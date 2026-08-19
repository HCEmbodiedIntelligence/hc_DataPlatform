-- Forward-only extraction of the inventory immutability change that was
-- accidentally written into the already-applied storage/0001 migration.
-- Existing snapshots were published before the explicit seal existed, so they
-- are sealed before the mutation guards are installed.
ALTER TABLE storage.inventory_snapshots
ADD COLUMN IF NOT EXISTS sealed boolean;

ALTER TABLE storage.inventory_snapshots
ALTER COLUMN sealed SET DEFAULT false;

UPDATE storage.inventory_snapshots
SET sealed = true
WHERE sealed IS DISTINCT FROM true;

ALTER TABLE storage.inventory_snapshots
ALTER COLUMN sealed SET NOT NULL;

CREATE OR REPLACE FUNCTION storage.protect_inventory_snapshot()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'storage inventory snapshots are immutable';
    END IF;
    IF OLD.sealed THEN
        RAISE EXCEPTION 'sealed storage inventory snapshots are immutable';
    END IF;
    IF NOT NEW.sealed OR (
        ROW(
            NEW.project_id,
            NEW.snapshot_id,
            NEW.observed_at,
            NEW.physical_total_bytes,
            NEW.physical_instance_count,
            NEW.candidate_business_total_bytes,
            NEW.candidate_logical_object_count,
            NEW.replica_overhead_bytes,
            NEW.temporary_bytes,
            NEW.duplicate_inventory_rows_ignored,
            NEW.content_digest,
            NEW.published_at
        ) IS DISTINCT FROM ROW(
            OLD.project_id,
            OLD.snapshot_id,
            OLD.observed_at,
            OLD.physical_total_bytes,
            OLD.physical_instance_count,
            OLD.candidate_business_total_bytes,
            OLD.candidate_logical_object_count,
            OLD.replica_overhead_bytes,
            OLD.temporary_bytes,
            OLD.duplicate_inventory_rows_ignored,
            OLD.content_digest,
            OLD.published_at
        )
    ) THEN
        RAISE EXCEPTION 'only the one-way storage inventory seal transition is allowed';
    END IF;
    RETURN NEW;
END
$function$;

CREATE OR REPLACE FUNCTION storage.protect_inventory_fact()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'storage inventory facts are immutable';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM storage.inventory_snapshots snapshot
        WHERE snapshot.project_id = NEW.project_id
          AND snapshot.snapshot_id = NEW.snapshot_id
          AND snapshot.sealed
    ) THEN
        RAISE EXCEPTION 'sealed storage inventory snapshots reject new facts';
    END IF;
    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS storage_inventory_snapshot_immutable
ON storage.inventory_snapshots;
CREATE TRIGGER storage_inventory_snapshot_immutable
BEFORE UPDATE OR DELETE ON storage.inventory_snapshots
FOR EACH ROW EXECUTE FUNCTION storage.protect_inventory_snapshot();

DROP TRIGGER IF EXISTS storage_inventory_fact_immutable
ON storage.inventory_facts;
CREATE TRIGGER storage_inventory_fact_immutable
BEFORE INSERT OR UPDATE OR DELETE ON storage.inventory_facts
FOR EACH ROW EXECUTE FUNCTION storage.protect_inventory_fact();
