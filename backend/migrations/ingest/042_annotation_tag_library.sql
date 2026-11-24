CREATE TABLE ingest.annotation_tag_library (
    organization_id text NOT NULL,
    name text NOT NULL CHECK (name = btrim(name) AND length(name) BETWEEN 1 AND 100),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, name)
);
SELECT core.apply_organization_rls('ingest.annotation_tag_library'::regclass);
