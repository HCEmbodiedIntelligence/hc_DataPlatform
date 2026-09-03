# Pipeline alerts

Every investigation starts with the alert's `project_id`, `resource_id`, and `workflow_id`.
Never paste JWTs, presigned URLs, object-store credentials, or raw payloads into tickets or chat.

## Upload backlog

1. Confirm object-storage readiness and multipart completion error rate for the project.
2. Locate the deterministic ingest workflow and check worker polling/heartbeat age.
3. Scale workers only after confirming downstream verification capacity.
4. Do not delete Raw objects or manifests to reduce the gauge.

## Workflow failures

1. Inspect the terminal `error_code` and last completed activity for the workflow ID.
2. Retry only retryable infrastructure failures. Quality `RISK`/`REJECT` is not technical failure.
3. For a killed worker, confirm Temporal heartbeat timeout and continuation on another worker.
4. Compare Rollout, Step, Revision, and Version counts before and after recovery.

## Quality rejects

1. Retrieve the immutable QC report by resource ID and verify its profile/version.
2. Separate a real quality conclusion from decoder/storage failure.
3. Never change thresholds or upgrade a `RISK` to make rollout processing continue.

## Lance commit

1. Determine whether the physical Lance commit succeeded before catalog registration failed.
2. If a pending reconciliation exists, run the catalog reconciler for the dataset workflow.
3. Never append the staged fragment a second time and never delete the shared Lance dataset.

## Preview transcode

1. Verify source step availability, FFmpeg worker resources, and cache attempt state.
2. A failed attempt must not expose an m3u8 or signed URL.
3. Retry with the same cache key; do not reuse an artifact from another annotation revision.

## Training export

1. Confirm the published manifest hash and immutable dataset version.
2. Inspect attempt staging and validate source step ranges before retry.
3. A failed export must not be downloadable. Never mutate or delete the Published manifest.

## Rollback

1. Record current API and worker ReplicaSet revisions and the release manifest.
2. Run `helm rollback` to the last verified application revision.
3. Verify `/health/ready`, worker polling, and a read-only published-version lookup.
4. Database migrations are forward-only; never roll back by deleting Raw/Lance/Published data.
