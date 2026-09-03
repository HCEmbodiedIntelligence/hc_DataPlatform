# Shared-provider HA and failover drill

Use this runbook for HA4-04. It proves that an unchanged application deployment reconnects through
stable PostgreSQL, object-store, and Temporal endpoints when one provider serving node fails. It
does not prove a correlated region loss, provider SLA, backup restore, or production capacity.

## Preconditions and global stop conditions

- Map Database, Object Storage, Temporal, Security/KMS, and Platform SRE responsibilities to named
  operators. Use an isolated environment and task-owned database, bucket/prefix, namespace, and IDs.
- Record provider topology, quorum, replica/catch-up status, versions, stable endpoint identities,
  application release digest, migration status, and the exact non-secret `HC_SECRET_BUNDLE_REVISION`.
- Every API and Worker must use the same immutable Secret Manager bundle revision and the same
  named Secret references. The bundle is high-entropy and versioned; evidence never includes its
  values or reusable fingerprints.
- Keep at least two application replicas Ready with zero restarts. Run a continuous authenticated
  read plus idempotent-write probe and a real Temporal workflow backlog during each provider event.
- Do not restart application Pods or change DSN/endpoint targets in the application. Do not rotate
  credentials, partially update a Secret, bypass fencing, or fail two providers at once.
- Do not rotate credentials as an attempted failover repair; reuse the exact current bundle.

Stop on replica lag outside the approved RPO, loss of provider quorum, a non-current migration
ledger, a Secret revision mismatch, an active maintenance operation, missing cleanup authority,
duplicate effects, or an unexplained application restart.

## PostgreSQL writer failover

1. Confirm the replica selected for promotion has replayed the last probe commit. Record writer and
   replay LSNs, timeline, replication mode, lag, and `pg_is_in_recovery()` on every member.
2. Start a unique idempotent profile/write command through the stable writer endpoint and retain
   its exact response hash. Keep session bootstrap and write probes running.
3. Terminate the current writer through the provider control plane. Promote only the caught-up
   replica, move the stable writer endpoint, and fence the old writer before any rejoin.
4. Without an application restart or DSN/credential change, require the original session to work,
   replay the original command exactly once, and commit a new command. Verify the committed LSN,
   migration ledger, audit/Outbox counts, and application error/recovery window.

## Object-store node failover

1. Confirm bucket versioning, replication/erasure quorum, exact test-object hash, and one paused
   multipart identity through the stable S3 endpoint.
2. Keep object reads and fresh presigned multipart PUT/HEAD probes running. Terminate the serving
   storage node selected by the endpoint without changing application configuration.
3. Require the existing object version/hash and multipart identity/parts to remain readable, then
   upload and reconcile the next bounded part through a fresh authorization.
4. Confirm quorum and replication health. Abort the multipart and delete only the exact test object,
   version, prefix, bucket identity, and test principal during cleanup.

## Temporal frontend failover

1. Record the external-managed namespace/cluster identity, service version, frontend membership,
   task queue, and provider RPO/RTO evidence. Start unique workflows long enough to overlap failure.
2. Terminate the frontend currently serving the stable endpoint. Do not change `HC_TEMPORAL_TARGET`
   or restart Worker/API Pods; let the SDK reconnect to another provider frontend.
3. Require all in-flight workflows and a new post-failover workflow to reach one terminal result,
   with unique workflow IDs, zero duplicate side effects, and backlog returning to zero.
4. Reconcile Temporal visibility with PostgreSQL job/Outbox facts and confirm provider membership
   is healthy before the failed frontend rejoins.

## Secret consistency and rotation

- A provider failover reuses the current bundle; it never generates replacement credentials.
- Rotate by creating a new immutable Secret Manager bundle and new Kubernetes Secret references,
  computing one non-secret revision digest, and rolling every backend workload together. In-place
  partial key updates and dynamic DSN/credential hot reload are forbidden.
- Reject HA render/startup when the revision is missing, `unversioned`, zero, malformed, or differs
  across evidence. Roll back with the exact previous bundle revision and references.

## Pass and cleanup

Pass only when all three provider events preserve endpoint identity, the original session and data,
all workflow terminal results, exact side-effect counts, and zero application restarts. Record each
failure and recovery window separately; sequential local protocol evidence cannot be presented as a
correlated-site or production-SLA claim.

Cancel/abort task work, remove exact task-owned provider members, databases, buckets, namespaces,
users, proxies, credentials, networks, volumes, and temporary evidence, then prove shared baseline
services and protected migration state are unchanged.

## Accepted local protocol evidence — 2026-08-29

- Two staging-configured APIs and two main Workers used unchanged stable PostgreSQL, S3, and
  Temporal endpoints and the same immutable Secret bundle revision. Their exact container IDs did
  not change and final restart counts were all zero.
- A synchronous PostgreSQL 16.10 standby had replayed the writer commit LSN before the writer was
  terminated. Promotion preserved all 105 migrations, the original opaque session, one exact
  idempotency row/response, and a new revision-3 write; recovery took 28.186 seconds.
- A four-node distributed MinIO erasure set retained quorum after its serving node was terminated.
  The exact version/hash and multipart identity/part 1 survived, a fresh presigned PUT created part
  2, and a new versioned object write succeeded; recovery took 16.481 seconds.
- Two Temporal 1.25.2 all-in-one processes shared the promoted PostgreSQL provider. After the
  serving frontend was terminated, all 300 in-flight timer workflows completed uniquely within
  38.785 seconds at the visibility authority; a fresh client reconciled all 300 exact results, 20
  new workflows completed, and the packaged main Worker processed one guarded application
  workflow. Both application Worker pollers reappeared on the surviving frontend.
- The first observer result long-poll was canceled during frontend loss even though all workflows
  completed; the runbook therefore requires authority reconciliation after reconnect rather than
  treating one observer socket as workflow state.

This closes HA4-04 local multi-process failover semantics. Same-host containers, community MinIO,
and self-managed Temporal do not prove a production provider SLA, correlated host/region loss,
external Secret Manager operation, backup restore, or capacity.
