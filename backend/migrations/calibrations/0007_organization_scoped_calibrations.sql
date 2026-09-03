-- Calibration identities predate organization-scoped project IDs. Refuse an
-- ambiguous legacy project rather than attaching a calibration or dataset
-- association to an arbitrary tenant during the forward upgrade.

DO $preflight$
DECLARE
    invalid_projects text;
    mismatched_associations text;
BEGIN
    WITH referenced_projects AS (
        SELECT project_id FROM calibrations.calibration_sets
        UNION
        SELECT project_id FROM calibrations.calibration_publish_preflights
        UNION
        SELECT project_id FROM calibrations.calibration_version_documents
        UNION
        SELECT project_id FROM calibrations.calibration_validation_reports
        UNION
        SELECT project_id FROM calibrations.calibration_command_receipts
        UNION
        SELECT project_id FROM calibrations.calibration_dataset_version_associations
    ), invalid AS (
        SELECT referenced.project_id
        FROM referenced_projects referenced
        LEFT JOIN registry.organization_projects project
          ON project.project_id = referenced.project_id
        GROUP BY referenced.project_id
        HAVING count(project.organization_id) <> 1
    )
    SELECT string_agg(project_id, ', ' ORDER BY project_id)
      INTO invalid_projects
      FROM invalid;

    IF invalid_projects IS NOT NULL THEN
        RAISE EXCEPTION
            'organization-scoped calibration upgrade requires exactly one registry organization for every existing project: %',
            invalid_projects;
    END IF;

    SELECT string_agg(association.set_id, ', ' ORDER BY association.set_id)
      INTO mismatched_associations
      FROM calibrations.calibration_dataset_version_associations association
      JOIN registry.organization_projects project
        ON project.project_id = association.project_id
     WHERE association.organization_id <> project.organization_id;

    IF mismatched_associations IS NOT NULL THEN
        RAISE EXCEPTION
            'calibration dataset associations do not match their legacy project organization: %',
            mismatched_associations;
    END IF;
END
$preflight$;

ALTER TABLE calibrations.calibration_sets
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE calibrations.calibration_publish_preflights
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE calibrations.calibration_version_documents
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE calibrations.calibration_validation_reports
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE calibrations.calibration_command_receipts
    ADD COLUMN IF NOT EXISTS organization_id text;

UPDATE calibrations.calibration_sets calibration
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = calibration.project_id
   AND calibration.organization_id IS NULL;
UPDATE calibrations.calibration_publish_preflights preflight
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = preflight.project_id
   AND preflight.organization_id IS NULL;
UPDATE calibrations.calibration_version_documents document
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = document.project_id
   AND document.organization_id IS NULL;
UPDATE calibrations.calibration_validation_reports report
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = report.project_id
   AND report.organization_id IS NULL;
UPDATE calibrations.calibration_command_receipts receipt
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = receipt.project_id
   AND receipt.organization_id IS NULL;

ALTER TABLE calibrations.calibration_sets
    ALTER COLUMN organization_id SET DEFAULT NULLIF(current_setting('app.organization_id', true), ''),
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT calibration_sets_organization_nonempty CHECK (organization_id <> '');
ALTER TABLE calibrations.calibration_publish_preflights
    ALTER COLUMN organization_id SET DEFAULT NULLIF(current_setting('app.organization_id', true), ''),
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT calibration_publish_preflights_organization_nonempty
        CHECK (organization_id <> '');
ALTER TABLE calibrations.calibration_version_documents
    ALTER COLUMN organization_id SET DEFAULT NULLIF(current_setting('app.organization_id', true), ''),
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT calibration_version_documents_organization_nonempty
        CHECK (organization_id <> '');
ALTER TABLE calibrations.calibration_validation_reports
    ALTER COLUMN organization_id SET DEFAULT NULLIF(current_setting('app.organization_id', true), ''),
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT calibration_validation_reports_organization_nonempty
        CHECK (organization_id <> '');
ALTER TABLE calibrations.calibration_command_receipts
    ALTER COLUMN organization_id SET DEFAULT NULLIF(current_setting('app.organization_id', true), ''),
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT calibration_command_receipts_organization_nonempty
        CHECK (organization_id <> '');

-- Rebuild every calibration identity edge before P15 replaces the legacy
-- robot-component key. The old component FK stays until the robotics migration
-- can recreate it against the new exact key.
ALTER TABLE calibrations.calibration_publish_preflights
    DROP CONSTRAINT calibration_publish_preflight_project_id_region_code_set_i_fkey;
ALTER TABLE calibrations.calibration_version_documents
    DROP CONSTRAINT calibration_version_documents_project_id_region_code_set_i_fkey;
ALTER TABLE calibrations.calibration_validation_reports
    DROP CONSTRAINT calibration_validation_report_project_id_region_code_set_i_fkey;
ALTER TABLE calibrations.calibration_dataset_version_associations
    DROP CONSTRAINT calibration_dataset_version_a_project_id_region_code_set_i_fkey;

ALTER TABLE calibrations.calibration_sets
    DROP CONSTRAINT calibration_sets_pkey,
    ADD CONSTRAINT calibration_sets_pkey
        PRIMARY KEY (organization_id, project_id, region_code, set_id),
    ADD CONSTRAINT calibration_sets_organization_project_fk
        FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id);

ALTER TABLE calibrations.calibration_publish_preflights
    DROP CONSTRAINT calibration_publish_preflights_pkey,
    DROP CONSTRAINT calibration_publish_preflight_project_id_region_code_set_id_key,
    ADD CONSTRAINT calibration_publish_preflights_pkey
        PRIMARY KEY (organization_id, project_id, region_code, preflight_id),
    ADD CONSTRAINT calibration_publish_preflight_scope_idempotency_key
        UNIQUE (organization_id, project_id, region_code, set_id, version, idempotency_key),
    ADD CONSTRAINT calibration_publish_preflight_set_organization_fk
        FOREIGN KEY (organization_id, project_id, region_code, set_id)
        REFERENCES calibrations.calibration_sets (organization_id, project_id, region_code, set_id);

ALTER TABLE calibrations.calibration_version_documents
    DROP CONSTRAINT calibration_version_documents_pkey,
    ADD CONSTRAINT calibration_version_documents_pkey
        PRIMARY KEY (organization_id, project_id, region_code, set_id, version),
    ADD CONSTRAINT calibration_version_document_set_organization_fk
        FOREIGN KEY (organization_id, project_id, region_code, set_id)
        REFERENCES calibrations.calibration_sets (organization_id, project_id, region_code, set_id);

ALTER TABLE calibrations.calibration_validation_reports
    DROP CONSTRAINT calibration_validation_reports_pkey,
    DROP CONSTRAINT calibration_validation_report_project_id_region_code_set_id_key,
    ADD CONSTRAINT calibration_validation_reports_pkey
        PRIMARY KEY (organization_id, project_id, region_code, report_id),
    ADD CONSTRAINT calibration_validation_report_scope_content_key
        UNIQUE (
            organization_id, project_id, region_code, set_id, version,
            content_hash, validation_context_hash
        ),
    ADD CONSTRAINT calibration_validation_report_document_organization_fk
        FOREIGN KEY (organization_id, project_id, region_code, set_id, version)
        REFERENCES calibrations.calibration_version_documents (
            organization_id, project_id, region_code, set_id, version
        );

ALTER TABLE calibrations.calibration_command_receipts
    DROP CONSTRAINT calibration_command_receipts_pkey,
    ADD CONSTRAINT calibration_command_receipts_pkey
        PRIMARY KEY (
            organization_id, project_id, region_code, resource_id,
            operation, idempotency_key
        );

ALTER TABLE calibrations.calibration_dataset_version_associations
    DROP CONSTRAINT calibration_dataset_version_associations_pkey,
    ADD CONSTRAINT calibration_dataset_version_associations_pkey
        PRIMARY KEY (
            organization_id, project_id, region_code, set_id, calibration_version,
            dataset_id, dataset_version_id
        ),
    ADD CONSTRAINT calibration_dataset_association_document_organization_fk
        FOREIGN KEY (organization_id, project_id, region_code, set_id, calibration_version)
        REFERENCES calibrations.calibration_version_documents (
            organization_id, project_id, region_code, set_id, version
        );

DROP INDEX IF EXISTS calibrations.calibrations_set_scope_robot_idx;
DROP INDEX IF EXISTS calibrations.calibrations_set_scope_component_idx;
DROP INDEX IF EXISTS calibrations.calibration_version_documents_scope_set_idx;
DROP INDEX IF EXISTS calibrations.calibration_validation_reports_scope_set_idx;
DROP INDEX IF EXISTS calibrations.calibration_dataset_associations_dataset_idx;
CREATE INDEX calibrations_set_organization_scope_robot_idx
ON calibrations.calibration_sets (
    organization_id, project_id, region_code, robot_instance_id, set_id
);
CREATE INDEX calibrations_set_organization_scope_component_idx
ON calibrations.calibration_sets (
    organization_id, project_id, region_code, component_id, set_id
);
CREATE INDEX calibration_version_documents_organization_scope_set_idx
ON calibrations.calibration_version_documents (
    organization_id, project_id, region_code, set_id, version DESC
);
CREATE INDEX calibration_validation_reports_organization_scope_set_idx
ON calibrations.calibration_validation_reports (
    organization_id, project_id, region_code, set_id, version DESC, checked_at DESC
);
CREATE INDEX calibration_dataset_associations_organization_dataset_idx
ON calibrations.calibration_dataset_version_associations (
    organization_id, project_id, region_code, dataset_id, dataset_version_id, associated_at DESC
);

SELECT core.apply_project_rls('calibrations.calibration_sets'::regclass);
SELECT core.apply_project_rls('calibrations.calibration_publish_preflights'::regclass);
SELECT core.apply_project_rls('calibrations.calibration_version_documents'::regclass);
SELECT core.apply_project_rls('calibrations.calibration_validation_reports'::regclass);
SELECT core.apply_project_rls('calibrations.calibration_command_receipts'::regclass);
SELECT core.apply_project_rls('calibrations.calibration_dataset_version_associations'::regclass);
