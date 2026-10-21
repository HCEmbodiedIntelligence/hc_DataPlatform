import { z } from "zod";
import { request } from "../../../shared/api/http-client";
import { parseWire } from "../../../shared/api/validate";
import { useScopedQuery } from "../../../shared/api/use-scoped-query";

const kindSchema = z.enum([
  "raw",
  "upload",
  "configuration",
  "component",
  "calibration",
  "source",
]);
export type DeletionKind = z.infer<typeof kindSchema>;
const referenceSchema = z.object({
  kind: kindSchema,
  id: z.string(),
  name: z.string(),
  status: z.string().nullable(),
  project_id: z.string().nullable(),
  region_code: z.string().nullable(),
  collection_task_id: z.string().nullable(),
  task_code: z.string().nullable(),
  task_name: z.string().nullable(),
  raw_source_id: z.string().nullable(),
  dataset_id: z.string().nullable(),
  scheme_id: z.string().nullable(),
  version: z.number().nullable(),
  origin_robot_id: z.string().nullable().optional(),
});
export type DeletionReference = z.infer<typeof referenceSchema>;
export const deletionCheckSchema = z.object({
  organization_id: z.string(),
  robot_id: z.string(),
  can_delete: z.boolean(),
  blockers: z.array(
    z.object({
      kind: kindSchema,
      label: z.string(),
      count: z.number(),
      resolution: z.string(),
    }),
  ),
  items: z.array(referenceSchema),
  total: z.number(),
  restricted_count: z.number(),
  offset: z.number(),
  limit: z.number(),
});

export function useRobotDeletionCheck(
  organizationId: string,
  robotId: string,
  kind: DeletionKind | undefined,
  offset: number,
) {
  return useScopedQuery(
    { organizationId },
    "robots",
    "deletion-check",
    { robotId, kind, offset },
    {
      staleTime: 0,
      refetchOnMount: "always",
      retry: false,
      queryFn: async ({ signal }) => {
        const raw = await request<unknown>({
          method: "GET",
          path: `/organizations/${encodeURIComponent(organizationId)}/robots/${encodeURIComponent(robotId)}/deletion-check`,
          scopeMode: "explicit",
          scope: { organizationId },
          cache: "no-store",
          signal,
          query: { ...(kind ? { kind } : {}), offset, limit: 3 },
        });
        return parseWire(
          deletionCheckSchema.extend({
            organization_id: z.literal(organizationId),
            robot_id: z.literal(robotId),
          }),
          raw,
          { endpoint: "getOrganizationRobotDeletionCheck" },
        );
      },
    },
  );
}
