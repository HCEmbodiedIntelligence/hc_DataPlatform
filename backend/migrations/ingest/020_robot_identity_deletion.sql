-- Retain upload/credential foreign keys while permanently removing an identity
-- from management and allowing a fresh identity for the same robot.
ALTER TABLE ingest.robot_ingest_identities
    ADD COLUMN deleted_at timestamptz,
    ADD CONSTRAINT robot_ingest_deleted_identity_disabled
        CHECK (deleted_at IS NULL OR state = 'DISABLED');

ALTER TABLE ingest.robot_ingest_identities
    DROP CONSTRAINT robot_ingest_identities_organization_id_robot_id_key;

CREATE UNIQUE INDEX robot_ingest_live_robot_identity_uq
    ON ingest.robot_ingest_identities (organization_id, robot_id)
    WHERE deleted_at IS NULL;

-- Serialize authentication, credential issuance, and deletion on the identity.
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
       AND value.ingest_identity_id = credential.ingest_identity_id
     FOR UPDATE;
    IF NOT FOUND OR identity.deleted_at IS NOT NULL OR identity.state <> 'ENABLED' THEN
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
