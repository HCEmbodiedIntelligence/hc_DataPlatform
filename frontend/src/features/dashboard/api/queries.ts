import { useQuery } from "@tanstack/react-query";
import type { DashboardScope } from "../types";
import {
  getDashboardActivity,
  getDashboardTaskStatus,
  listDashboardPendingItems,
  type DashboardWindow,
} from "./client";
import { dashboardQueryKeys } from "./query-keys";

export function useDashboardActivity(
  scope: DashboardScope | null,
  window: DashboardWindow | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey:
      scope && window
        ? dashboardQueryKeys.activity(scope, window)
        : ["dashboard", "disabled", "activity"],
    queryFn: ({ signal }) => getDashboardActivity(scope!, window!, signal),
    enabled: enabled && scope !== null && window !== null,
    staleTime: 60_000,
    retry: 2,
  });
}

export function useDashboardTaskStatus(
  scope: DashboardScope | null,
  taskId: string | undefined,
  enabled: boolean,
) {
  return useQuery({
    queryKey: scope
      ? dashboardQueryKeys.taskStatus(scope, taskId)
      : ["dashboard", "disabled", "task-status"],
    queryFn: ({ signal }) => getDashboardTaskStatus(scope!, taskId, signal),
    enabled: enabled && scope !== null,
    staleTime: 60_000,
    retry: 2,
  });
}

export function useDashboardPending(
  scope: DashboardScope | null,
  window: DashboardWindow | null,
  enabled: boolean,
) {
  const input = { limit: 5 } as const;
  return useQuery({
    queryKey:
      scope && window
        ? dashboardQueryKeys.pending(scope, window, input)
        : ["dashboard", "disabled", "pending"],
    queryFn: ({ signal }) =>
      listDashboardPendingItems(scope!, window!, input, signal),
    enabled: enabled && scope !== null && window !== null,
    staleTime: 60_000,
    retry: 2,
  });
}

export function useDashboardPendingPage(
  scope: DashboardScope | null,
  window: DashboardWindow | null,
  input: Readonly<{ limit: 50; after?: string; before?: string }>,
  enabled: boolean,
) {
  return useQuery({
    queryKey:
      scope && window
        ? dashboardQueryKeys.pending(scope, window, input)
        : ["dashboard", "disabled", "pending-page"],
    queryFn: ({ signal }) =>
      listDashboardPendingItems(scope!, window!, input, signal),
    enabled: enabled && scope !== null && window !== null,
    staleTime: 60_000,
    retry: 2,
  });
}
