# Safe rolling release: signed feed, canary, rollback, and contract

This is the operator procedure for REL6-01 through REL6-05 and DR7-05. It is valid only for an
adjacent release edge whose immutable artifacts exist and whose compatibility-matrix status is
`READY_FOR_CANARY`. A design-only edge, an `unreleased` identity, a tag-only image, an expired DR
bundle, or any release blocker is a stop condition.

The browser is an approval surface only. It never receives a kubeconfig, cluster-admin token,
Temporal credential, registry credential, signing key, or GitOps deploy key. Cluster mutations are
performed by the independently authenticated, least-privilege release controller.

## Required identities and retained inputs

Record and SHA-256 bind these before starting:

- source and target release manifest, signed monotonic release feed, signer fingerprint, feed
  sequence, compatibility edge, SBOM, provenance, Chart package, and all four exact image digests;
- signed DR evidence bundle verified with `hc-dr-evidence-gate`; its digest must equal
  `supply_chain.dr_evidence_bundle_sha256` in the signed target manifest;
- a current-release catalog fact with `RESTORE_VERIFIED`, current central-log access, current
  migration ledger, production Temporal server version, capacity evidence, and current node/config
  convergence;
- requester, distinct approver, release-controller identity, observation owner, operation/request
  IDs, and an evidence directory that does not contain credentials.

The source images retained for rollback are `frontend`, `api`, `worker`, and `media_worker` from the
preflight request. Do not reconstruct them from tags or from the current registry head.

## 1. Verify evidence and run server-side preflight

Verify the signed DR bundle without exposing a private key:

```bash
hc-dr-evidence-gate \
  --document "$DR_EVIDENCE_DOCUMENT" \
  --expected-release-manifest-digest "$TARGET_RELEASE_MANIFEST_DIGEST" \
  --trusted-public-key "$DR_EVIDENCE_PUBLIC_KEY"
```

Keep the single-line verifier result and hash it. Compare `bundle_sha256` with the target release
manifest. Then call `POST /api/v1/platform/releases:preflight` as the requester with the exact
`target_release_id`, minimum trusted feed sequence, source image references, and signed feed. The
server independently rejects a forged signer, feed rollback/downgrade, non-ready compatibility
edge, missing release identity, non-converged/unready nodes, config drift, missing verified backup,
missing `RESTORE_VERIFIED` evidence, unavailable central-log search, or source digest mismatch.

Success creates an append-only `PREFLIGHT_PASSED` event in state `AWAITING_APPROVAL`; it does not
change the cluster.

## 2. Four-eyes approval

A person other than the requester reviews the exact manifest/feed/evidence digests and enters a
reason on Platform Operations → Release history. The UI sends only
`POST /api/v1/platform/releases/{release_id}:approve` with the observed state version. Self-approval
and stale state versions fail closed. Retain the `APPROVED` event and opaque actor references.

The release controller now records `APPROVED -> EXPAND` using
`POST /api/v1/platform/releases/{release_id}:transition`. No human browser performs the following
mutations.

## 3. Expand database schema once

The Helm pre-install/pre-upgrade Job runs `hc-data-migrate upgrade-expand` with the backend image
digest from the signed release. It uses a session-scoped PostgreSQL advisory lock, the immutable
migration ledger, and only entries assigned to `expand` in `backend/migrations/phases.json`.

Stop if there is lock timeout, unknown migration, checksum drift, old application failure on the
expanded schema, target application failure on the source/expanded schema, or any attempt to put a
drop/rename/narrowing migration in expand. Contract migrations are not Helm upgrade hooks.

Retain the before/after ledger, lock-owner observation, and both source-on-expanded and
target-on-expanded test reports. Record `EXPAND -> CANARY` only after all pass.

## 4. Route compatible Temporal builds

Create a credential-free plan document with the two fixed task queues, exact source build ID, and
target build ID. The controller, not the browser, runs:

```bash
hc-release-controller temporal-route \
  --document "$TEMPORAL_ROUTING_DOCUMENT" \
  --target "$TEMPORAL_FRONTEND" \
  --namespace "$TEMPORAL_NAMESPACE"
```

The target is installed as a compatible default for both `hc-data-pipeline` and
`hc-media-pipeline`; the source build remains running for the rollback window. Stop on unknown or
unrouted history, nondeterminism, incomplete old-history replay, or a production server outside the
signed version range.

## 5. API and frontend canary

The GitOps controller deploys one API and one frontend canary at the target digests. Keep source
API/frontend and both source Worker builds available. Collect exactly one JSON observation per
component, including timezone-aware start/end, request/5xx counts, p95 latency, readiness, and
observed digest. The default gate requires at least 300 seconds and 1,000 requests per component,
5xx rate at most 1%, p95 at most 500 ms, all desired replicas ready, and the target digest.

```bash
set +e
hc-release-controller canary --document "$CANARY_DOCUMENT" > "$CANARY_DECISION"
CANARY_EXIT=$?
set -e
```

- exit 0 / `PROCEED`: record `CANARY -> ROLLOUT` and continue;
- exit 20 / `HOLD`: do not scale the target or change routing; extend the bounded observation;
- exit 30 / `ROLLBACK`: stop rollout and deploy every reference in `rollback_images` byte-for-byte.

A rollback must keep the forward expand schema and route new work back to the retained compatible source
build, never executes a database down migration, verifies source readiness and workflow/outbox
reconciliation, and records `ROLLED_BACK` with the emitted reason codes. If exact source artifacts
are unavailable, stop and escalate; do not substitute tags.

## 6. Rollout and observe

On `PROCEED`, roll API and frontend, then compatible Workers in bounded batches. At every batch
verify readiness, node/config identity convergence, HTTP/write-chain SLO, Outbox backlog, Temporal
pollers/history, object read/write, audit continuity, and central logs. A regression uses the same
exact-source rollback boundary. Record `ROLLOUT -> COMPLETED` when there is no contract work, or
`ROLLOUT -> CONTRACT_PENDING` after the stable rollback observation window.

## 7. Separate contract gate

Contract is a new approval and a separate non-hook Job. Confirm zero source Pods/Jobs, zero source
Temporal tasks on the contract path, the rollback window has expired, target stability remains
green, and backup plus restore evidence is current. Store the signed approval artifact and its
`sha256:<64 lowercase hex>` digest, then enable only:

```yaml
backend:
  migration:
    contract:
      enabled: true
      approvalDigest: sha256:<approved-artifact-sha256>
```

The Job runs `hc-data-migrate upgrade-contract --approval-digest ...` and only phase-manifest
contract entries. After incompatible contract, rollback means forward fix or verified restore;
blind database downgrade and ordinary Helm rollback are prohibited. Record the final `COMPLETED` or
`FAILED` transition and export the P19 audit chain.

## DR7-05 failure injection and completion

Before a major release, repeat the procedure on an isolated production-equivalent target with:

1. an untrusted/forged feed and a lower feed sequence;
2. target-client/source-server and source-client/target-server incompatibility;
3. injected API 5xx above 1% and frontend p95 above 500 ms;
4. an interrupted expand owner and a concurrent second migration owner;
5. unrouted Temporal history and an unavailable target Worker build;
6. an attempted contract without a valid approval digest.

Each case must stop before unsafe mutation or return the exact source digest rollback payload. The
evidence is admissible only when requester/executor, approver, and verifier are distinct, cleanup is
verified, no exclusions remain, and the DR7-05 record is included in a signed short-lived DR bundle.
