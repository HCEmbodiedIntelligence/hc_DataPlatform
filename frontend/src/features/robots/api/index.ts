import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import type { Component } from "../../../entities/component";
import type { Robot } from "../../../entities/robot";
import { request } from "../../../shared/api/http-client";
import { makeQueryKey } from "../../../shared/api/query-keys";
import { parseWire } from "../../../shared/api/validate";
import { useShellStore } from "../../../shared/scope/shell-store";

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
const componentWireSchema = z
  .object({
    id,
    robot_id: id,
    parent_component_id: id.nullable(),
    component_model_id: id,
    component_type: z.string(),
    display_name: z.string(),
    serial_no: z.string(),
    lifecycle_status: z.string(),
    sort_order: z.string().regex(/^(0|[1-9]\d*)$/u),
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
export const robotsPageWireSchema = z
  .object({
    items: z.array(robotWireSchema),
    page_info: pageInfoSchema,
    snapshot_at: z.string().datetime({ offset: true }),
    scope: z.object({ project_id: id, region_code: z.string() }).strict(),
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
    scope: z.object({ project_id: id, region_code: z.string() }).strict(),
    request_id: id,
    contract_version: z.string(),
  })
  .strict();
export const componentsPageWireSchema = z
  .object({
    items: z.array(componentWireSchema),
    page_info: pageInfoSchema,
    snapshot_at: z.string().datetime({ offset: true }),
    topology_revision: z.string(),
    scope: z.object({ project_id: id, region_code: z.string() }).strict(),
    request_id: id,
    contract_version: z.string(),
  })
  .strict();

const frameReferenceWireSchema = z
  .object({
    id,
    name: z.string(),
    parent_frame: z.string().nullable(),
    source: z.string(),
    calibration_set_id: id,
    status: z.string(),
    valid_from: z.string().datetime({ offset: true }),
    valid_to: z.string().datetime({ offset: true }).nullable(),
  })
  .strict();
const channelReferenceWireSchema = z
  .object({
    id,
    canonical_path: z.string(),
    display_name: z.string(),
    modality: z.string(),
    schema_id: id,
    schema_version: z.string().regex(/^[1-9]\d*$/u),
    role: z.string(),
    unit: z.string().nullable(),
    frequency_hz: z.string().nullable(),
    frame_id: id.nullable(),
    clock_id: id.nullable(),
    status: z.string(),
  })
  .strict();
const relationScopeSchema = z
  .object({ project_id: id, region_code: z.string() })
  .strict();
const framePageWireSchema = z
  .object({
    items: z.array(frameReferenceWireSchema),
    page_info: pageInfoSchema,
    snapshot_at: z.string().datetime({ offset: true }),
    scope: relationScopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();
const channelPageWireSchema = z
  .object({
    items: z.array(channelReferenceWireSchema),
    page_info: pageInfoSchema,
    snapshot_at: z.string().datetime({ offset: true }),
    scope: relationScopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();
const maintenanceRecordWireSchema = z
  .object({
    id,
    robot_id: id,
    component_id: id.nullable(),
    event_type: z.string().min(1).max(64),
    previous_lifecycle_status: z.string().min(1).max(64).nullable(),
    lifecycle_status: z.string().min(1).max(64).nullable(),
    summary: z.string().min(1).max(256),
    details: z.string().min(1).max(4_000).nullable(),
    actor_id: id,
    occurred_at: z.string().datetime({ offset: true }),
  })
  .strict();
const maintenanceRecordPageWireSchema = z
  .object({
    items: z.array(maintenanceRecordWireSchema),
    page_info: pageInfoSchema,
    snapshot_at: z.string().datetime({ offset: true }),
    scope: relationScopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();
const maintenanceRecordEnvelopeWireSchema = z
  .object({
    data: maintenanceRecordWireSchema,
    scope: relationScopeSchema,
    request_id: id,
    contract_version: z.string(),
  })
  .strict();
const componentMutationWireSchema = z
  .object({
    data: z
      .object({
        component: componentWireSchema,
        robot_etag: z.string().min(1).max(256),
        topology_revision: z.string().min(1).max(256),
      })
      .strict(),
    scope: relationScopeSchema,
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

export function adaptRobot(
  wire: z.infer<typeof robotWireSchema>,
  extras: {
    readonly etag: string;
    readonly topologyRevision: string;
    readonly allowedActions: readonly string[];
    readonly binding: z.infer<
      typeof robotBootstrapWireSchema
    >["data"]["effective_model_binding"];
  },
): Robot {
  return {
    id: wire.id,
    displayName: wire.display_name,
    serialNo: wire.serial_no,
    lifecycle: robotLifecycles.includes(
      wire.lifecycle_status as (typeof robotLifecycles)[number],
    )
      ? (wire.lifecycle_status as (typeof robotLifecycles)[number])
      : "UNKNOWN",
    connectivity: {
      state: connections.includes(
        wire.connectivity.state as (typeof connections)[number],
      )
        ? (wire.connectivity.state as (typeof connections)[number])
        : "UNKNOWN",
      observedAt: wire.connectivity.observed_at,
      source: wire.connectivity.source,
      reasonCode: wire.connectivity.reason_code,
    },
    effectiveModelBinding: extras.binding
      ? {
          id: extras.binding.id,
          scopeType: extras.binding.scope_type,
          scopeId: extras.binding.scope_id,
          robotModelVersionId: extras.binding.robot_model_version_id,
          validFrom: extras.binding.valid_from,
          validTo: extras.binding.valid_to,
          etag: extras.binding.etag,
        }
      : null,
    etag: extras.etag,
    topologyRevision: extras.topologyRevision,
    allowedActions: extras.allowedActions,
  };
}

export function adaptComponent(
  wire: z.infer<typeof componentWireSchema>,
): Component {
  return {
    id: wire.id,
    robotId: wire.robot_id,
    parentComponentId: wire.parent_component_id,
    componentModelId: wire.component_model_id,
    componentType: wire.component_type,
    displayName: wire.display_name,
    serialNo: wire.serial_no,
    sortOrder: wire.sort_order as `${bigint}`,
    lifecycle: robotLifecycles.includes(
      wire.lifecycle_status as (typeof robotLifecycles)[number],
    )
      ? (wire.lifecycle_status as (typeof robotLifecycles)[number])
      : "UNKNOWN",
  };
}

function useRobotScope(): {
  readonly projectId: string | null;
  readonly regionCode: string | null;
} {
  const scope = useShellStore((state) => state.scope);
  return {
    projectId: scope?.projectId ?? null,
    regionCode: scope?.regionCode ?? null,
  };
}

function robotPath(projectId: string, regionCode: string, suffix = ""): string {
  return `/projects/${encodeURIComponent(projectId)}/regions/${encodeURIComponent(regionCode)}/robots${suffix}`;
}

function requireRobotScope(scope: ReturnType<typeof useRobotScope>): {
  readonly projectId: string;
  readonly regionCode: string;
} {
  if (!scope.projectId || !scope.regionCode) {
    throw new Error("当前会话没有可用的项目与区域范围。");
  }
  return { projectId: scope.projectId, regionCode: scope.regionCode };
}

export type RobotLifecycleStatus = (typeof robotLifecycles)[number];
export type RobotConnectivityState = (typeof connections)[number];
export type RobotMaintenanceRecord = z.infer<
  typeof maintenanceRecordWireSchema
>;

export interface RobotComponentMutation {
  readonly component: Component;
  readonly robotEtag: string;
  readonly topologyRevision: string;
}

export interface CreateRobotIntent {
  readonly displayName: string;
  readonly serialNo: string;
  readonly lifecycleStatus: Extract<
    RobotLifecycleStatus,
    "DRAFT" | "ACTIVE" | "MAINTENANCE"
  >;
  readonly connectivityState: RobotConnectivityState;
  readonly connectivitySource?: string;
  readonly connectivityReasonCode?: string;
  readonly idempotencyKey: string;
}

export interface UpdateRobotIntent {
  readonly robotId: string;
  readonly etag: string;
  readonly displayName?: string;
  readonly connectivityState?: RobotConnectivityState;
  readonly connectivitySource?: string;
  readonly connectivityReasonCode?: string;
  readonly idempotencyKey: string;
}

export interface TransitionRobotIntent {
  readonly robotId: string;
  readonly etag: string;
  readonly lifecycleStatus: Exclude<RobotLifecycleStatus, "DRAFT">;
  readonly reason: string;
  readonly idempotencyKey: string;
}

export interface CreateRobotMaintenanceRecordIntent {
  readonly robotId: string;
  readonly summary: string;
  readonly details?: string;
  readonly idempotencyKey: string;
}

export interface CreateRobotComponentIntent {
  readonly robotId: string;
  readonly etag: string;
  readonly parentComponentId?: string | null;
  readonly componentModelId: string;
  readonly componentType: string;
  readonly displayName: string;
  readonly serialNo: string;
  readonly lifecycleStatus: Extract<
    RobotLifecycleStatus,
    "DRAFT" | "ACTIVE" | "MAINTENANCE"
  >;
  readonly sortOrder: number;
  readonly idempotencyKey: string;
}

export interface UpdateRobotComponentIntent {
  readonly componentId: string;
  readonly etag: string;
  readonly parentComponentId?: string | null;
  readonly componentModelId?: string;
  readonly componentType?: string;
  readonly displayName?: string;
  readonly serialNo?: string;
  readonly sortOrder?: number;
  readonly idempotencyKey: string;
}

export interface TransitionRobotComponentIntent {
  readonly componentId: string;
  readonly etag: string;
  readonly lifecycleStatus: Exclude<RobotLifecycleStatus, "DRAFT">;
  readonly reason: string;
  readonly idempotencyKey: string;
}

export interface RobotScopeInput {
  readonly projectId: string;
  readonly regionCode: string;
}

function adaptRobotBootstrapEnvelope(raw: unknown, endpoint: string): Robot {
  const envelope = parseWire(robotBootstrapWireSchema, raw, { endpoint });
  return adaptRobot(envelope.data.robot, {
    etag: envelope.data.etag,
    topologyRevision: envelope.data.topology_revision,
    allowedActions: envelope.data.allowed_actions,
    binding: envelope.data.effective_model_binding,
  });
}

export async function createRobot(
  scope: RobotScopeInput,
  intent: CreateRobotIntent,
): Promise<Robot> {
  const raw = await request<unknown>({
    method: "POST",
    path: robotPath(scope.projectId, scope.regionCode),
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
  return adaptRobotBootstrapEnvelope(raw, "createRobot");
}

export async function updateRobot(
  scope: RobotScopeInput,
  intent: UpdateRobotIntent,
): Promise<Robot> {
  const raw = await request<unknown>({
    method: "PATCH",
    path: robotPath(
      scope.projectId,
      scope.regionCode,
      `/${encodeURIComponent(intent.robotId)}`,
    ),
    body: {
      ...(intent.displayName !== undefined
        ? { display_name: intent.displayName }
        : {}),
      ...(intent.connectivityState !== undefined
        ? { connectivity_state: intent.connectivityState }
        : {}),
      ...(intent.connectivitySource !== undefined
        ? { connectivity_source: intent.connectivitySource }
        : {}),
      ...(intent.connectivityReasonCode !== undefined
        ? { connectivity_reason_code: intent.connectivityReasonCode }
        : {}),
    },
    ifMatch: intent.etag,
    idempotencyKey: intent.idempotencyKey,
  });
  return adaptRobotBootstrapEnvelope(raw, "updateRobot");
}

export async function transitionRobotLifecycle(
  scope: RobotScopeInput,
  intent: TransitionRobotIntent,
): Promise<Robot> {
  const raw = await request<unknown>({
    method: "POST",
    path: robotPath(
      scope.projectId,
      scope.regionCode,
      `/${encodeURIComponent(intent.robotId)}:transition`,
    ),
    body: { lifecycle_status: intent.lifecycleStatus, reason: intent.reason },
    ifMatch: intent.etag,
    idempotencyKey: intent.idempotencyKey,
  });
  return adaptRobotBootstrapEnvelope(raw, "transitionRobotLifecycle");
}

export async function createRobotMaintenanceRecord(
  scope: RobotScopeInput,
  intent: CreateRobotMaintenanceRecordIntent,
): Promise<RobotMaintenanceRecord> {
  const raw = await request<unknown>({
    method: "POST",
    path: robotPath(
      scope.projectId,
      scope.regionCode,
      `/${encodeURIComponent(intent.robotId)}/maintenance-records`,
    ),
    body: {
      summary: intent.summary,
      ...(intent.details ? { details: intent.details } : {}),
    },
    idempotencyKey: intent.idempotencyKey,
  });
  return parseWire(maintenanceRecordEnvelopeWireSchema, raw, {
    endpoint: "createRobotMaintenanceRecord",
  }).data;
}

export async function listRobotMaintenanceRecords(
  scope: RobotScopeInput,
  robotId: string,
  signal?: AbortSignal,
) {
  const raw = await request<unknown>({
    method: "GET",
    path: robotPath(
      scope.projectId,
      scope.regionCode,
      `/${encodeURIComponent(robotId)}/maintenance-records`,
    ),
    ...(signal ? { signal } : {}),
  });
  return parseWire(maintenanceRecordPageWireSchema, raw, {
    endpoint: "listRobotMaintenanceRecords",
  });
}

function adaptRobotComponentMutation(
  raw: unknown,
  endpoint: string,
): RobotComponentMutation {
  const envelope = parseWire(componentMutationWireSchema, raw, { endpoint });
  return {
    component: adaptComponent(envelope.data.component),
    robotEtag: envelope.data.robot_etag,
    topologyRevision: envelope.data.topology_revision,
  };
}

export async function createRobotComponent(
  scope: RobotScopeInput,
  intent: CreateRobotComponentIntent,
): Promise<RobotComponentMutation> {
  const raw = await request<unknown>({
    method: "POST",
    path: robotPath(
      scope.projectId,
      scope.regionCode,
      `/${encodeURIComponent(intent.robotId)}/components`,
    ),
    body: {
      ...(intent.parentComponentId !== undefined
        ? { parent_component_id: intent.parentComponentId }
        : {}),
      component_model_id: intent.componentModelId,
      component_type: intent.componentType,
      display_name: intent.displayName,
      serial_no: intent.serialNo,
      lifecycle_status: intent.lifecycleStatus,
      sort_order: intent.sortOrder,
    },
    ifMatch: intent.etag,
    idempotencyKey: intent.idempotencyKey,
  });
  return adaptRobotComponentMutation(raw, "createRobotComponent");
}

export async function updateRobotComponent(
  scope: RobotScopeInput,
  intent: UpdateRobotComponentIntent,
): Promise<RobotComponentMutation> {
  const raw = await request<unknown>({
    method: "PATCH",
    path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/components/${encodeURIComponent(intent.componentId)}`,
    body: {
      ...(intent.parentComponentId !== undefined
        ? { parent_component_id: intent.parentComponentId }
        : {}),
      ...(intent.componentModelId !== undefined
        ? { component_model_id: intent.componentModelId }
        : {}),
      ...(intent.componentType !== undefined
        ? { component_type: intent.componentType }
        : {}),
      ...(intent.displayName !== undefined
        ? { display_name: intent.displayName }
        : {}),
      ...(intent.serialNo !== undefined ? { serial_no: intent.serialNo } : {}),
      ...(intent.sortOrder !== undefined
        ? { sort_order: intent.sortOrder }
        : {}),
    },
    ifMatch: intent.etag,
    idempotencyKey: intent.idempotencyKey,
  });
  return adaptRobotComponentMutation(raw, "updateRobotComponent");
}

export async function transitionRobotComponentLifecycle(
  scope: RobotScopeInput,
  intent: TransitionRobotComponentIntent,
): Promise<RobotComponentMutation> {
  const raw = await request<unknown>({
    method: "POST",
    path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/components/${encodeURIComponent(intent.componentId)}:transition`,
    body: { lifecycle_status: intent.lifecycleStatus, reason: intent.reason },
    ifMatch: intent.etag,
    idempotencyKey: intent.idempotencyKey,
  });
  return adaptRobotComponentMutation(raw, "transitionRobotComponentLifecycle");
}

function invalidateRobotQueries(
  client: ReturnType<typeof useQueryClient>,
): void {
  void client.invalidateQueries({ queryKey: ["robots"] });
}

export function useCreateRobot() {
  const scope = useRobotScope();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: CreateRobotIntent) =>
      createRobot(requireRobotScope(scope), intent),
    onSuccess: () => invalidateRobotQueries(client),
  });
}

function useRobotBootstrapMutation<
  TIntent extends {
    readonly robotId: string;
    readonly etag: string;
    readonly idempotencyKey: string;
  },
>(mutateRobot: (scope: RobotScopeInput, intent: TIntent) => Promise<Robot>) {
  const scope = useRobotScope();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: TIntent) =>
      mutateRobot(requireRobotScope(scope), intent),
    onSuccess: () => invalidateRobotQueries(client),
  });
}

export function useUpdateRobot() {
  return useRobotBootstrapMutation<UpdateRobotIntent>(updateRobot);
}

export function useTransitionRobotLifecycle() {
  return useRobotBootstrapMutation<TransitionRobotIntent>(
    transitionRobotLifecycle,
  );
}

export function useCreateRobotMaintenanceRecord() {
  const scope = useRobotScope();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: CreateRobotMaintenanceRecordIntent) =>
      createRobotMaintenanceRecord(requireRobotScope(scope), intent),
    onSuccess: () => invalidateRobotQueries(client),
  });
}

function updateRobotBootstrapAfterComponentMutation(
  client: ReturnType<typeof useQueryClient>,
  result: RobotComponentMutation,
): void {
  client.setQueryData<Robot | undefined>(
    makeQueryKey("robots", "bootstrap", result.component.robotId),
    (previous) =>
      previous
        ? {
            ...previous,
            etag: result.robotEtag,
            topologyRevision: result.topologyRevision,
          }
        : previous,
  );
  invalidateRobotQueries(client);
}

function useRobotComponentMutation<
  TIntent extends { readonly etag: string; readonly idempotencyKey: string },
>(
  mutateComponent: (
    scope: RobotScopeInput,
    intent: TIntent,
  ) => Promise<RobotComponentMutation>,
) {
  const scope = useRobotScope();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: TIntent) =>
      mutateComponent(requireRobotScope(scope), intent),
    onSuccess: (result) =>
      updateRobotBootstrapAfterComponentMutation(client, result),
  });
}

export function useCreateRobotComponent() {
  return useRobotComponentMutation<CreateRobotComponentIntent>(
    createRobotComponent,
  );
}

export function useUpdateRobotComponent() {
  return useRobotComponentMutation<UpdateRobotComponentIntent>(
    updateRobotComponent,
  );
}

export function useTransitionRobotComponentLifecycle() {
  return useRobotComponentMutation<TransitionRobotComponentIntent>(
    transitionRobotComponentLifecycle,
  );
}

export function useRobots(filters: Readonly<Record<string, string>> = {}) {
  const { projectId, regionCode } = useRobotScope();
  return useQuery({
    queryKey: makeQueryKey("robots", "list", filters),
    enabled: Boolean(projectId && regionCode),
    staleTime: 30_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: "GET",
        path: `/projects/${encodeURIComponent(projectId ?? "")}/regions/${encodeURIComponent(regionCode ?? "")}/robots`,
        query: filters,
        signal,
      });
      const page = parseWire(robotsPageWireSchema, raw, {
        endpoint: "listRobots",
      });
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
    },
  });
}

export function useRobotBootstrap(robotId: string | null) {
  const { projectId, regionCode } = useRobotScope();
  return useQuery({
    queryKey: makeQueryKey("robots", "bootstrap", robotId),
    enabled: Boolean(projectId && regionCode && robotId),
    staleTime: 15_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: "GET",
        path: `/projects/${encodeURIComponent(projectId ?? "")}/regions/${encodeURIComponent(regionCode ?? "")}/robots/${encodeURIComponent(robotId ?? "")}/bootstrap`,
        signal,
      });
      const envelope = parseWire(robotBootstrapWireSchema, raw, {
        endpoint: "getRobotBootstrap",
      });
      return adaptRobot(envelope.data.robot, {
        etag: envelope.data.etag,
        topologyRevision: envelope.data.topology_revision,
        allowedActions: envelope.data.allowed_actions,
        binding: envelope.data.effective_model_binding,
      });
    },
  });
}

export function useRobotComponents(robotId: string | null) {
  const { projectId, regionCode } = useRobotScope();
  return useQuery({
    queryKey: makeQueryKey("robots", "components", robotId),
    enabled: Boolean(projectId && regionCode && robotId),
    staleTime: 15_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: "GET",
        path: `/projects/${encodeURIComponent(projectId ?? "")}/regions/${encodeURIComponent(regionCode ?? "")}/robots/${encodeURIComponent(robotId ?? "")}/components`,
        signal,
      });
      const page = parseWire(componentsPageWireSchema, raw, {
        endpoint: "listRobotComponents",
      });
      return {
        items: page.items.map(adaptComponent),
        topologyRevision: page.topology_revision,
      };
    },
  });
}

export function useRobotMaintenanceRecords(robotId: string | null) {
  const { projectId, regionCode } = useRobotScope();
  return useQuery({
    queryKey: makeQueryKey("robots", "maintenance-records", robotId),
    enabled: Boolean(projectId && regionCode && robotId),
    staleTime: 15_000,
    queryFn: ({ signal }) =>
      listRobotMaintenanceRecords(
        { projectId: projectId ?? "", regionCode: regionCode ?? "" },
        robotId ?? "",
        signal,
      ),
  });
}

export function useComponentFrames(componentId: string | null) {
  const { projectId, regionCode } = useRobotScope();
  return useQuery({
    queryKey: makeQueryKey("robots", "component-frames", componentId),
    enabled: Boolean(projectId && regionCode && componentId),
    staleTime: 15_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: "GET",
        path: `/projects/${encodeURIComponent(projectId ?? "")}/regions/${encodeURIComponent(regionCode ?? "")}/components/${encodeURIComponent(componentId ?? "")}/frames`,
        signal,
      });
      return parseWire(framePageWireSchema, raw, {
        endpoint: "listComponentFrames",
      });
    },
  });
}

export function useComponentChannels(componentId: string | null) {
  const { projectId, regionCode } = useRobotScope();
  return useQuery({
    queryKey: makeQueryKey("robots", "component-channels", componentId),
    enabled: Boolean(projectId && regionCode && componentId),
    staleTime: 15_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: "GET",
        path: `/projects/${encodeURIComponent(projectId ?? "")}/regions/${encodeURIComponent(regionCode ?? "")}/components/${encodeURIComponent(componentId ?? "")}/channels`,
        signal,
      });
      return parseWire(channelPageWireSchema, raw, {
        endpoint: "listComponentChannels",
      });
    },
  });
}
