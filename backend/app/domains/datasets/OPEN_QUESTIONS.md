# Dataset/version/review implementation assumptions

- Review Return uses a transaction-participating `ReviewSuccessorDraftPort`. The
  port receives the caller's `AsyncSession`, allocates the successor ID in the
  cleaning domain, and flushes without committing. Therefore Decision, non-empty
  Findings, `RETURNED` Version, successor Draft, lineage, audit and Outbox are
  either committed by the outer transaction together or all rolled back. A
  transactional outbox/eventual successor is intentionally not used because it
  would expose `RETURNED` without a Draft.
- The shared platform port catalog does not yet expose this transaction port. A
  local Protocol is used pending the dependency request in `docs/dep-requests/T9.md`.
- Deletion remains diagnostic-only. The two preflight operations require an
  explicit `intent=DELETE`, reason and expected ETag and always return
  `executable=false` plus `blocked_reasons`; no DELETE route or deletion job is
  implemented until the reserved capability is approved.
- Physical table names follow the contract's named relational facts. Domain 02 did
  not freeze a PostgreSQL schema namespace, so the migration uses unqualified
  snake_case table names.
