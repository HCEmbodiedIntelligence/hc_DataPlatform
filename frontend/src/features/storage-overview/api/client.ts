import { request } from '../../../shared/api/http-client';
import { createDomainError } from '../../../shared/api/domain-error';
import { parseWire } from '../../../shared/api/validate';
import type { StorageOverviewSearch } from '../routing';
import type { StorageScope } from '../types';
import { adaptStorageCost, adaptStorageInventoryPage, adaptStorageMultipartPage, adaptStorageObjectEnvelope, adaptStorageOverview } from './adapter';
import { storageCostWireSchema, storageMultipartPageWireSchema, storageObjectEnvelopeWireSchema, storageObjectPageWireSchema, storageOverviewWireSchema } from './schemas';

function storageRoot(scope: StorageScope): string {
  return `/projects/${encodeURIComponent(scope.projectId)}/storage`;
}

function assertStorageScope(
  actual: Readonly<{ organization_id: string; project_id: string; region_code: string }>,
  expected: StorageScope,
  requestId: string,
): void {
  if (actual.organization_id === expected.organizationId && actual.project_id === expected.projectId && actual.region_code === expected.regionCode) return;
  throw createDomainError({
    code: 'CONTRACT_MISMATCH', message: '存储响应作用域不匹配', fieldErrors: [], operationErrors: [],
    blockedReasons: [], requestId, retryable: false, httpStatus: null,
  });
}

export async function getStorageOverview(scope: StorageScope, months: 3 | 6 | 12, signal?: AbortSignal) {
  const endpoint = `${storageRoot(scope)}/overview`;
  const raw = await request<unknown>({ method: 'GET', path: endpoint, query: { regionCode: scope.regionCode, months }, ...(signal ? { signal } : {}) });
  const wire = parseWire(storageOverviewWireSchema, raw, { endpoint });
  assertStorageScope(wire.scope, scope, wire.request_id);
  return adaptStorageOverview(wire);
}

export async function listStorageInventory(scope: StorageScope, filters: StorageOverviewSearch, signal?: AbortSignal) {
  const endpoint = `${storageRoot(scope)}/objects`;
  const raw = await request<unknown>({
    method: 'GET',
    path: endpoint,
    query: {
      regionCode: scope.regionCode,
      ...(filters.objectRole ? { objectRole: filters.objectRole } : {}),
      ...(filters.storageClass ? { storageClass: filters.storageClass } : {}),
      ...(filters.anomaly.length ? { anomaly: filters.anomaly } : {}),
      ...(filters.status.length ? { status: filters.status } : {}),
      sort: filters.sort === 'physicalBytes:desc,objectId:desc' ? 'physical_bytes:desc,object_id:desc' : 'created_at:desc,object_id:desc',
      ...(filters.after ? { after: filters.after } : {}),
      ...(filters.before ? { before: filters.before } : {}),
      limit: filters.limit,
    },
    ...(signal ? { signal } : {}),
  });
  const wire = parseWire(storageObjectPageWireSchema, raw, { endpoint });
  assertStorageScope(wire.scope, scope, wire.request_id);
  return adaptStorageInventoryPage(wire);
}

export async function getStorageCost(scope: StorageScope, billingPeriod: string | undefined, signal?: AbortSignal) {
  const endpoint = `${storageRoot(scope)}/cost-breakdown`;
  const raw = await request<unknown>({
    method: 'GET',
    path: endpoint,
    query: { regionCode: scope.regionCode, ...(billingPeriod ? { billingPeriod } : {}) },
    ...(signal ? { signal } : {}),
  });
  const wire = parseWire(storageCostWireSchema, raw, { endpoint });
  assertStorageScope(wire.scope, scope, wire.request_id);
  return adaptStorageCost(wire);
}

export async function getStorageObject(scope: StorageScope, objectId: string, snapshotId: string, signal?: AbortSignal) {
  const endpoint = `${storageRoot(scope)}/objects/${encodeURIComponent(objectId)}`;
  const raw = await request<unknown>({
    method: 'GET', path: endpoint, query: { regionCode: scope.regionCode, snapshotId }, ...(signal ? { signal } : {}),
  });
  const wire = parseWire(storageObjectEnvelopeWireSchema, raw, { endpoint });
  assertStorageScope(wire.scope, scope, wire.request_id);
  return adaptStorageObjectEnvelope(wire);
}

export async function listStorageMultipart(scope: StorageScope, filters: StorageOverviewSearch, signal?: AbortSignal) {
  const endpoint = `${storageRoot(scope)}/multipart`;
  const raw = await request<unknown>({
    method: 'GET',
    path: endpoint,
    query: {
      regionCode: scope.regionCode,
      ...(filters.status.length ? { status: filters.status } : {}),
      sort: 'last_activity_at:asc,multipart_id:asc',
      ...(filters.after ? { after: filters.after } : {}),
      ...(filters.before ? { before: filters.before } : {}),
      limit: filters.limit,
    },
    ...(signal ? { signal } : {}),
  });
  const wire = parseWire(storageMultipartPageWireSchema, raw, { endpoint });
  assertStorageScope(wire.scope, scope, wire.request_id);
  return adaptStorageMultipartPage(wire);
}
