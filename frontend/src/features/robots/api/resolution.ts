import { z } from "zod";
import { useQueryClient } from "@tanstack/react-query";
import { request } from "../../../shared/api/http-client";
import { parseWire } from "../../../shared/api/validate";
import { useScopedQuery } from "../../../shared/api/use-scoped-query";
import { useScopedMutation } from "../../../shared/api/use-scoped-mutation";

const taskSchema = z.object({
  project_id: z.string(),
  region_code: z.string(),
  task_id: z.string(),
  task_code: z.string(),
  name: z.string(),
  etag: z.string(),
  raw_count: z.number(),
  other_robot_raw_count: z.number(),
  can_delete: z.boolean(),
  reason: z.string().nullable(),
  retry_key: z.string().nullable(),
});
export type ResolutionTask = z.infer<typeof taskSchema>;
export const resolutionPlanSchema = z.object({
  organization_id: z.string(),
  robot_id: z.string(),
  revision: z.string(),
  can_transfer: z.boolean(),
  transfer_reason: z.string().nullable(),
  targets: z.array(
    z.object({ id: z.string(), name: z.string(), serial_no: z.string() }),
  ),
  tasks: z.array(taskSchema),
  can_clean_config: z.boolean(),
  config_count: z.number(),
  cleanup_reason: z.string().nullable(),
});
export type ResolutionIntent = {
  action: "TRANSFER" | "DELETE_TASK" | "CLEAN_CONFIG";
  revision: string;
  target_robot_id?: string;
  project_id?: string;
  task_id?: string;
  key: string;
};
const root = (organizationId: string, robotId: string) =>
  `/organizations/${encodeURIComponent(organizationId)}/robots/${encodeURIComponent(robotId)}/deletion-actions`;

export function useRobotResolutionPlan(
  organizationId: string,
  robotId: string,
) {
  return useScopedQuery(
    { organizationId },
    "robots",
    "deletion-actions",
    { robotId },
    {
      staleTime: 0,
      retry: false,
      refetchOnMount: "always",
      queryFn: async ({ signal }) =>
        parseWire(
          resolutionPlanSchema.extend({
            organization_id: z.literal(organizationId),
            robot_id: z.literal(robotId),
          }),
          await request<unknown>({
            method: "GET",
            path: root(organizationId, robotId),
            scopeMode: "explicit",
            scope: { organizationId },
            signal,
            cache: "no-store",
          }),
          { endpoint: "getRobotDeletionActions" },
        ),
    },
  );
}

export function useResolveRobotAssociations(
  organizationId: string,
  robotId: string,
) {
  const client = useQueryClient();
  return useScopedMutation(
    { organizationId },
    async (scope, { key, ...body }: ResolutionIntent) =>
      parseWire(
        z.object({
          action: z.string(),
          robot_id: z.literal(robotId),
          message: z.string(),
        }),
        await request<unknown>({
          method: "POST",
          path: root(scope.organizationId, robotId),
          scopeMode: "explicit",
          scope,
          body,
          idempotencyKey: key,
        }),
        { endpoint: "resolveRobotDeletionAssociations" },
      ),
    // All affected pages (task, raw, directory and target instance) must reload.
    {
      onSuccess: () => {
        void client.invalidateQueries();
      },
    },
  );
}
