-- Keep credential IDs/digests and upload foreign keys intact.
ALTER TABLE ingest.robot_ingest_credentials
    ADD COLUMN secret_ciphertext text,
    ADD COLUMN deleted_at timestamptz,
    ADD CONSTRAINT robot_credential_deleted_is_revoked
        CHECK (deleted_at IS NULL OR (state='REVOKED' AND secret_ciphertext IS NULL));
COMMENT ON COLUMN ingest.robot_ingest_credentials.secret_ciphertext IS
    'Authenticated encrypted token, bound to organization/identity/credential; never list plaintext';
COMMENT ON COLUMN ingest.robot_ingest_credentials.deleted_at IS
    'Removed from credential management; minimal metadata retained for immutable upload foreign keys';
