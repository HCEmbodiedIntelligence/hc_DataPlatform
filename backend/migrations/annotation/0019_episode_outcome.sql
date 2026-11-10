ALTER TABLE annotation.annotation_revisions
    ADD COLUMN episode_outcome text NOT NULL DEFAULT 'UNMARKED'
    CHECK (episode_outcome IN ('UNMARKED', 'SUCCESS', 'FAILURE'));
