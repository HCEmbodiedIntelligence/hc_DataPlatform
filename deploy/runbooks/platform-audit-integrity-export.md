# Platform audit integrity and export

Use this runbook for the global backup, restore, release, and platform-control audit stream. It is
separate from runtime logs and from project-scoped P19 exports. The source rows and integrity
entries are append-only; the API returns only a redacted projection.

## Preconditions and authority

- Use the load-balanced HTTPS API for the exact environment under investigation and record its
  release identity first. Never query one Pod directly for acceptance evidence.
- Listing requires exactly `platform.operations.read` (or the read-only compatibility capability
  `platform.admin`). Integrity verification and export require exactly
  `platform.maintenance.verify`. Backup, restore, release, or break-glass operation capabilities
  do not confer export authority.
- Keep bearer credentials in an owner-only client configuration or approved secret helper. Do not
  place a token in shell history, process arguments, evidence, or this repository.
- Use explicit timezone-aware bounds. The interval is `[occurred_from, occurred_to)`; changing a
  bound while following a pagination cursor is rejected.

Set a non-secret endpoint and owner-only evidence directory:

```bash
export HC_PLATFORM_URL=https://platform.example.invalid
export HC_AUDIT_EVIDENCE_DIR=/approved/evidence/platform-audit-20260829T120000Z
install -d -m 0700 "$HC_AUDIT_EVIDENCE_DIR"
```

The examples below assume the approved HTTP client injects the bearer header without exposing it
in argv and is invoked as `hc-auth-curl`. Replace that name with the local approved helper.

## Verify before reading or exporting

```bash
hc-auth-curl --fail-with-body --silent --show-error \
  --dump-header "$HC_AUDIT_EVIDENCE_DIR/integrity.headers" \
  --output "$HC_AUDIT_EVIDENCE_DIR/integrity.json" \
  "$HC_PLATFORM_URL/api/v1/platform/audit/integrity"
jq -e '.format_version == "hc-platform-audit-integrity/v1" and
       .status == "PASSED" and .checked_event_count >= 0 and
       (.checked_chain_count == 0 or .checked_chain_count == 1)' \
  "$HC_AUDIT_EVIDENCE_DIR/integrity.json"
```

The verification request itself appends an audit event after taking the verified snapshot, so a
subsequent count can legitimately be larger by one. Require `Cache-Control: private, no-store`.
Do not copy internal hash material into tickets or chat.

## Search the redacted view

```bash
hc-auth-curl --fail-with-body --silent --show-error --get \
  --data-urlencode 'occurred_from=2026-08-29T00:00:00Z' \
  --data-urlencode 'occurred_to=2026-08-30T00:00:00Z' \
  --data-urlencode 'limit=200' \
  --output "$HC_AUDIT_EVIDENCE_DIR/page-001.json" \
  "$HC_PLATFORM_URL/api/v1/platform/audit/events"
jq -e '.format_version == "hc-platform-audit-page/v1" and
       ([.items[].domain] | all(. == "BACKUP" or . == "RESTORE" or
                                . == "RELEASE" or . == "PLATFORM")) and
       ([.items[]] | all(has("safe_details") | not))' \
  "$HC_AUDIT_EVIDENCE_DIR/page-001.json"
```

Follow `next_cursor` with the same bounds until it is null. Actor and resource IDs must have only
the `id-hmac-sha256:` representation. Detail values never cross the API; only approved detail key
names may appear under `redaction.retained_detail_keys`. `request_id` remains the authorized
correlation handle and must be treated as protected operational metadata.

## Export a bounded JSONL artifact

```bash
hc-auth-curl --fail-with-body --silent --show-error --get \
  --data-urlencode 'occurred_from=2026-08-29T00:00:00Z' \
  --data-urlencode 'occurred_to=2026-08-30T00:00:00Z' \
  --dump-header "$HC_AUDIT_EVIDENCE_DIR/export.headers" \
  --output "$HC_AUDIT_EVIDENCE_DIR/platform-audit.jsonl" \
  "$HC_PLATFORM_URL/api/v1/platform/audit/events:export"
jq -c -e 'select(.schema_version == "hc-platform-audit-event/v1") |
          select(.integrity == "CHAINED") |
          select(has("safe_details") | not)' \
  "$HC_AUDIT_EVIDENCE_DIR/platform-audit.jsonl" >/dev/null
sha256sum "$HC_AUDIT_EVIDENCE_DIR/platform-audit.jsonl" \
  >"$HC_AUDIT_EVIDENCE_DIR/platform-audit.jsonl.sha256"
chmod 0600 "$HC_AUDIT_EVIDENCE_DIR"/*
```

Require `Content-Type: application/x-ndjson`, `Cache-Control: private, no-store`, an attachment
filename, and `X-Audit-Event-Count` equal to the JSONL line count. A result over 10,000 events is
rejected; narrow the time window instead of bypassing the bound or querying database internals.

## Failure and incident handling

Any `FAILED` integrity result, missing sequence, predecessor/hash mismatch, or head mismatch is a
security and data-integrity incident. The export API already fails closed with HTTP 409. Pause
release, restore write-enable, backup promotion, and further audit export; preserve the database,
API response, release identity, timestamps, and authorized infrastructure snapshot. Notify the
security and database owners through the incident process.

Never update the source event, integrity entry, or chain head; never delete the mismatching row;
never rebuild the chain in place. Recovery is restore/reconciliation from independently verified
evidence. Only an isolated disposable database may be deliberately mutated for the acceptance
test below.

## Disposable PostgreSQL acceptance

The repository integration test runs the real signed backup-catalog rebuild and real RESTORE and
RELEASE maintenance repository paths, then proves source/entry immutability, capability denial,
zero-value export leakage, head-tamper detection, and 409 export refusal. Point it only at a fresh,
task-owned migrated database:

```bash
HC_PLATFORM_AUDIT_TEST_POSTGRES_DSN="$TASK_OWNED_DSN" \
HC_PLATFORM_AUDIT_PRINT_EVIDENCE=1 \
uv run pytest -q -s tests/platform_ops/test_platform_audit_postgres.py
```

Passing requires three classified business events, `PASSED` before tamper, both forbidden updates
rejected, forbidden export matches equal to zero, wrong capability 403, `FAILED` after head
tamper, and tampered export 409. Drop the disposable database after evidence capture.
