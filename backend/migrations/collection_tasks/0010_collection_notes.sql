ALTER TABLE collection_tasks.collection_tasks
    ADD COLUMN collection_description text NOT NULL DEFAULT '',
    ADD COLUMN upload_day_notes jsonb NOT NULL DEFAULT '{}'::jsonb;
