import { useQuery } from '@tanstack/react-query';
import type { DashboardScope } from '../types';
import {
  getDashboardActivity,
  getDashboardCoverage,
  getDashboardSnapshot,
  listDashboardPendingItems,
  type DashboardWindow,
} from './client';
import { dashboardQueryKeys } from './query-keys';

export function useDashboardActivity(scope: DashboardScope | null, window: DashboardWindow | null, enabled: boolean) {
  return useQuery({
    queryKey: scope && window ? dashboardQueryKeys.activity(scope, window) : ['dashboard', 'disabled', 'activity'],
    queryFn: ({ signal }) => getDashboardActivity(scope!, window!, signal),
    enabled: enabled && scope !== null && window !== null,
    staleTime: 60_000,
    retry: 2,
  });
}

export function useDashboardSnapshot(scope: DashboardScope | null, enabled: boolean) {
  return useQuery({
    queryKey: scope ? dashboardQueryKeys.snapshot(scope) : ['dashboard', 'disabled', 'snapshot'],
    queryFn: ({ signal }) => getDashboardSnapshot(scope!, signal),
    enabled: enabled && scope !== null,
    staleTime: 300_000,
    retry: 2,
  });
}

export function useDashboardCoverage(scope: DashboardScope | null, enabled: boolean) {
  return useQuery({
    queryKey: scope ? dashboardQueryKeys.coverage(scope) : ['dashboard', 'disabled', 'coverage'],
    queryFn: ({ signal }) => getDashboardCoverage(scope!, signal),
    enabled: enabled && scope !== null,
    staleTime: 300_000,
    retry: 2,
  });
}

export function useDashboardPending(scope: DashboardScope | null, enabled: boolean) {
  const input = { limit: 5 } as const;
  return useQuery({
    queryKey: scope ? dashboardQueryKeys.pending(scope, input) : ['dashboard', 'disabled', 'pending'],
    queryFn: ({ signal }) => listDashboardPendingItems(scope!, input, signal),
    enabled: enabled && scope !== null,
    staleTime: 60_000,
    retry: 2,
  });
}

export function useDashboardPendingPage(
  scope: DashboardScope | null,
  input: Readonly<{ limit: 50; after?: string; before?: string }>,
  enabled: boolean,
) {
  return useQuery({
    queryKey: scope ? dashboardQueryKeys.pending(scope, input) : ['dashboard', 'disabled', 'pending-page'],
    queryFn: ({ signal }) => listDashboardPendingItems(scope!, input, signal),
    enabled: enabled && scope !== null,
    staleTime: 60_000,
    retry: 2,
  });
}
