-- Remove the historical OPEN-10 fail-closed constraints after the
-- approval-bound execution contract was introduced in storage/0003.
-- PostgreSQL derives these names from their column expressions, so dropping
-- only the generic lifecycle_executions_check* names leaves them active.

ALTER TABLE storage.lifecycle_executions
    DROP CONSTRAINT IF EXISTS lifecycle_executions_production_check,
    DROP CONSTRAINT IF EXISTS lifecycle_executions_production_execution_approved_check;

ALTER TABLE storage.lifecycle_executions
    ADD CONSTRAINT lifecycle_executions_requester_approver_check
    CHECK (
        approved_by IS NULL
        OR requested_by <> approved_by
    );
