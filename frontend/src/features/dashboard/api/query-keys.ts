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
  snapshot: (scope: DashboardScope, window: DashboardWindow) => makeQueryKey('dashboard', 'snapshot', {
    organizationId: scope.organizationId,
    projectId: scope.projectId,
    regionCode: scope.regionCode,
    timezone: scope.timezone,
    ...window,
  }),
  coverage: (scope: DashboardScope, window: DashboardWindow) => makeQueryKey('dashboard', 'coverage', {
    organizationId: scope.organizationId,
    projectId: scope.projectId,
    regionCode: scope.regionCode,
    ...window,
  }),
  pending: (scope: DashboardScope, window: DashboardWindow, input: Readonly<{ limit: 5 | 50; after?: string; before?: string }>) =>
    makeQueryKey('dashboard', 'pending-items', {
      organizationId: scope.organizationId,
      projectId: scope.projectId,
      regionCode: scope.regionCode,
      ...window,
      ...input,
    }),
} as const;
