import { useInfiniteQuery } from "@tanstack/react-query";
import { z } from "zod";
import type { Scope } from "../../entities/scope";
import {
  adaptDataSourcePage,
  getDataSourcesPage,
  type DataSourcePage,
} from "../ingest/api";
import {
  fetchDatasets,
  type CursorPageVm,
  type DatasetListItemVm,
} from "../datasets/api";
import { createDomainError } from "../../shared/api/domain-error";
import { request } from "../../shared/api/http-client";
import { makeQueryKey } from "../../shared/api/query-keys";
import { parseWire } from "../../shared/api/validate";
import { useShellStore } from "../../shared/scope/shell-store";

export type GlobalDataSourceSearchPage = DataSourcePage;
export type GlobalDatasetSearchPage = CursorPageVm<DatasetListItemVm>;

const id = z.string().min(1).max(128);
const robotWireSchema = z
  .object({
    id,
    display_name: z.string().min(1).max(256),
    serial_no: z.string().min(1).max(128),
    lifecycle_status: z.string().min(1).max(64),
    connectivity: z
      .object({
        state: z.string().min(1).max(64),
        observed_at: z.string().datetime({ offset: true }).nullable(),
        source: z.string().min(1).max(128).nullable(),
        reason_code: z.string().min(1).max(128).nullable(),
      })
      .strict(),
  })
  .strict();
const pageInfo = z
  .object({
    has_next_page: z.boolean(),
    has_previous_page: z.boolean(),
    start_cursor: z.string().nullable(),
    end_cursor: z.string().nullable(),
  })
  .strict();

export const globalRobotSearchPageWireSchema = z
  .object({
    items: z.array(robotWireSchema),
    page_info: pageInfo,
    snapshot_at: z.string().datetime({ offset: true }),
    scope: z.object({ organization_id: id }).strict(),
    request_id: id,
    contract_version: z.string(),
  })
  .strict();

export interface GlobalRobotSearchPage {
  readonly query: string;
  readonly items: readonly Readonly<{
    entity_type: "ROBOT";
    robot: z.infer<typeof robotWireSchema>;
  }>[];
  readonly page_info: z.infer<typeof pageInfo>;
  readonly snapshot_at: string;
  readonly scope: Readonly<{ organization_id: string }>;
  readonly request_id: string;
  readonly contract_version: string;
}

export interface GlobalSearchScope extends Scope {
  readonly projectId: string;
  readonly regionCode: string;
}

function ensureRobotOrganizationScope(
  page: z.infer<typeof globalRobotSearchPageWireSchema>,
  scope: GlobalSearchScope,
): z.infer<typeof globalRobotSearchPageWireSchema> {
  if (page.scope.organization_id !== scope.organizationId) {
    throw createDomainError({
      code: "CONTRACT_MISMATCH",
      message: "机器人搜索响应与当前组织不匹配。",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: page.request_id,
      retryable: false,
      httpStatus: null,
    });
  }
  return page;
}

export async function searchRobots(
  scope: GlobalSearchScope,
  query: string,
  cursor: string | undefined,
  signal?: AbortSignal,
): Promise<GlobalRobotSearchPage> {
  const raw = await request<unknown>({
    method: "GET",
    path: `/organizations/${encodeURIComponent(scope.organizationId)}/robots`,
    scopeMode: "organization",
    scope: { organizationId: scope.organizationId },
    query: { q: query, cursor },
    ...(signal ? { signal } : {}),
  });
  const page = ensureRobotOrganizationScope(
    parseWire(globalRobotSearchPageWireSchema, raw, {
      endpoint: "listOrganizationRobots",
    }),
    scope,
  );
  return {
    query,
    items: page.items.map((robot) => ({ entity_type: "ROBOT", robot })),
    page_info: page.page_info,
    snapshot_at: page.snapshot_at,
    scope: page.scope,
    request_id: page.request_id,
    contract_version: page.contract_version,
  };
}

export async function searchDataSources(
  scope: GlobalSearchScope,
  query: string,
  cursor: string | undefined,
  signal?: AbortSignal,
): Promise<GlobalDataSourceSearchPage> {
  const page = await getDataSourcesPage(
    scope,
    {
      q: query,
      sort: "name:asc,id:asc",
      after: cursor,
      limit: 10,
    },
    signal,
  );
  return adaptDataSourcePage(page, scope);
}

export async function searchDatasets(
  scope: GlobalSearchScope,
  query: string,
  cursor: string | undefined,
  signal?: AbortSignal,
): Promise<GlobalDatasetSearchPage> {
  const page = await fetchDatasets(
    {
      q: query,
      sort: "nameAsc",
      after: cursor,
      limit: 20,
    },
    signal,
  );
  if (
    page.scope.organizationId !== scope.organizationId ||
    page.scope.projectId !== scope.projectId ||
    page.scope.regionCode !== scope.regionCode
  ) {
    throw createDomainError({
      code: "CONTRACT_MISMATCH",
      message: "数据集搜索响应与当前组织、项目或区域不匹配。",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: page.requestId,
      retryable: false,
      httpStatus: null,
    });
  }
  return page;
}

function useSearchScope(): GlobalSearchScope | null {
  return useShellStore((state) => {
    const scope = state.scope;
    return scope?.projectId && scope.regionCode
      ? (scope as GlobalSearchScope)
      : null;
  });
}

export function useGlobalRobotSearch(query: string, enabled: boolean) {
  const scope = useSearchScope();
  const trimmed = query.trim();
  const key = scope
    ? makeQueryKey("global-search", "robots", {
        organizationId: scope.organizationId,
        query: trimmed,
      })
    : ["global-search", "robots", "disabled"];
  return useInfiniteQuery({
    queryKey: key,
    enabled: enabled && scope !== null && trimmed.length > 0,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      searchRobots(scope!, trimmed, pageParam, signal),
    getNextPageParam: (page) =>
      page.page_info.has_next_page
        ? (page.page_info.end_cursor ?? undefined)
        : undefined,
  });
}

export function useGlobalDataSourceSearch(query: string, enabled: boolean) {
  const scope = useSearchScope();
  const trimmed = query.trim();
  const key = scope
    ? makeQueryKey("global-search", "data-sources", {
        projectId: scope.projectId,
        regionCode: scope.regionCode,
        query: trimmed,
      })
    : ["global-search", "data-sources", "disabled"];
  return useInfiniteQuery({
    queryKey: key,
    enabled: enabled && scope !== null && trimmed.length > 0,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      searchDataSources(scope!, trimmed, pageParam, signal),
    getNextPageParam: (page) =>
      page.pageInfo.hasNextPage
        ? (page.pageInfo.endCursor ?? undefined)
        : undefined,
  });
}

export function useGlobalDatasetSearch(query: string, enabled: boolean) {
  const scope = useSearchScope();
  const trimmed = query.trim();
  const key = scope
    ? makeQueryKey("global-search", "datasets", {
        organizationId: scope.organizationId,
        projectId: scope.projectId,
        regionCode: scope.regionCode,
        query: trimmed,
      })
    : ["global-search", "datasets", "disabled"];
  return useInfiniteQuery({
    queryKey: key,
    enabled: enabled && scope !== null && trimmed.length > 0,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      searchDatasets(scope!, trimmed, pageParam, signal),
    getNextPageParam: (page) =>
      page.pageInfo.hasNextPage
        ? (page.pageInfo.after ?? undefined)
        : undefined,
  });
}
