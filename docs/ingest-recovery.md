# Raw ingest scheduling and recovery

Raw uploads remain in the durable outbox until their Dataset is available. The
dispatcher reserves a PENDING job before starting Temporal, so a process crash
between the reservation and the start RPC can reconnect to the same workflow ID.
The guard also waits for preceding Lance commits to have product projections;
it permits the owner of an incomplete commit to recover that commit.

## Version consistency

Aligned videos reserve a Dataset version before encoding. `commit_fragment` now
accepts `expected_version` and validates it while holding the Dataset writer lock,
after reconciling storage receipts and before staging/appending data. Competing
writers cannot both pass a check outside the lock and silently receive different
versions. Raw ingest and continuous Episode commits both pass this reservation.

Idempotent recovery checks the original receipt's version, even if later versions
exist. Raw processing reuses the first version containing its rollout, preserving
the original media identity after a crash between storage commit and publication.
A reservation mismatch fails without writing another immutable storage receipt.

## Bounded automatic recovery

New raw-ingest workflows retain their Dataset reservation through cleanup. A
terminal status is persisted only after cleanup finishes. Selected transient
failures (media generation cancellation, version conflicts, activity/connection
timeouts and catalog reconciliation/indexing failures) automatically continue as
a new execution with the same validated input and workflow ID. There are at most
three processing attempts, separated by durable 30- and 60-second timers.

During these timers the job remains PENDING at `retry_wait`, keeping other writers
out of the Dataset. The package API reports `RETRY_PENDING` and the UI displays
“等待重试”. Exhausted or non-transient failures remain `TECHNICAL_FAILED` with the
original error code. Quality risk/rejection is never retried into a pass.

Cancellation is identified from exception types and causes, not the word “cancel”
in an error message. An explicit workflow cancellation remains cancelled and is
not automatically restarted. Its package is displayed as “已取消”. A cancelled
synchronous activity keeps heartbeating until its worker thread actually exits;
it cannot acknowledge cancellation after ten seconds while a commit is still running.

The `ingest-durable-recovery-v1` Temporal patch preserves replay of existing
histories. Executions already closed under the previous implementation require
an explicit recovery operation. Such recovery must use a fresh execution and the
Dataset guard: resetting to a first workflow task can replay a cancellation that
arrived before that task completed. Existing raw objects and published versions
are retained throughout recovery.

## Validation

Regression coverage includes competing real Lance writers, historical-version
idempotency, incomplete-publication dispatch blocking, bounded Temporal retries,
cleanup-before-retry ordering, real cancellation, cancellation drain heartbeats,
and distinct cancelled/retrying package states. Successful, cancelled and failed
pre-change production histories were also replayed against the updated workflow.
