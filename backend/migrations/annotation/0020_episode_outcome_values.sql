ALTER TABLE annotation.annotation_revisions
    DROP CONSTRAINT annotation_revisions_episode_outcome_check,
    ADD CONSTRAINT annotation_revisions_episode_outcome_check
        CHECK (episode_outcome IN ('success', 'fail', 'UNMARKED', 'SUCCESS', 'FAILURE')),
    ALTER COLUMN episode_outcome SET DEFAULT 'success';
