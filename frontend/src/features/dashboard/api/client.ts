import { request } from '../../../shared/api/http-client';
import { createDomainError } from '../../../shared/api/domain-error';
import { parseWire } from '../../../shared/api/validate';
import type { DashboardScope } from '../types';
import {
  adaptDashboardActivity,
  adaptDashboardPendingPage,
  adaptDashboardSnapshot,
} from './adapter';
import {
  dashboardActivityWireSchema,
  dashboardPendingPageWireSchema,
  dashboardSnapshotWireSchema,
} from './schemas';

export type DashboardWindow = Readonly<{ from: string; to: string }>;

function dashboardRoot(scope: DashboardScope): string {
  return `/projects/${encodeURIComponent(scope.projectId)}/dashboard`;
}

function assertDashboardScope(
  actual: Readonly<{ project_id: string; region_code: string }>,
  expected: DashboardScope,
  requestId: string,
): void {
  if (actual.project_id === expected.projectId && actual.region_code === expected.regionCode) return;
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
    query: { region_code: scope.regionCode, from: window.from, to: window.to, timezone: scope.timezone },
    ...(signal ? { signal } : {}),
  });
  const wire = parseWire(dashboardActivityWireSchema, raw, { endpoint });
  assertDashboardScope(wire, scope, 'dashboard-activity');
  return adaptDashboardActivity(wire);
}

export async function getDashboardSnapshot(scope: DashboardScope, window: DashboardWindow, signal?: AbortSignal) {
  const endpoint = `${dashboardRoot(scope)}/snapshot`;
  const raw = await request<unknown>({
    method: 'GET',
    path: endpoint,
    query: { region_code: scope.regionCode, from: window.from, to: window.to, timezone: scope.timezone },
    ...(signal ? { signal } : {}),
  });
  const wire = parseWire(dashboardSnapshotWireSchema, raw, { endpoint });
  assertDashboardScope(wire, scope, 'dashboard-snapshot');
  return adaptDashboardSnapshot(wire);
}

export async function listDashboardPendingItems(
  scope: DashboardScope,
  window: DashboardWindow,
  input: Readonly<{ limit: 5 | 50; after?: string; before?: string }>,
  signal?: AbortSignal,
) {
  const endpoint = `${dashboardRoot(scope)}/pending-items`;
  const raw = await request<unknown>({
    method: 'GET',
    path: endpoint,
    query: {
      region_code: scope.regionCode,
      from: window.from,
      to: window.to,
      timezone: scope.timezone,
      limit: input.limit,
      ...((input.after ?? input.before) ? { cursor: input.after ?? input.before } : {}),
    },
    ...(signal ? { signal } : {}),
  });
  const wire = parseWire(dashboardPendingPageWireSchema, raw, { endpoint });
  assertDashboardScope(wire, scope, 'dashboard-pending');
  return adaptDashboardPendingPage(wire);
}
