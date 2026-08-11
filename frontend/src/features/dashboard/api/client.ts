import { request } from '../../../shared/api/http-client';
import { createDomainError } from '../../../shared/api/domain-error';
import { parseWire } from '../../../shared/api/validate';
import type { DashboardScope } from '../types';
import {
  adaptDashboardActivity,
  adaptDashboardCoverage,
  adaptDashboardPendingPage,
  adaptDashboardSnapshot,
} from './adapter';
import {
  dashboardActivityWireSchema,
  dashboardCoverageWireSchema,
  dashboardPendingPageWireSchema,
  dashboardSnapshotWireSchema,
} from './schemas';

export type DashboardWindow = Readonly<{ from: string; to: string }>;

function dashboardRoot(scope: DashboardScope): string {
  return `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/dashboard`;
}

function assertDashboardScope(
  actual: Readonly<{ organization_id: string; project_id: string; region_code: string }>,
  expected: DashboardScope,
  requestId: string,
): void {
  if (actual.organization_id === expected.organizationId && actual.project_id === expected.projectId && actual.region_code === expected.regionCode) return;
  throw createDomainError({
    code: 'CONTRACT_MISMATCH', message: '工作台响应作用域不匹配', fieldErrors: [], operationErrors: [],
    blockedReasons: [], requestId, retryable: false, httpStatus: null,
  });
}

export async function getDashboardActivity(scope: DashboardScope, window: DashboardWindow, signal?: AbortSignal) {
  const endpoint = `${dashboardRoot(scope)}/activity`;
  const raw = await request<unknown>({
    method: 'GET',
    path: endpoint,
    query: { from: window.from, to: window.to, timezone: scope.timezone },
    ...(signal ? { signal } : {}),
  });
  const wire = parseWire(dashboardActivityWireSchema, raw, { endpoint });
  assertDashboardScope(wire.scope, scope, wire.request_id);
  return adaptDashboardActivity(wire);
}

export async function getDashboardSnapshot(scope: DashboardScope, signal?: AbortSignal) {
  const endpoint = `${dashboardRoot(scope)}/snapshot`;
  const raw = await request<unknown>({
    method: 'GET',
    path: endpoint,
    query: { timezone: scope.timezone, storageMonths: 6 },
    ...(signal ? { signal } : {}),
  });
  const wire = parseWire(dashboardSnapshotWireSchema, raw, { endpoint });
  assertDashboardScope(wire.scope, scope, wire.request_id);
  return adaptDashboardSnapshot(wire);
}

export async function getDashboardCoverage(scope: DashboardScope, signal?: AbortSignal) {
  const endpoint = `${dashboardRoot(scope)}/coverage`;
  const raw = await request<unknown>({ method: 'GET', path: endpoint, ...(signal ? { signal } : {}) });
  const wire = parseWire(dashboardCoverageWireSchema, raw, { endpoint });
  assertDashboardScope(wire.scope, scope, wire.request_id);
  return adaptDashboardCoverage(wire);
}

export async function listDashboardPendingItems(
  scope: DashboardScope,
  input: Readonly<{ limit: 5 | 50; after?: string; before?: string }>,
  signal?: AbortSignal,
) {
  const endpoint = `${dashboardRoot(scope)}/pending-items`;
  const raw = await request<unknown>({
    method: 'GET',
    path: endpoint,
    query: {
      sort: 'priority:desc,updated_at:desc,item_id:desc',
      limit: input.limit,
      ...(input.after ? { after: input.after } : {}),
      ...(input.before ? { before: input.before } : {}),
    },
    ...(signal ? { signal } : {}),
  });
  const wire = parseWire(dashboardPendingPageWireSchema, raw, { endpoint });
  assertDashboardScope(wire.scope, scope, wire.request_id);
  return adaptDashboardPendingPage(wire);
}
