-- P20 target integrity: exploratory tasks may omit target_json, but a configured
-- target must have at least one positive supported dimension even for direct SQL.

ALTER TABLE collection_tasks.collection_tasks
    ADD CONSTRAINT collection_tasks_target_positive_dimensions_check
    CHECK (
        target_json IS NULL
        OR (
            jsonb_typeof(target_json) = 'object'
            AND (
                (
                    target_json ? 'package_count'
                    AND jsonb_typeof(target_json -> 'package_count') = 'number'
                    AND (target_json ->> 'package_count')::numeric > 0
                )
                OR (
                    target_json ? 'duration_seconds'
                    AND jsonb_typeof(target_json -> 'duration_seconds') = 'number'
                    AND (target_json ->> 'duration_seconds')::numeric > 0
                )
            )
        )
    );
