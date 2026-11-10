ALTER TABLE storage.lifecycle_policies
    DROP CONSTRAINT IF EXISTS lifecycle_policies_action_check;
ALTER TABLE storage.lifecycle_policies
    ADD CONSTRAINT lifecycle_policies_action_check
    CHECK (action IN ('RETAIN', 'REVIEW_EXPIRATION', 'ARCHIVE', 'TRANSITION_TO_COLD',
                      'CLEAN_REBUILDABLE_CACHE', 'CLEAN_PROCESSING_CACHE'));
ALTER TABLE storage.lifecycle_policies
    ADD CONSTRAINT lifecycle_processing_cache_role_check
    CHECK (action <> 'CLEAN_PROCESSING_CACHE' OR object_role = 'REBUILDABLE_DERIVATIVE');
