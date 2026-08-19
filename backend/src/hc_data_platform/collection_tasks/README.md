# Collection tasks (P20 backend)

This module owns task definitions and read-only progress. It does not own upload
execution, Manifest parsing, frontend P20, or account/bootstrap behavior.

## Frozen state table

| Current state | Read/list/progress | Update definition | Close | Reopen |
|---|---:|---:|---:|---:|
| `ACTIVE` | yes | yes, with `If-Match` | `CLOSED`, with `If-Match` and `Idempotency-Key` | not defined |
| `CLOSED` | yes | rejected | idempotent no-op | not defined |

OPEN-04 remains explicit: there is no reopen route or transition until the product
decision is confirmed. OPEN-05 remains explicit: package-count and duration targets
are independently optional, neither is required, and no product-level range or
combination validation is imposed. The service does not infer an overall completion
percentage or automatically close a task. OPEN-06 remains explicit:
the quality threshold is nullable and has no platform default or inherited fallback.

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

The response always includes numerator and denominator. The progress route requires an
authorized `X-Region-Code` and aggregates only that region, so RLS cannot turn an
unselected or unauthorized region into a misleading project-wide zero. Target attainment
is not folded into this formula while OPEN-05 is unresolved. Source identities remain
owned by the uploaded Manifest and are not editable task configuration.

`observed_sources` is a read-only union of device, camera, and Topic identities in
persisted Manifest preflight facts for the same received-package cohort. No personnel
identity is returned because the current Manifest contract contains no such fact.

## Concurrency and compatibility

The migration installs an insert trigger on new ingest rollouts. It takes a shared lock
on a matching active task row; close takes the conflicting update lock. Whichever commits
first defines the ordering. A new rollout loses after close, while an already-associated
rollout can be retried idempotently. Unknown historical ingest task IDs remain readable
and are not retroactively rejected.

Legacy ingest collection-job upload states are not P20 task states. Consumers must use
the P20 create/list/detail/update/close/progress routes and stop sending scheduling,
assignment, source-configuration, or upload-lifecycle commands as task fields.

Migrations are forward-only under the repository migration runner. Rollback is therefore
an application rollback followed by a reviewed forward migration; an automatic down
script is intentionally not provided.
