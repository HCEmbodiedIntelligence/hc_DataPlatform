# Whole-platform restore drill evidence and stop rules

Use this worksheet with either `restore-new-bare-metal-server.md` or
`restore-kubernetes.md`. It is part of the RST3-05 operator packet. It does not authorize a
production cutover, write enable, or `RESTORE_VERIFIED` catalog fact.

## Roles and handoff

Assign three different identities before touching the target:

| Role | May do | Must not do |
| --- | --- | --- |
| Restore executor | inspect the target, run `restore plan`, create the restore Job, and retain evidence | hold the approval signing key; approve their own mutation; reopen writes |
| Restore approver | review the exact plan and sign `hc-platform-restore-approval/v1` with the approved Ed25519 KMS/HSM key | operate the Job; set `allow_write_enable` or `allow_restore_verified` true |
| Restore verifier | run reconciliation, review every check, and sign the drill record in the external evidence system | reuse executor or approver credentials; edit checkpoint/report bytes |

Record the executor, approver, verifier, incident/change ticket, UTC start, and UTC end. Stop if
the three duties are not separated. Break-glass access still obeys target identity, owner, lease,
fencing token, and state predicates.

## Immutable identity record

Complete every row before mutation. Store this worksheet in the approved append-only evidence
system; do not put credentials, bearer tokens, private keys, DSNs, bucket physical names, or
plaintext Secret values in it.

| Field | Exact value/evidence reference |
| --- | --- |
| drill ID and change/incident ticket | |
| source platform/environment | |
| target instance/environment | |
| backup ID | |
| backup status and catalog evidence | `INTEGRITY_VERIFIED`; never infer `RESTORE_VERIFIED` |
| manifest SHA-256 / signature SHA-256 / signing-key fingerprint | |
| release ID / release-manifest digest / Git commit | |
| frontend, API, Worker, and backup-maintenance image digests | |
| Chart digest and migration-manifest SHA-256 | |
| PostgreSQL major and empty target database evidence | |
| source and target object-store references and empty/versioned target-prefix evidence | |
| repository Object Lock/KMS/cross-site evidence | |
| exact Secret/KMS versions and provider readiness evidence | references only |
| Temporal cluster/namespace/version/protected-coordinate evidence | |
| target PostgreSQL/object/PVC available bytes and 30% headroom result | |
| restore request/plan/checkpoint SHA-256 | |
| approval ID/reference/SHA-256/signature SHA-256/key fingerprint/expiry | |
| execution owner ID and fencing token | |
| reconciliation verifier identity/report SHA-256 | |

## Global stop conditions

Stop before mutation when any of these is true:

- the backup is not independently authenticated and `INTEGRITY_VERIFIED`;
- source and target identity, exact release, PostgreSQL major, signing pin, KMS version, Temporal
  identity, target database, or object prefix differ from the signed request;
- the target database has user objects, the target prefix is non-empty, object versioning is off,
  or any of PostgreSQL/object/PVC capacity lacks 30% headroom;
- the target can route public traffic, API writes are enabled, any Worker replica is non-zero, or
  the database fence is not read-only;
- an input directory/file is not owned by the executor and mode `0700`/`0600`, an input is a
  symlink, or a Secret appears in argv/log/evidence;
- approval signer and backup signer are the same duty, approval is expired, or either
  `allow_write_enable` or `allow_restore_verified` is not literal `false`;
- an earlier checkpoint/report differs from the immutable plan, approval, owner, fencing token, or
  predecessor SHA-256.

After mutation begins, any failure keeps the target isolated and read-only. Do not clean up the
target, delete a PVC, reuse the prefix/database, or retry with changed inputs until the incident
commander and verifier have captured the failure evidence. Resume only from the exact retained
checkpoint with identical plan, approval, owner, and fencing token.

## Required phase evidence

| Phase | Required evidence | Expected safe state |
| --- | --- | --- |
| preflight | canonical plan output and 12 named PASS checks | `PREFLIGHT_PASSED`; target unchanged |
| approval | approval/signature hashes and independent KMS audit reference | mutation only; no write/verified authority |
| payload | Job log + checkpoint hash | `PAYLOAD_VERIFIED`, all services read-only |
| objects | Job log + checkpoint hash | `OBJECTS_RESTORED`, no target write service |
| PostgreSQL | Job log + checkpoint hash | `POSTGRESQL_RESTORED`, DB fence read-only |
| Temporal | Job log + checkpoint hash | `TEMPORAL_READY`, schedules/workers remain paused |
| services | Job log + final checkpoint hash | `READ_ONLY_READY`, API may be 1, all Workers 0 |
| reconciliation | report ID/SHA-256 and nine ordered FULL PASS checks | all authorization flags remain false |
| replay | second execution/reconciliation output with identical hashes | no new object version/content or DB drift |

Run these checks against captured JSON output; a missing field is a failure:

```bash
jq -e '.status == "READ_ONLY_READY"
  and .writes_enabled == false
  and .reconciliation_completed == false
  and .restore_verified == false
  and (.completed_steps == ["payload_verified", "objects_restored",
    "postgresql_restored", "temporal_ready", "services_read_only"])' execute-output.json

jq -e '.status == "RECONCILIATION_PASSED"
  and .writes_enabled == false
  and .workers_enabled == false
  and .restore_verified == false
  and .next_action == "restore_smoke_and_write_enable_approval_required"' \
  reconcile-output.json
```

The verifier must separately read `restore-reconciliation-report.json` from the evidence export,
verify its SHA-256 equals the Job output, and confirm all nine checks are present in this order:
`restore_checkpoint`, `database_content`, `object_inventory`,
`database_object_references`, `audit_integrity`, `outbox`, `workflow_temporal`, `permissions`, and
`read_only_runtime`.

## Drill classification

- `RST3-05 operator drill complete`: an operator who did not implement the feature followed the
  packet, reached `READ_ONLY_READY`, obtained nine-check reconciliation PASS, proved exact replay,
  retained evidence, and left the target isolated/read-only.
- `DR7-01 complete`: additionally requires an independently approved write-enable controller,
  real login/read/write/upload/publish/audit/Temporal smoke, a catalog `RESTORE_VERIFIED` fact, and
  safe teardown/retention evidence. Those paths are not implemented in this RST3-05 packet.

Never relabel the first result as the second. The current `hc-platform restore` and Helm Job contain
no write-enable or `RESTORE_VERIFIED` path by design.
