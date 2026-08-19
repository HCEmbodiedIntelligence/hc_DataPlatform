import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import type { Scope } from '../../entities/scope';
import type { components } from '../../shared/api/generated/storage';
import { createDomainError } from '../../shared/api/domain-error';
import { request } from '../../shared/api/http-client';
import { makeQueryKey } from '../../shared/api/query-keys';
import { parseWire } from '../../shared/api/validate';

export type BusinessCapacityCategory = components['schemas']['BusinessCapacityCategory'];
export type CapacitySnapshot = components['schemas']['CapacitySnapshot'];
export type CapacityInventoryFact = components['schemas']['CapacityInventoryFact'];
export type CapacityInventoryPage = components['schemas']['CapacityInventoryPage'];
export type CapacityScope = Scope & { readonly projectId: string };

const byteString = z.string().regex(/^(0|[1-9]\d*)$/u);
const id = z.string().min(1).max(256);
const instant = z.string().datetime({ offset: true });
const businessCategory = z.enum([
  'RAW',
  'ANNOTATION_COMPLETE',
  'PENDING_ANNOTATION',
  'ISSUE_DATA',
]);
const disposition = z.enum(['PRIMARY', 'REPLICA', 'TEMPORARY']);
const objectRole = z.enum([
  'RAW',
  'MANIFEST',
  'PUBLISHED_MANIFEST',
  'REBUILDABLE_DERIVATIVE',
  'OTHER',
]);
const pageInfo = z.object({
  has_next_page: z.boolean(),
  has_previous_page: z.boolean(),
  start_cursor: z.string().nullable().optional(),
  end_cursor: z.string().nullable().optional(),
}).strict();

const categoryTotal = z.object({
  category: businessCategory,
  candidate_bytes: byteString,
  logical_object_count: z.number().int().nonnegative(),
}).strict();

export const capacitySnapshotWireSchema: z.ZodType<CapacitySnapshot> = z.object({
  snapshot_id: id,
  project_id: id,
  observed_at: instant,
  physical_total_bytes: byteString,
  physical_instance_count: z.number().int().nonnegative(),
  candidate_business_total_bytes: byteString,
  candidate_logical_object_count: z.number().int().nonnegative(),
  categories: z.array(categoryTotal).length(4),
  reconciliation: z.object({
    replica_overhead_bytes: byteString,
    replica_instance_count: z.number().int().nonnegative(),
    temporary_bytes: byteString,
    temporary_instance_count: z.number().int().nonnegative(),
    duplicate_inventory_rows_ignored: z.number().int().nonnegative(),
    formula: z.literal('physical_total_bytes = candidate_business_total_bytes + replica_overhead_bytes + temporary_bytes'),
    balanced: z.boolean(),
  }).strict(),
}).strict().superRefine((value, context) => {
  const expected: readonly BusinessCapacityCategory[] = [
    'RAW',
    'ANNOTATION_COMPLETE',
    'PENDING_ANNOTATION',
    'ISSUE_DATA',
  ];
  if (value.categories.some((entry, index) => entry.category !== expected[index])) {
    context.addIssue({ code: 'custom', path: ['categories'], message: 'fixed category order mismatch' });
  }
  const categoryBytes = value.categories.reduce((sum, entry) => sum + BigInt(entry.candidate_bytes), 0n);
  const candidateBytes = BigInt(value.candidate_business_total_bytes);
  const reconciledPhysical = candidateBytes
    + BigInt(value.reconciliation.replica_overhead_bytes)
    + BigInt(value.reconciliation.temporary_bytes);
  if (categoryBytes !== candidateBytes) {
    context.addIssue({ code: 'custom', path: ['categories'], message: 'candidate category total mismatch' });
  }
  if (!value.reconciliation.balanced || reconciledPhysical !== BigInt(value.physical_total_bytes)) {
    context.addIssue({ code: 'custom', path: ['reconciliation'], message: 'physical reconciliation mismatch' });
  }
});

export const capacityInventoryFactWireSchema: z.ZodType<CapacityInventoryFact> = z.object({
  snapshot_id: id,
  project_id: id,
  physical_instance_id: z.string().min(1).max(1024),
  logical_object_id: z.string().min(1).max(1024).nullable().optional(),
  physical_bytes: byteString,
  disposition,
  business_category: businessCategory.nullable().optional(),
  object_role: objectRole,
  observed_at: instant,
}).strict().superRefine((value, context) => {
  if (value.disposition === 'TEMPORARY') {
    if (value.business_category != null) {
      context.addIssue({ code: 'custom', path: ['business_category'], message: 'temporary fact entered a business category' });
    }
    return;
  }
  if (!value.logical_object_id || !value.business_category) {
    context.addIssue({ code: 'custom', message: 'primary and replica facts require logical identity and category' });
  }
});

export const capacityInventoryPageWireSchema: z.ZodType<CapacityInventoryPage> = z.object({
  snapshot_id: id,
  project_id: id,
  items: z.array(capacityInventoryFactWireSchema),
  page_info: pageInfo,
}).strict();

function storageRoot(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/storage`;
}

export async function getCapacitySnapshot(
  scope: CapacityScope,
  signal?: AbortSignal,
): Promise<CapacitySnapshot> {
  const endpoint = `${storageRoot(scope.projectId)}/capacity`;
  const raw = await request<unknown>({ method: 'GET', path: endpoint, scope, ...(signal ? { signal } : {}) });
  const result = parseWire(capacitySnapshotWireSchema, raw, { endpoint: 'getStorageCapacity' });
  if (result.project_id !== scope.projectId) {
    throw createDomainError({
      code: 'CONTRACT_MISMATCH',
      message: '容量快照与当前项目不匹配。',
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: null,
      retryable: false,
      httpStatus: null,
    });
  }
  return result;
}

export async function getCapacityInventory(
  scope: CapacityScope,
  snapshotId: string,
  cursor: string | undefined,
  limit: number,
  signal?: AbortSignal,
): Promise<CapacityInventoryPage> {
  const endpoint = `${storageRoot(scope.projectId)}/inventory`;
  const raw = await request<unknown>({
    method: 'GET',
    path: endpoint,
    scope,
    query: { snapshotId, cursor, limit },
    ...(signal ? { signal } : {}),
  });
  const result = parseWire(capacityInventoryPageWireSchema, raw, { endpoint: 'listStorageInventory' });
  if (result.project_id !== scope.projectId || result.snapshot_id !== snapshotId) {
    throw createDomainError({
      code: 'CONTRACT_MISMATCH',
      message: '容量清单与当前项目或固定快照不匹配。',
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: null,
      retryable: false,
      httpStatus: null,
    });
  }
  return result;
}

export const capacityQueryKeys = {
  snapshot: (projectId: string) => makeQueryKey('storage', 'capacity', { projectId }),
  inventory: (projectId: string, snapshotId: string, cursor: string | undefined, limit: number) =>
    makeQueryKey('storage', 'capacity-inventory', { projectId, snapshotId, cursor, limit }),
} as const;

export function useCapacitySnapshot(scope: CapacityScope | null, enabled: boolean) {
  return useQuery({
    queryKey: scope ? capacityQueryKeys.snapshot(scope.projectId) : ['storage', 'capacity', 'disabled'],
    queryFn: ({ signal }) => getCapacitySnapshot(scope!, signal),
    enabled: enabled && scope !== null,
    staleTime: 30_000,
  });
}

export function useCapacityInventory(
  scope: CapacityScope | null,
  snapshotId: string | undefined,
  cursor: string | undefined,
  limit: number,
  enabled: boolean,
) {
  return useQuery({
    queryKey: scope && snapshotId
      ? capacityQueryKeys.inventory(scope.projectId, snapshotId, cursor, limit)
      : ['storage', 'capacity-inventory', 'disabled'],
    queryFn: ({ signal }) => getCapacityInventory(scope!, snapshotId!, cursor, limit, signal),
    enabled: enabled && scope !== null && snapshotId !== undefined,
  });
}
