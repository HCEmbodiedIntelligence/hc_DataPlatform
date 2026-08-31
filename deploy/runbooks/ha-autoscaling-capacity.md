# HA autoscaling and production capacity gate

Use this runbook to configure and validate API/main-Worker HorizontalPodAutoscalers and to bind a
release to separately produced production-like capacity evidence. This drill does not prove
PostgreSQL, object-store, or Temporal provider failover; use the HA4-04/DR7-04 runbook for those
dependencies.

## Preconditions and stop conditions

- Use an isolated namespace and task-owned database/object prefix. Record the exact release,
  image digests, migration count, Temporal task queue, and database side-effect counts.
- Require a working Kubernetes Metrics API. Record its version and compatibility with the target
  Kubernetes version; do not install a test-only insecure kubelet mode in production.
- Pin API and Worker images by immutable digest. Require HA mode, PDBs, CPU requests, and
  `autoscaling/v2` HPAs with `minReplicas >= 2` and `maxReplicas > minReplicas`.
- Keep main-Worker per-Pod concurrency bounded. The supported baseline is four concurrent
  workflow tasks and sixteen cached workflows; raise it only after measured CPU/memory and
  workflow-task timeout evidence for the exact Pod request/limit profile.
- Any pre-existing failed probe, Pod restart, queue backlog, expired maintenance lease, migration
  drift, mutable image tag, missing capacity artifact, or placeholder digest is a stop condition.

## Capacity evidence contract

Production install and upgrade must consume an external `hc-capacity-evidence/v1` JSON artifact.
The artifact must be no larger than 1,000,000 bytes and must prove all of the following:

- at least 1,800 seconds in a `PRODUCTION` or `PRODUCTION_LIKE` environment with mock mode off;
- 5,000,000,000,000 bytes/day plus 30% headroom, or at least 75,231,481.481 bytes/second;
- complete API, PostgreSQL, object-storage, Temporal, quality-control, Lance, aligned-media, and Export
  stage evidence, including stage rates/latencies and bounded saturation;
- object-storage interruption and Worker-restart recovery, zero duplicate side effects, and exact
  cleanup with no database or object residue.

Sparse files, logical/deduplicated byte counts, local partial tests, extrapolated throughput, and
signed self-assertions are not acceptable evidence.

Store the immutable artifact in the approved evidence system, calculate SHA-256 from the exact
downloaded bytes, and create a release-specific ConfigMap in the target namespace. Do not edit the
ConfigMap in place:

```bash
sha256sum /approved/evidence/capacity-evidence.json
kubectl --namespace <exact-namespace> create configmap <exact-release-evidence-name> \
  --from-file=capacity-evidence.json=/approved/evidence/capacity-evidence.json
```

Set all three production values before rendering:

```yaml
backend:
  capacityGate:
    enabled: true
    evidenceConfigMap:
      name: <exact-release-evidence-name>
      key: capacity-evidence.json
      sha256: sha256:<exact-64-lowercase-hex-digest>
```

Run `helm lint` and `helm template` with the exact release values. An empty name, malformed digest,
zero placeholder digest, absent ConfigMap, digest mismatch, invalid JSON, or rejected capacity
contract must block the release. Never bypass the hook with `--no-hooks`.

The pre-install/pre-upgrade Job must show the same digest in its annotation and arguments, use the
release API image, disable service-account token automount, mount evidence read-only, and finish
with one `hc-capacity-gate-result/v1` `PASS` result. On failure, preserve Job logs and `helm history`,
confirm that live Deployment image digests did not change, then restore the last deployed Helm
revision. A failed gate is evidence, not a reason to delete or rewrite the source artifact.

## Autoscaling drill

1. Start from API and main Worker at their exact minimum replicas. Require every current Pod Ready
   with zero restarts, PDB `allowedDisruptions >= 1`, empty Temporal backlog, and an idle metrics
   baseline.
2. Acquire a real PostgreSQL-authoritative maintenance lease from one baseline API Pod. Renew more
   frequently than the ten-second normal interval and record owner UUID, fencing token, state
   version, database-clock deadline, and environment fence throughout the drill.
3. Probe API readiness through the Kubernetes Service, never a Pod IP. Continue the probe across
   the complete scale-out and scale-in window.
4. Submit real unique-ID workflows to the production task queue. Use an operation with explicit
   expected effects; a no-side-effect safety drill may use requests that must terminate `BLOCKED`
   before activities with an empty processed-ID set.
5. Apply an approved bounded API load and workflow load. Record HPA current/desired replicas, CPU,
   Deployment Ready replicas, Pod restarts/memory, Temporal backlog age/count/dispatch rate, and
   workflow terminal counts at a fixed interval.
6. Stop load and observe both HPAs return to their minimum. Continue lease renewal until scale-in
   completes, then move the test operation to an approved terminal state without entering or
   leaving a production read-only fence.
7. Re-read migration, business-side-effect, Outbox, workflow, queue, Pod restart, PDB, and lease
   evidence. Search all participating Worker logs for workflow-task loss, eviction timeout,
   traceback, and fatal errors.

## Pass criteria

- API and Worker both reach the intended desired/Ready peak and return to their exact minimum;
- Service probes have zero failures and meet the approved latency SLO across both transitions;
- the maintenance owner and fencing token never change, every renewal occurs before expiry, lease
  deadlines advance monotonically, and the environment fence remains correct;
- every accepted workflow has one unique terminal result, backlog returns to zero, and no workflow
  is failed, timed out, terminated, or canceled;
- protected side-effect counts have the exact expected delta, with no duplicates;
- all final Pods are Ready with zero restarts and no workflow-task-loss or eviction-timeout logs.

A local multi-node drill may close the autoscaling mechanics and fail-closed hook tests. It cannot
close the production capacity gate: HA4-02 remains open until the separately produced full-chain
5 TB/day plus 30% artifact passes the digest-bound release hook in the target production-like
environment.
