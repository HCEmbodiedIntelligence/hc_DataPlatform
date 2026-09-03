# BE-12 disposable Kubernetes pilot report

Evidence date: 2026-08-14. This was a single-node disposable kind environment on the hardware
described in `tests/load/CAPACITY-REPORT.md`; it is deployment evidence, not production capacity
evidence.

Current-state qualification (2026-08-17): this retained rehearsal predates the split production
images, verified-JWT middleware, domain emitters, non-retryable activity fix, and stricter Worker
readiness probe. The immutable-data rollback evidence remains valid for the exercised chart, but
it is not a pass for the current chart. BE12-001 now supplies an importable
`workflowActivityFactory` that constructs all fourteen ports, and the current-source local image
smokes pass. Owner-managed image promotion and the repeated rehearsal remain pending. Helm
lint/template passes.

## Runtime

- kind v0.27.0 with Kubernetes v1.32.2 node image pinned to the official release digest
  `sha256:f226345927d7e348497136874b6d207e0b32cc52154ad8323129352923a3142f`.
- kubectl v1.32.13 and Helm v3.16.4.
- Cached same-day backend image `hc-data-platform-backend:be01`, loaded directly into kind.
- Disposable PostgreSQL 16.10, MinIO `RELEASE.2025-07-23T15-54-02Z`, and Temporal test server
  reachable from the kind node through its host gateway.
- Three pre-created Kubernetes Secrets; no credential values were stored in Helm values or this
  report.

## Upgrade and rollback result

The baseline had two API and two Worker replicas. All four Pods became Ready, both PDBs allowed
one disruption, and API readiness reported PostgreSQL, object storage, and Temporal as ready.

```bash
HELM_BIN=/tmp/linux-amd64/helm KUBECTL_BIN=/tmp/kubectl \
  PYTHON_BIN=.venv/bin/python \
  deploy/scripts/exercise-upgrade-rollback.sh be12-pilot hc-data-pilot \
  deploy/helm/hc-data-platform/values-ci.yaml
```

Two complete candidate upgrade/rollback cycles passed. Helm history retained install, upgrade,
and rollback revisions; after the final evidence capture, revision 7 was a successful rollback.
Both Deployments returned to 2/2 Ready.

Before the second cycle, distinct immutable proof objects were written under Raw, Lance, and
Published prefixes. Their SHA-256 values were identical after rollback:

| Prefix | SHA-256 after rollback |
| --- | --- |
| Raw | `2f648553b38dfdfb1c149eb4601c3cba67cd75d1b124aca23356606acf5e57fd` |
| Lance | `5c0861a2a7b0e7515604a31fc61220f9aeb7ee439375acecedb059e320b3386b` |
| Published | `f9ef5fdd40c56adb15083f2f6c3833a19480e48f575a2b893c952fe8545a99aa` |

## Runtime observations

- A production Preview workflow was picked up by the historical deployed Worker, proving workflow
  and activity polling. It could not execute because the production Activity factory was absent; the
  old source also retried the unconfigured dependency. BE12-009 is now closed in source, while
  BE12-001 is closed in source; neither corrected behavior has been rerun in Kubernetes.
- A test-only OTLP gRPC sink received 232 spans, 12 framework metric records, and four log
  envelopes from both API and Worker. It retained only counts/names and never log bodies. The
  sanitized result is `tests/system/results/pilot-otlp-summary.json`.
- Framework metrics in that run were limited to HTTP server activity. None of the then-missing
  domain metrics were emitted. BE12-002 is now closed in source but needs a current pilot capture.
- Fifty historical deployed upload-control requests all returned 401. BE12-003 is now closed in
  source and the signed-JWT in-process probe passes 50/50, but the external 20 GiB pilot gate has
  not been rerun.

The namespace, kind node, external disposable dependencies, and test-only data were removed after
evidence collection. No non-test Raw, Lance, or Published data was touched. The exercised chart's
rolling and rollback mechanics passed, but current-chart application release acceptance remains
blocked by owner-managed image promotion and a repeated authenticated/observable rollout with
the tightened Worker readiness gate.
