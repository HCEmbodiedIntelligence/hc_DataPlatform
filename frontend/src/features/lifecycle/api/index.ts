import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import type { Scope } from '../../../entities/scope';
import type { components } from '../../../shared/api/generated/storage';
import { createDomainError } from '../../../shared/api/domain-error';
import { request } from '../../../shared/api/http-client';
import { makeQueryKey } from '../../../shared/api/query-keys';
import { parseWire } from '../../../shared/api/validate';
import { useShellStore } from '../../../shared/scope/shell-store';

export type LifecyclePolicy = components['schemas']['LifecyclePolicy'];
export type LifecyclePolicyPage = components['schemas']['LifecyclePolicyPage'];
export type LifecycleAuditEvent = components['schemas']['LifecycleAuditEvent'];
export type LifecycleAuditPage = components['schemas']['LifecycleAuditPage'];
export type LifecyclePolicyCommand = components['schemas']['CreateLifecyclePolicy'];
export type BusinessCapacityCategory = components['schemas']['BusinessCapacityCategory'];
export type ObjectRole = components['schemas']['ObjectRole'];
export type LifecyclePolicyAction = components['schemas']['LifecyclePolicyAction'];
type LifecycleScope = Scope & { readonly projectId: string };

const id = z.string().min(1).max(256);
const instant = z.string().datetime({ offset: true });
const businessCategory = z.enum([
  'RAW',
  'ANNOTATION_COMPLETE',
  'PENDING_ANNOTATION',
  'ISSUE_DATA',
]);
const objectRole = z.enum([
  'RAW',
  'MANIFEST',
  'PUBLISHED_MANIFEST',
  'REBUILDABLE_DERIVATIVE',
  'OTHER',
]);
const policyAction = z.enum(['RETAIN', 'REVIEW_EXPIRATION', 'CLEAN_REBUILDABLE_CACHE']);
const policyState = z.enum(['DRAFT', 'ENABLED', 'PAUSED']);
const pageInfo = z.object({
  has_next_page: z.boolean(),
  has_previous_page: z.boolean(),
  start_cursor: z.string().nullable().optional(),
  end_cursor: z.string().nullable().optional(),
}).strict();

export const lifecyclePolicyWireSchema: z.ZodType<LifecyclePolicy> = z.object({
  policy_id: id,
  project_id: id,
  name: z.string().min(1).max(256),
  business_category: businessCategory,
  object_role: objectRole,
  action: policyAction,
  minimum_age_days: z.number().int().min(0).max(36_500),
  priority: z.number().int().min(0).max(10_000),
  state: policyState,
  version: z.number().int().positive(),
  etag: z.string().min(1),
  created_at: instant,
  updated_at: instant,
}).strict().superRefine((value, context) => {
  if (value.action === 'CLEAN_REBUILDABLE_CACHE' && value.object_role !== 'REBUILDABLE_DERIVATIVE') {
    context.addIssue({ code: 'custom', path: ['object_role'], message: 'cleanup target is not rebuildable' });
  }
});

export const lifecyclePolicyPageWireSchema: z.ZodType<LifecyclePolicyPage> = z.object({
  project_id: id,
  items: z.array(lifecyclePolicyWireSchema),
  page_info: pageInfo,
}).strict();

export const lifecycleAuditEventWireSchema: z.ZodType<LifecycleAuditEvent> = z.object({
  audit_id: id,
  project_id: id,
  policy_id: id,
  actor_id: id,
  action: z.string().min(1),
  before_digest: z.string().nullable().optional(),
  after_digest: z.string().nullable().optional(),
  request_id: id,
  details: z.record(z.string(), z.union([z.string(), z.number(), z.boolean(), z.null()])).optional(),
  occurred_at: instant,
}).strict();

export const lifecycleAuditPageWireSchema: z.ZodType<LifecycleAuditPage> = z.object({
  project_id: id,
  items: z.array(lifecycleAuditEventWireSchema),
  page_info: pageInfo,
}).strict();

function useProjectScope(): LifecycleScope | null {
  return useShellStore((state) =>
    state.scope?.projectId ? (state.scope as LifecycleScope) : null,
  );
}

function storageRoot(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/storage`;
}

function ensureProject<T extends { readonly project_id: string }>(value: T, projectId: string): T {
  const items = 'items' in value && Array.isArray(value.items)
    ? value.items as readonly { readonly project_id?: string }[]
    : [];
  if (
    value.project_id !== projectId ||
    items.some((item) => item.project_id !== projectId)
  ) {
    throw createDomainError({
      code: 'CONTRACT_MISMATCH',
      message: '生命周期响应与当前项目不匹配。',
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: null,
      retryable: false,
      httpStatus: null,
    });
  }
  return value;
}

export const lifecycleQueryKeys = {
  policies: (projectId: string, cursor: string | undefined, limit: number) =>
    makeQueryKey('lifecycle', 'policies', { projectId, cursor, limit }),
  audit: (projectId: string, cursor: string | undefined, limit: number) =>
    makeQueryKey('lifecycle', 'audit', { projectId, cursor, limit }),
} as const;

export function useLifecyclePolicies(cursor: string | undefined, limit: number, enabled = true) {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  return useQuery({
    queryKey: projectId
      ? lifecycleQueryKeys.policies(projectId, cursor, limit)
      : ['lifecycle', 'policies', 'disabled'],
    enabled: enabled && projectId !== null,
    queryFn: async ({ signal }) => {
      const endpoint = `${storageRoot(projectId!)}/lifecycle-policies`;
      const raw = await request<unknown>({ method: 'GET', path: endpoint, scope: scope!, query: { cursor, limit }, signal });
      return ensureProject(
        parseWire(lifecyclePolicyPageWireSchema, raw, { endpoint: 'listLifecyclePolicies' }),
        projectId!,
      );
    },
  });
}

export function useLifecycleAudit(cursor: string | undefined, limit: number, enabled = true) {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  return useQuery({
    queryKey: projectId
      ? lifecycleQueryKeys.audit(projectId, cursor, limit)
      : ['lifecycle', 'audit', 'disabled'],
    enabled: enabled && projectId !== null,
    queryFn: async ({ signal }) => {
      const endpoint = `${storageRoot(projectId!)}/lifecycle-audit`;
      const raw = await request<unknown>({ method: 'GET', path: endpoint, scope: scope!, query: { cursor, limit }, signal });
      return ensureProject(
        parseWire(lifecycleAuditPageWireSchema, raw, { endpoint: 'listLifecycleAudit' }),
        projectId!,
      );
    },
  });
}

function useInvalidateLifecycle() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ['lifecycle'] });
}

export interface CreatePolicyIntent {
  readonly command: LifecyclePolicyCommand;
  readonly idempotencyKey: string;
}

export function useCreateLifecyclePolicy() {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  const invalidate = useInvalidateLifecycle();
  return useMutation({
    mutationFn: async ({ command, idempotencyKey }: CreatePolicyIntent) => {
      const endpoint = `${storageRoot(projectId!)}/lifecycle-policies`;
      const raw = await request<unknown>({ method: 'POST', path: endpoint, scope: scope!, body: command, idempotencyKey });
      return ensureProject(parseWire(lifecyclePolicyWireSchema, raw, { endpoint: 'createLifecyclePolicy' }), projectId!);
    },
    onSuccess: invalidate,
  });
}

export interface UpdatePolicyIntent extends CreatePolicyIntent {
  readonly policyId: string;
  readonly etag: string;
}

export function useUpdateLifecyclePolicy() {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  const invalidate = useInvalidateLifecycle();
  return useMutation({
    mutationFn: async ({ policyId, etag, command, idempotencyKey }: UpdatePolicyIntent) => {
      const endpoint = `${storageRoot(projectId!)}/lifecycle-policies/${encodeURIComponent(policyId)}`;
      const raw = await request<unknown>({
        method: 'PUT', path: endpoint, scope: scope!, body: command, ifMatch: etag, idempotencyKey,
      });
      return ensureProject(parseWire(lifecyclePolicyWireSchema, raw, { endpoint: 'updateLifecyclePolicy' }), projectId!);
    },
    onSuccess: invalidate,
  });
}

export interface PolicyStateIntent {
  readonly policyId: string;
  readonly etag: string;
  readonly idempotencyKey: string;
}

function usePolicyStateMutation(action: 'enable' | 'pause') {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  const invalidate = useInvalidateLifecycle();
  return useMutation({
    mutationFn: async ({ policyId, etag, idempotencyKey }: PolicyStateIntent) => {
      const endpoint = `${storageRoot(projectId!)}/lifecycle-policies/${encodeURIComponent(policyId)}/${action}`;
      const raw = await request<unknown>({
        method: 'POST', path: endpoint, scope: scope!, ifMatch: etag, idempotencyKey,
      });
      return ensureProject(
        parseWire(lifecyclePolicyWireSchema, raw, {
          endpoint: action === 'enable' ? 'enableLifecyclePolicy' : 'pauseLifecyclePolicy',
        }),
        projectId!,
      );
    },
    onSuccess: invalidate,
  });
}

export function useEnableLifecyclePolicy() {
  return usePolicyStateMutation('enable');
}

export function usePauseLifecyclePolicy() {
  return usePolicyStateMutation('pause');
}

export function useDeleteLifecyclePolicy() {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  const invalidate = useInvalidateLifecycle();
  return useMutation({
    mutationFn: ({ policyId, etag, idempotencyKey }: PolicyStateIntent) => request<void>({
      method: 'DELETE',
      path: `${storageRoot(projectId!)}/lifecycle-policies/${encodeURIComponent(policyId)}`,
      scope: scope!,
      ifMatch: etag,
      idempotencyKey,
    }),
    onSuccess: invalidate,
  });
}
