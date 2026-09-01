# Collection tasks (P20 backend)

This module owns task definitions, explicit lifecycle transitions, and read-only
progress facts. It does not own upload execution, Manifest parsing, frontend P20,
or account/bootstrap behavior.

## Frozen state table

| Current state | Read/list/progress | Update definition | Close | Cancel | Reopen |
|---|---:|---:|---:|---:|---:|
| `ACTIVE` | yes | yes, with `If-Match` | `CLOSED`, with `If-Match` and `Idempotency-Key` | `CANCELLED`, with `If-Match` and `Idempotency-Key` | not needed |
| `CLOSED` | yes | rejected | idempotent no-op | rejected | `ACTIVE`, with `If-Match` and `Idempotency-Key` |
| `CANCELLED` | yes | rejected | rejected | idempotent no-op | `ACTIVE`, with `If-Match` and `Idempotency-Key` |

`target` is optional for exploratory work. When supplied, it must contain at least
one strictly positive package-count or duration dimension. The quality threshold is
independently optional, has no inherited default, and is evaluated only after every
received package has a QC outcome. Attainment is a progress fact, never an automatic
state transition: an attained task remains `ACTIVE` until an authorized caller
explicitly closes or cancels it.

## Progress formula

Only committed cloud packages (`ingest.rollout_objects`) count as received. A package is
counted once by stable `data_package_id`. The latest `quality_rollout_summaries` row for a
received package contributes exactly one of PASS/RISK/REJECT; received packages without
a summary are pending.

```text
evaluated = pass + risk + reject
pending = received - evaluated
pass_rate = pass / evaluated, or null when evaluated = 0
```

The response always includes numerator and denominator. It also derives duration from
each immutable Manifest `time_range` as `[start_time, end_time)`: duration is `null`
when any received package has no usable time range, rather than a fabricated zero.
For each configured package/duration target it reports `IN_PROGRESS`, `MET`,
`EXCEEDED`, or `UNKNOWN`; quality is `PENDING_QC`, `NOT_MET`, or `MET`. The overall
attainment is `NOT_CONFIGURED`, `IN_PROGRESS`, `ATTAINED`, or `EXCEEDED` only after all
configured dimensions are known and the quality requirement, if any, is met.

The progress route requires an authorized `X-Region-Code` and aggregates only that
region, so RLS cannot turn an unselected or unauthorized region into a misleading
project-wide zero. Source identities remain owned by the uploaded Manifest and are not
editable task configuration.

`observed_sources` is a read-only union of device, camera, and Topic identities in
persisted Manifest preflight facts for the same received-package cohort. No personnel
identity is returned because the current Manifest contract contains no such fact.

## Concurrency and compatibility

The migration installs an insert trigger on new ingest rollouts. It takes a shared lock
on a matching active task row; close/cancel/reopen take the conflicting update lock.
Whichever commits first defines the ordering. A new rollout loses after close or cancel,
while an already-associated rollout can be retried idempotently. Unknown historical
ingest task IDs remain readable and are not retroactively rejected.

Ingest collection-job upload states are not P20 task states. Consumers must use
the P20 create/list/detail/update/close/cancel/reopen/progress routes and stop sending
scheduling, assignment, source-configuration, or upload-lifecycle commands as task
fields.

Migrations are forward-only under the repository migration runner. Rollback is therefore
an application rollback followed by a reviewed forward migration; an automatic down
script is intentionally not provided.
