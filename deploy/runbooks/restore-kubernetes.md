# Restore the whole platform into an isolated Kubernetes target

This runbook executes the RST3-01/02/03 product path: read-only plan, independently approved
payload restore, and independent nine-domain reconciliation. Use
`restore-operator-evidence.md`. For a new physical/VM target, complete
`restore-new-bare-metal-server.md` first.

## Safety boundary and prerequisites

The target namespace, database, object prefix, Temporal recovery target, PVC, and identities are
new and isolated. Public ingress is off. API, Worker, Media Worker, Frontend, normal migration,
backup, restore, and planned-migration Jobs are initially disabled. The source platform and backup
repository are never mounted as writable volumes.

Required tools on the secured executor host are `kubectl`, `helm`, `jq`, `sha256sum`, and a
digest-pinned `backup-maintenance` image matching the authenticated release. Create a private local
workspace and refuse symlinks:

```bash
umask 077
install -d -m 0700 "$RESTORE_WORK"
test -O "$RESTORE_WORK" && test ! -L "$RESTORE_WORK"
cd "$RESTORE_WORK"
```

Copy in owner-only `whole-backup-plan.json` and `restore-target.json` from the approved evidence
handoff. Do not copy a private signing key. Verify mode, owner, and hashes:

```bash
test -O whole-backup-plan.json && test ! -L whole-backup-plan.json
test -O restore-target.json && test ! -L restore-target.json
chmod 0600 whole-backup-plan.json restore-target.json
sha256sum whole-backup-plan.json restore-target.json
```

### Assign immutable inputs

Record these shell variables in the session, using values already approved in the worksheet:

```bash
export BACKUP_ID='replace-with-exact-backup-id'
export TARGET_ENVIRONMENT_ID='replace-with-new-target-environment-id'
export RESTORE_NAMESPACE='replace-with-new-namespace'
export RESTORE_RELEASE='replace-with-new-release-name'
export RESTORE_WORK='/absolute/owner-only/path'
export DORMANT_VALUES='/absolute/path/dormant-values.yaml'
export RESTORE_VALUES='/absolute/path/restore-job-values.yaml'
export RESTORE_PVC='replace-with-owner-only-staging-pvc'
export RESTORE_SERVICE_ACCOUNT='replace-with-dedicated-restore-sa'
```

Do not put credentials into these variables or command arguments.

## 1. Prove the target is dormant

```bash
kubectl -n "$RESTORE_NAMESPACE" get deploy -o json > deployments-before.json
jq -e '[.items[] | .spec.replicas // 0] | all(. == 0)' deployments-before.json
kubectl -n "$RESTORE_NAMESPACE" get ingress
kubectl -n "$RESTORE_NAMESPACE" get jobs
kubectl -n "$RESTORE_NAMESPACE" get pvc "$RESTORE_PVC"
```

The empty PostgreSQL database, empty versioned object prefix, exact release, KMS, Temporal,
certificate, dependency, and capacity observations must already be bound into
`restore-target.json`. The planner rechecks live PostgreSQL/object/PVC state; do not waive a failed
check.

## 2. Run read-only restore planning

Run the exact maintenance image in the secured administration environment with repository read,
Vault Transit public-key access, target read-only inspection, and no target mutation permission.
Supply configuration through the platform secret mechanism/environment, never argv. The command
inside that environment is:

```bash
hc-platform restore plan "$BACKUP_ID" \
  --target "$TARGET_ENVIRONMENT_ID" \
  --backup-plan "$RESTORE_WORK/whole-backup-plan.json" \
  --target-document "$RESTORE_WORK/restore-target.json" \
  --staging-directory "$RESTORE_WORK" \
  --dry-run > "$RESTORE_WORK/restore-plan.json"
chmod 0600 "$RESTORE_WORK/restore-plan.json"
```

Capture stderr separately in the protected evidence system, scan it for Secret material, and stop
on any non-zero exit. Verify all 12 preflight checks and three 30%-headroom domains:

```bash
jq -e '.status == "PREFLIGHT_PASSED"
  and .writes_enabled == false
  and .next_action == "restore_execute_requires_signed_approval"
  and (.checks | length == 12)
  and ([.checks[].passed] | all)' restore-plan.json
sha256sum restore-plan.json
```

Re-run target emptiness immediately before approval. Any target mutation invalidates the plan.

## 3. Obtain independent restore approval

The executor sends only the exact plan plus its SHA-256 and evidence references to the restore
approver. The approver creates `hc-platform-restore-approval/v1` from the generated plan and signs
its canonical bytes with the approved external Ed25519 KMS/HSM key. Required fixed authorization
values are:

```json
{
  "allow_restore_mutation": true,
  "allow_write_enable": false,
  "allow_restore_verified": false,
  "approver_role": "restore_approver"
}
```

The detached envelope format is `hc-platform-restore-approval-signature/v1`, algorithm `Ed25519`,
and binds the approval SHA-256/key fingerprint. The executor must not have access to the private
key. Copy the two returned documents as `restore-approval.json` and `restore-approval.sig`, mode
`0600`, then verify the fixed flags, plan/target identities, UTC validity, hashes, KMS audit
reference, and duty separation. Do not hand-edit signed bytes.

## 4. Create least-privilege runtime objects

Create a dedicated ServiceAccount with only `get` and `patch` on the exact API/Worker/Media Worker
Deployment names in this namespace. `reconcile` needs only `get`; retaining `patch` is acceptable
only for the same approved execute identity. Do not bind cluster-admin or Secret read/list.

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: REPLACE_RESTORE_SERVICE_ACCOUNT
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: hc-restore-runtime
rules:
  - apiGroups: ["apps"]
    resources: ["deployments"]
    resourceNames:
      - REPLACE_API_DEPLOYMENT
      - REPLACE_WORKER_DEPLOYMENT
      - REPLACE_MEDIA_WORKER_DEPLOYMENT
    verbs: ["get", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: hc-restore-runtime
subjects:
  - kind: ServiceAccount
    name: REPLACE_RESTORE_SERVICE_ACCOUNT
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: hc-restore-runtime
```

Use an explicitly reviewed manifest with replacements and namespace set by `kubectl -n`. Confirm:

```bash
kubectl -n "$RESTORE_NAMESPACE" auth can-i get deployments \
  --as="system:serviceaccount:$RESTORE_NAMESPACE:$RESTORE_SERVICE_ACCOUNT"
kubectl -n "$RESTORE_NAMESPACE" auth can-i patch deployments \
  --as="system:serviceaccount:$RESTORE_NAMESPACE:$RESTORE_SERVICE_ACCOUNT"
kubectl -n "$RESTORE_NAMESPACE" auth can-i get secrets \
  --as="system:serviceaccount:$RESTORE_NAMESPACE:$RESTORE_SERVICE_ACCOUNT"
```

The first two are `yes`; the Secret check is `no`. The projected token audience must match the
target API issuer.

Create the immutable input Secret from files, the non-secret ConfigMap from reviewed literal
settings, and the credential Secret through the approved secret controller. Never use literal
secret values in shell history:

```bash
kubectl -n "$RESTORE_NAMESPACE" create secret generic hc-restore-input \
  --from-file=whole-backup-plan.json \
  --from-file=restore-target.json \
  --from-file=restore-plan.json \
  --from-file=restore-approval.json \
  --from-file=restore-approval.sig \
  --dry-run=client -o yaml | kubectl apply -f -
```

The environment ConfigMap contains only public endpoints/references, exact target identities,
release/fence owner and token, public approval key/pin, Kubernetes namespace/deployment names,
Temporal public TLS settings, limits, and verifier identity. The environment Secret contains
repository/source/target object credentials, target PostgreSQL password, Vault token, optional
Temporal API key, and no local private signing key. Portable backups mount the age identity as its
own Secret; Temporal CA/client identity use the dedicated TLS Secret. Use the exact per-mode key
inventory in `restore-environment-reference.md`; an unlisted or shared broad identity is a stop
condition.

## 5. Render and execute the restore Job

Set `backend.restoreJob` in a copy of the dormant values:

```yaml
backend:
  restoreJob:
    enabled: true
    operation: execute
    backupId: REPLACE_BACKUP_ID
    targetEnvironment: REPLACE_TARGET_ENVIRONMENT
    image:
      repository: REPLACE_DIGEST_PINNED_REPOSITORY
      digest: REPLACE_SHA256_DIGEST
    serviceAccountName: REPLACE_RESTORE_SERVICE_ACCOUNT
    backoffLimit: 0
    inputSecret:
      name: hc-restore-input
    environmentConfigMap:
      name: hc-restore-environment
    environmentSecret:
      name: hc-restore-credentials
    staging:
      existingClaim: REPLACE_RESTORE_PVC
```

Keep application replicas, normal migration, ingress, backup Job, and migration Job disabled.
Render and inspect before mutation:

```bash
helm lint deploy/helm/hc-data-platform -f "$RESTORE_VALUES"
helm template "$RESTORE_RELEASE" deploy/helm/hc-data-platform \
  --namespace "$RESTORE_NAMESPACE" -f "$RESTORE_VALUES" > rendered-restore-execute.yaml
```

Verify the Job image digest, exact Secret/ConfigMap/PVC/SA, `backoffLimit: 0`, no default token
automount, non-root/read-only root filesystem, and no private key in ConfigMap/env/argv. Apply:

```bash
export RESTORE_EXECUTE_JOB='replace-with-exact-job-metadata-name-from-render'
helm upgrade "$RESTORE_RELEASE" deploy/helm/hc-data-platform \
  --namespace "$RESTORE_NAMESPACE" -f "$RESTORE_VALUES"
kubectl -n "$RESTORE_NAMESPACE" wait \
  --for=condition=complete job/"$RESTORE_EXECUTE_JOB" \
  --timeout=8h
kubectl -n "$RESTORE_NAMESPACE" logs \
  job/"$RESTORE_EXECUTE_JOB" > execute-output.json
```

If the Job fails, capture `get job,pod -o yaml`, init/main logs, events, and current PVC snapshot;
leave the target read-only. Do not create a new plan/approval merely to bypass a retained
checkpoint.

Validate `execute-output.json` with the common worksheet. Confirm API desired/available/ready is
exactly the approved read-only count and every Worker/Media Worker count is zero.

## 6. Run independent reconciliation

The verifier receives read-only provider credentials and the retained PVC/checkpoint, not the
mutation approval. Create a reconciliation input Secret containing only whole plan, target, and
restore plan. Change the values to `operation: reconcile`, set an exact UTC `reconciledAt`, and
ensure approval files are absent from the rendered volume/items/argv.

```bash
kubectl -n "$RESTORE_NAMESPACE" create secret generic hc-restore-reconcile-input \
  --from-file=whole-backup-plan.json \
  --from-file=restore-target.json \
  --from-file=restore-plan.json \
  --dry-run=client -o yaml | kubectl apply -f -

helm lint deploy/helm/hc-data-platform -f "$RECONCILE_VALUES"
helm template "$RESTORE_RELEASE" deploy/helm/hc-data-platform \
  --namespace "$RESTORE_NAMESPACE" -f "$RECONCILE_VALUES" \
  > rendered-restore-reconcile.yaml
export RESTORE_RECONCILE_JOB='replace-with-exact-job-metadata-name-from-render'
helm upgrade "$RESTORE_RELEASE" deploy/helm/hc-data-platform \
  --namespace "$RESTORE_NAMESPACE" -f "$RECONCILE_VALUES"
kubectl -n "$RESTORE_NAMESPACE" wait \
  --for=condition=complete job/"$RESTORE_RECONCILE_JOB" \
  --timeout=8h
kubectl -n "$RESTORE_NAMESPACE" logs \
  job/"$RESTORE_RECONCILE_JOB" > reconcile-output.json
```

A failed reconciliation still retains a FAIL report and must return non-zero. Capture it before
repair; no write/Worker enable is permitted. On PASS, validate output and all nine FULL checks using
the common worksheet.

## 7. Exact replay drill

Capture completed Job YAML/logs and PVC snapshot first. Delete only the two exact completed Job
objects—not their PVC, inputs, database, prefix, or evidence—then rerun with identical inputs and
timestamps:

```bash
kubectl -n "$RESTORE_NAMESPACE" delete \
  job/"$RESTORE_EXECUTE_JOB" \
  job/"$RESTORE_RECONCILE_JOB"
helm upgrade "$RESTORE_RELEASE" deploy/helm/hc-data-platform \
  --namespace "$RESTORE_NAMESPACE" -f "$RESTORE_VALUES"
```

After execute completes, apply the unchanged reconcile values. The final restore checkpoint SHA,
report ID/SHA, every check evidence SHA, PostgreSQL deterministic content, and target object
version/content tuples must be identical. Any new object version, row/content drift, changed report,
or checkpoint conflict fails the drill.

## 8. Retain the target read-only and close RST3-05 evidence

Keep ingress disabled, source untouched, target DB fenced, API read-only, and Workers zero. Export
Job logs/YAML, rendered manifests, plan/approval/checkpoint/report hashes, nine-check report,
provider inventories, Kubernetes deployment states/events, and replay comparison to the approved
evidence store. Delete temporary local credential material through the organization's secure
disposal process only after evidence verification; do not delete target state as part of this
runbook.

An independent operator/verifier signs the worksheet. This closes an RST3-05 operator drill only.
There is no supported command here to open writes or append `RESTORE_VERIFIED`; DR7-01 remains open.
