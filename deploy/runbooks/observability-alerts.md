# Observability alerts and delivery verification

This runbook applies to the `hc-platform-*` Prometheus rules and the GitOps-provisioned
`HC Data Platform Operations` dashboard. Use metrics only for bounded aggregate detection. Use
the fixed runtime-log fields (`request_id`, `operation_id`, `workflow_id`) for investigation; never
add project, user, object, resource, workflow, or request identifiers as metric labels.

For every page, record the alert fingerprint, first firing time, release identity, dashboard time
range, commands executed, mitigation owner, and resolution time. A missing dashboard, missing
runbook link, failing Collector queue, or notification that cannot reach the approved receiver is
an observability incident in its own right.

## API 5xx

Confirm the affected bounded route template and release, then query structured logs by time,
service, release, and `event_code`. Use a request or operation identifier only in Loki/log search.
Stop a rollout if the 5xx ratio is sustained or one release is overrepresented. Do not log request
bodies or copy authentication headers into evidence.

## API 429

Confirm `status_code="429"`, affected route template, and whether the abuse-control policy is
operating as configured. Check upstream retry behavior and `Retry-After`. Never add client IP,
principal, session, or project as a metric label; use the protected audit trail for authorized
subject-level investigation.

## Workflow failures

Classify by bounded `workflow_kind` and `error_code`. Correlate the durable workflow through
structured logs, inspect Temporal history, and distinguish retryable infrastructure failure from
quality rejection. Never restart a non-idempotent activity manually.

## Outbox lag

Check database availability, dispatcher heartbeats, destination health, and oldest pending age.
Scale only after confirming connection-pool headroom. Preserve ordered outbox records and allow
the idempotent dispatcher to converge; do not delete or mark records delivered by hand.

## Temporal lag

Check compatible Worker pollers, task-queue routing, Worker drain state, and Temporal frontend
health. Restore compatible pollers before scaling producers. Do not reset histories to clear lag.

## Backup stale or failed

Stop release or migration progression. Inspect the immutable backup catalog, signed operation,
integrity verification status, object/WAL checkpoint, and alert time. Run the backup command from
the backup runbook; never relabel an incomplete backup as verified.

## Backup deep verification

Quarantine the affected backup generation, retain its deep-verification report and hashes, and
select the newest independently integrity-verified predecessor. Open a data-safety incident before
the generation can return to restore eligibility.

## Instance stale

Use the node directory to identify the missing bounded role. Confirm Kubernetes node readiness,
Pod scheduling, process heartbeat, and dependency readiness. Preserve the stale row and timestamps
as evidence; replace the process through the HA runbook instead of editing heartbeat state.

## Runtime config drift

Pause configuration changes. Compare the authoritative revision with each active process, check
notification delivery, and wait for bounded polling fallback. Roll back only through a new
monotonic signed revision; never mutate an existing revision.

## Node version drift

Stop rollout progression and new maintenance operations. Compare each active node's release digest
against the signed release manifest. Drain or replace the divergent node with the approved image;
do not accept a tag-only identity or patch binaries inside a running container.

## Migration failure

Keep maintenance fencing active and stop cutover. Preserve the signed phase checkpoint,
observation, database/object evidence, and error class. Resume or reverse only through the planned
migration runbook's permitted transition.

## Object replication lag

Block backup eligibility and migration cutover. Check source and replica health, backlog, object
version parity, and approved RPO. Do not delete source objects or promote a replica until inventory
and sample hashes converge.

## Audit integrity

Freeze export and mutation paths, preserve append-only P19 records, and run the independent chain
verifier. Treat any missing predecessor, head mismatch, or event-hash mismatch as a security and
data-integrity incident. Never rebuild the chain in place.

## Log export failure

Check Collector readiness, exporter failure metrics, persistent agent checkpoints, queue depth,
Loki readiness, retention capacity, and NetworkPolicy. Restore the sink before queue capacity is
exhausted. Collector debug/body logging is forbidden because it bypasses the runtime log contract.

## Database pool

Stop unsafe API/Worker scaling, identify pool ownership by bounded service/role, and check long
transactions and provider limits. Reduce load or restore database capacity before increasing pool
sizes. Preserve capacity and query evidence.

## Disk space

Identify the filesystem and owner, then use the approved retention or capacity procedure. Never
delete backup generations, audit facts, PostgreSQL files, Loki indexes, or object-store data ad hoc.
Confirm free-space recovery and alert resolution.

## Delivery canary

This is an approved, time-bounded end-to-end drill only. Notify the on-call receiver owner, expose
`hc_observability_alert_canary 1` from the dedicated drill target, and record all of these before
calling the drill successful:

1. Prometheus shows `HcObservabilityDeliveryCanary` firing with `severity=critical` and
   `synthetic=true`.
2. Alertmanager shows the alert active and its receiver attempt successful.
3. The final webhook/pager receiver records the alert fingerprint, status, severity, and receive
   timestamp.
4. Set the canary to `0`, confirm resolution is delivered, and remove the drill target.

Do not route the canary through a real paging escalation without the receiver owner's approval.

## Node-loss log retention drill

Place the application log producer and Loki on different nodes. Emit a unique safe `event_code`,
query it through Loki, record the stream/timestamp, then stop the producer's Kubernetes node. Query
the same historical record again while that node remains unavailable. Passing requires the second
query to return the original record from the central backend. Restart the node, confirm workload
and agent health, and retain the before/after query JSON plus node-state timeline.
