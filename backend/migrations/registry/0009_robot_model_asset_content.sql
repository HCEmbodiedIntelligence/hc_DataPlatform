-- Robot model packages are small, organization-owned master data.  Persist the
-- authoritative bytes with their registry rows so PostgreSQL backup/restore is
-- sufficient to migrate a deployment.  The server filesystem remains upload
-- staging only and is not part of the durable model contract.

ALTER TABLE registry.robot_model_assets
    ADD COLUMN IF NOT EXISTS content bytea;

ALTER TABLE registry.robot_model_assets
    DROP CONSTRAINT IF EXISTS registry_robot_model_assets_content_size_check;

ALTER TABLE registry.robot_model_assets
    ADD CONSTRAINT registry_robot_model_assets_content_size_check
    CHECK (content IS NULL OR octet_length(content) = size_bytes);

COMMENT ON COLUMN registry.robot_model_assets.content IS
    'Authoritative robot-model file bytes; NULL only for legacy filesystem assets awaiting migration.';
