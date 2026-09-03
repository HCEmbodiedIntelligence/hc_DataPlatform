-- A schedule may point only to an execution from its own project.  The due
-- dispatcher writes this lineage after the idempotent dry-run is durable.

ALTER TABLE storage.lifecycle_schedules
    ADD CONSTRAINT lifecycle_schedules_last_execution_fkey
    FOREIGN KEY (project_id, last_execution_id)
    REFERENCES storage.lifecycle_executions (project_id, execution_id)
    ON DELETE RESTRICT;
