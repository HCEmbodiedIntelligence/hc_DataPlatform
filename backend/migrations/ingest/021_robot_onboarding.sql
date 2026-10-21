-- Additive only: existing IDs, digests, states, Raw and project links are retained.
ALTER TABLE ingest.robot_ingest_credentials ADD COLUMN issuance_key text;
CREATE UNIQUE INDEX robot_credential_issuance_key_uq
ON ingest.robot_ingest_credentials (organization_id, ingest_identity_id, issuance_key)
WHERE issuance_key IS NOT NULL;
-- Persist even a no-op issuance decision: replaying it after a later revocation
-- must not turn that old request into a fresh credential issue. Never store tokens.
CREATE TABLE ingest.robot_credential_requests (
    organization_id text NOT NULL,
    ingest_identity_id text NOT NULL,
    request_key text NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, ingest_identity_id, request_key),
    FOREIGN KEY (organization_id, ingest_identity_id)
        REFERENCES ingest.robot_ingest_identities (organization_id, ingest_identity_id)
);
SELECT core.apply_organization_rls('ingest.robot_credential_requests'::regclass);
CREATE INDEX raw_sources_commit_volume_idx
ON ingest.raw_sources (organization_id, project_id, region_code, committed_at)
INCLUDE (total_bytes, robot_id) WHERE raw_status='COMMITTED';

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
    SELECT * INTO identity
      FROM ingest.robot_ingest_identities value
     WHERE value.organization_id = credential.organization_id
       AND value.ingest_identity_id = credential.ingest_identity_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::text, NULL::bigint, NULL::jsonb,
                            'ROBOT_IDENTITY_DISABLED'::text;
        RETURN;
    END IF;
    -- Re-read after locking the identity: issuance/revocation cannot race the
    -- credential state used for this authentication. Only matching digests can
    -- attribute a failure to a robot; guessed tokens cannot poison health.
    SELECT * INTO credential FROM ingest.robot_ingest_credentials value
     WHERE value.credential_id = selected_credential_id;
    IF credential.state = 'REVOKED' OR
       (credential.expires_at IS NOT NULL AND credential.expires_at <= observed_at) THEN
        UPDATE ingest.robot_ingest_identities value
           SET identity_document = value.identity_document || jsonb_build_object(
             'last_auth_failure_at', observed_at, 'last_auth_failure_code',
             CASE WHEN credential.state='REVOKED' THEN 'ROBOT_CREDENTIAL_REVOKED'
                  ELSE 'ROBOT_CREDENTIAL_EXPIRED' END)
         WHERE value.organization_id=identity.organization_id
           AND value.ingest_identity_id=identity.ingest_identity_id
           AND value.deleted_at IS NULL AND value.state='ENABLED';
        RETURN QUERY SELECT NULL::text, NULL::bigint, NULL::jsonb,
            CASE WHEN credential.state='REVOKED' THEN 'ROBOT_CREDENTIAL_REVOKED'
                 ELSE 'ROBOT_CREDENTIAL_EXPIRED' END;
        RETURN;
    END IF;
    IF identity.deleted_at IS NOT NULL OR identity.state <> 'ENABLED' THEN
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

-- A historical vault decrypt check was not a network probe. Keep the check
-- evidence and credential intact, but withdraw the unsupported ONLINE assertion.
UPDATE ingest.data_sources
SET connectivity_state='UNKNOWN',
    source_document=jsonb_set(source_document, '{connectivity,state}', '"UNKNOWN"'::jsonb)
WHERE connectivity_state='ONLINE'
  AND source_document->'connectivity'->>'last_check_state'='SUCCEEDED';
