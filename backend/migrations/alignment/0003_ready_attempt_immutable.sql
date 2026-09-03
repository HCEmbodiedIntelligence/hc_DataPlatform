-- WRITING attempts may transition to READY once. A READY attempt is frozen; retries
-- must compare and reuse it instead of issuing an UPDATE that can change its lineage.
CREATE OR REPLACE FUNCTION reject_ready_alignment_attempt_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF OLD.status = 'READY' THEN
        RAISE EXCEPTION 'ready alignment attempts are immutable' USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS aligned_fragment_ready_immutable ON aligned_fragment_attempts;
CREATE TRIGGER aligned_fragment_ready_immutable
BEFORE UPDATE OR DELETE ON aligned_fragment_attempts
FOR EACH ROW EXECUTE FUNCTION reject_ready_alignment_attempt_mutation();
