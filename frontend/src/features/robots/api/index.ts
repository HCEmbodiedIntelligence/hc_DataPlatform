import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import type { Component } from '../../../entities/component';
import type { Robot } from '../../../entities/robot';
import { request } from '../../../shared/api/http-client';
import { makeQueryKey } from '../../../shared/api/query-keys';
import { parseWire } from '../../../shared/api/validate';
import { useShellStore } from '../../../shared/scope/shell-store';

const id = z.string().min(1).max(128);
const connectionSchema = z.object({ state: z.string(), observed_at: z.string().datetime({ offset: true }).nullable(), source: z.string().nullable(), reason_code: z.string().nullable() }).strict();
const robotWireSchema = z.object({
  id,
  display_name: z.string(),
  serial_no: z.string(),
  lifecycle_status: z.string(),
  connectivity: connectionSchema,
}).strict();
const componentWireSchema = z.object({
  id,
  robot_id: id,
  parent_component_id: id.nullable(),
  component_model_id: id,
  component_type: z.string(),
  display_name: z.string(),
  serial_no: z.string(),
  lifecycle_status: z.string(),
  sort_order: z.string().regex(/^(0|[1-9]\d*)$/u),
}).strict();
const pageInfoSchema = z.object({ has_next_page: z.boolean(), has_previous_page: z.boolean(), start_cursor: z.string().nullable(), end_cursor: z.string().nullable() }).strict();
export const robotsPageWireSchema = z.object({
  items: z.array(robotWireSchema), page_info: pageInfoSchema, snapshot_at: z.string().datetime({ offset: true }), scope: z.object({ project_id: id, region_code: z.string() }).strict(), request_id: id, contract_version: z.string(),
}).strict();
export const robotBootstrapWireSchema = z.object({
  data: z.object({
    robot: robotWireSchema,
    etag: z.string(),
    topology_revision: z.string(),
    effective_model_binding: z.object({ id, scope_type: z.enum(['ROBOT_MODEL_DEFAULT', 'ROBOT_INSTANCE']), scope_id: id, robot_model_version_id: id, valid_from: z.string().datetime({ offset: true }), valid_to: z.string().datetime({ offset: true }).nullable(), etag: z.string() }).nullable(),
    allowed_actions: z.array(z.string()),
  }).strict(),
  scope: z.object({ project_id: id, region_code: z.string() }).strict(), request_id: id, contract_version: z.string(),
}).strict();
export const componentsPageWireSchema = z.object({
  items: z.array(componentWireSchema), page_info: pageInfoSchema, snapshot_at: z.string().datetime({ offset: true }), topology_revision: z.string(), scope: z.object({ project_id: id, region_code: z.string() }).strict(), request_id: id, contract_version: z.string(),
}).strict();

const frameReferenceWireSchema = z.object({
  id, name: z.string(), parent_frame: z.string().nullable(), source: z.string(), calibration_set_id: id, status: z.string(),
  valid_from: z.string().datetime({ offset: true }), valid_to: z.string().datetime({ offset: true }).nullable(),
}).strict();
const channelReferenceWireSchema = z.object({
  id, canonical_path: z.string(), display_name: z.string(), modality: z.string(), schema_id: id, schema_version: z.string().regex(/^[1-9]\d*$/u),
  role: z.string(), unit: z.string().nullable(), frequency_hz: z.string().nullable(), frame_id: id.nullable(), clock_id: id.nullable(), status: z.string(),
}).strict();
const relationScopeSchema = z.object({ project_id: id, region_code: z.string() }).strict();
const framePageWireSchema = z.object({ items: z.array(frameReferenceWireSchema), page_info: pageInfoSchema, snapshot_at: z.string().datetime({ offset: true }), scope: relationScopeSchema, request_id: id, contract_version: z.string() }).strict();
const channelPageWireSchema = z.object({ items: z.array(channelReferenceWireSchema), page_info: pageInfoSchema, snapshot_at: z.string().datetime({ offset: true }), scope: relationScopeSchema, request_id: id, contract_version: z.string() }).strict();

const robotLifecycles = ['DRAFT', 'ACTIVE', 'MAINTENANCE', 'DISABLED', 'RETIRED'] as const;
const connections = ['ONLINE', 'OFFLINE', 'DEGRADED'] as const;

export function adaptRobot(wire: z.infer<typeof robotWireSchema>, extras: { readonly etag: string; readonly topologyRevision: string; readonly allowedActions: readonly string[]; readonly binding: z.infer<typeof robotBootstrapWireSchema>['data']['effective_model_binding'] }): Robot {
  return {
    id: wire.id,
    displayName: wire.display_name,
    serialNo: wire.serial_no,
    lifecycle: robotLifecycles.includes(wire.lifecycle_status as (typeof robotLifecycles)[number]) ? wire.lifecycle_status as (typeof robotLifecycles)[number] : 'UNKNOWN',
    connectivity: {
      state: connections.includes(wire.connectivity.state as (typeof connections)[number]) ? wire.connectivity.state as (typeof connections)[number] : 'UNKNOWN',
      observedAt: wire.connectivity.observed_at,
      source: wire.connectivity.source,
      reasonCode: wire.connectivity.reason_code,
    },
    effectiveModelBinding: extras.binding ? {
      id: extras.binding.id, scopeType: extras.binding.scope_type, scopeId: extras.binding.scope_id, robotModelVersionId: extras.binding.robot_model_version_id, validFrom: extras.binding.valid_from, validTo: extras.binding.valid_to, etag: extras.binding.etag,
    } : null,
    etag: extras.etag,
    topologyRevision: extras.topologyRevision,
    allowedActions: extras.allowedActions,
  };
}

export function adaptComponent(wire: z.infer<typeof componentWireSchema>): Component {
  return {
    id: wire.id,
    robotId: wire.robot_id,
    parentComponentId: wire.parent_component_id,
    componentModelId: wire.component_model_id,
    componentType: wire.component_type,
    displayName: wire.display_name,
    serialNo: wire.serial_no,
    sortOrder: wire.sort_order as `${bigint}`,
    lifecycle: robotLifecycles.includes(wire.lifecycle_status as (typeof robotLifecycles)[number]) ? wire.lifecycle_status as (typeof robotLifecycles)[number] : 'UNKNOWN',
  };
}

function useRobotScope(): { readonly projectId: string | null; readonly regionCode: string | null } {
  const scope = useShellStore((state) => state.scope);
  return { projectId: scope?.projectId ?? null, regionCode: scope?.regionCode ?? null };
}

export function useRobots(filters: Readonly<Record<string, string>> = {}) {
  const { projectId, regionCode } = useRobotScope();
  return useQuery({
    queryKey: makeQueryKey('robots', 'list', filters), enabled: Boolean(projectId && regionCode), staleTime: 30_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({ method: 'GET', path: `/projects/${encodeURIComponent(projectId ?? '')}/regions/${encodeURIComponent(regionCode ?? '')}/robots`, query: filters, signal });
      const page = parseWire(robotsPageWireSchema, raw, { endpoint: 'listRobots' });
      return {
        items: page.items.map((item) => ({
          id: item.id,
          displayName: item.display_name,
          serialNo: item.serial_no,
          lifecycle: robotLifecycles.includes(item.lifecycle_status as (typeof robotLifecycles)[number])
            ? item.lifecycle_status as (typeof robotLifecycles)[number]
            : 'UNKNOWN' as const,
          connectivity: connections.includes(item.connectivity.state as (typeof connections)[number])
            ? item.connectivity.state as (typeof connections)[number]
            : 'UNKNOWN' as const,
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
    queryKey: makeQueryKey('robots', 'bootstrap', robotId), enabled: Boolean(projectId && regionCode && robotId), staleTime: 15_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({ method: 'GET', path: `/projects/${encodeURIComponent(projectId ?? '')}/regions/${encodeURIComponent(regionCode ?? '')}/robots/${encodeURIComponent(robotId ?? '')}/bootstrap`, signal });
      const envelope = parseWire(robotBootstrapWireSchema, raw, { endpoint: 'getRobotBootstrap' });
      return adaptRobot(envelope.data.robot, { etag: envelope.data.etag, topologyRevision: envelope.data.topology_revision, allowedActions: envelope.data.allowed_actions, binding: envelope.data.effective_model_binding });
    },
  });
}

export function useRobotComponents(robotId: string | null) {
  const { projectId, regionCode } = useRobotScope();
  return useQuery({
    queryKey: makeQueryKey('robots', 'components', robotId), enabled: Boolean(projectId && regionCode && robotId), staleTime: 15_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({ method: 'GET', path: `/projects/${encodeURIComponent(projectId ?? '')}/regions/${encodeURIComponent(regionCode ?? '')}/robots/${encodeURIComponent(robotId ?? '')}/components`, signal });
      const page = parseWire(componentsPageWireSchema, raw, { endpoint: 'listRobotComponents' });
      return { items: page.items.map(adaptComponent), topologyRevision: page.topology_revision };
    },
  });
}

export function useComponentFrames(componentId: string | null) {
  const { projectId, regionCode } = useRobotScope();
  return useQuery({
    queryKey: makeQueryKey('robots', 'component-frames', componentId), enabled: Boolean(projectId && regionCode && componentId), staleTime: 15_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({ method: 'GET', path: `/projects/${encodeURIComponent(projectId ?? '')}/regions/${encodeURIComponent(regionCode ?? '')}/components/${encodeURIComponent(componentId ?? '')}/frames`, signal });
      return parseWire(framePageWireSchema, raw, { endpoint: 'listComponentFrames' });
    },
  });
}

export function useComponentChannels(componentId: string | null) {
  const { projectId, regionCode } = useRobotScope();
  return useQuery({
    queryKey: makeQueryKey('robots', 'component-channels', componentId), enabled: Boolean(projectId && regionCode && componentId), staleTime: 15_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({ method: 'GET', path: `/projects/${encodeURIComponent(projectId ?? '')}/regions/${encodeURIComponent(regionCode ?? '')}/components/${encodeURIComponent(componentId ?? '')}/channels`, signal });
      return parseWire(channelPageWireSchema, raw, { endpoint: 'listComponentChannels' });
    },
  });
}
