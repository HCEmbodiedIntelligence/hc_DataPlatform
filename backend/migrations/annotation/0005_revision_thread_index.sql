-- BE-24: bounded project/region revision-thread index for the P08 information architecture.
-- The current revision is already a task pointer, so no duplicate mutable projection is
-- introduced.  The index supports the exact keyset order used by the scoped API.

CREATE INDEX IF NOT EXISTS annotation_tasks_revision_thread_page_idx
ON annotation.annotation_tasks (project_id, region_code, updated_at DESC, task_id DESC)
WHERE region_code IS NOT NULL;
