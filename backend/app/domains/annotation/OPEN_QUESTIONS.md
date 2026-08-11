# Data-annotation implementation assumptions

- Domain number 09 is used throughout code and documentation. The reconciliation
  report confirms that the former integration directory number 08 referred to an
  integration pass, not this bounded context.
- The JSON schema and domain README define 15 operations while the current OpenAPI
  contains 12. The implementation includes the 12 OpenAPI operations plus the
  explicitly required resolver, token materialization, and submission-review
  operations. They are isolated so the OpenAPI source can absorb them without route
  changes.
- `annotation_task.read`, `annotation_task.claim`, `annotation_task.create`,
  `annotation_task.assign`, `annotation_task.rebase`, `annotation_draft.edit`,
  `annotation.submit`, `annotation.review`, and `annotation_set.read` are declared
  on their respective endpoints. Role labels are never consulted. Owner approval
  for the three new-key role grants remains outside this service.
- Resolver materialization uses the persisted Coverage Key plus a partial unique
  index for active tasks. It locks/reuses an existing task before insertion and
  handles a concurrent unique-conflict by re-reading that same task, so two callers
  cannot observe two active tasks.
- Revision replacement is consumed as an outbox event through a local
  `AnnotationSourcePort`: affected tasks become `source_status=STALE`, their active
  drafts become `STALE_READ_ONLY`, and ETags rotate. Rebase always creates a new
  empty revision-0 Draft and never copies entries.
