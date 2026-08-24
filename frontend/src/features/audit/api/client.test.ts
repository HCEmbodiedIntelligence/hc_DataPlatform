// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../../shared/config/runtime";
import { useShellStore } from "../../../shared/scope/shell-store";
import type { AuditSearch } from "../routing";
import type { AuditScope } from "../types";
import {
  getAuditBootstrap,
  getAuditExport,
  getAuditEvent,
  getAuditFacets,
  getAuditIntegrity,
  listAuditEvents,
  authorizeAuditExportDownload,
  createAuditExport,
  createAuditLegalHold,
  getAuditRetentionPolicy,
  listAuditLegalHolds,
  releaseAuditLegalHold,
  updateAuditRetentionPolicy,
} from "./client";

const scope: AuditScope = {
  organizationId: "org-p19-client",
  projectId: "project-p19-client",
  regionCode: "region-p19-client",
};

const search: AuditSearch = {
  from: "2026-08-19T12:00:00.000Z",
  to: "2026-08-20T12:00:00.000Z",
  actorId: [],
  eventName: [],
  resourceType: [],
  result: [],
  riskLevel: [],
  sort: "occurredAt:desc",
  limit: 20,
};

const wireScope = {
  organization_id: scope.organizationId,
  project_id: scope.projectId,
  region_code: scope.regionCode,
};

const event = {
  schema_version: 1,
  event_id: "audit-p19-client-01",
  event_name: "cleaning.draft.updated",
  occurred_at: "2026-08-20T11:59:00Z",
  recorded_at: "2026-08-20T11:59:00Z",
  actor: {
    type: "USER",
    principal_id: null,
    display_name: "已脱敏主体",
    role_ids: [],
    delegated_by_principal_id: null,
  },
  scope: wireScope,
  resource: {
    type: "CLEANING_DRAFT",
    id: "draft-p19-client",
    display_name: "已审计资源",
    parent_refs: [],
  },
  request: {
    request_id: "request-p19-client-01",
    job_id: null,
    client_type: "API",
    ip_address: null,
    device_summary: null,
  },
  outcome: { status: "SUCCEEDED", reason_code: null, http_status: null },
  risk: { level: "LOW", signal_codes: [] },
  change: null,
  relationships: {
    parent_event_id: null,
    related_event_ids: [],
    resource_refs: [],
  },
  retention: {
    class: "STANDARD",
    policy_version: "retention-unconfigured-v1",
    retain_until: null,
    legal_hold: false,
  },
  integrity: {
    status: "UNKNOWN",
    version: "core-audit-integrity-unverified-v1",
    record_digest: null,
    checkpoint_id: null,
  },
  producer: {
    service: "hc-data-platform",
    producer_event_id: "audit-p19-client-01",
    contract_version: "audit-event-v1",
  },
  allowed_actions: ["VIEW"],
  blocked_reasons: [],
} as const;

function json(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "/api/v1",
    sseBaseUrl: "/api/v1",
    buildVersion: "p19-client-test",
    releaseEnv: "test",
  });
  const shell = useShellStore.getState();
  shell.setSession(
    { actorId: "actor-p19-client", displayName: "审计读取者", roleIds: [] },
    "p19-session-token",
  );
  shell.setScope({
    organizationId: scope.organizationId,
    projectId: scope.projectId,
    ...(scope.regionCode ? { regionCode: scope.regionCode } : {}),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  useShellStore.getState().setSession(null, null);
  resetRuntimeConfigForTests();
});

describe("P19 formal runtime audit client", () => {
  it("uses all five production OpenAPI operations with exact scope and redacted projection parsing", async () => {
    const calls: Array<{
      input: RequestInfo | URL;
      init?: RequestInit;
    }> = [];
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ input, init });
      const url = String(input);
      if (url.includes("/audit/bootstrap")) {
        return Promise.resolve(
          json({
            data: {
              scope: wireScope,
              metrics: {
                today: "1",
                high_risk: "0",
                failed: "0",
                active_actors: "1",
              },
              as_of: "2026-08-20T12:00:00Z",
              catalog_version: "core-audit-catalog-v1",
              policy_version: "audit-read-redaction-v1",
              integrity: "UNKNOWN",
              allowed_actions: ["VIEW"],
              blocked_reasons: [],
            },
            scope: wireScope,
            request_id: "request-p19-client-bootstrap",
            contract_version: "v1",
          }),
        );
      }
      if (url.includes("/audit/integrity")) {
        return Promise.resolve(
          json({
            data: {
              status: "PASSED",
              version: "core-audit-integrity-chain-v1",
              checked_at: "2026-08-20T12:00:00Z",
              checked_event_count: 1,
              checked_chain_count: 1,
              verified_through: "2026-08-20T11:59:00Z",
            },
            scope: wireScope,
            request_id: "request-p19-client-integrity",
            contract_version: "v1",
          }),
        );
      }
      if (url.includes("/audit/events/facets")) {
        return Promise.resolve(
          json({
            data: {
              event_names: ["cleaning.draft.updated"],
              actor_ids: [],
              resource_types: ["CLEANING_DRAFT"],
              outcomes: ["SUCCEEDED"],
              risk_levels: ["LOW"],
            },
            scope: wireScope,
            request_id: "request-p19-client-facets",
            contract_version: "v1",
          }),
        );
      }
      if (url.endsWith("/audit/events/audit-p19-client-01")) {
        return Promise.resolve(
          json({
            data: event,
            scope: wireScope,
            request_id: "request-p19-client-detail",
            contract_version: "v1",
          }),
        );
      }
      if (url.includes("/audit/events")) {
        return Promise.resolve(
          json({
            items: [event],
            page_info: {
              has_next_page: false,
              has_previous_page: false,
              start_cursor: "opaque-p19-start-cursor",
              end_cursor: "opaque-p19-end-cursor",
            },
            snapshot_at: "2026-08-20T12:00:00Z",
            redaction: {
              policy_version: "audit-read-redaction-v1",
              omitted_field_classes: ["raw_details", "ip_address"],
            },
            scope: wireScope,
            request_id: "request-p19-client-list",
            contract_version: "v1",
          }),
        );
      }
      return Promise.reject(new Error(`unexpected URL: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(getAuditBootstrap(scope, search)).resolves.toMatchObject({
      integrity: "UNKNOWN",
      metrics: { today: 1n },
    });
    await expect(getAuditIntegrity(scope)).resolves.toMatchObject({
      status: "PASSED",
      checkedEventCount: 1n,
      checkedChainCount: 1n,
    });
    await expect(getAuditFacets(scope, search)).resolves.toMatchObject({
      eventNames: ["cleaning.draft.updated"],
    });
    await expect(listAuditEvents(scope, search)).resolves.toMatchObject({
      items: [{ eventId: event.event_id, actor: { principalId: null } }],
      redactionPolicyVersion: "audit-read-redaction-v1",
    });
    await expect(getAuditEvent(scope, event.event_id)).resolves.toMatchObject({
      eventId: event.event_id,
      integrity: { status: "UNKNOWN", recordDigest: null },
    });

    expect(fetchMock).toHaveBeenCalledTimes(5);
    const bootstrapCall = calls[0];
    if (!bootstrapCall) throw new Error("missing bootstrap request");
    expect(String(bootstrapCall.input)).toContain(
      "/projects/project-p19-client/audit/bootstrap?",
    );
    expect(String(bootstrapCall.input)).toContain("occurred_from=");
    const headers = new Headers(bootstrapCall.init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer p19-session-token");
    expect(headers.get("X-Organization-Id")).toBe(scope.organizationId);
    expect(headers.get("X-Project-Id")).toBe(scope.projectId);
    expect(headers.get("X-Region-Code")).toBe(scope.regionCode);
    expect(String(calls[3]?.input)).toContain("/audit/events?");
    expect(String(calls[3]?.input)).toContain(
      "sort=occurred_at%3Adesc%2Cevent_id%3Adesc",
    );
  });

  it("fails closed when an audit response reports a different project scope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          json({
            data: {
              scope: { ...wireScope, project_id: "project-p19-other" },
              metrics: {
                today: "0",
                high_risk: "0",
                failed: "0",
                active_actors: "0",
              },
              as_of: "2026-08-20T12:00:00Z",
              catalog_version: "core-audit-catalog-v1",
              policy_version: "audit-read-redaction-v1",
              integrity: "UNKNOWN",
              allowed_actions: ["VIEW"],
              blocked_reasons: [],
            },
            scope: { ...wireScope, project_id: "project-p19-other" },
            request_id: "request-p19-client-mismatch",
            contract_version: "v1",
          }),
        ),
      ),
    );

    await expect(getAuditBootstrap(scope, search)).rejects.toMatchObject({
      code: "CONTRACT_MISMATCH",
      retryable: false,
    });
  });

  it("binds retention, legal hold, export and fresh download authorization to scope", async () => {
    const job = {
      job_id: "audit-export-client-01",
      scope: wireScope,
      status: "SUCCEEDED",
      progress: { exported_event_count: 2, scanned_page_count: 1 },
      occurred_from: search.from,
      occurred_to: search.to,
      created_by: "actor-p19-client",
      created_at: "2026-08-20T12:00:00Z",
      updated_at: "2026-08-20T12:00:01Z",
      artifact: {
        media_type: "application/x-ndjson",
        sha256: "a".repeat(64),
        size_bytes: "2048",
      },
      error_code: null,
      error_message: null,
    } as const;
    const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ input, init });
        const url = String(input);
        if (url.endsWith("/retention-policy"))
          return Promise.resolve(
            json({
              scope: wireScope,
              policy_version: 2,
              standard_days: 365,
              security_days: 2555,
              etag: '"audit-retention:test"',
              updated_by: "actor-p19-client",
              updated_at: "2026-08-20T12:00:00Z",
            }),
          );
        if (url.endsWith("/legal-holds")) {
          if (init?.method === "POST")
            return Promise.resolve(
              json({
                hold_id: "audit-hold-client-01",
                scope: wireScope,
                reason: "regulatory review",
                occurred_from: search.from,
                occurred_to: search.to,
                status: "ACTIVE",
                created_by: "actor-p19-client",
                created_at: "2026-08-20T12:00:00Z",
                released_by: null,
                released_at: null,
              }),
            );
          return Promise.resolve(json([]));
        }
        if (url.includes("legal-holds/audit-hold-client-01:release"))
          return Promise.resolve(
            json({
              hold_id: "audit-hold-client-01",
              scope: wireScope,
              reason: "regulatory review",
              occurred_from: search.from,
              occurred_to: search.to,
              status: "RELEASED",
              created_by: "actor-p19-client",
              created_at: "2026-08-20T12:00:00Z",
              released_by: "actor-p19-client",
              released_at: "2026-08-20T12:01:00Z",
            }),
          );
        if (url.endsWith("/exports") || url.endsWith(`/exports/${job.job_id}`))
          return Promise.resolve(json(job));
        if (url.endsWith(`/exports/${job.job_id}/download`))
          return Promise.resolve(
            json({
              job_id: job.job_id,
              download_url:
                "https://download.example.test/audit.jsonl?signature=opaque",
              expires_at: "2026-08-20T12:15:00Z",
              artifact: job.artifact,
            }),
          );
        return Promise.reject(new Error(`unexpected URL: ${url}`));
      }),
    );

    await expect(getAuditRetentionPolicy(scope)).resolves.toMatchObject({
      policyVersion: 2,
    });
    await expect(
      updateAuditRetentionPolicy(scope, {
        standardDays: 365,
        securityDays: 2555,
        etag: '"audit-retention:test"',
      }),
    ).resolves.toMatchObject({ etag: '"audit-retention:test"' });
    await expect(listAuditLegalHolds(scope)).resolves.toEqual([]);
    const hold = await createAuditLegalHold(scope, {
      reason: "regulatory review",
      occurredFrom: search.from,
      occurredTo: search.to,
    });
    await expect(
      releaseAuditLegalHold(scope, hold.holdId),
    ).resolves.toMatchObject({
      status: "RELEASED",
    });
    const created = await createAuditExport(scope, {
      occurredFrom: search.from,
      occurredTo: search.to,
      idempotencyKey: "audit-export-idempotency",
    });
    await expect(getAuditExport(scope, created.jobId)).resolves.toMatchObject({
      status: "SUCCEEDED",
      exportedEventCount: 2n,
      artifact: { sizeBytes: 2048n },
    });
    await expect(
      authorizeAuditExportDownload(scope, created.jobId),
    ).resolves.toMatchObject({ jobId: created.jobId });

    const put = calls.find((call) => call.init?.method === "PUT");
    expect(new Headers(put?.init?.headers).get("If-Match")).toBe(
      '"audit-retention:test"',
    );
    const exportCall = calls.find((call) =>
      String(call.input).endsWith("/exports"),
    );
    expect(new Headers(exportCall?.init?.headers).get("Idempotency-Key")).toBe(
      "audit-export-idempotency",
    );
    expect(new Headers(exportCall?.init?.headers).get("X-Project-Id")).toBe(
      scope.projectId,
    );
  });
});
