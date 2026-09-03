# Runtime configuration revision and convergence drill

Use this runbook for HA4-05. It changes only the three keys in the machine-readable release
allowlist and proves notification plus 30-second polling convergence. It is not a Secret rotation,
release, schema migration, or substitute for rolling application workloads.

## Fixed boundary

The only accepted keys are:

- `scheduling.media_maintenance_interval_seconds` — strict integer, 10–86400 seconds;
- `scheduling.storage_inventory_interval_seconds` — strict integer, 10–86400 seconds;
- `ui.maintenance_banner_enabled` — strict boolean.

DSNs, passwords, tokens, credentials, object-store access, TLS/certificates, component images,
migration/schema identity, and unknown keys are release-owned and must be rejected before a
revision row is written. Do not put sensitive values in `reason`, logs, screenshots, audit details,
or evidence files.

## Preconditions

1. Confirm migration status is current and includes `platform/005_runtime_config_revisions.sql`.
2. Confirm every target API/main Worker/media Worker is Ready, uses the expected release, and has
   a recent node-directory heartbeat. Record current `applied_config_revision` values.
3. Confirm no active maintenance operation or release rollout, and assign a release operator with
   the exact `platform.release.operate` capability. Readers use `platform.operations.read` or
   `platform.admin`.
4. Record the current snapshot digest and full allowlisted values from
   `GET /api/v1/platform/runtime-config`; never record application Secrets.

Stop on schema drift, a stale node, a mixed release, an unexpected revision/digest collision,
Secret-like input reaching PostgreSQL/audit, or any node failing to converge within 30 seconds.

## Publish and notification path

1. POST `/api/v1/platform/runtime-config/revisions` with the exact current
   `expected_revision`, schema `hc-runtime-config/v1`, a non-empty allowlisted patch, and a safe
   operational reason. A successful compare-and-swap returns the next monotonic revision.
2. Confirm exactly one immutable revision and one `APPLY` event were appended. The head moves
   forward once and `pg_notify('platform_runtime_config', '<environment>:<revision>')` is emitted
   in the same committed transaction.
3. Poll the protected runtime-config endpoint on every API replica and the instance directory for
   API and Worker heartbeats. Require the same revision and content digest everywhere. Worker
   media-maintenance/inventory loops read their next interval from the applied snapshot rather than caching
   the boot value.
4. Confirm P19 platform audit contains actor, request ID, capability, schema/revision, and changed
   key names only. Values and reason text must not be copied into audit details.

## Lost-notification drill

1. On one isolated follower, disable or replace only its LISTEN connection while leaving ordinary
   PostgreSQL reads available. Do not restart the process and do not change its DSN.
2. Publish a second valid revision and record the database commit timestamp. The connected nodes
   should refresh from the notification. The isolated follower must refresh through its periodic
   authoritative read no later than 30.000 seconds after commit.
3. Restore the LISTEN connection and require all node-directory heartbeats to report the same
   `applied_config_revision`. A notification is only a hint; PostgreSQL head/revision rows remain
   authoritative.

## Rejection and rollback

1. Submit one request containing a Secret/DSN/TLS/image/schema-like key and one unknown key.
   Require stable 422 problem codes, zero new revision/event/head movement, and no submitted value
   in response, log, or P19 audit output.
2. Submit one stale `expected_revision`; require 409 and zero writes.
3. POST `/api/v1/platform/runtime-config/revisions/{target_revision}:rollback` using the current
   expected revision. Rollback must append a new monotonic `ROLLBACK` revision containing the
   target snapshot; it must not update/delete old facts or move the head backward.
4. Repeat normal and lost-notification convergence checks for the rollback revision.

## Pass evidence and cleanup

Pass requires: exact allowlist enforcement; immutable revision/event history; compare-and-swap;
notification convergence; notification-loss convergence within 30 seconds; all API/Worker
heartbeats at one revision; runtime scheduling consuming the new values; safe P19 audit; and a
monotonic rollback. Retain non-secret timings, revision numbers, content digests, row counts,
process identities, and test results.

Rollback the test values to the recorded baseline by creating one final revision. Re-enable any
task-owned notification fault, remove exact temporary databases/processes/evidence, and prove the
shared baseline services are healthy. Never delete revision/event facts from a retained
environment.
