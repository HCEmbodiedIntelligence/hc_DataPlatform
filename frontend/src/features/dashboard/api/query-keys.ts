import { makeQueryKey } from "../../../shared/api/query-keys";
import type { DashboardScope } from "../types";
import type { DashboardWindow } from "./client";

export const dashboardQueryKeys = {
  taskStatus: (scope: DashboardScope, taskId?: string) =>
    makeQueryKey("dashboard", "task-status", {
      organizationId: scope.organizationId,
      projectId: scope.projectId,
      regionCode: scope.regionCode,
      ...(taskId ? { taskId } : {}),
    }),
  activity: (scope: DashboardScope, window: DashboardWindow) =>
    makeQueryKey("dashboard", "activity", {
      organizationId: scope.organizationId,
      projectId: scope.projectId,
      regionCode: scope.regionCode,
      timezone: scope.timezone,
      from: window.from,
      to: window.to,
    }),
  snapshot: (scope: DashboardScope, window: DashboardWindow) =>
    makeQueryKey("dashboard", "snapshot", {
      organizationId: scope.organizationId,
      projectId: scope.projectId,
      regionCode: scope.regionCode,
      timezone: scope.timezone,
      ...window,
    }),
  pending: (
    scope: DashboardScope,
    window: DashboardWindow,
    input: Readonly<{ limit: 5 | 50; after?: string; before?: string }>,
  ) =>
    makeQueryKey("dashboard", "pending-items", {
      organizationId: scope.organizationId,
      projectId: scope.projectId,
      regionCode: scope.regionCode,
      ...window,
      ...input,
    }),
} as const;
