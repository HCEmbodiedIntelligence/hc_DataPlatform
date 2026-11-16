-- A submission may contain multiple uploads, and resubmitting immutable bytes
-- may legitimately reference the same upload from a different submission.
CREATE TABLE ingest.upload_batches (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    batch_key text NOT NULL,
    batch_number bigint NOT NULL CHECK (batch_number > 0),
    channel text NOT NULL CHECK (channel IN ('web', 'oss', 'historical')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (organization_id, project_id, region_code, batch_key),
    UNIQUE (organization_id, project_id, region_code, batch_number)
);

CREATE TABLE ingest.upload_batch_members (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    batch_key text NOT NULL,
    upload_id text NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, batch_key, upload_id),
    FOREIGN KEY (organization_id, project_id, region_code, batch_key)
        REFERENCES ingest.upload_batches
);
CREATE INDEX upload_batch_member_lookup ON ingest.upload_batch_members
    (organization_id, project_id, region_code, upload_id);

-- Pre-batch clients retained individual upload identities. Preserve these as
-- identifiable historical sources without guessing multi-upload submissions.
-- Historical uploads span all scopes. The migration runner wraps this file in one transaction.
SET LOCAL app.platform_admin = 'true';
WITH sources AS (
    SELECT organization_id,project_id,region_code,upload_id,created_at FROM ingest.raw_sources
    UNION ALL
    SELECT organization_id,project_id,region_code,session_id::text,created_at FROM ingest.upload_sessions
    UNION ALL
    SELECT u.organization_id,u.project_id,u.region_code,u.upload_id::text,u.created_at
    FROM ingest.recording_uploads u
    WHERE NOT EXISTS (SELECT FROM ingest.mcap_processing_files m WHERE
        (m.organization_id,m.project_id,m.region_code,m.recording_id)=
        (u.organization_id,u.project_id,u.region_code,u.recording_id))
), distinct_sources AS (
    SELECT organization_id,project_id,region_code,upload_id,min(created_at) AS created_at
    FROM sources GROUP BY organization_id,project_id,region_code,upload_id
)
INSERT INTO ingest.upload_batches
    (organization_id,project_id,region_code,batch_key,batch_number,channel,created_at)
SELECT organization_id,project_id,region_code,'historical:'||upload_id,
       row_number() OVER (PARTITION BY organization_id,project_id,region_code ORDER BY created_at,upload_id),
       'historical',created_at FROM distinct_sources;
INSERT INTO ingest.upload_batch_members
SELECT organization_id,project_id,region_code,batch_key,substr(batch_key,12)
FROM ingest.upload_batches WHERE channel='historical';

SELECT core.apply_project_rls('ingest.upload_batches'::regclass);
SELECT core.apply_project_rls('ingest.upload_batch_members'::regclass);
