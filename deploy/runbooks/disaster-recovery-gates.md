# DR7 production exercise gates

This runbook is the execution index and evidence contract for DR7-01 through DR7-06. Local unit
tests, schema validation, Kind, same-host containers, sparse files, or a signed self-description do
not prove an external exercise. A scenario remains blocked until it has real authority evidence,
three distinct duties, exact artifact hashes, verified cleanup, no exclusions, and a passing signed
bundle for the release manifest under test.

## Common evidence workflow

For every scenario, create one `hc-platform-dr-exercise/v1` record containing the exact
`release_manifest_digest`, opaque environment/actor references, timezone-aware start/completion,
`result: PASS`, distinct executor/approver/verifier references, at least one immutable artifact
digest, `cleanup_verified: true`, and `exclusions: []`. Never put hostnames, DSNs, repository paths,
tokens, passwords, Secret values, kubeconfigs, or private keys in the record.

The independent verifier assembles exactly DR7-01 through DR7-06 into
`hc-platform-dr-evidence-bundle/v1`, sets a validity no longer than 31 days, signs its canonical JSON
with the organizational Ed25519 evidence key, and runs `hc-dr-evidence-gate`. The output digest is
inserted as `supply_chain.dr_evidence_bundle_sha256` before the release feed itself is signed.
Tampering, an untrusted signer, a different release, expiry, duplicated/missing scenario, shared
duties, exclusions, or an incomplete cleanup is a release stop.

## DR7-01 — isolated clean-server restore

Use `restore-new-bare-metal-server.md`, `restore-kubernetes.md`, and
`restore-operator-evidence.md`. Restore the same signed release into a new isolated server/cluster;
verify database aggregates/constraints, every required object count/bytes/hash, audit chain,
permissions, Outbox, and Temporal reconciliation. Then use the separately approved write-enable
controller for real login/read/write/upload/publish/audit/workflow smoke and append the signed
catalog `RESTORE_VERIFIED` fact. Read-only reconciliation alone is not a PASS.

Required artifacts include plan/approval/execute/reconcile reports, smoke report, catalog fact,
component identities, duration/RTO, measured RPO, and exact cleanup/isolation proof.

## DR7-02 — cross-server planned migration

Use `planned-migration-cutover.md`. Run initial object/database replication, repeat bounded deltas,
enter signed fenced maintenance, capture the final WAL/object boundary, apply the final delta,
reconcile, and switch only the stable traffic endpoint. Prove RPO 0 at the declared authority
boundary, measured write-stop duration within target, no old-owner write after fencing, and a safe
unchanged source during the rollback window. The exercise uses the actual referenced-object
inventory and its selected `reuse_external`, `copy_referenced`, or `portable` semantics; a real
100 MB inventory is sufficient for the foundational functional gate. The independent 5 TB/day
production-equivalent throughput benchmark remains a separate capacity/release gate and a reduced
functional dataset must never be presented as that benchmark.

## DR7-03 — API, Worker, and Kubernetes node loss

Use `ha-node-drain.md`. On the candidate release, fail each application role/node while continuous
API/frontend traffic and idempotent side-effect probes run. Prove no data loss or duplicate
non-idempotent effect, replacement and capacity recovery within five minutes, PDB/spread behavior,
Worker graceful shutdown, stale heartbeat expiry, and immutable component identity. Current-version
historical evidence cannot be copied to a new release manifest.

## DR7-04 — PostgreSQL, object-store, and Temporal provider loss

Use `ha-shared-provider-failover.md` and execute its three provider failures sequentially through
stable application endpoints without application credential rotation. Prove fail closed while the
authority is unavailable; PostgreSQL writer fencing/LSN continuity; object versions, multipart
authority, and hashes; Temporal visibility/history/poller reconciliation; alert delivery; and
automatic application recovery. Same-host provider processes do not establish a production
failure-domain SLA unless that topology is the declared production-equivalent target.

## DR7-05 — bad release, canary, and migration failure

Use `rolling-upgrade.md`. Inject an untrusted/downgraded feed, incompatible edge, 5xx and latency
regressions, interrupted/concurrent expand, unrouted history, and unapproved contract. Prove the
controller stops, emits exact source image rollback references, retains the forward schema, never
runs a blind down migration, and appends every release transition/audit event.

## DR7-06 — missing/wrong Secret and tampered backup

Use the negative cases in `restore-kubernetes.md`, `restore-environment-reference.md`, and the backup
verification procedure. Remove or substitute one required KMS key, signing key, PostgreSQL/object/
Temporal credential, age identity, and Kubernetes token in separate attempts. Tamper manifest
bytes, signature, database artifact, inventory, one portable shard, and one object version. Each
attempt must fail preflight before writes or service startup, must not silently generate a key or
fall back to ambient/cloud credentials, and must emit only redacted logs/audit details.

## Release decision

The release is still blocked if any scenario is absent, expired, tied to a different release
manifest, contains exclusions, lacks production-equivalent scale/failure domains, or cannot be
independently reproduced from its hashed artifacts. Passing the machine verifier proves signature,
identity, completeness, duties, time window, and envelope integrity; the verifier does not turn a
synthetic/local run into production evidence. Keep those limitations in the signed exercise facts
and do not mark the fact `PASS` until they are removed by a real rerun. In particular, the verifier
does not turn a synthetic/local run into production evidence.
