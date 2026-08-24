-- A project ID is scoped by organization.  Robot-directory rows predate that
-- identity, so an ambiguous legacy project must stop the upgrade rather than
-- silently joining a robot, component, or command receipt to the wrong tenant.

DO $preflight$
DECLARE
    invalid_projects text;
BEGIN
    WITH referenced_projects AS (
        SELECT project_id FROM robotics.robot_instances
        UNION
        SELECT project_id FROM robotics.robot_components
        UNION
        SELECT project_id FROM robotics.component_frames
        UNION
        SELECT project_id FROM robotics.component_channels
        UNION
        SELECT project_id FROM robotics.robot_maintenance_records
        UNION
        SELECT project_id FROM robotics.robot_command_receipts
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
            'organization-scoped robotics upgrade requires exactly one registry organization for every existing project: %',
            invalid_projects;
    END IF;
END
$preflight$;

ALTER TABLE robotics.robot_instances
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE robotics.robot_components
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE robotics.component_frames
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE robotics.component_channels
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE robotics.robot_maintenance_records
    ADD COLUMN IF NOT EXISTS organization_id text;
ALTER TABLE robotics.robot_command_receipts
    ADD COLUMN IF NOT EXISTS organization_id text;

UPDATE robotics.robot_instances robot
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = robot.project_id
   AND robot.organization_id IS NULL;
UPDATE robotics.robot_components component
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = component.project_id
   AND component.organization_id IS NULL;
UPDATE robotics.component_frames frame
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = frame.project_id
   AND frame.organization_id IS NULL;
UPDATE robotics.component_channels channel
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = channel.project_id
   AND channel.organization_id IS NULL;
UPDATE robotics.robot_maintenance_records record
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = record.project_id
   AND record.organization_id IS NULL;
UPDATE robotics.robot_command_receipts receipt
   SET organization_id = project.organization_id
  FROM registry.organization_projects project
 WHERE project.project_id = receipt.project_id
   AND receipt.organization_id IS NULL;

ALTER TABLE robotics.robot_instances
    ALTER COLUMN organization_id SET DEFAULT NULLIF(current_setting('app.organization_id', true), ''),
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT robotics_robot_instances_organization_nonempty
        CHECK (organization_id <> '');
ALTER TABLE robotics.robot_components
    ALTER COLUMN organization_id SET DEFAULT NULLIF(current_setting('app.organization_id', true), ''),
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT robotics_robot_components_organization_nonempty
        CHECK (organization_id <> '');
ALTER TABLE robotics.component_frames
    ALTER COLUMN organization_id SET DEFAULT NULLIF(current_setting('app.organization_id', true), ''),
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT robotics_component_frames_organization_nonempty
        CHECK (organization_id <> '');
ALTER TABLE robotics.component_channels
    ALTER COLUMN organization_id SET DEFAULT NULLIF(current_setting('app.organization_id', true), ''),
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT robotics_component_channels_organization_nonempty
        CHECK (organization_id <> '');
ALTER TABLE robotics.robot_maintenance_records
    ALTER COLUMN organization_id SET DEFAULT NULLIF(current_setting('app.organization_id', true), ''),
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT robotics_robot_maintenance_records_organization_nonempty
        CHECK (organization_id <> '');
ALTER TABLE robotics.robot_command_receipts
    ALTER COLUMN organization_id SET DEFAULT NULLIF(current_setting('app.organization_id', true), ''),
    ALTER COLUMN organization_id SET NOT NULL,
    ADD CONSTRAINT robotics_robot_command_receipts_organization_nonempty
        CHECK (organization_id <> '');

-- Existing project/region foreign keys and keys cannot distinguish duplicate
-- project IDs across organizations.  Rebuild every identity edge with the
-- organization key before enabling the new policy.
ALTER TABLE robotics.robot_components
    DROP CONSTRAINT IF EXISTS robot_components_project_id_region_code_robot_id_fkey,
    DROP CONSTRAINT IF EXISTS robotics_robot_components_parent_same_robot_fk,
    DROP CONSTRAINT IF EXISTS robot_components_project_id_region_code_parent_component_id_robot_id_fkey;
ALTER TABLE calibrations.calibration_sets
    DROP CONSTRAINT calibration_sets_project_id_region_code_component_id_robot_fkey;
ALTER TABLE robotics.component_frames
    DROP CONSTRAINT IF EXISTS component_frames_project_id_region_code_component_id_fkey;
ALTER TABLE robotics.component_channels
    DROP CONSTRAINT IF EXISTS component_channels_project_id_region_code_component_id_fkey;
ALTER TABLE robotics.robot_maintenance_records
    DROP CONSTRAINT IF EXISTS robot_maintenance_records_project_id_region_code_robot_id_fkey,
    DROP CONSTRAINT IF EXISTS robot_maintenance_records_project_id_region_code_component_fkey,
    DROP CONSTRAINT IF EXISTS robot_maintenance_records_project_id_region_code_component_id_fkey;

ALTER TABLE robotics.robot_instances
    DROP CONSTRAINT robot_instances_pkey,
    DROP CONSTRAINT robot_instances_project_id_region_code_serial_no_key,
    ADD CONSTRAINT robot_instances_pkey
        PRIMARY KEY (organization_id, project_id, region_code, robot_id),
    ADD CONSTRAINT robot_instances_organization_project_fk
        FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id),
    ADD CONSTRAINT robot_instances_organization_scope_serial_key
        UNIQUE (organization_id, project_id, region_code, serial_no);

ALTER TABLE robotics.robot_components
    DROP CONSTRAINT robot_components_pkey,
    DROP CONSTRAINT robot_components_project_id_region_code_component_id_robot__key,
    ADD CONSTRAINT robot_components_pkey
        PRIMARY KEY (organization_id, project_id, region_code, component_id),
    ADD CONSTRAINT robot_components_organization_parent_identity_key
        UNIQUE (organization_id, project_id, region_code, component_id, robot_id),
    ADD CONSTRAINT robot_components_robot_organization_fk
        FOREIGN KEY (organization_id, project_id, region_code, robot_id)
        REFERENCES robotics.robot_instances (organization_id, project_id, region_code, robot_id),
    ADD CONSTRAINT robotics_robot_components_parent_same_robot_fk
        FOREIGN KEY (organization_id, project_id, region_code, parent_component_id, robot_id)
        REFERENCES robotics.robot_components (
            organization_id, project_id, region_code, component_id, robot_id
        );

ALTER TABLE calibrations.calibration_sets
    ADD CONSTRAINT calibration_sets_robot_component_organization_fk
        FOREIGN KEY (
            organization_id, project_id, region_code, component_id, robot_instance_id
        ) REFERENCES robotics.robot_components (
            organization_id, project_id, region_code, component_id, robot_id
        );

ALTER TABLE robotics.component_frames
    DROP CONSTRAINT component_frames_pkey,
    ADD CONSTRAINT component_frames_pkey
        PRIMARY KEY (organization_id, project_id, region_code, frame_id),
    ADD CONSTRAINT component_frames_component_organization_fk
        FOREIGN KEY (organization_id, project_id, region_code, component_id)
        REFERENCES robotics.robot_components (organization_id, project_id, region_code, component_id);

ALTER TABLE robotics.component_channels
    DROP CONSTRAINT component_channels_pkey,
    ADD CONSTRAINT component_channels_pkey
        PRIMARY KEY (organization_id, project_id, region_code, channel_id),
    ADD CONSTRAINT component_channels_component_organization_fk
        FOREIGN KEY (organization_id, project_id, region_code, component_id)
        REFERENCES robotics.robot_components (organization_id, project_id, region_code, component_id);

ALTER TABLE robotics.robot_maintenance_records
    DROP CONSTRAINT robot_maintenance_records_pkey,
    ADD CONSTRAINT robot_maintenance_records_pkey
        PRIMARY KEY (organization_id, project_id, region_code, record_id),
    ADD CONSTRAINT robot_maintenance_records_robot_organization_fk
        FOREIGN KEY (organization_id, project_id, region_code, robot_id)
        REFERENCES robotics.robot_instances (organization_id, project_id, region_code, robot_id),
    ADD CONSTRAINT robot_maintenance_records_component_organization_fk
        FOREIGN KEY (organization_id, project_id, region_code, component_id)
        REFERENCES robotics.robot_components (organization_id, project_id, region_code, component_id);

ALTER TABLE robotics.robot_command_receipts
    DROP CONSTRAINT robot_command_receipts_pkey,
    ADD CONSTRAINT robot_command_receipts_pkey
        PRIMARY KEY (
            organization_id, project_id, region_code, resource_type, resource_id,
            operation, idempotency_key
        ),
    ADD CONSTRAINT robot_command_receipts_organization_project_fk
        FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id);

CREATE INDEX robotics_robot_organization_scope_name_idx
ON robotics.robot_instances (
    organization_id, project_id, region_code, lower(display_name), robot_id
);
CREATE INDEX robotics_component_organization_robot_idx
ON robotics.robot_components (
    organization_id, project_id, region_code, robot_id, sort_order, component_id
);
CREATE INDEX robotics_frame_organization_component_idx
ON robotics.component_frames (
    organization_id, project_id, region_code, component_id, valid_from DESC, frame_id
);
CREATE INDEX robotics_channel_organization_component_idx
ON robotics.component_channels (
    organization_id, project_id, region_code, component_id, canonical_path, channel_id
);
CREATE INDEX robotics_maintenance_organization_robot_occurred_idx
ON robotics.robot_maintenance_records (
    organization_id, project_id, region_code, robot_id, occurred_at DESC, record_id
);

SELECT core.apply_project_rls('robotics.robot_instances'::regclass);
SELECT core.apply_project_rls('robotics.robot_components'::regclass);
SELECT core.apply_project_rls('robotics.component_frames'::regclass);
SELECT core.apply_project_rls('robotics.component_channels'::regclass);
SELECT core.apply_project_rls('robotics.robot_maintenance_records'::regclass);
SELECT core.apply_project_rls('robotics.robot_command_receipts'::regclass);
