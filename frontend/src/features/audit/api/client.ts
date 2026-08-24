import { request } from "../../../shared/api/http-client";
import { createDomainError } from "../../../shared/api/domain-error";
import { parseWire } from "../../../shared/api/validate";
import type { AuditSearch } from "../routing";
import type { AuditScope } from "../types";
import {
  adaptAuditBootstrap,
  adaptAuditEventEnvelope,
  adaptAuditEventPage,
  adaptAuditExportDownloadAuthorization,
  adaptAuditExportJob,
  adaptAuditFacets,
  adaptAuditIntegrity,
  adaptAuditLegalHold,
  adaptAuditRetentionPolicy,
} from "./adapter";
import {
  auditBootstrapWireSchema,
  auditEventEnvelopeWireSchema,
  auditEventPageWireSchema,
  auditExportDownloadAuthorizationWireSchema,
  auditExportJobWireSchema,
  auditFacetsWireSchema,
  auditIntegrityWireSchema,
  auditLegalHoldWireSchema,
  auditRetentionPolicyWireSchema,
} from "./schemas";

function auditRoot(scope: AuditScope): string {
  return `/projects/${encodeURIComponent(scope.projectId)}/audit`;
}

function requestScope(scope: AuditScope) {
  return {
    organizationId: scope.organizationId,
    projectId: scope.projectId,
    ...(scope.regionCode ? { regionCode: scope.regionCode } : {}),
  };
}

function assertAuditScope(
  actual: Readonly<{
    organization_id: string;
    project_id: string | null;
    region_code: string | null;
  }>,
  expected: AuditScope,
  requestId: string,
): void {
  if (
    actual.organization_id === expected.organizationId &&
    actual.project_id === expected.projectId &&
    actual.region_code === expected.regionCode
  )
    return;
  throw createDomainError({
    code: "CONTRACT_MISMATCH",
    message: "审计响应作用域不匹配",
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId,
    retryable: false,
    httpStatus: null,
  });
}

function filters(search: AuditSearch) {
  return {
    occurredFrom: search.from,
    occurredTo: search.to,
    ...(search.actorId.length ? { actorId: search.actorId } : {}),
    ...(search.eventName.length ? { eventName: search.eventName } : {}),
    ...(search.resourceType.length
      ? { resourceType: search.resourceType }
      : {}),
    ...(search.resourceId ? { resourceId: search.resourceId } : {}),
    ...(search.result.length ? { outcome: search.result } : {}),
    ...(search.riskLevel.length ? { riskLevel: search.riskLevel } : {}),
    ...(search.requestId ? { requestId: search.requestId } : {}),
  };
}

export async function getAuditBootstrap(
  scope: AuditScope,
  search: AuditSearch,
  signal?: AbortSignal,
) {
  const endpoint = `${auditRoot(scope)}/bootstrap`;
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    query: { occurredFrom: search.from, occurredTo: search.to },
    ...(signal ? { signal } : {}),
  });
  const wire = parseWire(auditBootstrapWireSchema, raw, { endpoint });
  assertAuditScope(wire.scope, scope, wire.request_id);
  return adaptAuditBootstrap(wire);
}

export async function getAuditIntegrity(
  scope: AuditScope,
  signal?: AbortSignal,
) {
  const endpoint = `${auditRoot(scope)}/integrity`;
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    ...(signal ? { signal } : {}),
  });
  const wire = parseWire(auditIntegrityWireSchema, raw, { endpoint });
  assertAuditScope(wire.scope, scope, wire.request_id);
  return adaptAuditIntegrity(wire);
}

export async function getAuditFacets(
  scope: AuditScope,
  search: AuditSearch,
  signal?: AbortSignal,
) {
  const endpoint = `${auditRoot(scope)}/events/facets`;
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    query: {
      ...filters(search),
      ...(scope.regionCode ? { regionCode: [scope.regionCode] } : {}),
    },
    ...(signal ? { signal } : {}),
  });
  const wire = parseWire(auditFacetsWireSchema, raw, { endpoint });
  assertAuditScope(wire.scope, scope, wire.request_id);
  return adaptAuditFacets(wire);
}

export async function listAuditEvents(
  scope: AuditScope,
  search: AuditSearch,
  signal?: AbortSignal,
) {
  const endpoint = `${auditRoot(scope)}/events`;
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    query: {
      ...filters(search),
      ...(scope.regionCode ? { regionCode: [scope.regionCode] } : {}),
      sort: "occurred_at:desc,event_id:desc",
      ...(search.after ? { after: search.after } : {}),
      ...(search.before ? { before: search.before } : {}),
      limit: search.limit,
    },
    ...(signal ? { signal } : {}),
  });
  const wire = parseWire(auditEventPageWireSchema, raw, { endpoint });
  assertAuditScope(wire.scope, scope, wire.request_id);
  return adaptAuditEventPage(wire);
}

export async function getAuditEvent(
  scope: AuditScope,
  eventId: string,
  signal?: AbortSignal,
) {
  const endpoint = `${auditRoot(scope)}/events/${encodeURIComponent(eventId)}`;
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    ...(signal ? { signal } : {}),
  });
  const wire = parseWire(auditEventEnvelopeWireSchema, raw, { endpoint });
  assertAuditScope(wire.scope, scope, wire.request_id);
  return adaptAuditEventEnvelope(wire);
}

export async function getAuditRetentionPolicy(
  scope: AuditScope,
  signal?: AbortSignal,
) {
  const endpoint = `${auditRoot(scope)}/retention-policy`;
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scope: requestScope(scope),
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  const wire = parseWire(auditRetentionPolicyWireSchema, raw, { endpoint });
  assertAuditScope(wire.scope, scope, "audit-retention-policy");
  return adaptAuditRetentionPolicy(wire);
}

export async function updateAuditRetentionPolicy(
  scope: AuditScope,
  input: Readonly<{ standardDays: number; securityDays: number; etag: string }>,
) {
  const endpoint = `${auditRoot(scope)}/retention-policy`;
  const raw = await request<unknown>({
    method: "PUT",
    path: endpoint,
    scope: requestScope(scope),
    ifMatch: input.etag,
    cache: "no-store",
    body: {
      standard_days: input.standardDays,
      security_days: input.securityDays,
    },
  });
  const wire = parseWire(auditRetentionPolicyWireSchema, raw, { endpoint });
  assertAuditScope(wire.scope, scope, "audit-retention-policy-update");
  return adaptAuditRetentionPolicy(wire);
}

export async function listAuditLegalHolds(
  scope: AuditScope,
  signal?: AbortSignal,
) {
  const endpoint = `${auditRoot(scope)}/legal-holds`;
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scope: requestScope(scope),
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  return parseWire(auditLegalHoldWireSchema.array(), raw, { endpoint }).map(
    (wire) => {
      assertAuditScope(wire.scope, scope, "audit-legal-hold");
      return adaptAuditLegalHold(wire);
    },
  );
}

export async function createAuditLegalHold(
  scope: AuditScope,
  input: Readonly<{ reason: string; occurredFrom: string; occurredTo: string }>,
) {
  const endpoint = `${auditRoot(scope)}/legal-holds`;
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    scope: requestScope(scope),
    cache: "no-store",
    body: {
      reason: input.reason,
      occurred_from: input.occurredFrom,
      occurred_to: input.occurredTo,
    },
  });
  const wire = parseWire(auditLegalHoldWireSchema, raw, { endpoint });
  assertAuditScope(wire.scope, scope, "audit-legal-hold-create");
  return adaptAuditLegalHold(wire);
}

export async function releaseAuditLegalHold(scope: AuditScope, holdId: string) {
  const endpoint = `${auditRoot(scope)}/legal-holds/${encodeURIComponent(holdId)}:release`;
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    scope: requestScope(scope),
    cache: "no-store",
  });
  const wire = parseWire(auditLegalHoldWireSchema, raw, { endpoint });
  assertAuditScope(wire.scope, scope, "audit-legal-hold-release");
  return adaptAuditLegalHold(wire);
}

export async function createAuditExport(
  scope: AuditScope,
  input: Readonly<{
    occurredFrom: string;
    occurredTo: string;
    idempotencyKey: string;
  }>,
) {
  const endpoint = `${auditRoot(scope)}/exports`;
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    scope: requestScope(scope),
    idempotencyKey: input.idempotencyKey,
    cache: "no-store",
    body: {
      occurred_from: input.occurredFrom,
      occurred_to: input.occurredTo,
    },
  });
  const wire = parseWire(auditExportJobWireSchema, raw, { endpoint });
  assertAuditScope(wire.scope, scope, "audit-export-create");
  return adaptAuditExportJob(wire);
}

export async function getAuditExport(
  scope: AuditScope,
  jobId: string,
  signal?: AbortSignal,
) {
  const endpoint = `${auditRoot(scope)}/exports/${encodeURIComponent(jobId)}`;
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scope: requestScope(scope),
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  const wire = parseWire(auditExportJobWireSchema, raw, { endpoint });
  assertAuditScope(wire.scope, scope, "audit-export");
  return adaptAuditExportJob(wire);
}

async function mutateAuditExport(
  scope: AuditScope,
  jobId: string,
  action: "cancel" | "retry",
) {
  const endpoint = `${auditRoot(scope)}/exports/${encodeURIComponent(jobId)}:${action}`;
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    scope: requestScope(scope),
    cache: "no-store",
  });
  const wire = parseWire(auditExportJobWireSchema, raw, { endpoint });
  assertAuditScope(wire.scope, scope, `audit-export-${action}`);
  return adaptAuditExportJob(wire);
}

export const cancelAuditExport = (scope: AuditScope, jobId: string) =>
  mutateAuditExport(scope, jobId, "cancel");

export const retryAuditExport = (scope: AuditScope, jobId: string) =>
  mutateAuditExport(scope, jobId, "retry");

export async function authorizeAuditExportDownload(
  scope: AuditScope,
  jobId: string,
) {
  const endpoint = `${auditRoot(scope)}/exports/${encodeURIComponent(jobId)}/download`;
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scope: requestScope(scope),
    cache: "no-store",
  });
  const wire = parseWire(auditExportDownloadAuthorizationWireSchema, raw, {
    endpoint,
  });
  if (wire.job_id !== jobId)
    throw createDomainError({
      code: "CONTRACT_MISMATCH",
      message: "审计导出下载授权与任务不匹配",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: null,
      retryable: false,
      httpStatus: null,
    });
  return adaptAuditExportDownloadAuthorization(wire);
}
