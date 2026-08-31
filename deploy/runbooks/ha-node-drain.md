# HA application-node drain drill

Use this runbook to validate the application tier on a Kubernetes cluster with at least three
eligible nodes and two labeled zones. It covers Frontend, API, main Worker, and media Worker. It
does not prove PostgreSQL, object-store, or Temporal provider HA; those require the HA4-04/DR7-04
dependency-failover drill.

## Preconditions and stop conditions

- Use an isolated namespace and task-owned database/object prefix. Never use a database with
  unresolved migration drift.
- Pin Frontend, API, and Worker by immutable image digest and record the rendered Helm SHA-256.
- Require two Ready replicas for every component, one PDB disruption allowed for every component,
  and two `DoNotSchedule` topology constraints (`kubernetes.io/hostname` and
  `topology.kubernetes.io/zone`).
- Require at least one schedulable replacement node outside the node being drained.
- Require the Worker readiness sentinel at `/tmp/hc-runtime/worker-ready`. A missing sentinel,
  runtime-importing readiness probe, replica restart, unavailable PDB, or pre-existing SLO failure
  is a stop condition.
- Establish baseline counts for schema migrations, lifecycle execution/items, Outbox rows, and the
  business-side-effect tables exercised by the selected workflow.

## Continuous evidence

Run service probes through the Kubernetes Service proxy or an equivalent load-balanced path; do
not bind the probe to one Pod. Probe at least:

- API `/health/ready`, requiring HTTP 200 and `status=ready`;
- Frontend `/healthz`, requiring HTTP 200;
- a real Temporal workflow on the production task queue with unique workflow IDs.

Use a workflow whose expected side effects are explicit. For a no-side-effect safety drill, a
non-physical lifecycle request must finish `BLOCKED` before activities with zero processed object
IDs. Record accepted/completed/unique counts and verify the relevant database counts did not
change. A retry is acceptable only when Temporal returns one correct terminal result and the
idempotency/side-effect evidence remains exact.

Capture the evicted main Worker log. It must contain, in order:

1. `platform worker graceful drain started`;
2. Temporal worker shutdown with the configured activity grace period;
3. `platform worker graceful drain completed`.

## Drain procedure

1. Record node names and zone labels, Pod placement, replica readiness, restart counts, PDB state,
   image digests, and baseline database counts.
2. Start the HTTP and Temporal probes before cordoning the node.
3. Drain exactly one application node with the organization-approved equivalent of:

   ```bash
   kubectl drain <exact-node> \
     --ignore-daemonsets \
     --delete-emptydir-data \
     --timeout=5m
   ```

4. Require all four selected Pods to be evicted under the PDBs and replacement Pods to become
   Ready on another labeled node/zone. Do not force-delete application Pods or bypass a PDB.
5. Continue HTTP probing for at least 180 seconds total. Continue paced workflow submission across
   the full drain window and wait for every terminal result.
6. Re-read database counts, Pod restart counts, instance identities, and Worker logs. Uncordon the
   node only after evidence capture.

## Pass criteria

- drain finishes within five minutes and all deployments return to 2/2 Ready;
- API and Frontend have zero failed probes for the full observation window;
- every submitted workflow has one unique ID and one valid terminal result;
- protected/business side-effect counts are unchanged unless the test explicitly specifies an
  idempotent mutation, in which case the exact expected delta occurs once;
- replacement Pods have new instance IDs, evicted instances become stale after 90 seconds, and no
  Pod restarts are needed for recovery;
- the main Worker log proves stop-claims-before-Temporal-drain ordering.

Any failed probe, duplicate effect, forced eviction, unlabeled placement, stale image tag, or
missing evidence makes the drill fail. Fix the defect and repeat from a fresh workflow-ID prefix;
never discard the failed trial from the execution ledger.
