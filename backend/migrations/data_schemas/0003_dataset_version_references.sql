-- P17 immutable provenance: a dataset may consume only one fixed PUBLISHED
-- schema version.  Later schema drafts/successors must never rewrite this fact.
CREATE TABLE IF NOT EXISTS data_schemas.stream_schema_dataset_version_references (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    schema_id text NOT NULL,
    schema_version bigint NOT NULL,
    dataset_id text NOT NULL,
    dataset_version_id text NOT NULL,
    associated_by text NOT NULL,
    associated_at timestamptz NOT NULL,
    PRIMARY KEY (
        organization_id, project_id, region_code,
        schema_id, schema_version, dataset_id, dataset_version_id
    ),
    FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id),
    FOREIGN KEY (organization_id, schema_id, schema_version)
        REFERENCES data_schemas.stream_schema_versions
            (organization_id, schema_id, schema_version),
    FOREIGN KEY (
        organization_id, project_id, region_code, dataset_id, dataset_version_id
    ) REFERENCES dataset_registry.dataset_versions (
        organization_id, project_id, region_code, dataset_id, version_id
    ),
    CHECK (organization_id <> ''),
    CHECK (project_id <> ''),
    CHECK (region_code <> ''),
    CHECK (schema_id <> ''),
    CHECK (schema_version > 0),
    CHECK (dataset_id ~ '^dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (dataset_version_id ~ '^version_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (associated_by <> '')
);

CREATE INDEX IF NOT EXISTS stream_schema_dataset_references_schema_idx
ON data_schemas.stream_schema_dataset_version_references (
    organization_id, project_id, region_code, schema_id, schema_version,
    associated_at DESC, dataset_id DESC, dataset_version_id DESC
);

CREATE INDEX IF NOT EXISTS stream_schema_dataset_references_dataset_idx
ON data_schemas.stream_schema_dataset_version_references (
    organization_id, project_id, region_code, dataset_id, dataset_version_id,
    associated_at DESC
);

ALTER TABLE data_schemas.schema_command_receipts
    DROP CONSTRAINT IF EXISTS schema_command_receipts_operation_check;
ALTER TABLE data_schemas.schema_command_receipts
    ADD CONSTRAINT schema_command_receipts_operation_check
    CHECK (
        operation IN (
            'CREATE', 'IMPORT', 'UPDATE', 'VALIDATE', 'PREFLIGHT', 'PUBLISH',
            'ASSOCIATE_DATASET'
        )
    ) NOT VALID;
ALTER TABLE data_schemas.schema_command_receipts
    VALIDATE CONSTRAINT schema_command_receipts_operation_check;

SELECT core.apply_project_rls(
    'data_schemas.stream_schema_dataset_version_references'::regclass
);
