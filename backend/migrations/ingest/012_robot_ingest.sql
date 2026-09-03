-- Project-independent robot identities and one format-neutral resumable Raw upload
-- protocol. Identity lookup is organization-scoped; collection_task_id establishes
-- the authoritative project/dataset/Region before any project-owned row is written.

CREATE TABLE IF NOT EXISTS ingest.robot_ingest_identities (
    organization_id text NOT NULL,
    ingest_identity_id text NOT NULL,
    robot_id text NOT NULL,
    state text NOT NULL,
    credential_revision bigint NOT NULL DEFAULT 0,
    identity_document jsonb NOT NULL,
    last_authenticated_at timestamptz,
    last_seen_at timestamptz,
    last_upload_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, ingest_identity_id),
    UNIQUE (organization_id, robot_id),
    UNIQUE (organization_id, ingest_identity_id, robot_id),
    FOREIGN KEY (organization_id, robot_id)
        REFERENCES robotics.robot_assets (organization_id, robot_id),
    CHECK (organization_id <> ''),
    CHECK (ingest_identity_id ~ '^[A-Za-z0-9._-]{1,128}$'),
    CHECK (robot_id ~ '^[A-Za-z0-9._-]{1,128}$'),
    CHECK (state IN ('ENABLED', 'DISABLED')),
    CHECK (credential_revision >= 0),
    CHECK (jsonb_typeof(identity_document) = 'object'),
    CHECK (NOT (identity_document ? 'token')),
    CHECK (NOT (identity_document ? 'secret')),
    CHECK (updated_at >= created_at)
);

CREATE TABLE IF NOT EXISTS ingest.robot_ingest_credentials (
    credential_id text PRIMARY KEY,
    organization_id text NOT NULL,
    ingest_identity_id text NOT NULL,
    credential_version bigint NOT NULL,
    token_prefix text NOT NULL,
    token_digest char(64) NOT NULL,
    state text NOT NULL,
    issued_at timestamptz NOT NULL,
    expires_at timestamptz,
    revoked_at timestamptz,
    last_authenticated_at timestamptz,
    UNIQUE (organization_id, ingest_identity_id, credential_version),
    UNIQUE (
        organization_id, ingest_identity_id, credential_id, credential_version
    ),
    FOREIGN KEY (organization_id, ingest_identity_id)
        REFERENCES ingest.robot_ingest_identities (organization_id, ingest_identity_id),
    CHECK (credential_id ~ '^[A-Za-z0-9._-]{1,128}$'),
    CHECK (credential_version > 0),
    CHECK (token_prefix ~ '^hcri_[0-9a-f]{32}\.[A-Za-z0-9_-]{8}$'),
    CHECK (token_digest ~ '^[0-9a-f]{64}$'),
    CHECK (state IN ('ACTIVE', 'REVOKED')),
    CHECK (expires_at IS NULL OR expires_at > issued_at),
    CHECK ((state = 'REVOKED') = (revoked_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS robot_ingest_credentials_identity_state_idx
ON ingest.robot_ingest_credentials (
    organization_id, ingest_identity_id, state, credential_version DESC
);

CREATE TABLE IF NOT EXISTS ingest.robot_ingest_uploads (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    upload_id text PRIMARY KEY,
    ingest_identity_id text NOT NULL,
    authenticated_robot_id text NOT NULL,
    request_robot_id text NOT NULL,
    credential_id text NOT NULL,
    credential_version bigint NOT NULL,
    collection_task_id text NOT NULL,
    collection_job_id text NOT NULL,
    dataset_id text NOT NULL,
    client_upload_id uuid NOT NULL,
    source_format text NOT NULL,
    capture_mode text NOT NULL,
    state text NOT NULL,
    manifest_fingerprint char(64) NOT NULL,
    raw_source_id text,
    upload_document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    committed_at timestamptz,
    UNIQUE (ingest_identity_id, collection_task_id, client_upload_id),
    UNIQUE (organization_id, project_id, region_code, upload_id),
    FOREIGN KEY (organization_id, ingest_identity_id, authenticated_robot_id)
        REFERENCES ingest.robot_ingest_identities (
            organization_id, ingest_identity_id, robot_id
        ),
    FOREIGN KEY (
        organization_id, ingest_identity_id, credential_id, credential_version
    ) REFERENCES ingest.robot_ingest_credentials (
        organization_id, ingest_identity_id, credential_id, credential_version
    ),
    FOREIGN KEY (organization_id, project_id, collection_task_id, dataset_id)
        REFERENCES collection_tasks.collection_tasks (
            organization_id, project_id, collection_task_id, dataset_id
        ),
    FOREIGN KEY (project_id, collection_job_id)
        REFERENCES ingest.collection_jobs (project_id, collection_job_id),
    CHECK (upload_id ~ '^[A-Za-z0-9._-]{1,128}$'),
    CHECK (authenticated_robot_id = request_robot_id),
    CHECK (source_format ~ '^[A-Za-z0-9._+-]{1,128}$'),
    CHECK (capture_mode IN ('PRESEGMENTED', 'CONTINUOUS')),
    CHECK (state IN (
        'UPLOADING', 'PAUSED', 'READY_TO_COMMIT', 'COMMITTED', 'FAILED', 'CANCELLED'
    )),
    CHECK (manifest_fingerprint ~ '^[0-9a-f]{64}$'),
    CHECK (jsonb_typeof(upload_document) = 'object'),
    CHECK (NOT (upload_document ? 'token')),
    CHECK (NOT (upload_document ? 'secret')),
    CHECK ((state = 'COMMITTED') = (committed_at IS NOT NULL)),
    CHECK (updated_at >= created_at)
);

CREATE OR REPLACE FUNCTION ingest.enforce_robot_ingest_job_authority()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, ingest
AS $function$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM ingest.collection_jobs job
         WHERE job.organization_id = NEW.organization_id
           AND job.project_id = NEW.project_id
           AND job.region_code = NEW.region_code
           AND job.collection_job_id = NEW.collection_job_id
           AND job.task_id = NEW.collection_task_id
           AND job.robot_id = NEW.authenticated_robot_id
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23503',
            MESSAGE = 'ROBOT_INGEST_COLLECTION_JOB_AUTHORITY_MISMATCH',
            CONSTRAINT = 'robot_ingest_uploads_job_authority_fk';
    END IF;
    RETURN NEW;
END
$function$;

CREATE TRIGGER robot_ingest_uploads_job_authority_guard
BEFORE INSERT OR UPDATE OF
    organization_id, project_id, region_code, collection_job_id,
    collection_task_id, authenticated_robot_id
ON ingest.robot_ingest_uploads
FOR EACH ROW EXECUTE FUNCTION ingest.enforce_robot_ingest_job_authority();

CREATE OR REPLACE FUNCTION ingest.prevent_robot_ingest_job_authority_change()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, ingest
AS $function$
BEGIN
    IF ROW(
        OLD.organization_id, OLD.project_id, OLD.region_code,
        OLD.collection_job_id, OLD.task_id, OLD.robot_id
    ) IS DISTINCT FROM ROW(
        NEW.organization_id, NEW.project_id, NEW.region_code,
        NEW.collection_job_id, NEW.task_id, NEW.robot_id
    ) AND EXISTS (
        SELECT 1
          FROM ingest.robot_ingest_uploads upload
         WHERE upload.organization_id = OLD.organization_id
           AND upload.project_id = OLD.project_id
           AND upload.region_code = OLD.region_code
           AND upload.collection_job_id = OLD.collection_job_id
           AND upload.collection_task_id = OLD.task_id
           AND upload.authenticated_robot_id = OLD.robot_id
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23503',
            MESSAGE = 'ROBOT_INGEST_COLLECTION_JOB_AUTHORITY_IMMUTABLE',
            CONSTRAINT = 'robot_ingest_uploads_job_authority_fk';
    END IF;
    RETURN NEW;
END
$function$;

CREATE TRIGGER collection_jobs_robot_ingest_authority_guard
BEFORE UPDATE OF
    organization_id, project_id, region_code, collection_job_id, task_id, robot_id
ON ingest.collection_jobs
FOR EACH ROW EXECUTE FUNCTION ingest.prevent_robot_ingest_job_authority_change();

CREATE INDEX IF NOT EXISTS robot_ingest_uploads_robot_history_idx
ON ingest.robot_ingest_uploads (
    organization_id, authenticated_robot_id, created_at DESC, upload_id
);
CREATE INDEX IF NOT EXISTS robot_ingest_uploads_task_history_idx
ON ingest.robot_ingest_uploads (
    organization_id, project_id, collection_task_id, created_at DESC, upload_id
);

CREATE TABLE IF NOT EXISTS ingest.robot_ingest_assets (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    upload_id text NOT NULL,
    asset_id text NOT NULL,
    object_key text NOT NULL UNIQUE,
    multipart_upload_id text NOT NULL,
    state text NOT NULL,
    asset_document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (upload_id, asset_id),
    FOREIGN KEY (organization_id, project_id, region_code, upload_id)
        REFERENCES ingest.robot_ingest_uploads (
            organization_id, project_id, region_code, upload_id
        ),
    CHECK (asset_id ~ '^[A-Za-z0-9._-]{1,128}$'),
    CHECK (object_key <> ''),
    CHECK (multipart_upload_id <> ''),
    CHECK (state IN ('UPLOADING', 'COMPLETED', 'FAILED', 'CANCELLED')),
    CHECK (jsonb_typeof(asset_document) = 'object'),
    CHECK (updated_at >= created_at)
);

CREATE TABLE IF NOT EXISTS ingest.robot_ingest_camera_metrics (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    upload_id text NOT NULL,
    camera_id text NOT NULL,
    declared_metrics jsonb NOT NULL,
    verification_metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (upload_id, camera_id),
    FOREIGN KEY (organization_id, project_id, region_code, upload_id)
        REFERENCES ingest.robot_ingest_uploads (
            organization_id, project_id, region_code, upload_id
        ),
    CHECK (camera_id ~ '^[A-Za-z0-9._-]{1,128}$'),
    CHECK (jsonb_typeof(declared_metrics) = 'object'),
    CHECK (jsonb_typeof(verification_metrics) = 'object'),
    CHECK (updated_at >= created_at)
);

CREATE TABLE IF NOT EXISTS ingest.robot_ingest_attempts (
    attempt_id text PRIMARY KEY,
    organization_id text,
    project_id text,
    region_code text,
    authenticated_robot_id text,
    request_robot_id text,
    collection_task_id text,
    source_format text,
    outcome text NOT NULL,
    failure_stage text NOT NULL,
    failure_code text,
    upload_id text,
    raw_source_id text,
    attempt_document jsonb NOT NULL,
    occurred_at timestamptz NOT NULL,
    CHECK (attempt_id ~ '^[A-Za-z0-9._-]{1,128}$'),
    CHECK (project_id IS NULL OR project_id ~ '^[A-Za-z0-9._-]{1,128}$'),
    CHECK (region_code IS NULL OR region_code ~ '^[A-Za-z0-9._-]{1,128}$'),
    CHECK (project_id IS NULL OR organization_id IS NOT NULL),
    CHECK ((project_id IS NULL) = (region_code IS NULL)),
    CHECK (outcome IN ('ACCEPTED', 'REJECTED', 'COMMITTED', 'FAILED')),
    CHECK (failure_stage ~ '^[A-Za-z0-9._-]{1,64}$'),
    CHECK (failure_code IS NULL OR failure_code ~ '^[A-Z0-9_]{1,128}$'),
    CHECK (jsonb_typeof(attempt_document) = 'object'),
    CHECK (NOT (attempt_document ? 'token')),
    CHECK (NOT (attempt_document ? 'secret'))
);

CREATE INDEX IF NOT EXISTS robot_ingest_attempts_robot_time_idx
ON ingest.robot_ingest_attempts (
    organization_id, project_id, region_code, authenticated_robot_id,
    occurred_at DESC, attempt_id
);

-- Existing raw_sources becomes the format-neutral immutable registry for this protocol.
ALTER TABLE ingest.raw_sources
    DROP CONSTRAINT IF EXISTS raw_sources_source_format_check;
ALTER TABLE ingest.raw_sources
    ADD CONSTRAINT raw_sources_source_format_check
        CHECK (source_format ~ '^[A-Za-z0-9._+-]{1,128}$'),
    ADD COLUMN IF NOT EXISTS authenticated_robot_id text,
    ADD COLUMN IF NOT EXISTS request_robot_id text,
    ADD COLUMN IF NOT EXISTS ingest_identity_id text,
    ADD COLUMN IF NOT EXISTS credential_id text,
    ADD COLUMN IF NOT EXISTS credential_version bigint,
    ADD COLUMN IF NOT EXISTS capture_mode text,
    ADD COLUMN IF NOT EXISTS client_upload_id uuid,
    ADD COLUMN IF NOT EXISTS upload_batch_count integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS raw_capture_count integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS declared_episode_count integer,
    ADD COLUMN IF NOT EXISTS verified_episode_count integer,
    ADD COLUMN IF NOT EXISTS derived_episode_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS verified_frame_count bigint NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS verified_sample_count bigint NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS qc_pass_episode_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS qc_risk_episode_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS qc_reject_episode_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS quality_status text NOT NULL DEFAULT 'PENDING';

ALTER TABLE ingest.raw_sources
    ADD CONSTRAINT raw_sources_authenticated_robot_format
        CHECK (authenticated_robot_id IS NULL OR authenticated_robot_id ~ '^[A-Za-z0-9._-]{1,128}$'),
    ADD CONSTRAINT raw_sources_request_robot_format
        CHECK (request_robot_id IS NULL OR request_robot_id ~ '^[A-Za-z0-9._-]{1,128}$'),
    ADD CONSTRAINT raw_sources_credential_version_positive
        CHECK (credential_version IS NULL OR credential_version > 0),
    ADD CONSTRAINT raw_sources_capture_mode_valid
        CHECK (capture_mode IS NULL OR capture_mode IN ('PRESEGMENTED', 'CONTINUOUS')),
    ADD CONSTRAINT raw_sources_count_facts_valid CHECK (
        upload_batch_count > 0 AND raw_capture_count > 0
        AND (declared_episode_count IS NULL OR declared_episode_count >= 0)
        AND (verified_episode_count IS NULL OR verified_episode_count >= 0)
        AND derived_episode_count >= 0 AND verified_frame_count >= 0
        AND verified_sample_count >= 0
        AND qc_pass_episode_count >= 0 AND qc_risk_episode_count >= 0
        AND qc_reject_episode_count >= 0
    ),
    ADD CONSTRAINT raw_sources_quality_status_valid
        CHECK (quality_status IN ('PENDING', 'PASS', 'RISK', 'REJECT'));

ALTER TABLE ingest.raw_sources
    ADD CONSTRAINT raw_sources_robot_identity_fk
        FOREIGN KEY (organization_id, ingest_identity_id, authenticated_robot_id)
        REFERENCES ingest.robot_ingest_identities (
            organization_id, ingest_identity_id, robot_id
        ),
    ADD CONSTRAINT raw_sources_robot_credential_fk
        FOREIGN KEY (
            organization_id, ingest_identity_id, credential_id, credential_version
        ) REFERENCES ingest.robot_ingest_credentials (
            organization_id, ingest_identity_id, credential_id, credential_version
        );

CREATE UNIQUE INDEX IF NOT EXISTS raw_sources_robot_upload_uq
ON ingest.raw_sources (organization_id, project_id, region_code, upload_id);

ALTER TABLE ingest.raw_ingest_jobs
    DROP CONSTRAINT IF EXISTS raw_ingest_jobs_adapter_name_check;
ALTER TABLE ingest.raw_ingest_jobs
    ADD CONSTRAINT raw_ingest_jobs_adapter_name_check
        CHECK (adapter_name ~ '^[A-Za-z0-9._+-]{1,128}$');

-- Downstream Adapters write per-Episode processing/QC facts into the existing Raw
-- lineage table. Aggregate robot statistics remain a projection of these facts.
ALTER TABLE ingest.raw_source_episodes
    ADD COLUMN IF NOT EXISTS sample_count bigint,
    ADD COLUMN IF NOT EXISTS quality_status text NOT NULL DEFAULT 'PENDING',
    ADD COLUMN IF NOT EXISTS qc_report_id text;

ALTER TABLE ingest.raw_source_episodes
    ADD CONSTRAINT raw_source_episodes_sample_count_valid
        CHECK (sample_count IS NULL OR sample_count >= 0),
    ADD CONSTRAINT raw_source_episodes_quality_status_valid
        CHECK (quality_status IN ('PENDING', 'PASS', 'RISK', 'REJECT')),
    ADD CONSTRAINT raw_source_episodes_qc_report_id_valid
        CHECK (qc_report_id IS NULL OR qc_report_id ~ '^[A-Za-z0-9._-]{1,128}$');

SELECT core.apply_organization_rls('ingest.robot_ingest_identities'::regclass);
SELECT core.apply_organization_rls('ingest.robot_ingest_credentials'::regclass);
SELECT core.apply_project_rls('ingest.robot_ingest_uploads'::regclass);
SELECT core.apply_project_rls('ingest.robot_ingest_assets'::regclass);
SELECT core.apply_project_rls('ingest.robot_ingest_camera_metrics'::regclass);

ALTER TABLE ingest.robot_ingest_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingest.robot_ingest_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY robot_ingest_attempt_scope ON ingest.robot_ingest_attempts
USING (
    current_setting('app.platform_admin', true) = 'true'
    OR (
        organization_id = NULLIF(current_setting('app.organization_id', true), '')
        AND (
            project_id IS NULL
            OR (
                project_id = NULLIF(current_setting('app.project_id', true), '')
                AND region_code = NULLIF(current_setting('app.region_code', true), '')
            )
        )
    )
)
WITH CHECK (
    current_setting('app.platform_admin', true) = 'true'
    OR (
        organization_id = NULLIF(current_setting('app.organization_id', true), '')
        AND (
            project_id IS NULL
            OR (
                project_id = NULLIF(current_setting('app.project_id', true), '')
                AND region_code = NULLIF(current_setting('app.region_code', true), '')
            )
        )
    )
);

CREATE OR REPLACE FUNCTION ingest.authenticate_robot_ingest_credential(
    selected_credential_id text,
    selected_token_digest text,
    observed_at timestamptz
) RETURNS TABLE (
    credential_id text,
    credential_version bigint,
    identity_document jsonb,
    failure_code text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, ingest
AS $function$
DECLARE
    credential ingest.robot_ingest_credentials%ROWTYPE;
    identity ingest.robot_ingest_identities%ROWTYPE;
BEGIN
    SELECT * INTO credential
      FROM ingest.robot_ingest_credentials value
     WHERE value.credential_id = selected_credential_id
       AND value.token_digest = selected_token_digest;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::text, NULL::bigint, NULL::jsonb,
                            'ROBOT_CREDENTIAL_INVALID'::text;
        RETURN;
    END IF;
    IF credential.state = 'REVOKED' THEN
        RETURN QUERY SELECT NULL::text, NULL::bigint, NULL::jsonb,
                            'ROBOT_CREDENTIAL_REVOKED'::text;
        RETURN;
    END IF;
    IF credential.expires_at IS NOT NULL AND credential.expires_at <= observed_at THEN
        RETURN QUERY SELECT NULL::text, NULL::bigint, NULL::jsonb,
                            'ROBOT_CREDENTIAL_EXPIRED'::text;
        RETURN;
    END IF;
    SELECT * INTO identity
      FROM ingest.robot_ingest_identities value
     WHERE value.organization_id = credential.organization_id
       AND value.ingest_identity_id = credential.ingest_identity_id;
    IF NOT FOUND OR identity.state <> 'ENABLED' THEN
        RETURN QUERY SELECT NULL::text, NULL::bigint, NULL::jsonb,
                            'ROBOT_IDENTITY_DISABLED'::text;
        RETURN;
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM robotics.robot_assets robot
         WHERE robot.organization_id = identity.organization_id
           AND robot.robot_id = identity.robot_id
           AND robot.lifecycle_status = 'ACTIVE'
    ) THEN
        RETURN QUERY SELECT NULL::text, NULL::bigint, NULL::jsonb,
                            'ROBOT_IDENTITY_DISABLED'::text;
        RETURN;
    END IF;
    UPDATE ingest.robot_ingest_credentials value
       SET last_authenticated_at = observed_at
     WHERE value.credential_id = selected_credential_id;
    UPDATE ingest.robot_ingest_identities value
       SET last_authenticated_at = observed_at,
           last_seen_at = observed_at,
           updated_at = observed_at,
           identity_document = jsonb_set(
               jsonb_set(value.identity_document, '{last_authenticated_at}', to_jsonb(observed_at)),
               '{last_seen_at}', to_jsonb(observed_at)
           )
     WHERE value.organization_id = identity.organization_id
       AND value.ingest_identity_id = identity.ingest_identity_id
     RETURNING value.identity_document INTO identity.identity_document;
    RETURN QUERY SELECT credential.credential_id, credential.credential_version,
                        identity.identity_document, NULL::text;
END
$function$;

CREATE OR REPLACE FUNCTION ingest.resolve_robot_ingest_upload(
    selected_organization_id text,
    selected_upload_id text,
    selected_ingest_identity_id text
) RETURNS TABLE (upload_document jsonb)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, ingest
AS $function$
    SELECT value.upload_document
      FROM ingest.robot_ingest_uploads value
     WHERE value.organization_id = selected_organization_id
       AND value.upload_id = selected_upload_id
       AND value.ingest_identity_id = selected_ingest_identity_id
$function$;

CREATE OR REPLACE FUNCTION ingest.record_robot_ingest_attempt(document jsonb)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, ingest
AS $function$
    INSERT INTO ingest.robot_ingest_attempts (
        attempt_id, organization_id, project_id, region_code,
        authenticated_robot_id, request_robot_id,
        collection_task_id, source_format, outcome, failure_stage, failure_code,
        upload_id, raw_source_id, attempt_document, occurred_at
    ) VALUES (
        document->>'attempt_id', NULLIF(document->>'organization_id', ''),
        NULLIF(document->>'project_id', ''), NULLIF(document->>'region_code', ''),
        NULLIF(document->>'authenticated_robot_id', ''), NULLIF(document->>'request_robot_id', ''),
        NULLIF(document->>'collection_task_id', ''), NULLIF(document->>'source_format', ''),
        document->>'outcome', document->>'failure_stage', NULLIF(document->>'failure_code', ''),
        NULLIF(document->>'upload_id', ''), NULLIF(document->>'raw_source_id', ''),
        document, (document->>'occurred_at')::timestamptz
    ) ON CONFLICT (attempt_id) DO NOTHING
$function$;

REVOKE ALL ON FUNCTION ingest.authenticate_robot_ingest_credential(text, text, timestamptz)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION ingest.resolve_robot_ingest_upload(text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION ingest.record_robot_ingest_attempt(jsonb) FROM PUBLIC;
