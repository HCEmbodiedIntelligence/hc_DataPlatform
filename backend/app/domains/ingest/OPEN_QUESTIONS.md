# Ingest implementation assumptions

The upstream contract is marked conditional review. This implementation keeps the
following choices centralized so they can be replaced without changing route names or
public core APIs.

## Verification pipeline

`service.VERIFICATION_STAGES` freezes the provisional order as:

1. `MANIFEST_SCHEMA`
2. `OBJECT_EXISTENCE_SIZE`
3. `OBJECT_SHA256`
4. `ADAPTER_PARSE`
5. `DATASET_SCHEMA_SEMANTIC`
6. `ATOMIC_AVAILABILITY_COMMIT`

Submitting a manifest only appends a `QUEUED` VerificationRun and returns HTTP 202. It
never implies `AVAILABLE`. Availability still requires the future worker to persist a
matching DatasetVersion registration receipt atomically with the terminal facts.

## Quarantine and retry

Only a `QUARANTINED` session with a current `OPEN` Quarantine may be retried or replaced.
Retry preserves the object-set and manifest hashes, CASes the Quarantine to
`REVERIFY_REQUESTED`, and appends a new run. Replacement creates one new session with a
unique `supersedes_upload_id` and marks the old Quarantine `REPLACED`; failed facts are
never overwritten.

## Upload authorization

The approved production STS trust policy remains open as `ING-P0-07`. In test and local
development, the adapter exercises `ObjectStoragePort.presign_put`, limits authorization
to at most 900 seconds, and returns the isolated no-store fixture handoff defined by the
conditional schema. Signed URLs and temporary credential values remain request-local and
are excluded from projections, logs, audit, outbox, and idempotency rows.

Production fails closed with `UPLOAD_STS_POLICY_NOT_APPROVED`; it does not mint plausible
but over-broad credentials. Once the cloud policy is approved, the local fixture issuer
must be replaced by the platform temporary-credential adapter without changing the 29
operation contracts.

`renewUploadAuthorization` has no dedicated canonical audit event in the current
registry. Until one is approved, the successful command records the closest registered
technical event, `upload.transfer.retry_requested`, while preserving the exact operation
ID in audit detail and emitting `upload.authorization.renewed` to the outbox.

## Credential storage and connector execution

The current shared port catalog has no Secret Manager or connector-execution port.
DataSource writes therefore persist only an opaque managed reference and never secret
bytes; connection tests append the pinned request/job facts for an external worker. The
worker-side Secret Manager and connector adapter remain deployment integration work.

