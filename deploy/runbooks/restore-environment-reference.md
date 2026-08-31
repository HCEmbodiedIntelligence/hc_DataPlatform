# Restore Job configuration reference

This is the RST3-05 split between the public environment ConfigMap, credential Secret, and
file-mounted private material. It covers `hc-platform restore plan|execute|reconcile`. The exact
signed whole-backup plan and target document remain authoritative; a value that disagrees with
either document fails closed.

Do not copy this file into a ConfigMap. Create environment objects through the approved secret and
GitOps systems, then compare their key names with this reference. Values must not contain NUL, CR,
or LF. Credential values never belong in Helm values, argv, logs, evidence, or the input-document
Secret.

## Public/referenced ConfigMap keys

| Key | Required/default | Purpose |
| --- | --- | --- |
| `HC_BACKUP_REPOSITORY_ENDPOINT` | provider-dependent | locked backup-repository S3 endpoint |
| `HC_BACKUP_REPOSITORY_PREFIX` | default `whole-platform-backups` | exact repository prefix |
| `HC_BACKUP_REPOSITORY_REGION` | default `us-east-1` | repository region |
| `HC_BACKUP_REPOSITORY_ADDRESSING_STYLE` | default `path` | S3 addressing style |
| `HC_BACKUP_REPOSITORY_REPLICA_DESTINATION` | required | provider replica destination bound by the plan |
| `HC_BACKUP_SIGNER_VAULT_ENDPOINT` | required | TLS Vault endpoint used to retrieve/verify the backup public key |
| `HC_BACKUP_SIGNER_PROVIDER_REFERENCE` | required | signer provider identity reference |
| `HC_BACKUP_SIGNER_TRANSIT_MOUNT` | default `transit` | exact Transit mount |
| `HC_BACKUP_SIGNER_KEY_NAME` | required | exact Ed25519 backup key name |
| `HC_BACKUP_SIGNER_KEY_VERSION` | required positive integer | exact key version in the backup plan |
| `HC_RESTORE_APPROVAL_PUBLIC_KEY_BASE64URL` | execute only | independent approval Ed25519 public key |
| `HC_RESTORE_APPROVAL_KEY_SHA256` | execute only | approval public-key pin |
| `HC_RESTORE_POSTGRES_HOST` | required | isolated target PostgreSQL host |
| `HC_RESTORE_POSTGRES_PORT` | default `5432` | target port |
| `HC_RESTORE_POSTGRES_DATABASE` | required | explicit empty target database |
| `HC_RESTORE_POSTGRES_USERNAME` | required | least-privilege restore role |
| `HC_RESTORE_POSTGRES_SSLMODE` | default `verify-full` | PostgreSQL TLS verification |
| `HC_RESTORE_OBJECT_STORE_ENDPOINT` | provider-dependent | target S3 endpoint |
| `HC_RESTORE_OBJECT_STORE_REGION` | default `us-east-1` | target region |
| `HC_RESTORE_OBJECT_STORE_ADDRESSING_STYLE` | default `path` | target addressing style |
| `HC_RESTORE_OBJECT_STORE_BUCKET_REFERENCE` | required | logical target bucket reference from the request |
| `HC_RESTORE_OBJECT_STORE_PREFIX` | required | isolated non-root target prefix |
| `HC_RESTORE_SOURCE_OBJECT_STORE_ENDPOINT` | provider-dependent | source object endpoint for exact version reads |
| `HC_RESTORE_SOURCE_OBJECT_STORE_REGION` | default `us-east-1` | source region |
| `HC_RESTORE_SOURCE_OBJECT_STORE_ADDRESSING_STYLE` | default `path` | source addressing style |
| `HC_RESTORE_SOURCE_OBJECT_STORE_BUCKET_REFERENCE` | execute/reconcile | logical source reference from the manifest |
| `HC_RESTORE_EXECUTION_OWNER_ID` | execute | stable owner ID, unchanged for replay |
| `HC_RESTORE_EXECUTION_FENCING_TOKEN` | execute | positive database-authoritative token, unchanged for replay |
| `HC_RESTORE_KUBERNETES_API_SERVER` | execute/reconcile | target API URL, normally `https://kubernetes.default.svc` |
| `HC_RESTORE_KUBERNETES_NAMESPACE` | execute/reconcile | isolated target namespace |
| `HC_RESTORE_KUBERNETES_API_DEPLOYMENT` | execute/reconcile | exact API Deployment name |
| `HC_RESTORE_KUBERNETES_WORKER_DEPLOYMENTS` | execute/reconcile | sorted comma-separated Worker/Media Worker names |
| `HC_RESTORE_KUBERNETES_API_REPLICAS` | default `1` | allowed read-only API replicas |
| `HC_RESTORE_TEMPORAL_DEPLOYMENT_MODE` | default `external_managed` | production restore mode; other modes fail the production contract |
| `HC_RESTORE_TEMPORAL_TARGET` | required | Temporal frontend endpoint |
| `HC_RESTORE_TEMPORAL_NAMESPACE` | required | exact namespace |
| `HC_RESTORE_TEMPORAL_CLUSTER_REFERENCE` | required | exact provider identity |
| `HC_RESTORE_TEMPORAL_TLS_ENABLED` | default true for managed | TLS boundary |
| `HC_RESTORE_TEMPORAL_TLS_SERVER_NAME` | TLS deployment-dependent | certificate server-name verification |
| `HC_RESTORE_RECONCILIATION_VERIFIER_IDENTITY` | reconcile only | independent verifier identity |

The bounded optional tuning keys are
`HC_RESTORE_COMMAND_TIMEOUT_SECONDS`, `HC_RESTORE_KUBERNETES_TIMEOUT_SECONDS`,
`HC_RESTORE_KUBERNETES_READINESS_ATTEMPTS`,
`HC_RESTORE_KUBERNETES_READINESS_INTERVAL_SECONDS`, `HC_RESTORE_TEMPORAL_RPC_TIMEOUT_SECONDS`,
`HC_RESTORE_TEMPORAL_MAXIMUM_SCHEDULES`, `HC_RESTORE_TEMPORAL_MAXIMUM_OPEN_WORKFLOWS`,
`HC_RESTORE_TEMPORAL_STABILITY_ATTEMPTS`, and
`HC_RESTORE_TEMPORAL_STABILITY_INTERVAL_MILLISECONDS`. Change them only in the approved plan; a
large timeout is not evidence that an RPO/RTO objective passed.

## Credential Secret keys

| Key | Scope |
| --- | --- |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, optional `AWS_SESSION_TOKEN` | locked backup-repository read identity used by boto's standard provider chain |
| `HC_BACKUP_REPOSITORY_BUCKET` | physical repository bucket; keep out of exported evidence |
| `HC_BACKUP_REPOSITORY_KMS_KEY_ID` | exact provider key ID required on repository objects |
| `HC_BACKUP_REPOSITORY_KMS_OBSERVED_KEY_ID` | exact provider-returned key ID |
| `HC_BACKUP_SIGNER_VAULT_TOKEN` | minimum policy for the exact Transit public-key/read operation |
| optional `HC_BACKUP_SIGNER_VAULT_NAMESPACE` | Vault Enterprise namespace when required |
| `HC_RESTORE_POSTGRES_PASSWORD` | isolated target restore role password |
| `HC_RESTORE_OBJECT_STORE_BUCKET` | physical target bucket |
| `HC_RESTORE_OBJECT_STORE_ACCESS_KEY`, `HC_RESTORE_OBJECT_STORE_SECRET_KEY` | target prefix inspect/write identity; both or neither when workload identity is used |
| `HC_RESTORE_SOURCE_OBJECT_STORE_BUCKET` | physical source bucket |
| `HC_RESTORE_SOURCE_OBJECT_STORE_ACCESS_KEY`, `HC_RESTORE_SOURCE_OBJECT_STORE_SECRET_KEY` | exact-version source read identity; both or neither |
| optional `HC_RESTORE_TEMPORAL_API_KEY` | managed Temporal identity |

Repository, source-object, and target-object identities are distinct duties. Planning and execution
use `HC_RESTORE_OBJECT_STORE_*` for the target; they do not require the repository identity to
inspect or write the target. If workload identity is used, prove the Pod receives the intended
identity and omit both members of the corresponding explicit access/secret pair.

## File-mounted private material

The Helm Job creates these owner-only files in a private `emptyDir`; do not expose them through
`envFrom`:

| Runtime key | Mounted source |
| --- | --- |
| `HC_RESTORE_KUBERNETES_TOKEN_FILE` | audience-bound projected ServiceAccount token |
| `HC_RESTORE_KUBERNETES_CA_FILE` | namespace `kube-root-ca.crt` projection |
| `HC_RESTORE_AGE_IDENTITY_FILE` | dedicated age identity Secret for portable backup only |
| `HC_RESTORE_TEMPORAL_TLS_CA_FILE` | dedicated Temporal TLS Secret |
| `HC_RESTORE_TEMPORAL_TLS_CERT_FILE` and `HC_RESTORE_TEMPORAL_TLS_KEY_FILE` | paired mTLS Secret keys; configure both or neither |

The implementation also accepts `HC_RESTORE_AGE_BINARY` and
`HC_RESTORE_AGE_REQUIRED_VERSION` (default `age` and `1.3.1`), but production uses the binary
already pinned in the digest image.

## Per-mode minimum

- `plan`: repository and backup public-key verification, target PostgreSQL read-only inspection,
  dedicated target-object inspection, and staging path; no approval/Kubernetes/Temporal mutation.
- `execute`: all repository, approval, target/source object, PostgreSQL, Kubernetes, Temporal, and
  optional age/TLS keys; mutation approval still fixes writes/restore-verified false.
- `reconcile`: repository, target/source object, PostgreSQL, Kubernetes GET, Temporal visibility,
  verifier identity, and optional age/TLS keys; no approval document/key is mounted or consumed.

Before each mode, render the Job and compare its ConfigMap/Secret/file sources against this table.
Unexpected keys, missing keys, a private key in environment, or a shared broad cloud identity are
stop conditions.
