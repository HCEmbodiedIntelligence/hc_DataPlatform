# Runtime JSON logging and redaction verification

Use this runbook for OBS5-01 producer verification and for investigating a suspected runtime-log
leak. Central storage, retention, dashboards, and alert delivery belong to OBS5-02. P19 audit facts
remain a separate append-only record and must not be reconstructed from runtime logs.

## Fixed contract

Every API, main Worker, media Worker, frontend, and Compose gateway access event uses
`hc-runtime-log/v1`. The required correlation envelope is `timestamp`, `severity`, `service`,
`instance_id`, `node_name`, `role`, `release_id`, `request_id`, `trace_id`, `operation_id`,
`workflow_id`, `event_code`, `duration_ms`, `retry_count`, and `error_type`. Missing values are JSON
`null`; fields are not omitted. HTTP events may add only route template, method, and status.

Python log messages, interpolation arguments, exception messages, tracebacks, and unknown `extra`
attributes are discarded before stdout or an OTel logging handler receives the record. The event
code is the only message body. Controlled identifiers may remain reversible; invalid or
Secret-like identifiers become `id-sha256:<digest>`.

Nginx access formats must never reference raw URI/query/body/header/cookie variables. Frontend
routes are recorded as `/`; the Compose gateway records only `/`, `/api/`, `/health/live`, or
`/health/ready`. The gateway prefers the API's validated response `X-Request-ID`, allowing its
access event to join the API event without trusting an arbitrary client identifier.

## Correlation verification

1. Send an API request with a valid UUID `X-Request-ID` and a test-only query/cookie sentinel.
2. Start a durable workflow through that request. Find `WORKFLOW.STARTED`; it must contain the same
   request ID and both the workflow and operation identifiers. The workflow job is the operation
   boundary, so those two identifiers are equal for this event.
3. Find a Worker activity event. Its workflow and operation identifiers must match the start
   event. When tracing is enabled, join the active 32-character lowercase OTel trace ID as an
   independent correlation axis.
4. Find the API `HTTP.REQUEST_COMPLETED` event and the gateway event by request ID. API `route`
   must be the framework template, never the submitted URL.

Stop if any producer emits non-JSON access output, omits a required field, accepts a malformed
trace ID, or loses the request-to-workflow join.

## Zero-leak probe

Use a unique non-production sentinel in each forbidden surface: Authorization, Cookie, password,
token, DSN, signed URL/object locator, request body, exception message, and query string. Exercise
one successful request, one validation failure, one internal error, one workflow start, and one
activity retry. Search stdout and exported OTel log bodies for the exact sentinel and its URL-
encoded/base64 forms. Pass requires zero matches.

Do not use a real Secret as a probe. A match is an incident: stop evidence export, restrict the
affected sink, preserve access metadata without copying the leaked record, rotate the exposed
credential through the approved Secret process, and file a P19 security event. Do not edit or
delete P19 audit facts.

## Evidence

Retain producer release/image identity, test-only sentinel digest, event counts by service/event
code, correlation IDs, exact zero-match scan commands/results, and test results. Never retain the
sentinel plaintext in long-lived evidence. OBS5-01 does not claim that logs survive node loss;
that acceptance requires the OBS5-02 external collector and central backend drill.
