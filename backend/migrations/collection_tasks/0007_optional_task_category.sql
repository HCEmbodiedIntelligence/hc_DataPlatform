-- Classification is optional metadata. Preserve existing categories and the
-- non-null string wire format; an empty string represents an unclassified task.
ALTER TABLE collection_tasks.collection_tasks
    DROP CONSTRAINT collection_tasks_task_type_check,
    ALTER COLUMN task_type SET DEFAULT '';
