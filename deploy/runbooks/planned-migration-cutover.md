# Planned cross-server migration and cutover

This runbook implements RST3-04. It is a controlled migration, not a disaster-recovery shortcut.
The source and target start on the exact same release. RPO is zero, the source write pause may not
exceed 1,800 seconds, and the source remains read-only for the approved rollback window.

The `hc-platform migration` CLI is an evidence gate. Database Operations establish PostgreSQL
physical streaming/WAL continuity, Object Storage Operations establish versioned replication, and
Temporal Operations establish the supported namespace migration or shared-provider boundary. An
independent migration verifier reads those providers and signs each canonical observation with an
Ed25519 key distinct from the cutover approver. The Job never receives either private key.

## Hard prerequisites

- Source and target have distinct environment, instance, and PostgreSQL database identities but the
  same platform and release-identity SHA-256. Object location identity is mode-specific as described
  below.
- Target PostgreSQL is a physical standby of the source: same major and system identifier, with a
  continuous WAL path. Target remains read-only and all target Workers are 0.
- The signed plan chooses exactly one object mode:
  - `reuse_external` (default): source and target retain the exact same external endpoint, bucket,
    and prefix. No object bytes are copied. Verify every referenced key/version/size/hash and source
    and target read permission. Target capacity covers only database, temporary files, and logs.
  - `copy_referenced`: source and target object locations differ. Copy only the exact versions named
    by the database/reference inventory; do not enumerate unrelated bucket contents.
  - `portable`: explicitly requested offline transfer. Create encrypted, bounded object parts for the
    exact referenced inventory and verify their inventory hash before target import.
- `actual_referenced_bytes` is the sum of the exact referenced inventory. For `copy_referenced` and
  `portable`, `required_capacity_bytes = ceil(actual_referenced_bytes * (1 + headroom_ratio))`.
  `reuse_external` uses the sum of database, temporary-file, and log requirements instead. The
  default `headroom_ratio` is 0.30; there is no fixed minimum data size or 6.5 TB disk requirement.
  Sparse or synthetic bytes remain invalid evidence for bytes claimed as real object content.
- DNS TTL is lowered to the plan limit (maximum 300 seconds) before the window. Certificates and
  target routing are already valid. The source rollback retention is at least one hour.
- A cutover approver signs `hc-platform-migration-cutover-approval/v1`, binding the canonical plan
  SHA, source/target, operation, validity, and the permanent ban on dual writes and direct rollback
  after target writes.
- The independent observer public-key pin and cutover-approval public-key pin are in the migration
  Job ConfigMap. They must differ.

Validate the owner-only plan before any provider change:

```bash
hc-platform migration plan \
  --document /migration-staging/work/migration-plan.json \
  --validated-at 2026-08-29T01:00:00Z
```

The expected result is `MIGRATION_PLAN_VALID`, `rpo_target_bytes=0`,
`rpo_target_seconds=0`, the selected `object_migration_mode`, the inventory-derived
`actual_referenced_bytes`, and its exact `required_capacity_bytes`.

## Observation and checkpoint rules

For each phase, the verifier creates a strict `hc-platform-migration-observation/v1` document from
live provider readings and signs the exact canonical bytes in a detached
`hc-platform-migration-observation-signature/v1` envelope. At minimum, live readings include:

- source/target release identity;
- PostgreSQL major, `pg_control_system().system_identifier`, timeline,
  `pg_current_wal_lsn()` and target `pg_last_wal_replay_lsn()`, plus `pg_is_in_recovery()`;
- exact source/target object endpoint, bucket, prefix, full referenced inventory count/bytes, and
  deterministic key/version/size/content digest; source and target read permission; provider
  pending-replication count when the selected mode copies objects;
- Temporal namespace/cluster/version/schedule/open-workflow inventory digest;
- source/target environment fence, Worker replicas, active writer permits, and unexpired presigned
  upload grants;
- target reconciliation digest, DNS/route evidence, write-pause duration, and source retention as
  required by the current phase.

Advance exactly one phase with a digest-pinned external Job or this equivalent CLI invocation:

```bash
hc-platform migration advance \
  --plan /migration-staging/work/migration-plan.json \
  --approval /migration-staging/work/migration-cutover-approval.json \
  --approval-signature /migration-staging/work/migration-cutover-approval.sig \
  --observation /migration-staging/work/migration-observation.json \
  --observation-signature /migration-staging/work/migration-observation.sig \
  --checkpoint /migration-staging/work/migration-checkpoint.json \
  --execution-owner-id migration-controller-01 \
  --fencing-token 17 \
  --advanced-at 2026-08-29T01:00:00Z
```

The checkpoint is owner-only `0600`, predecessor-CAS bound, and advances one fixed phase. Repeating
identical observation bytes is idempotent. Changed bytes, owner, token, plan, approval, or phase are
rejected.

## Phase procedure

### 1. `precopy_verified` → `PRECOPY_READY`

Keep source traffic and Workers running. Verify the physical standby lag is within the plan,
target remains in recovery and read-only, current object inventories match at the signed coordinate,
pending replication is zero, and the mode-specific dynamic capacity check passes. In
`reuse_external`, the observation must report zero pre-copy bytes. In `portable`, it must bind the
encrypted part set to the referenced inventory hash. A functional 100 MB real-object rehearsal may
advance a rehearsal plan; it is not evidence for the independent 5 TB/day performance benchmark.

### 2. `source_fenced` → `SOURCE_FENCED`

At the recorded pause start, transition the source MIGRATION maintenance operation through
`READ_ONLY → DRAINING → FENCED`. Reject new API writes and upload grants; pause schedules and new
Outbox claims; drain active work; scale source and target Workers to 0. The observation must show
both sides read-only, no active source writer permit, and no unexpired upload grant. If ownership or
the fencing token is uncertain, remain read-only and escalate—never reopen the source.

### 3. `final_sync_verified` → `FINAL_SYNCED`

Wait for target replay LSN to equal the frozen source LSN exactly. Apply and verify every final object
version/delta; pending replication must be zero and source/target count, bytes, and inventory digest
must match. Temporal inventory must match its approved migration boundary. Any non-zero WAL byte,
missing version, pending replica, multipart write, or Temporal divergence is RPO failure and keeps
both sites read-only.

### 4. `target_verified` → `TARGET_VERIFIED`

Run the same full read-only content, DB→object, audit, Outbox, workflow/Temporal, permission/RLS,
and runtime reconciliation used by RST3-03 against the final coordinate. The observation binds the
PASS report digest. Keep target PostgreSQL in recovery/read-only and target Workers at 0.

### 5. `traffic_switched` → `TRAFFIC_SWITCHED`

Promote no writes yet. Change the authoritative route/DNS to the target, wait through the bounded
TTL, and probe from independent resolvers/edges. The signed observation must prove traffic reaches
the target with observed TTL no greater than the plan while both sites are still read-only. Before
target writes, a routing rollback remains possible.

### 6. `target_writes_enabled` → `TARGET_WRITABLE`

Promote target PostgreSQL, install the target `READ_WRITE` fence with a new epoch, then start target
Workers and enable target API writes. Source stays fenced with Workers at 0. Record pause duration
from source fence to target write enable; more than 1,800 seconds fails the acceptance objective even
if the migration is otherwise consistent. The checkpoint now permanently records:

- `source_writes_enabled=false`;
- `target_writes_enabled=true`;
- `direct_rollback_allowed=false`;
- `reverse_sync_required=true`;
- RPO bytes/seconds both zero.

Do not point traffic directly back to the old source after this phase. Any rollback requires a new
reverse-sync or disaster-migration plan that incorporates every target write.

### 7. `rollback_window_retained` → `ROLLBACK_WINDOW`

Keep the old source isolated and read-only for at least the plan duration, preserve WAL/object
versions/keys/release artifacts, and continue target monitoring. The final signed observation must
prove the source retention deadline. Expiry or teardown of the old source is a separate approved
operation after the window.

Inspect state without mutation:

```bash
hc-platform migration status \
  --checkpoint /migration-staging/work/migration-checkpoint.json
```

## Failure actions

- Before source fencing: stop and repair pre-copy; source stays live.
- After source fencing but before target writes: keep both sides read-only. Repair sync/verification,
  or restore routing to the still-frozen source only with explicit recovery approval.
- After target writes: never directly reopen or route to the old source. Start reverse synchronization
  or the disaster-migration procedure.
- Never edit a checkpoint or overwrite an observation. A verifier correction is a new signed
  observation for the still-next phase; conflicting replay bytes are evidence of an incident.
- RST3-04 functional completion requires an end-to-end cutover of the actual referenced inventory.
  Unit-only evidence, sparse files, or extrapolated bytes cannot be recorded as that PASS. The
  independent 5 TB/day production throughput benchmark remains a separate release/capacity gate and
  cannot be satisfied by a 100 MB functional migration.
