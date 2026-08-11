import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import type { LifecyclePolicy } from '../../../entities/lifecycle-policy';
import { request } from '../../../shared/api/http-client';
import { makeQueryKey } from '../../../shared/api/query-keys';
import { parseWire } from '../../../shared/api/validate';
import { useShellStore } from '../../../shared/scope/shell-store';
import { lifecyclePolicyStateMachine, simulationStateMachine, type SimulationState } from '../state-machines';

const uint64 = z.string().regex(/^(0|[1-9]\d*)$/u);
const id = z.string().min(1).max(128);
const blockedReasonWireSchema = z.object({
  code: id,
  label: z.string().max(200),
  blocking: z.boolean(),
  object_count: uint64.nullable(),
  physical_bytes: uint64.nullable(),
}).strict();
const allowedActionWireSchema = z.object({ action: id, allowed: z.boolean(), reason_code: id.nullable() }).strict();
const actionWireSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('TRANSITION'), after_days: z.number().int().min(1), target_class: z.enum(['IA', 'ARCHIVE']) }).strict(),
  z.object({ type: z.literal('DELETE_OBJECT'), after_days: z.number().int().min(1) }).strict(),
  z.object({ type: z.literal('ABORT_MULTIPART'), after_days: z.number().int().min(1) }).strict(),
]);
export const lifecyclePolicyWireSchema = z.object({
  id,
  name: z.string().min(1),
  version: uint64,
  etag: z.string().min(1),
  status: z.string().min(1),
  target: z.object({ scope_id: id, object_role: z.string().min(1) }).passthrough(),
  actions: z.array(actionWireSchema),
  simulation_input_hash: z.string().min(8),
  allowed_actions: z.array(allowedActionWireSchema),
  protected_reasons: z.array(blockedReasonWireSchema),
}).passthrough();

const scopeWireSchema = z.object({
  organization_id: id,
  project_id: id,
  region_code: z.string().min(1),
  timezone: z.string().min(1),
}).strict();
const simulationMeasureWireSchema = z.object({
  target_kind: z.enum(['STORAGE_OBJECTS', 'INCOMPLETE_MULTIPART']),
  object_role: z.string().nullable(),
  action: z.enum(['TRANSITION_IA', 'TRANSITION_ARCHIVE', 'DELETE', 'ABORT_MULTIPART']),
  logical_object_count: uint64,
  physical_object_count: uint64,
  physical_bytes: uint64,
}).strict();
const auditRefWireSchema = z.object({ audit_event_id: z.string().nullable(), request_id: id }).strict();
const simulationWireSchema = z.object({
  id,
  status: z.string(),
  mode: z.enum(['SAVED_POLICIES', 'DRAFT_POLICY']),
  input_hash: id,
  impact_digest: id,
  policy_set_version: id,
  policy_versions: z.array(z.object({ policy_id: id, version: id }).strict()),
  snapshot_id: id,
  snapshot_at: z.string().datetime({ offset: true }),
  created_at: z.string().datetime({ offset: true }),
  expires_at: z.string().datetime({ offset: true }),
  server_now: z.string().datetime({ offset: true }),
  candidates: z.array(simulationMeasureWireSchema),
  protected: z.array(blockedReasonWireSchema),
  unknown_summary: z.object({ object_count: uint64, physical_bytes: uint64.nullable() }).strict(),
  estimated_monthly_savings: z.union([
    z.object({ availability: z.literal('KNOWN'), amount_minor: z.string().regex(/^-?(0|[1-9]\d*)$/u), currency: z.string().length(3), kind: z.enum(['ACTUAL', 'FORECAST', 'ESTIMATE']), billing_period: z.string(), source_revision: id, formula_version: id, as_of: z.string().datetime({ offset: true }) }).strict(),
    z.object({ availability: z.literal('FORBIDDEN') }).strict(),
    z.object({ availability: z.enum(['COMPUTING', 'NOT_SETTLED', 'FAILED']), currency: z.string().nullable(), formula_version: z.string().nullable() }).strict(),
  ]),
  restore_sla_summary: z.array(z.object({ storage_class: z.enum(['IA', 'ARCHIVE']), method: z.enum(['STANDARD', 'EXPEDITED']), min_ready_seconds: uint64, max_ready_seconds: uint64 }).strict()),
  failure: z.object({ code: id, stage: id, retryable: z.boolean() }).strict().nullable(),
  job_id: id,
  audit_ref: auditRefWireSchema,
}).strict();
const asyncJobAcceptedWireSchema = z.object({
  id, status: z.string(), resource_type: id, resource_id: id,
  created_at: z.string().datetime({ offset: true }), updated_at: z.string().datetime({ offset: true }),
  attempt: z.number().int().nonnegative(), progress: z.number().min(0).max(1).nullable(),
  failure: z.object({ code: id, retryable: z.boolean() }).strict().nullable(),
}).strict();
export const lifecycleSimulationEnvelopeWireSchema = z.object({
  simulation: simulationWireSchema,
  job: asyncJobAcceptedWireSchema,
  scope: scopeWireSchema,
  request_id: id,
  contract_version: z.string(),
}).strict();

export interface LifecycleSimulationVm {
  readonly id: string;
  readonly jobId: string;
  readonly status: SimulationState;
  readonly inputHash: string;
  readonly impactDigest: string;
  readonly snapshotId: string;
  readonly policySetVersion: string;
  readonly policyVersions: Readonly<Record<string, string>>;
  readonly objectCount: string;
  readonly physicalBytes: string;
  readonly unknownObjectCount: bigint;
  readonly blockedReasons: readonly { readonly code: string; readonly message: string; readonly blocking: boolean }[];
}

export function adaptLifecycleSimulation(wire: z.infer<typeof simulationWireSchema>): LifecycleSimulationVm {
  return {
    id: wire.id,
    jobId: wire.job_id,
    status: simulationStateMachine.project(wire.status).value,
    inputHash: wire.input_hash,
    impactDigest: wire.impact_digest,
    snapshotId: wire.snapshot_id,
    policySetVersion: wire.policy_set_version,
    policyVersions: Object.fromEntries(wire.policy_versions.map((entry) => [entry.policy_id, entry.version])),
    objectCount: wire.candidates.reduce((sum, entry) => sum + BigInt(entry.physical_object_count), 0n).toString(),
    physicalBytes: wire.candidates.reduce((sum, entry) => sum + BigInt(entry.physical_bytes), 0n).toString(),
    unknownObjectCount: BigInt(wire.unknown_summary.object_count),
    blockedReasons: wire.protected.map((reason) => ({ code: reason.code, message: reason.label, blocking: reason.blocking })),
  };
}

export const lifecyclePageWireSchema = z.object({
  data: z.object({
    scope: scopeWireSchema,
    snapshot: z.object({ id, as_of: z.string().datetime({ offset: true }), status: z.string() }).strict(),
    policy_set_version: uint64,
    policy_versions: z.array(z.object({ policy_id: id, version: uint64 }).strict()),
    simulation_input_hash: z.string().min(8),
    policies: z.array(lifecyclePolicyWireSchema),
    impact: z.object({
      snapshot_id: id,
      policy_set_version: uint64,
      standard_bytes: uint64,
      to_ia_bytes: uint64,
      to_archive_bytes: uint64,
      reclaimable_bytes: uint64,
    }).passthrough(),
    capabilities: z.array(z.string()),
  }).passthrough(),
  scope: scopeWireSchema,
  request_id: id,
  contract_version: z.string().min(1),
}).strict();

export type LifecyclePageWire = z.infer<typeof lifecyclePageWireSchema>;

export interface LifecyclePageVm {
  readonly policies: readonly LifecyclePolicy[];
  readonly snapshotId: string;
  readonly snapshotAt: string;
  readonly snapshotStatus: string;
  readonly policySetVersion: string;
  readonly policyVersions: Readonly<Record<string, string>>;
  readonly simulationInputHash: string;
  readonly impact: {
    readonly standardBytes: string;
    readonly toIaBytes: string;
    readonly toArchiveBytes: string;
    readonly reclaimableBytes: string;
  };
  readonly capabilities: readonly string[];
  readonly requestId: string;
}

export function adaptLifecyclePolicy(wire: z.infer<typeof lifecyclePolicyWireSchema>): LifecyclePolicy {
  const status = lifecyclePolicyStateMachine.project(wire.status).value;
  const knownRoles = ['SOURCE', 'DERIVED', 'PREVIEW', 'EXPORT', 'INCOMPLETE_MULTIPART'] as const;
  const objectRole = knownRoles.includes(wire.target.object_role as (typeof knownRoles)[number])
    ? wire.target.object_role as (typeof knownRoles)[number]
    : 'UNKNOWN';
  return {
    id: wire.id,
    name: wire.name,
    version: wire.version as `${bigint}`,
    etag: wire.etag,
    status,
    objectRole,
    scopeId: wire.target.scope_id,
    actions: wire.actions.map((action) => action.type === 'TRANSITION'
      ? { type: action.type, afterDays: action.after_days, targetClass: action.target_class }
      : { type: action.type, afterDays: action.after_days }),
    simulationInputHash: wire.simulation_input_hash,
    allowedActions: wire.allowed_actions.filter((action) => action.allowed).map((action) => action.action),
    blockedReasons: wire.protected_reasons.map((reason) => ({
      code: reason.code,
      message: reason.label,
      blocking: reason.blocking,
      objectCount: reason.object_count as `${bigint}` | null,
      physicalBytes: reason.physical_bytes as `${bigint}` | null,
    })),
  };
}

export function adaptLifecyclePage(wire: LifecyclePageWire): LifecyclePageVm {
  if (wire.data.scope.project_id !== wire.scope.project_id || wire.data.impact.snapshot_id !== wire.data.snapshot.id || wire.data.impact.policy_set_version !== wire.data.policy_set_version) {
    throw new Error('Lifecycle scope/snapshot contract mismatch');
  }
  return {
    policies: wire.data.policies.map(adaptLifecyclePolicy),
    snapshotId: wire.data.snapshot.id,
    snapshotAt: wire.data.snapshot.as_of,
    snapshotStatus: wire.data.snapshot.status,
    policySetVersion: wire.data.policy_set_version,
    policyVersions: Object.fromEntries(wire.data.policy_versions.map((entry) => [entry.policy_id, entry.version])),
    simulationInputHash: wire.data.simulation_input_hash,
    impact: {
      standardBytes: wire.data.impact.standard_bytes,
      toIaBytes: wire.data.impact.to_ia_bytes,
      toArchiveBytes: wire.data.impact.to_archive_bytes,
      reclaimableBytes: wire.data.impact.reclaimable_bytes,
    },
    capabilities: wire.data.capabilities,
    requestId: wire.request_id,
  };
}

function useProjectScope(): { readonly projectId: string | null; readonly regionCode: string | null } {
  const scope = useShellStore((state) => state.scope);
  return { projectId: scope?.projectId ?? null, regionCode: scope?.regionCode ?? null };
}

export function useLifecyclePage() {
  const { projectId, regionCode } = useProjectScope();
  return useQuery({
    queryKey: makeQueryKey('lifecycle', 'page', { projectId, regionCode }),
    enabled: Boolean(projectId && regionCode),
    staleTime: 30_000,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({ method: 'GET', path: `/projects/${encodeURIComponent(projectId ?? '')}/storage/lifecycle-page`, query: { regionCode }, signal });
      return adaptLifecyclePage(parseWire(lifecyclePageWireSchema, raw, { endpoint: 'getLifecyclePage' }));
    },
  });
}

export interface SimulationIntent {
  readonly body: Readonly<Record<string, unknown>>;
  readonly idempotencyKey: string;
}

export function useCreateLifecycleSimulation() {
  const { projectId, regionCode } = useProjectScope();
  return useMutation({
    mutationFn: async ({ body, idempotencyKey }: SimulationIntent) => {
      const raw = await request<unknown>({
        method: 'POST',
        path: `/projects/${encodeURIComponent(projectId ?? '')}/storage/lifecycle-simulations`,
        query: { regionCode },
        body,
        idempotencyKey,
      });
      const envelope = parseWire(lifecycleSimulationEnvelopeWireSchema, raw, { endpoint: 'createLifecycleSimulation' });
      return adaptLifecycleSimulation(envelope.simulation);
    },
  });
}

export function useLifecycleSimulation(simulationId: string | null) {
  const { projectId, regionCode } = useProjectScope();
  return useQuery<LifecycleSimulationVm>({
    queryKey: makeQueryKey('lifecycle', 'simulation', simulationId),
    enabled: Boolean(projectId && regionCode && simulationId),
    refetchInterval: (query) => {
      const state = query.state.data?.status;
      return state && simulationStateMachine.isTerminal(state) ? false : 2_000;
    },
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({ method: 'GET', path: `/projects/${encodeURIComponent(projectId ?? '')}/storage/lifecycle-simulations/${encodeURIComponent(simulationId ?? '')}`, query: { regionCode }, signal });
      const envelope = parseWire(lifecycleSimulationEnvelopeWireSchema, raw, { endpoint: 'getLifecycleSimulation' });
      return adaptLifecycleSimulation(envelope.simulation);
    },
  });
}

export interface EnablePolicyIntent {
  readonly policyId: string;
  readonly etag: string;
  readonly body: Readonly<Record<string, unknown>>;
  readonly idempotencyKey: string;
}

export function useEnableLifecyclePolicy() {
  const client = useQueryClient();
  const { projectId, regionCode } = useProjectScope();
  return useMutation({
    mutationFn: ({ policyId, etag, body, idempotencyKey }: EnablePolicyIntent) => request<unknown>({
      method: 'POST',
      path: `/projects/${encodeURIComponent(projectId ?? '')}/storage/lifecycle-policies/${encodeURIComponent(policyId)}:enable`,
      query: { regionCode },
      body,
      ifMatch: etag,
      idempotencyKey,
    }),
    onSuccess: () => client.invalidateQueries({ queryKey: makeQueryKey('lifecycle', 'page', { projectId, regionCode }) }),
  });
}

export interface RestoreIntent {
  readonly body: Readonly<Record<string, unknown>>;
  readonly idempotencyKey?: string;
}

export function useCreateRestoreTask() {
  const { projectId, regionCode } = useProjectScope();
  return useMutation({
    mutationFn: ({ body, idempotencyKey }: RestoreIntent) => request<unknown>({
      method: 'POST',
      path: `/projects/${encodeURIComponent(projectId ?? '')}/storage/restore-tasks`,
      query: { regionCode },
      body,
      ...(idempotencyKey ? { idempotencyKey } : {}),
    }),
  });
}
