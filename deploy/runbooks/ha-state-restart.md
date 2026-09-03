# HA process-state and Pod-local-file restart drill

Use this runbook to prove that API/Worker replacement does not lose authenticated sessions,
idempotency results, multipart upload control, or workflow/task ownership. It covers HA4-03 only;
it does not prove shared-provider failover or production throughput.

## Preconditions and stop conditions

- Use an isolated namespace, task-owned database rows/object prefix, unique Temporal workflow-ID
  prefix, and immutable Frontend/API/Worker image digests.
- Require at least three eligible nodes, HA topology spread, Ready replicas at the configured
  minimum, PDB `allowedDisruptions >= 1`, and zero Pod restarts.
- Confirm `HC_RUNTIME_BACKEND=production`. PostgreSQL, S3, and Temporal must be real external
  processes from the application Pods' perspective; no in-memory adapter is acceptable.
- Record the rendered bounded `emptyDir` sizes. `/tmp/hc-data` and `/tmp/hc-runtime` must not use a
  hostPath or PVC and must not contain the only copy of a business artifact.
- Pre-existing migration drift, failed probes, stale task backlog, non-empty test prefix, missing
  cleanup authority, or a session/upload/workflow created outside the isolated scope is a stop
  condition.

Never print bearer tokens, presigned URLs, passwords, or object-store credentials into evidence.
Record stable IDs and one-way token fingerprints only.

## Establish durable state

1. Register or seed one task-owned account and exact organization/project/region grants. Create a
   real bearer session and confirm session bootstrap through the Kubernetes API Service.
2. Execute one approved idempotent mutation with a unique `Idempotency-Key`. Record status, exact
   canonical response SHA-256, resource ID/version, and the matching PostgreSQL idempotency row.
3. Create a real multipart upload session, upload at least one bounded part through its presigned
   URL, and record application session ID, provider multipart ID fingerprint, part number/ETag,
   manifest identity, and PostgreSQL row counts. Pause it so restart behavior is deterministic.
4. Start a real Temporal workflow-task backlog with unique IDs and enough duration to overlap the
   Worker eviction window. Include an operation with an explicit idempotent or zero-side-effect
   result. Record workflow IDs and baseline business/Outbox counts.

## Replace API and Worker capacity

1. Identify the API Pod that served the last bootstrap/mutation/upload request from request and
   instance evidence. Delete that exact Pod or drain its node without `--force` and without PDB
   bypass. Wait for a replacement Pod with a different UID to become Ready.
2. Route through the Service and require the original bearer session to bootstrap successfully.
   Replay the original idempotency key and exact body; require the exact response SHA and no second
   resource/audit/business mutation.
3. Read the original upload session from the replacement API, list the uploaded part, resume it,
   obtain a fresh authorization, upload/verify the next part if the fixture permits, and pause it
   again. Session/provider IDs and prior part evidence must remain unchanged.
4. Delete or drain the node of a Worker polling the exercised task queue. Require Temporal to
   retry/resume on a replacement Worker and wait for every workflow terminal result.
5. Do not copy `/tmp` between Pods. Record that replacement Pod UIDs and emptyDir volume identities
   changed while all durable state remained available.

## Pass criteria

- the original session works after API replacement and no replacement login is needed;
- the original idempotency key replays byte-equivalent output and protected row/effect counts have
  an exact delta of one;
- multipart upload identity, manifest, state, and prior part list survive replacement, while a
  newly issued presign is allowed to differ;
- every Temporal workflow has one valid terminal result, task backlog returns to zero, and the
  expected side effect occurs exactly once;
- all replacement Pods are Ready with zero restarts, old instance UIDs become stale on schedule,
  and no correctness step reads an old Pod-local path;
- task-owned database rows, multipart uploads, objects, sessions, and workflows are cleaned by
  exact ID/prefix, with protected database migration state unchanged.

Any session rejection, second mutation, missing part, reliance on a copied file, duplicate task
effect, forced PDB bypass, or unexplained retry makes the drill fail. Preserve the failed evidence,
fix the defect, and repeat with a fresh identity prefix.

## Accepted evidence — 2026-08-29

- A disposable Kind v1.32.2 cluster used four eligible nodes across two zones, four two-replica
  workloads, and four PDBs. Two real node drains used neither `--force` nor a PDB bypass.
- The API that created the state had instance ID `d3968a17-ed3a-4dfe-9722-51c4b67bed4c`.
  Replacement APIs used different instance/Pod UIDs; all final Pods were Ready with zero restarts.
- The original opaque session bootstrapped after both drains and a subsequent full API/Worker
  rolling update. Profile revision 3 replayed the exact canonical response SHA-256
  `ab318f07bbf16bbc4ac924468fe81fd6ff84eb4effa1300d11080d45b7afcf18`; the durable
  idempotency row stayed singular and replay created no audit mutation.
- One real MinIO multipart session retained its provider identity and `PAUSED` state. The API issued
  an HTTPS presigned authorization through an isolated TLS browser edge; a 30-byte part was PUT,
  reconciled as `UPLOADED`, and remained present after replacement.
- During the first node drain, 500 real `StorageLifecycleExecutionWorkflow` executions completed in
  142.407 seconds: 500 unique terminal `BLOCKED` results, zero processed IDs, and zero storage
  lifecycle/item/workflow-job/Outbox rows.
- API and Worker emptyDir sentinels from each drained node were absent on replacements; no file was
  copied. Rendered limits were Frontend 64 MiB, API 256 MiB, Worker/Media staging 20 GiB, and both
  Worker runtime volumes 16 MiB.

This closes HA4-03 process-state replacement only. It is not evidence for shared PostgreSQL,
object-store, or Temporal provider failover (HA4-04), production capacity (HA4-02), or an external
observability backend.
