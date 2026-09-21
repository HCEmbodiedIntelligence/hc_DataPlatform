import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { request } from "../../../shared/api/http-client";
import { parseWire } from "../../../shared/api/validate";
import { useShellStore } from "../../../shared/scope/shell-store";

const autoQualityProblemSchema = z
  .object({
    schema_version: z.literal("auto-quality-problem/v1"),
    id: z.string().regex(/^qc_[0-9a-f]{32}$/u),
    source: z.literal("AUTO_QC"),
    session_id: z.string().min(1).nullable(),
    source_import_id: z.string().min(1).nullish(),
    source_episode_index: z.number().int().nonnegative().nullish(),
    rollout_id: z.string().min(1),
    data_package_id: z.string().min(1).nullable(),
    status: z.enum(["RISK", "REJECT"]),
    severity: z.enum(["HIGH", "CRITICAL"]),
    start_ns: z.string().regex(/^(?:0|[1-9][0-9]*)$/u),
    end_ns: z.string().regex(/^[1-9][0-9]*$/u),
    message: z.string().min(1).max(1_000),
    finding_count: z.number().int().positive(),
    finding_codes: z.array(z.string().min(1)).min(1),
    topics: z.array(z.string().min(1)).min(1),
    report_sha256: z.string().regex(/^[0-9a-f]{64}$/u),
    updated_at: z.iso.datetime({ offset: true }),
  })
  .strict();

const autoQualityProblemListSchema = z
  .object({
    schema_version: z.literal("auto-quality-problem-list/v1"),
    items: z.array(autoQualityProblemSchema),
    total: z.number().int().nonnegative(),
  })
  .strict();

export interface AutoQualityProblem {
  readonly id: string;
  readonly source: "AUTO_QC";
  readonly sessionId: string | null;
  readonly sourceImportId: string | null;
  readonly sourceEpisodeIndex: number | null;
  readonly rolloutId: string;
  readonly dataPackageId: string | null;
  readonly status: "RISK" | "REJECT";
  readonly severity: "HIGH" | "CRITICAL";
  readonly startNs: string;
  readonly endNs: string;
  readonly message: string;
  readonly findingCount: number;
  readonly findingCodes: readonly string[];
  readonly topics: readonly string[];
  readonly reportSha256: string;
  readonly updatedAt: string;
}

export function useAutoQualityProblems(allowed = true) {
  const scope = useShellStore((state) => state.scope);
  return useQuery({
    queryKey: [
      "quality-problems",
      scope?.organizationId,
      scope?.projectId,
      scope?.regionCode,
    ],
    enabled: allowed && Boolean(scope?.projectId && scope.regionCode),
    staleTime: 15_000,
    queryFn: async ({ signal }) => {
      const path = `/projects/${encodeURIComponent(scope!.projectId!)}/regions/${encodeURIComponent(scope!.regionCode!)}/quality-problems`;
      const wire = parseWire(
        autoQualityProblemListSchema,
        await request<unknown>({ method: "GET", path, scope: scope!, signal }),
        {
          endpoint: "listAutoQualityProblems",
          schemaVersion: "auto-quality-problem-list/v1",
        },
      );
      return {
        items: wire.items.map(
          (item): AutoQualityProblem => ({
            id: item.id,
            source: item.source,
            sessionId: item.session_id,
            sourceImportId: item.source_import_id ?? null,
            sourceEpisodeIndex: item.source_episode_index ?? null,
            rolloutId: item.rollout_id,
            dataPackageId: item.data_package_id,
            status: item.status,
            severity: item.severity,
            startNs: item.start_ns,
            endNs: item.end_ns,
            message: item.message,
            findingCount: item.finding_count,
            findingCodes: item.finding_codes,
            topics: item.topics,
            reportSha256: item.report_sha256,
            updatedAt: item.updated_at,
          }),
        ),
        total: wire.total,
      };
    },
  });
}
