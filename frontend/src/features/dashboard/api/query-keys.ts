import { makeQueryKey } from '../../../shared/api/query-keys';
import type { DashboardScope } from '../types';
import type { DashboardWindow } from './client';

export const dashboardQueryKeys = {
  activity: (scope: DashboardScope, window: DashboardWindow) => makeQueryKey('dashboard', 'activity', {
    organizationId: scope.organizationId,
    projectId: scope.projectId,
    regionCode: scope.regionCode,
    timezone: scope.timezone,
    from: window.from,
    to: window.to,
  }),
  snapshot: (scope: DashboardScope) => makeQueryKey('dashboard', 'snapshot', {
    organizationId: scope.organizationId,
    projectId: scope.projectId,
    regionCode: scope.regionCode,
    timezone: scope.timezone,
    storageMonths: 6,
  }),
  coverage: (scope: DashboardScope) => makeQueryKey('dashboard', 'coverage', {
    organizationId: scope.organizationId,
    projectId: scope.projectId,
    regionCode: scope.regionCode,
  }),
  pending: (scope: DashboardScope, input: Readonly<{ limit: 5 | 50; after?: string; before?: string }>) =>
    makeQueryKey('dashboard', 'pending-items', {
      organizationId: scope.organizationId,
      projectId: scope.projectId,
      regionCode: scope.regionCode,
      sort: 'priority:desc,updatedAt:desc,itemId:desc',
      ...input,
    }),
} as const;
