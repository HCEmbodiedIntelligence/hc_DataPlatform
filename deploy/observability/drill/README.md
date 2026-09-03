# OBS5-02 disposable acceptance drill

These assets exercise two independent acceptance properties in a three-node Kind cluster:

- Frontend stdout → node-local Collector agent → HA Collector gateway → PVC/emptyDir-backed Loki,
  followed by stopping the producer node and querying the same historical record.
- Prometheus → critical synthetic rule → Alertmanager → bounded webhook evidence receiver, including
  the resolved delivery after the canary returns to zero.

The drill uses exact linux/amd64 platform-manifest digests for Collector, Loki, Grafana,
Prometheus, and Alertmanager, and exact locally built Frontend/receiver digests. Production values
remain pinned to the corresponding upstream multi-platform manifest digests. `receiver.py` retains
only alert status, fingerprint, timestamps, and four bounded labels. The cluster is disposable; do
not point the canary at a real pager without approval.

Validate assets before a run:

```bash
docker run --rm -v "$PWD/deploy/observability/drill/prometheus.yaml:/prometheus.yaml:ro" \
  --entrypoint /bin/promtool quay.io/prometheus/prometheus:v3.14.0 \
  check config /prometheus.yaml
docker run --rm -v "$PWD/deploy/observability/drill/canary-rules.yaml:/rules.yaml:ro" \
  --entrypoint /bin/promtool quay.io/prometheus/prometheus:v3.14.0 check rules /rules.yaml
docker run --rm -v "$PWD/deploy/observability/drill/alertmanager.yaml:/alertmanager.yaml:ro" \
  --entrypoint /bin/amtool quay.io/prometheus/alertmanager:v0.32.1 \
  check-config /alertmanager.yaml
```

The full operator sequence and stop conditions are in
`deploy/runbooks/observability-alerts.md`.
