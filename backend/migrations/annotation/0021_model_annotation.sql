CREATE TABLE annotation.model_connections (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    config jsonb NOT NULL,
    api_key_ciphertext bytea NOT NULL,
    PRIMARY KEY (organization_id, project_id)
);
SELECT core.apply_project_rls('annotation.model_connections'::regclass);

CREATE TABLE annotation.model_annotation_guides (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    name text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    instructions text NOT NULL,
    prompt text NOT NULL,
    PRIMARY KEY (organization_id, project_id, name)
);
SELECT core.apply_project_rls('annotation.model_annotation_guides'::regclass);
