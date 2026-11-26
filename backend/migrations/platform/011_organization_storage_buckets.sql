-- A bucket assigned to one organization cannot be claimed by another in the same environment.
CREATE UNIQUE INDEX organization_storage_bucket_owner
ON platform.platform_object_store_configurations
    (split_part(environment_id, '/organization/', 1), bucket)
WHERE position('/organization/' in environment_id) > 0;
