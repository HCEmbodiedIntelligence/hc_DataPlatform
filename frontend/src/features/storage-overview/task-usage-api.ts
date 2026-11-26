import { useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import type { components } from "../../shared/api/generated/platform";
import {
  ProjectTaskStorageWire,
  TaskStorageFilePageWire,
} from "../../shared/api/generated/platform-wires";
import { request } from "../../shared/api/http-client";
import { makeQueryKey } from "../../shared/api/query-keys";
import { parseWire } from "../../shared/api/validate";
import type { CapacityScope } from "./capacity-api";

export type TaskUsage = components["schemas"]["ProjectTaskStorage"];
export type TaskUsageRow = components["schemas"]["TaskStorageSummary"];
export type StorageFile = components["schemas"]["TaskStorageFile"];
export type UsageCategory =
  components["schemas"]["TaskStorageCategory"]["category"];

export function useTaskUsage(
  scope: CapacityScope | null,
  enabled: boolean,
) {
  const forceRefresh = useRef(false);
  const query = useQuery({
    queryKey: makeQueryKey("storage", "task-usage", {
      projectId: scope?.projectId,
    }),
    queryFn: async ({ signal }) => {
      const refresh = forceRefresh.current;
      forceRefresh.current = false;
      return parseWire(
        ProjectTaskStorageWire,
        await request({
          method: "GET",
          path: `/projects/${encodeURIComponent(scope!.projectId)}/storage/task-usage`,
          scope: scope!,
          signal,
          query: { refresh },
        }),
        { endpoint: "getTaskStorageUsage" },
      );
    },
    enabled: enabled && scope !== null,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
  return {
    ...query,
    refreshUsage: () => {
      forceRefresh.current = true;
      return query.refetch();
    },
  };
}

export function useTaskFiles(
  scope: CapacityScope,
  groupId: string,
  category: UsageCategory | undefined,
  search: string,
  offset: number,
  observedAt: string,
) {
  return useQuery({
    queryKey: makeQueryKey("storage", "task-files", {
      groupId,
      category,
      search,
      offset,
      observedAt,
    }),
    queryFn: async ({ signal }) =>
      parseWire(
        TaskStorageFilePageWire,
        await request({
          method: "GET",
          path: `/projects/${encodeURIComponent(scope.projectId)}/storage/task-usage/files`,
          scope,
          signal,
          query: { group_id: groupId, category, search, offset, limit: 30 },
        }),
        { endpoint: "listTaskStorageFiles" },
      ),
  });
}
