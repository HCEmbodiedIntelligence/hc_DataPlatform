# Platform maintenance runbooks

- `restore-operator-evidence.md`: mandatory roles, stop rules, evidence worksheet, and completion
  classification for every restore drill.
- `restore-new-bare-metal-server.md`: clean physical/VM server bootstrap into the supported
  Kubernetes/Helm recovery architecture.
- `restore-kubernetes.md`: complete isolated Kubernetes plan/approve/execute/reconcile/replay
  procedure.
- `restore-environment-reference.md`: exact ConfigMap, Secret, and file-mounted runtime key split.
- `planned-migration-cutover.md`: signed seven-phase planned cross-server migration gate.
- `ha-node-drain.md`: multi-node application drain, continuity, graceful Worker shutdown, and
  duplicate-side-effect evidence gate.
- `ha-autoscaling-capacity.md`: API/Worker HPA, lease continuity, backlog/SLO, and digest-bound
  production capacity evidence gate.
- `ha-state-restart.md`: API/Worker replacement proof for durable sessions, idempotency, multipart
  uploads, tasks, and bounded Pod-local scratch.
- `ha-shared-provider-failover.md`: sequential PostgreSQL writer, object-store node, and Temporal
  frontend failover through stable application endpoints, plus immutable Secret consistency.
- `ha-runtime-config-revisions.md`: exact hot-reload allowlist, monotonic revision/rollback,
  P19 audit, multi-process convergence, and lost-notification fallback drill.
- `observability-runtime-logging.md`: fixed JSON envelope, API-to-Workflow correlation, Nginx
  safe-route access logs, hostile-value zero-leak probe, and incident stop conditions.
- `observability-alerts.md`: bounded metric alerts, per-alert response, synthetic critical-alert
  delivery verification, and node-loss historical-log query drill.
- `platform-audit-integrity-export.md`: global backup/restore/release audit search, independent
  chain verification, capability-bound redacted JSONL export, and tamper incident handling.
- `rolling-upgrade.md`: signed release feed, four-eyes approval, expand, Temporal routing,
  API/frontend canary, exact-digest rollback, rollout, and separate contract gate.
- `disaster-recovery-gates.md`: DR7-01 through DR7-06 execution index and signed, short-lived
  production evidence requirements.

Runbooks never override strict contracts in the CLI, schemas, release compatibility matrix, or
signed plan. A discrepancy is a stop condition and documentation defect; do not improvise around
it during an incident.
