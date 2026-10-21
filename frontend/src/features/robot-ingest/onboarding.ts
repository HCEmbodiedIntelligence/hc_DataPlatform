import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import type { IngestScope } from "../../entities/data-source";
import { request } from "../../shared/api/http-client";
import {
  credentialSummarySchema,
  robotIdentityEnvelopeSchema,
  robotIdentitySchema,
} from "./model";

export const onboardingRobotSchema = z.object({
  robot_id: z.string(),
  display_name: z.string(),
  serial_no: z.string(),
  lifecycle_status: z.string(),
  model_version_id: z.string().nullable(),
  updated_at: z.string(),
  identity: robotIdentitySchema.nullable(),
  credentials: z.array(credentialSummarySchema),
  legacy_sources: z.array(
    z.object({
      source_id: z.string(),
      project_id: z.string(),
      region_code: z.string(),
      administrative_state: z.string(),
    }),
  ),
  upload_permission: z.enum(["NOT_ENABLED", "ENABLED", "DISABLED"]),
  credential_availability: z.enum(["NOT_ISSUED", "AVAILABLE", "UNAVAILABLE"]),
  connection_state: z.enum(["ONLINE", "OFFLINE", "UNVERIFIED", "UNKNOWN"]),
  last_communication_at: z.string().nullable(),
  pending: z.boolean(),
  faults: z.array(
    z.object({
      code: z.string(),
      reason: z.string(),
      occurred_at: z.string(),
      action: z.string(),
    }),
  ),
});
export type OnboardingRobot = z.infer<typeof onboardingRobotSchema>;
export const onboardingPageSchema = z.object({
  items: z.array(onboardingRobotSchema),
  total: z.number(),
  page: z.number(),
  page_size: z.number(),
  summary: z.object({
    total_count: z.number(),
    online_count: z.number().nullable(),
    pending_count: z.number(),
    attention_count: z.number(),
    unknown_count: z.number(),
  }),
  observed_at: z.string(),
  online_timeout_seconds: z.number(),
});
export interface RobotFilters {
  q: string;
  connection: string;
  permission: string;
  credential: string;
  segment: string;
  sort: string;
  page: number;
  page_size: number;
}
export const robotFilterDefaults: RobotFilters = {
  q: "",
  connection: "",
  permission: "",
  credential: "",
  segment: "",
  sort: "updated",
  page: 1,
  page_size: 20,
};
const root = (scope: IngestScope) =>
  `/projects/${encodeURIComponent(scope.projectId)}/robot-ingest`;
export function useRobotOnboarding(
  scope: IngestScope | null,
  filters: RobotFilters,
  enabled: boolean,
) {
  return useQuery({
    queryKey: [
      "robot-onboarding",
      scope?.organizationId,
      scope?.projectId,
      filters,
    ],
    enabled: Boolean(scope) && enabled,
    refetchInterval: 15_000,
    queryFn: async ({ signal }) => {
      if (!scope) throw new Error("请选择组织");
      return onboardingPageSchema.parse(
        await request({
          method: "GET",
          path: `${root(scope)}/onboarding`,
          scope,
          scopeMode: "explicit",
          signal,
          cache: "no-store",
          query: Object.fromEntries(
            Object.entries(filters).filter(([, value]) => value !== ""),
          ),
        }),
      );
    },
  });
}
export interface OnboardCommand {
  robot_id?: string;
  display_name?: string;
  serial_no?: string;
  model_version_id?: string;
  allowed_formats: string[];
  idempotency_key: string;
  generate_credential: boolean;
}
const resultSchema = z.object({
  robot: onboardingRobotSchema,
  identity: robotIdentityEnvelopeSchema,
});
export function useOnboardRobot(scope: IngestScope) {
  const client = useQueryClient();
  return useMutation({
    gcTime: 0,
    mutationFn: async (body: OnboardCommand) =>
      resultSchema.parse(
        await request({
          method: "POST",
          path: `${root(scope)}/onboarding`,
          body,
          scope,
          scopeMode: "explicit",
          cache: "no-store",
        }),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["robot-onboarding"] });
      void client.invalidateQueries({ queryKey: ["robot-ingest"] });
    },
  });
}
export function useSuccessfulVolume(
  scope: IngestScope | null,
  robotId?: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: ["successful-upload-volume", scope, robotId],
    enabled: Boolean(scope) && enabled,
    refetchInterval: 30_000,
    queryFn: async ({ signal }) => {
      if (!scope) throw new Error("请选择项目和 Region");
      return z
        .object({
          day: z.string(),
          timezone: z.string(),
          raw_bytes: z.number(),
          upload_count: z.number(),
          from_inclusive: z.string(),
          to_exclusive: z.string(),
        })
        .parse(
          await request({
            method: "GET",
            path: `${root(scope)}/successful-volume`,
            scope,
            scopeMode: "explicit",
            signal,
            cache: "no-store",
            query: {
              tz: "Asia/Shanghai",
              ...(robotId ? { robot_id: robotId } : {}),
            },
          }),
        );
    },
  });
}
