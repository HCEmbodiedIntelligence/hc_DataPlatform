# Cleaning feature ownership

`ManualIssue` and `CleaningDraft` belong to the manual-cleaning feature. P09 is
the sole ManualIssue mutation owner; P10 is a read projection; P11 edits only the
server-selected Draft ID.

P07-owned `ReviewFinding` is an immutable review-decision fact. It is strictly
separate from ManualIssue: no shared ID type or sequence, status enum, DTO/wire
schema, query-key domain, capability, mutation, audit event, or owner. P11 may
read an authorized Finding projection for display and time positioning only.

Two independent handoffs must remain visible in code and tests:

- P09 ManualIssue -> server-created/already-linked CleaningDraft.
- P07 ReviewFinding -> atomically-created successor CleaningDraft.

Never merge these into a generic issue-to-draft helper. Never import or invoke a
Review mutation from this feature.

