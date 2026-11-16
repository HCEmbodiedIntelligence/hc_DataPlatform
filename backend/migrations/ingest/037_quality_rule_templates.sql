CREATE TABLE ingest.quality_rule_templates (
    organization_id text NOT NULL,
    name text NOT NULL,
    document jsonb NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, name)
);
SELECT core.apply_organization_rls('ingest.quality_rule_templates'::regclass);
