# Access and audit implementation assumptions

Status: conditional. Nothing in this file promotes a role, capability, audit
event, retention policy, Legal Hold workflow, or integrity mechanism.

## Defaults used by this implementation

- The runtime capability/event snapshot is the vendored copy of
  `backend/99-integration/capability-event-registry.json`, parsed at startup. It
  produces 76 canonical capabilities, 18 reserved-denylist entries, three fixed
  role ceilings, and 142 canonical audit events. No capability list or count is
  duplicated in Python.
- The source `open-questions.md` still describes an older 61/20/124 snapshot.
  The current integration registry and signed 76/18/142 artifacts take precedence
  for validation; the stale table needs an owning-contract correction.
- Successful access and invitation preflights have no canonical audit event in
  the 142-event registry. They remain preflight protocol facts and do not invent
  a producer name. Rejections may use `access.change.rejected`.
- Audit ingestion is an internal trusted-service method, not a browser endpoint.
  Browser routes cannot author actor, final scope, outcome, risk or safe-change
  fields.
- Integrity is a pluggable `IntegrityStrategy`. The conditional default is
  `sha256-chain-v1-conditional`: SHA-256 over canonical JSON containing the
  previous digest and immutable record. Keyed signatures/checkpoint custody,
  checkpoint cadence and failure runbook remain Security/P19 decisions.
- Retention and Legal Hold are separate append-oriented facts. They affect query
  visibility and purge eligibility; the PostgreSQL trigger rejects every UPDATE
  or DELETE against `audit_events`. Automatic purge remains disabled.
- `STANDARD` readers do not receive change summaries, producer identity, exact
  network/device context or record digests. Readers additionally holding
  `audit.export` receive approved safe change/producer fields. Omitted fields are
  absent rather than returned as null.

## Canonical events with no declared producer

At implementation start the dispatch note described roughly 85 missing producer
declarations. Parallel domain work updated the authoritative integration registry;
the current snapshot now reports these 34 names with an empty
`declared_by_domains` array. This does not block registry validation or the
trusted append path, and this domain does not fabricate producer ownership:

1. `access.invitation.accepted`
2. `audit.event.correction_recorded`
3. `audit.retention.purge_completed`
4. `calibration.availability.changed`
5. `calibration.report_download.completed`
6. `calibration.report_download.requested`
7. `calibration.source_download.completed`
8. `calibration.source_download.requested`
9. `calibration.validation.cancelled`
10. `calibration.warning.confirmed`
11. `cleaning.draft.archived`
12. `cleaning.draft.successor_created`
13. `manual_issue.assigned`
14. `manual_issue.dismissed`
15. `manual_issue.export.created`
16. `manual_issue.reopened`
17. `robot.disabled`
18. `robot_component.disabled`
19. `robot_model.asset_manifest_download.completed`
20. `robot_model.asset_manifest_download.requested`
21. `robot_model.validation_report_download.completed`
22. `robot_model.validation_report_download.requested`
23. `storage.inventory_refresh.completed`
24. `storage.inventory_refresh.requested`
25. `storage.lifecycle_execution.completed`
26. `storage.lifecycle_policy.created`
27. `storage.lifecycle_policy.enabled`
28. `storage.lifecycle_policy.paused`
29. `storage.lifecycle_policy.updated`
30. `storage.lifecycle_simulation.created`
31. `storage.multipart_abort.requested`
32. `storage.object.viewed`
33. `storage.object_delete.requested`
34. `storage.restore.requested`
