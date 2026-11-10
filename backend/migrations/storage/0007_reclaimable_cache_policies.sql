ALTER TABLE storage.lifecycle_policies DROP CONSTRAINT lifecycle_policies_action_check;
ALTER TABLE storage.lifecycle_policies ADD CONSTRAINT lifecycle_policies_action_check
    CHECK (action IN ('RETAIN', 'REVIEW_EXPIRATION', 'ARCHIVE', 'TRANSITION_TO_COLD',
                      'CLEAN_REBUILDABLE_CACHE', 'CLEAN_PROCESSING_CACHE', 'CLEAN_RECORDING_PREVIEWS', 'CLEAN_EPISODE_PREVIEWS', 'CLEAN_EXPORT_ARCHIVES', 'CLEAN_PROCESSING_STAGING', 'CLEAN_MEDIA_STAGING', 'CLEAN_OSS_TEST_COPIES'));
ALTER TABLE storage.lifecycle_policies ADD CONSTRAINT lifecycle_automatic_cache_target_check
    CHECK (action NOT IN ('CLEAN_PROCESSING_CACHE', 'CLEAN_RECORDING_PREVIEWS', 'CLEAN_EPISODE_PREVIEWS', 'CLEAN_EXPORT_ARCHIVES', 'CLEAN_PROCESSING_STAGING', 'CLEAN_MEDIA_STAGING', 'CLEAN_OSS_TEST_COPIES') OR
           (object_role = 'REBUILDABLE_DERIVATIVE' AND business_category = 'PENDING_ANNOTATION'));
CREATE TABLE storage.cache_test_copies (
    organization_id text NOT NULL, project_id text NOT NULL, region_code text NOT NULL,
    object_key text NOT NULL CHECK (starts_with(object_key, 'oss-smoke/')),
    original_key text NOT NULL CHECK (starts_with(original_key, 'raw/')),
    size_bytes bigint NOT NULL CHECK (size_bytes > 0),
    sha256 text NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(), deleted_at timestamptz,
    PRIMARY KEY (organization_id, project_id, region_code, object_key)
);
SELECT core.apply_project_rls('storage.cache_test_copies'::regclass);
