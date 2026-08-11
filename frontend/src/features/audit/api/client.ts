import { request } from '../../../shared/api/http-client';
import { createDomainError } from '../../../shared/api/domain-error';
import { parseWire } from '../../../shared/api/validate';
import type { AuditSearch } from '../routing';
import type { AuditScope } from '../types';
import { adaptAuditBootstrap, adaptAuditEventEnvelope, adaptAuditEventPage, adaptAuditFacets } from './adapter';
import { auditBootstrapWireSchema, auditEventEnvelopeWireSchema, auditEventPageWireSchema, auditFacetsWireSchema } from './schemas';

function auditRoot(scope: AuditScope): string {
  return `/projects/${encodeURIComponent(scope.projectId)}/audit`;
}

function assertAuditScope(
  actual: Readonly<{ organization_id: string; project_id: string | null; region_code: string | null }>,
  expected: AuditScope,
  requestId: string,
): void {
  if (actual.organization_id === expected.organizationId && actual.project_id === expected.projectId && actual.region_code === expected.regionCode) return;
  throw createDomainError({
    code: 'CONTRACT_MISMATCH', message: '审计响应作用域不匹配', fieldErrors: [], operationErrors: [],
    blockedReasons: [], requestId, retryable: false, httpStatus: null,
  });
}

function filters(search: AuditSearch) {
  return {
    occurredFrom: search.from,
    occurredTo: search.to,
    ...(search.actorId.length ? { actorId: search.actorId } : {}),
    ...(search.eventName.length ? { eventName: search.eventName } : {}),
    ...(search.resourceType.length ? { resourceType: search.resourceType } : {}),
    ...(search.resourceId ? { resourceId: search.resourceId } : {}),
    ...(search.result.length ? { outcome: search.result } : {}),
    ...(search.riskLevel.length ? { riskLevel: search.riskLevel } : {}),
    ...(search.requestId ? { requestId: search.requestId } : {}),
  };
}

export async function getAuditBootstrap(scope: AuditScope, search: AuditSearch, signal?: AbortSignal) {
  const endpoint = `${auditRoot(scope)}/bootstrap`;
  const raw = await request<unknown>({ method: 'GET', path: endpoint, query: { occurredFrom: search.from, occurredTo: search.to }, ...(signal ? { signal } : {}) });
  const wire = parseWire(auditBootstrapWireSchema, raw, { endpoint });
  assertAuditScope(wire.scope, scope, wire.request_id);
  return adaptAuditBootstrap(wire);
}

export async function getAuditFacets(scope: AuditScope, search: AuditSearch, signal?: AbortSignal) {
  const endpoint = `${auditRoot(scope)}/events/facets`;
  const raw = await request<unknown>({
    method: 'GET', path: endpoint,
    query: { ...filters(search), ...(scope.regionCode ? { regionCode: [scope.regionCode] } : {}) },
    ...(signal ? { signal } : {}),
  });
  const wire = parseWire(auditFacetsWireSchema, raw, { endpoint });
  assertAuditScope(wire.scope, scope, wire.request_id);
  return adaptAuditFacets(wire);
}

export async function listAuditEvents(scope: AuditScope, search: AuditSearch, signal?: AbortSignal) {
  const endpoint = `${auditRoot(scope)}/events`;
  const raw = await request<unknown>({
    method: 'GET',
    path: endpoint,
    query: {
      ...filters(search),
      ...(scope.regionCode ? { regionCode: [scope.regionCode] } : {}),
      sort: 'occurred_at:desc,event_id:desc',
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

export async function getAuditEvent(scope: AuditScope, eventId: string, signal?: AbortSignal) {
  const endpoint = `${auditRoot(scope)}/events/${encodeURIComponent(eventId)}`;
  const raw = await request<unknown>({ method: 'GET', path: endpoint, ...(signal ? { signal } : {}) });
  const wire = parseWire(auditEventEnvelopeWireSchema, raw, { endpoint });
  assertAuditScope(wire.scope, scope, wire.request_id);
  return adaptAuditEventEnvelope(wire);
}
