-- P19 hardening: project IDs are organization-local.  The first governance
-- migration predates the organization-aware key migration and therefore used
-- project/region uniqueness alone.  Rekey durable governance rows before
-- allowing two organizations to use the same project identifier.

DO $rekey$
DECLARE
    primary_key_name text;
BEGIN
    SELECT constraint_name
      INTO primary_key_name
      FROM information_schema.table_constraints
     WHERE table_schema = 'core'
       AND table_name = 'audit_retention_policies'
       AND constraint_type = 'PRIMARY KEY';
    IF primary_key_name IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE core.audit_retention_policies DROP CONSTRAINT %I',
            primary_key_name
        );
    END IF;
END
$rekey$;

ALTER TABLE core.audit_retention_policies
    ADD CONSTRAINT audit_retention_policies_pkey
    PRIMARY KEY (organization_id, project_id, region_code);

ALTER TABLE core.audit_export_jobs
    DROP CONSTRAINT IF EXISTS audit_export_jobs_project_id_region_code_idempotency_key_key;
ALTER TABLE core.audit_export_jobs
    ADD CONSTRAINT audit_export_jobs_organization_scope_idempotency_key
    UNIQUE (organization_id, project_id, region_code, idempotency_key);

DROP INDEX IF EXISTS core.audit_legal_holds_scope_status_time_idx;
CREATE INDEX audit_legal_holds_organization_scope_status_time_idx
ON core.audit_legal_holds (
    organization_id, project_id, region_code, status, occurred_from, occurred_to
);

DROP INDEX IF EXISTS core.audit_export_jobs_scope_created_idx;
CREATE INDEX audit_export_jobs_organization_scope_created_idx
ON core.audit_export_jobs (organization_id, project_id, region_code, created_at DESC, job_id DESC);

SELECT core.apply_project_rls('core.audit_retention_policies'::regclass);
SELECT core.apply_project_rls('core.audit_legal_holds'::regclass);
SELECT core.apply_project_rls('core.audit_export_jobs'::regclass);
