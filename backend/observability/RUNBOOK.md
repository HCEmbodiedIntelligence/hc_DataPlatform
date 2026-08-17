# Backend operational runbook

## Workflow failures

Locate the `workflow_id`, `project_id`, and `rollout_id` in structured logs. Classify the
failure as retryable infrastructure failure, non-retryable raw validation failure, or data
quality outcome. Never retry a quality `REJECT` as a technical failure.

## Upload backlog

Confirm OSS/MinIO availability, Temporal task queue polling, and raw-verification worker
heartbeats. Scaling verification workers is safe because rollout workflow IDs are deterministic.

## Lance commit

Inspect the per-dataset writer workflow. If Lance committed but PostgreSQL did not, run the
catalog reconciler; do not manually append the staged fragment again.

## Rollback

Roll back the complete platform Helm release to the last verified release manifest. PostgreSQL
migrations are forward-only and must remain compatible with the previous platform release;
published dataset versions and raw object keys are immutable and must never be deleted during an
application rollback.
