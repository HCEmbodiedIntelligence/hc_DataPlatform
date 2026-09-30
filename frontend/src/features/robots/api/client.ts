import { z } from "zod";
import type { Robot } from "../../../entities/robot";
import { request } from "../../../shared/api/http-client";
import { parseOrganizationWire } from "../../../shared/api/validate";

const id = z.string().min(1).max(128);

const connectionSchema = z
  .object({
    state: z.string(),
    observed_at: z.string().datetime({ offset: true }).nullable(),
    source: z.string().nullable(),
    reason_code: z.string().nullable(),
  })
  .strict();

const robotWireSchema = z
  .object({
    id,
    display_name: z.string(),
    serial_no: z.string(),
    lifecycle_status: z.string(),
    connectivity: connectionSchema,
  })
  .strict();

const pageInfoSchema = z
  .object({
    has_next_page: z.boolean(),
    has_previous_page: z.boolean(),
    start_cursor: z.string().nullable(),
    end_cursor: z.string().nullable(),
  })
  .strict();

const organizationScopeSchema = z.object({ organization_id: id }).strict();

export const robotsPageWireSchema = z
  .object({
    items: z.array(robotWireSchema),
    page_info: pageInfoSchema,
    snapshot_at: z.string().datetime({ offset: true }),
    scope: organizationScopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();

export const robotBootstrapWireSchema = z
  .object({
    data: z
      .object({
        robot: robotWireSchema,
        etag: z.string(),
        topology_revision: z.string(),
        effective_model_binding: z
          .object({
            id,
            scope_type: z.enum(["ROBOT_MODEL_DEFAULT", "ROBOT_INSTANCE"]),
            scope_id: id,
            robot_model_version_id: id,
            valid_from: z.string().datetime({ offset: true }),
            valid_to: z.string().datetime({ offset: true }).nullable(),
            etag: z.string(),
          })
          .nullable(),
        allowed_actions: z.array(z.string()),
      })
      .strict(),
    scope: organizationScopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();

const robotLifecycles = [
  "DRAFT",
  "ACTIVE",
  "MAINTENANCE",
  "DISABLED",
  "RETIRED",
] as const;

const connections = ["ONLINE", "OFFLINE", "DEGRADED"] as const;

function adaptRobotBootstrap(
  wire: z.infer<typeof robotBootstrapWireSchema>,
): Robot {
  const value = wire.data;
  return {
    id: value.robot.id,
    displayName: value.robot.display_name,
    serialNo: value.robot.serial_no,
    lifecycle: robotLifecycles.includes(
      value.robot.lifecycle_status as (typeof robotLifecycles)[number],
    )
      ? (value.robot.lifecycle_status as (typeof robotLifecycles)[number])
      : "UNKNOWN",
    connectivity: {
      state: connections.includes(
        value.robot.connectivity.state as (typeof connections)[number],
      )
        ? (value.robot.connectivity.state as (typeof connections)[number])
        : "UNKNOWN",
      observedAt: value.robot.connectivity.observed_at,
      source: value.robot.connectivity.source,
      reasonCode: value.robot.connectivity.reason_code,
    },
    effectiveModelBinding: value.effective_model_binding
      ? {
          id: value.effective_model_binding.id,
          scopeType: value.effective_model_binding.scope_type,
          scopeId: value.effective_model_binding.scope_id,
          robotModelVersionId:
            value.effective_model_binding.robot_model_version_id,
          validFrom: value.effective_model_binding.valid_from,
          validTo: value.effective_model_binding.valid_to,
          etag: value.effective_model_binding.etag,
        }
      : null,
    etag: value.etag,
    topologyRevision: value.topology_revision,
    allowedActions: value.allowed_actions,
  };
}

export interface CreateRobotIntent {
  readonly displayName: string;
  readonly serialNo: string;
  readonly lifecycleStatus: "DRAFT" | "ACTIVE" | "MAINTENANCE";
  readonly connectivityState: "ONLINE" | "OFFLINE" | "DEGRADED";
  readonly connectivitySource?: string;
  readonly connectivityReasonCode?: string;
  readonly idempotencyKey: string;
}

export async function createRobot(
  organizationId: string,
  intent: CreateRobotIntent,
): Promise<Robot> {
  const raw = await request<unknown>({
    method: "POST",
    path: `/organizations/${encodeURIComponent(organizationId)}/robots`,
    scopeMode: "explicit",
    scope: { organizationId },
    body: {
      display_name: intent.displayName,
      serial_no: intent.serialNo,
      lifecycle_status: intent.lifecycleStatus,
      connectivity_state: intent.connectivityState,
      ...(intent.connectivitySource
        ? { connectivity_source: intent.connectivitySource }
        : {}),
      ...(intent.connectivityReasonCode
        ? { connectivity_reason_code: intent.connectivityReasonCode }
        : {}),
    },
    idempotencyKey: intent.idempotencyKey,
  });
  return adaptRobotBootstrap(
    parseOrganizationWire(
      robotBootstrapWireSchema,
      raw,
      { endpoint: "createOrganizationRobot" },
      organizationId,
    ),
  );
}

export async function deleteRobot(
  organizationId: string,
  robotId: string,
): Promise<void> {
  await request<void>({
    method: "DELETE",
    path: `/organizations/${encodeURIComponent(organizationId)}/robots/${encodeURIComponent(robotId)}`,
    scopeMode: "explicit",
    scope: { organizationId },
    cache: "no-store",
  });
}

export interface UpdateRobotIntent {
  readonly robotId: string;
  readonly etag: string;
  readonly displayName: string;
}

export interface TransitionRobotLifecycleIntent {
  readonly robotId: string;
  readonly etag: string;
  readonly lifecycleStatus: Exclude<Robot["lifecycle"], "DRAFT" | "UNKNOWN">;
  readonly reason: string;
}

export async function getRobotBootstrap(
  organizationId: string,
  robotId: string,
  signal?: AbortSignal,
): Promise<Robot> {
  const raw = await request<unknown>({
    method: "GET",
    path: `/organizations/${encodeURIComponent(organizationId)}/robots/${encodeURIComponent(robotId)}/bootstrap`,
    scopeMode: "explicit",
    scope: { organizationId },
    ...(signal ? { signal } : {}),
  });
  return adaptRobotBootstrap(
    parseOrganizationWire(
      robotBootstrapWireSchema,
      raw,
      { endpoint: "getOrganizationRobotBootstrap" },
      organizationId,
    ),
  );
}

export async function updateRobot(
  organizationId: string,
  intent: UpdateRobotIntent,
): Promise<Robot> {
  const raw = await request<unknown>({
    method: "PATCH",
    path: `/organizations/${encodeURIComponent(organizationId)}/robots/${encodeURIComponent(intent.robotId)}`,
    scopeMode: "explicit",
    scope: { organizationId },
    body: { display_name: intent.displayName },
    ifMatch: intent.etag,
  });
  return adaptRobotBootstrap(
    parseOrganizationWire(
      robotBootstrapWireSchema,
      raw,
      { endpoint: "updateOrganizationRobot" },
      organizationId,
    ),
  );
}

export async function transitionRobotLifecycle(
  organizationId: string,
  intent: TransitionRobotLifecycleIntent,
): Promise<Robot> {
  const raw = await request<unknown>({
    method: "POST",
    path: `/organizations/${encodeURIComponent(organizationId)}/robots/${encodeURIComponent(intent.robotId)}/lifecycle`,
    scopeMode: "explicit",
    scope: { organizationId },
    body: { lifecycle_status: intent.lifecycleStatus, reason: intent.reason },
    ifMatch: intent.etag,
  });
  return adaptRobotBootstrap(
    parseOrganizationWire(
      robotBootstrapWireSchema,
      raw,
      { endpoint: "transitionOrganizationRobotLifecycle" },
      organizationId,
    ),
  );
}

export async function listRobots(
  organizationId: string,
  filters: Readonly<Record<string, string>> = {},
  signal?: AbortSignal,
) {
  const raw = await request<unknown>({
    method: "GET",
    path: `/organizations/${encodeURIComponent(organizationId)}/robots`,
    scopeMode: "explicit",
    scope: { organizationId },
    query: filters,
    ...(signal ? { signal } : {}),
  });
  const page = parseOrganizationWire(
    robotsPageWireSchema,
    raw,
    { endpoint: "listOrganizationRobots" },
    organizationId,
  );
  return {
    items: page.items.map((item) => ({
      id: item.id,
      displayName: item.display_name,
      serialNo: item.serial_no,
      lifecycle: robotLifecycles.includes(
        item.lifecycle_status as (typeof robotLifecycles)[number],
      )
        ? (item.lifecycle_status as (typeof robotLifecycles)[number])
        : ("UNKNOWN" as const),
      connectivity: connections.includes(
        item.connectivity.state as (typeof connections)[number],
      )
        ? (item.connectivity.state as (typeof connections)[number])
        : ("UNKNOWN" as const),
    })),
    pageInfo: page.page_info,
    snapshotAt: page.snapshot_at,
  };
}
