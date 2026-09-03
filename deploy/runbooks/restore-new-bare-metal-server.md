# Restore the whole platform on a clean server

This is the bare-metal/new-server entry point for RST3-05. The supported production architecture
is Kubernetes/Helm, so a clean physical server or VM is first bootstrapped with the organization's
approved Kubernetes distribution. Direct Docker Compose, host PostgreSQL restore, ad-hoc `psql`,
and systemd process mutation are not supported recovery paths.

Use `restore-operator-evidence.md` throughout. The terminal state is an isolated, read-only target
with reconciliation PASS. This runbook does not cut over traffic or mark a backup
`RESTORE_VERIFIED`.

## 1. Declare and isolate the target

The incident commander assigns a new target instance ID, target environment ID, DNS name, network
segment, and evidence location. They must not equal any live environment identity. Keep public DNS
and ingress disabled.

On the clean server, record rather than repair any failed check:

```bash
date --utc --iso-8601=seconds
hostnamectl
findmnt --json
lsblk --json --output NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS
ip -brief address
```

Stop if a source production disk, database volume, object-store volume, kubeconfig, or application
data directory is mounted. Do not format or delete it from this procedure. The server must have
separate target storage sized from the signed request with 30% headroom.

## 2. Bootstrap the approved Kubernetes substrate

Install the exact Kubernetes distribution/version and CSI/CNI/ingress components approved in the
release compatibility matrix and infrastructure change ticket. This repository intentionally does
not provide a curl-to-shell installer. Save package/image signatures and the infrastructure
provisioning run ID in the evidence worksheet.

Before platform installation, all of these must pass:

```bash
kubectl version
kubectl get nodes -o wide
kubectl get storageclass
kubectl auth can-i create namespaces
helm version
```

For an isolated drill, a single schedulable server proves the new-server recovery procedure but
does not prove HA4 or DR7 node failure. Production recovery must use the topology and storage class
required by the release manifest.

Create a new namespace with the target identity; never reuse the source namespace:

```bash
kubectl create namespace "$RESTORE_NAMESPACE"
kubectl label namespace "$RESTORE_NAMESPACE" \
  hc-data-platform/environment="$TARGET_ENVIRONMENT_ID"
```

## 3. Provision dependencies without application data

Provision, through the approved infrastructure provider:

- an empty PostgreSQL database on the exact recorded major, with only the restore role and network
  path required by the maintenance Job;
- an empty, versioned object-store target prefix distinct from the source and backup repository;
- a bounded staging PVC with at least the planner-required bytes;
- exact KMS/Secret versions, repository read-only identity, source-object read identity, and
  target-object write identity;
- the backed-up external-managed Temporal namespace/provider recovery coordinate. Self-hosted or
  development Temporal cannot be silently substituted;
- private DNS/certificates for read-only probes, with public routing still disabled.

Do not initialize the application schema or run the normal migration hook. Capture provider-native
empty/capacity/versioning/KMS/Temporal evidence.

## 4. Install the exact dormant platform release

Render a target values file from the authenticated release manifest. It must use digest-pinned
images and these dormant overrides:

```yaml
frontend:
  replicaCount: 0
ingress:
  enabled: false
podDisruptionBudget:
  enabled: false
backend:
  api:
    replicaCount: 0
  worker:
    replicaCount: 0
  mediaWorker:
    replicaCount: 0
  migration:
    enabled: false
  backupJob:
    enabled: false
  restoreJob:
    enabled: false
  migrationJob:
    enabled: false
```

Validate before installation:

```bash
helm lint deploy/helm/hc-data-platform -f "$DORMANT_VALUES"
helm template "$RESTORE_RELEASE" deploy/helm/hc-data-platform \
  --namespace "$RESTORE_NAMESPACE" -f "$DORMANT_VALUES" > rendered-dormant.yaml
```

The executor and verifier inspect `rendered-dormant.yaml`: all application replicas are zero,
ingress is absent, normal migration/backup/restore/migration Jobs are absent, all image references
are digests, and the exact release identity matches the backup. Then install it:

```bash
helm upgrade --install "$RESTORE_RELEASE" deploy/helm/hc-data-platform \
  --namespace "$RESTORE_NAMESPACE" -f "$DORMANT_VALUES" --atomic --wait
kubectl -n "$RESTORE_NAMESPACE" get deploy,pod,job
```

Stop if any application Pod or schema migration Job starts.

## 5. Continue with the common Kubernetes restore

The clean server is now a supported isolated Kubernetes target. Follow
`restore-kubernetes.md` from “Assign immutable inputs” through its final evidence/retention step.
Use the same namespace, release, target identities, dormant values, dependency identities, and
evidence worksheet established above; do not regenerate them during handoff.

## 6. Bare-metal completion record

In addition to the common packet, record server hardware/VM identity, disk/controller facts,
Kubernetes/CSI/CNI versions, node UID, storage-class parameters, and whether this was single-node or
production topology. A single-node result is `RST3-05 new-server drill evidence`; it is not an HA,
node-loss, provider-failover, or production cutover result.
