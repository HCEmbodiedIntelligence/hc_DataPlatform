# Manual-cleaning implementation assumptions

- The only ManualIssue states are `OPEN`, `IN_PROGRESS`, and `RESOLVED`; the
  transition map is centralized at the top of `service.py`. Triage is an action,
  not a state. `RESOLVED` is terminal in V1.
- Issue-to-Draft accepts the path `manual_issue_id`, `Idempotency-Key`, and current
  `If-Match` only. Its body is empty for discovery or contains only the signed
  selection token and an already-linked candidate. Dataset/Version/Revision/Stream
  and range fields are rejected as extra fields and are always derived from the
  locked authoritative Issue.
- Review Return successor creation is not exposed as an HTTP endpoint. It is only
  callable through the transaction-participating port used by domain 02.
- Commit is always conditional and idempotent. Preconditions are evaluated before
  inserting the `QUEUED` Commit/Job, and a blocked response carries
  `blocked_reasons`. The successful request returns the shared Platform AsyncJob
  shape with HTTP 202.
- Split mappings are persisted as the immutable `source_to_output_map` JSON on the
  Preview row. This uses the contract's named `cleaning_previews` relation and
  avoids inventing an unfrozen mapping table identifier.
- Lease duration, idempotency retention, and worker retry counts remain deployment
  policy. No correctness rule depends on a chosen duration.
