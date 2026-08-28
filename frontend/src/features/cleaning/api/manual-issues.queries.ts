import { useQuery } from "@tanstack/react-query";
import { request } from "../../../shared/api/http-client";
import { parseWire } from "../../../shared/api/validate";
import { useShellStore } from "../../../shared/scope/shell-store";
import type { ManualIssuesSearch } from "../routing";
import {
  adaptManualIssueEnvelope,
  adaptManualIssueListEnvelope,
} from "./manual-issues.adapter";
import { manualIssuesPageEnvelopeWireSchema } from "./manual-issues.schemas";
import { manualIssueKeys } from "./query-keys";

const SORT_TO_WIRE = {
  updatedAtDesc: "updated_at:desc,id:desc",
  updatedAtAsc: "updated_at:asc,id:asc",
  severityDesc: "severity:desc,updated_at:desc,id:desc",
  createdAtDesc: "created_at:desc,id:desc",
} as const;

function filters(
  search: ManualIssuesSearch,
): Readonly<Record<string, unknown>> {
  return {
    q: search.q,
    datasetId: search.datasetId,
    versionId: search.versionId,
    episodeId: search.episodeId,
    status: search.status,
    issueType: search.issueType,
    severity: search.severity,
    discoverySource: search.discoverySource,
    assigneeId: search.assigneeId,
    sort: search.sort,
    limit: search.limit,
  };
}

function useCleaningScope() {
  const scope = useShellStore((state) => state.scope);
  return {
    organizationId: scope?.organizationId ?? null,
    projectId: scope?.projectId ?? null,
    regionCode: scope?.regionCode ?? null,
  };
}

function endpoint(projectId: string, regionCode: string, suffix = ""): string {
  return `/projects/${encodeURIComponent(projectId)}/regions/${encodeURIComponent(regionCode)}/manual-issues${suffix}`;
}

export function useManualIssues(search: ManualIssuesSearch, allowed = true) {
  const scope = useCleaningScope();
  const normalized = filters(search);
  const cursor = search.after ?? search.before;
  return useQuery({
    queryKey: manualIssueKeys.list(normalized, cursor),
    enabled:
      allowed &&
      Boolean(scope.organizationId && scope.projectId && scope.regionCode),
    staleTime: 15_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: "GET",
        path: endpoint(scope.projectId!, scope.regionCode!),
        query: {
          q: search.q,
          datasetId: search.datasetId,
          versionId: search.versionId,
          episodeId: search.episodeId,
          status: search.status,
          issueType: search.issueType,
          severity: search.severity,
          discoverySource: search.discoverySource?.filter(
            (source) => source !== "AUTO_QC",
          ),
          assigneeId: search.assigneeId,
          sort: SORT_TO_WIRE[search.sort],
          after: search.after,
          before: search.before,
          limit: search.limit,
        },
        signal,
      });
      return adaptManualIssueListEnvelope(raw, {
        organizationId: scope.organizationId!,
        projectId: scope.projectId!,
        regionCode: scope.regionCode!,
      });
    },
  });
}

export function useManualIssuesPage(
  search: ManualIssuesSearch,
  allowed = true,
) {
  const scope = useCleaningScope();
  const normalized = filters(search);
  return useQuery({
    queryKey: manualIssueKeys.page(normalized),
    enabled:
      allowed &&
      Boolean(scope.organizationId && scope.projectId && scope.regionCode),
    staleTime: 60_000,
    queryFn: async ({ signal }) => {
      const path = endpoint(scope.projectId!, scope.regionCode!, ":page");
      const raw = await request<unknown>({
        method: "GET",
        path,
        query: {
          q: search.q,
          datasetId: search.datasetId,
          versionId: search.versionId,
          episodeId: search.episodeId,
          status: search.status,
          issueType: search.issueType,
          severity: search.severity,
          discoverySource: search.discoverySource?.filter(
            (source) => source !== "AUTO_QC",
          ),
          assigneeId: search.assigneeId,
        },
        signal,
      });
      const wire = parseWire(manualIssuesPageEnvelopeWireSchema, raw, {
        endpoint: "getManualIssuesPage",
        schemaVersion: "manual-cleaning.v1",
      });
      if (
        wire.scope.project_id !== scope.projectId ||
        wire.scope.region_code !== scope.regionCode
      ) {
        throw new Error("SCOPE_MISMATCH");
      }
      return {
        counts: {
          total: wire.data.counts.total,
          open: wire.data.counts.open,
          inProgress: wire.data.counts.in_progress,
          resolved: wire.data.counts.resolved,
        },
        facets: wire.data.facets,
        allowedActions: wire.data.allowed_actions,
        snapshotAt: wire.data.snapshot_at,
        requestId: wire.request_id,
      };
    },
  });
}

export function useManualIssue(
  manualIssueId: string | undefined,
  allowed = true,
) {
  const scope = useCleaningScope();
  return useQuery({
    queryKey: manualIssueKeys.detail(manualIssueId ?? "invalid"),
    enabled:
      allowed &&
      Boolean(
        manualIssueId &&
          scope.organizationId &&
          scope.projectId &&
          scope.regionCode,
      ),
    staleTime: 15_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: "GET",
        path: `${endpoint(scope.projectId!, scope.regionCode!)}/${encodeURIComponent(manualIssueId!)}`,
        signal,
      });
      return adaptManualIssueEnvelope(raw, {
        organizationId: scope.organizationId!,
        projectId: scope.projectId!,
        regionCode: scope.regionCode!,
      });
    },
  });
}
