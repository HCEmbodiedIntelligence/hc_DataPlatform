import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import type { Scope } from "../../entities/scope";
import type { components } from "../../shared/api/generated/platform";
import { createDomainError } from "../../shared/api/domain-error";
import { request } from "../../shared/api/http-client";
import { makeQueryKey } from "../../shared/api/query-keys";
import { parseWire } from "../../shared/api/validate";
import { useShellStore } from "../../shared/scope/shell-store";

export type BusinessCapacityCategory =
  components["schemas"]["BusinessCapacityCategory"];
export type CapacitySnapshot = components["schemas"]["CapacitySnapshot"];
export type CapacityInventoryFact =
  components["schemas"]["CapacityInventoryFact"];
export type CapacityInventoryPage =
  components["schemas"]["CapacityInventoryPage"];
export type CapacityHistory = components["schemas"]["CapacityHistory"];
export type CapacityHistoryPoint =
  components["schemas"]["CapacityHistoryPoint"];
export type CapacityPortfolio = components["schemas"]["CapacityPortfolio"];
export type ManagedStorageObject =
  components["schemas"]["ManagedStorageObject"];
export type ManagedStorageObjectPage =
  components["schemas"]["ManagedStorageObjectPage"];
export type StorageObjectDownloadGrant =
  components["schemas"]["StorageObjectDownloadGrant"];
export type CapacityScope = Scope & { readonly projectId: string };

const byteString = z.string().regex(/^(0|[1-9]\d*)$/u);
const signedByteString = z.string().regex(/^-?(0|[1-9]\d*)$/u);
const id = z.string().min(1).max(256);
const instant = z.string().datetime({ offset: true });
const businessCategory = z.enum([
  "RAW",
  "ANNOTATION_COMPLETE",
  "PENDING_ANNOTATION",
  "ISSUE_DATA",
]);
const disposition = z.enum(["PRIMARY", "REPLICA", "TEMPORARY"]);
const objectRole = z.enum([
  "RAW",
  "MANIFEST",
  "PUBLISHED_MANIFEST",
  "REBUILDABLE_DERIVATIVE",
  "OTHER",
]);
const storageTier = z.enum(["HOT", "COLD", "ARCHIVE"]);
const storageObjectStatus = z.enum([
  "ACTIVE",
  "TRASHED",
  "ARCHIVED",
  "TRANSITIONING",
  "FAILED",
]);
const storageObjectAction = z.enum([
  "DOWNLOAD",
  "TRASH",
  "RESTORE",
  "ARCHIVE",
  "TRANSITION_TO_COLD",
  "PURGE",
]);
const pageInfo = z
  .object({
    has_next_page: z.boolean(),
    has_previous_page: z.boolean(),
    start_cursor: z.string().nullable().optional(),
    end_cursor: z.string().nullable().optional(),
  })
  .strict();

const categoryTotal = z
  .object({
    category: businessCategory,
    candidate_bytes: byteString,
    logical_object_count: z.number().int().nonnegative(),
  })
  .strict();

export const capacitySnapshotWireSchema: z.ZodType<CapacitySnapshot> = z
  .object({
    snapshot_id: id,
    project_id: id,
    observed_at: instant,
    physical_total_bytes: byteString,
    physical_instance_count: z.number().int().nonnegative(),
    candidate_business_total_bytes: byteString,
    candidate_logical_object_count: z.number().int().nonnegative(),
    categories: z.array(categoryTotal).length(4),
    reconciliation: z
      .object({
        replica_overhead_bytes: byteString,
        replica_instance_count: z.number().int().nonnegative(),
        temporary_bytes: byteString,
        temporary_instance_count: z.number().int().nonnegative(),
        duplicate_inventory_rows_ignored: z.number().int().nonnegative(),
        formula: z.literal(
          "physical_total_bytes = candidate_business_total_bytes + replica_overhead_bytes + temporary_bytes",
        ),
        balanced: z.boolean(),
      })
      .strict(),
  })
  .strict()
  .superRefine((value, context) => {
    const expected: readonly BusinessCapacityCategory[] = [
      "RAW",
      "ANNOTATION_COMPLETE",
      "PENDING_ANNOTATION",
      "ISSUE_DATA",
    ];
    if (
      value.categories.some(
        (entry, index) => entry.category !== expected[index],
      )
    ) {
      context.addIssue({
        code: "custom",
        path: ["categories"],
        message: "fixed category order mismatch",
      });
    }
    const categoryBytes = value.categories.reduce(
      (sum, entry) => sum + BigInt(entry.candidate_bytes),
      0n,
    );
    const candidateBytes = BigInt(value.candidate_business_total_bytes);
    const reconciledPhysical =
      candidateBytes +
      BigInt(value.reconciliation.replica_overhead_bytes) +
      BigInt(value.reconciliation.temporary_bytes);
    if (categoryBytes !== candidateBytes) {
      context.addIssue({
        code: "custom",
        path: ["categories"],
        message: "candidate category total mismatch",
      });
    }
    if (
      !value.reconciliation.balanced ||
      reconciledPhysical !== BigInt(value.physical_total_bytes)
    ) {
      context.addIssue({
        code: "custom",
        path: ["reconciliation"],
        message: "physical reconciliation mismatch",
      });
    }
  });

export const capacityInventoryFactWireSchema: z.ZodType<CapacityInventoryFact> =
  z
    .object({
      snapshot_id: id,
      project_id: id,
      physical_instance_id: z.string().min(1).max(1024),
      logical_object_id: z.string().min(1).max(1024).nullable().optional(),
      physical_bytes: byteString,
      disposition,
      business_category: businessCategory.nullable().optional(),
      object_role: objectRole,
      observed_at: instant,
    })
    .strict()
    .superRefine((value, context) => {
      if (value.disposition === "TEMPORARY") {
        if (value.business_category != null) {
          context.addIssue({
            code: "custom",
            path: ["business_category"],
            message: "temporary fact entered a business category",
          });
        }
        return;
      }
      if (!value.logical_object_id || !value.business_category) {
        context.addIssue({
          code: "custom",
          message:
            "primary and replica facts require logical identity and category",
        });
      }
    });

export const capacityInventoryPageWireSchema: z.ZodType<CapacityInventoryPage> =
  z
    .object({
      snapshot_id: id,
      project_id: id,
      items: z.array(capacityInventoryFactWireSchema),
      page_info: pageInfo,
    })
    .strict();

const capacityHistoryPointWireSchema: z.ZodType<CapacityHistoryPoint> = z
  .object({
    snapshot_id: id,
    observed_at: instant,
    physical_total_bytes: byteString,
    candidate_business_total_bytes: byteString,
  })
  .strict();

const capacityGrowthWireSchema = z
  .object({
    from_snapshot_id: id,
    from_observed_at: instant,
    to_snapshot_id: id,
    to_observed_at: instant,
    candidate_change_bytes: signedByteString,
    elapsed_seconds: z.number().int().positive(),
    candidate_bytes_per_day: signedByteString,
  })
  .strict();

export const capacityHistoryWireSchema: z.ZodType<CapacityHistory> = z
  .object({
    project_id: id,
    window_start: instant,
    window_end: instant,
    items: z.array(capacityHistoryPointWireSchema).max(31),
    growth: capacityGrowthWireSchema.nullable(),
  })
  .strict()
  .superRefine((value, context) => {
    for (let index = 1; index < value.items.length; index += 1) {
      const previous = value.items[index - 1];
      const current = value.items[index];
      if (!previous || !current) continue;
      if (
        Date.parse(previous.observed_at) > Date.parse(current.observed_at) ||
        (Date.parse(previous.observed_at) === Date.parse(current.observed_at) &&
          previous.snapshot_id >= current.snapshot_id)
      ) {
        context.addIssue({
          code: "custom",
          path: ["items", index],
          message: "history points must be chronological and unique",
        });
      }
    }
    if (value.growth && value.items.length >= 2) {
      const first = value.items[0];
      const last = value.items.at(-1);
      if (
        !first ||
        !last ||
        value.growth.from_snapshot_id !== first.snapshot_id ||
        value.growth.to_snapshot_id !== last.snapshot_id ||
        value.growth.from_observed_at !== first.observed_at ||
        value.growth.to_observed_at !== last.observed_at
      ) {
        context.addIssue({
          code: "custom",
          path: ["growth"],
          message: "growth endpoints mismatch history",
        });
      }
    }
    if (value.growth && value.items.length < 2) {
      context.addIssue({
        code: "custom",
        path: ["growth"],
        message: "single-point history cannot have growth",
      });
    }
    if (!value.growth && value.items.length >= 2) {
      context.addIssue({
        code: "custom",
        path: ["growth"],
        message: "multi-point history requires exact growth",
      });
    }
  });

const projectCapacitySummaryWireSchema = z
  .object({
    project_id: id,
    snapshot_id: id,
    observed_at: instant,
    physical_total_bytes: byteString,
    candidate_business_total_bytes: byteString,
    categories: z.array(categoryTotal),
    balanced: z.boolean(),
  })
  .strict()
  .superRefine((value, context) => {
    const expected: readonly BusinessCapacityCategory[] = [
      "RAW",
      "ANNOTATION_COMPLETE",
      "PENDING_ANNOTATION",
      "ISSUE_DATA",
    ];
    const categoryBytes = value.categories.reduce(
      (sum, category) => sum + BigInt(category.candidate_bytes),
      0n,
    );
    if (
      value.categories.length !== expected.length ||
      value.categories.some(
        (category, index) => category.category !== expected[index],
      ) ||
      categoryBytes !== BigInt(value.candidate_business_total_bytes) ||
      !value.balanced
    ) {
      context.addIssue({
        code: "custom",
        path: ["categories"],
        message: "project capacity summary is not reconciled",
      });
    }
  });

export const capacityPortfolioWireSchema: z.ZodType<CapacityPortfolio> = z
  .object({
    project_ids: z.array(id).min(1).max(100),
    physical_total_bytes: byteString,
    candidate_business_total_bytes: byteString,
    categories: z.array(categoryTotal),
    items: z.array(projectCapacitySummaryWireSchema),
  })
  .strict()
  .superRefine((value, context) => {
    const expected: readonly BusinessCapacityCategory[] = [
      "RAW",
      "ANNOTATION_COMPLETE",
      "PENDING_ANNOTATION",
      "ISSUE_DATA",
    ];
    const uniqueProjects = new Set(value.project_ids);
    const itemProjects = new Set(value.items.map((item) => item.project_id));
    const itemsStayInRequestedOrder = value.items.every((item, index) => {
      const previous = value.items[index - 1];
      return (
        value.project_ids.includes(item.project_id) &&
        (!previous ||
          value.project_ids.indexOf(previous.project_id) <
            value.project_ids.indexOf(item.project_id))
      );
    });
    if (
      uniqueProjects.size !== value.project_ids.length ||
      itemProjects.size !== value.items.length ||
      !itemsStayInRequestedOrder
    ) {
      context.addIssue({
        code: "custom",
        path: ["items"],
        message: "portfolio projects are duplicated, unrequested, or unordered",
      });
    }

    const physicalBytes = value.items.reduce(
      (sum, item) => sum + BigInt(item.physical_total_bytes),
      0n,
    );
    const businessBytes = value.items.reduce(
      (sum, item) => sum + BigInt(item.candidate_business_total_bytes),
      0n,
    );
    if (
      physicalBytes !== BigInt(value.physical_total_bytes) ||
      businessBytes !== BigInt(value.candidate_business_total_bytes)
    ) {
      context.addIssue({
        code: "custom",
        path: ["physical_total_bytes"],
        message: "portfolio totals do not match project summaries",
      });
    }

    if (
      value.categories.length !== expected.length ||
      value.categories.some(
        (category, index) => category.category !== expected[index],
      )
    ) {
      context.addIssue({
        code: "custom",
        path: ["categories"],
        message: "portfolio category order mismatch",
      });
      return;
    }
    value.categories.forEach((category, categoryIndex) => {
      const expectedBytes = value.items.reduce(
        (sum, item) =>
          sum + BigInt(item.categories[categoryIndex]?.candidate_bytes ?? "0"),
        0n,
      );
      const expectedObjects = value.items.reduce(
        (sum, item) =>
          sum + (item.categories[categoryIndex]?.logical_object_count ?? 0),
        0,
      );
      if (
        BigInt(category.candidate_bytes) !== expectedBytes ||
        category.logical_object_count !== expectedObjects
      ) {
        context.addIssue({
          code: "custom",
          path: ["categories", categoryIndex],
          message: "portfolio category total mismatch",
        });
      }
    });
  });

export const managedStorageObjectWireSchema: z.ZodType<ManagedStorageObject> = z
  .object({
    object_id: id,
    project_id: id,
    display_key: z.string().min(1).max(512),
    physical_bytes: byteString,
    checksum_sha256: z.string().regex(/^[0-9a-f]{64}$/u),
    business_category: businessCategory,
    object_role: objectRole,
    storage_tier: storageTier,
    status: storageObjectStatus,
    active_reference_count: z.number().int().nonnegative(),
    retention_until: instant.nullable().optional(),
    legal_hold: z.boolean(),
    governance_hold: z.boolean(),
    rebuild_source_id: z.string().max(1024).nullable().optional(),
    recoverable_until: instant.nullable().optional(),
    version: z.number().int().positive(),
    etag: z.string().min(1).max(256),
    allowed_actions: z.array(storageObjectAction),
    created_at: instant,
    updated_at: instant,
  })
  .strict();

export const managedStorageObjectPageWireSchema: z.ZodType<ManagedStorageObjectPage> =
  z
    .object({
      project_id: id,
      items: z.array(managedStorageObjectWireSchema),
      page_info: pageInfo,
    })
    .strict();

const storageObjectDownloadGrantWireSchema: z.ZodType<StorageObjectDownloadGrant> =
  z
    .object({
      object_id: id,
      url: z.string().min(1).max(8192),
      expires_at: instant,
      physical_bytes: byteString,
      checksum_sha256: z.string().regex(/^[0-9a-f]{64}$/u),
    })
    .strict();

function contractMismatch(message: string) {
  return createDomainError({
    code: "CONTRACT_MISMATCH",
    message,
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId: null,
    retryable: false,
    httpStatus: null,
  });
}

function storageRoot(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/storage`;
}

export async function getCapacitySnapshot(
  scope: CapacityScope,
  signal?: AbortSignal,
): Promise<CapacitySnapshot> {
  const endpoint = `${storageRoot(scope.projectId)}/capacity`;
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scope,
    ...(signal ? { signal } : {}),
  });
  const result = parseWire(capacitySnapshotWireSchema, raw, {
    endpoint: "getStorageCapacity",
  });
  if (result.project_id !== scope.projectId) {
    throw contractMismatch("容量快照与当前项目不匹配。");
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
    method: "GET",
    path: endpoint,
    scope,
    query: { snapshotId, cursor, limit },
    ...(signal ? { signal } : {}),
  });
  const result = parseWire(capacityInventoryPageWireSchema, raw, {
    endpoint: "listStorageInventory",
  });
  if (
    result.project_id !== scope.projectId ||
    result.snapshot_id !== snapshotId
  ) {
    throw contractMismatch("容量清单与当前项目或固定快照不匹配。");
  }
  return result;
}

export async function getCapacityHistory(
  scope: CapacityScope,
  window: Readonly<{ from?: string; to?: string }> = {},
  signal?: AbortSignal,
): Promise<CapacityHistory> {
  const endpoint = `${storageRoot(scope.projectId)}/capacity/history`;
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scope,
    query: window,
    ...(signal ? { signal } : {}),
  });
  const result = parseWire(capacityHistoryWireSchema, raw, {
    endpoint: "getStorageCapacityHistory",
  });
  if (result.project_id !== scope.projectId) {
    throw contractMismatch("容量历史与当前项目不匹配。");
  }
  return result;
}

export async function getCapacityPortfolio(
  scope: CapacityScope,
  projectIds: readonly string[],
  signal?: AbortSignal,
): Promise<CapacityPortfolio> {
  const raw = await request<unknown>({
    method: "GET",
    path: `${storageRoot(scope.projectId)}/capacity/portfolio`,
    scope,
    query: { projectId: projectIds },
    ...(signal ? { signal } : {}),
  });
  const result = parseWire(capacityPortfolioWireSchema, raw, {
    endpoint: "getStorageCapacityPortfolio",
  });
  if (
    result.project_ids.length !== projectIds.length ||
    result.project_ids.some(
      (projectId, index) => projectId !== projectIds[index],
    )
  ) {
    throw contractMismatch("跨项目容量响应与所选项目不匹配。");
  }
  return result;
}

export async function getManagedStorageObjects(
  scope: CapacityScope,
  cursor: string | undefined,
  limit: number,
  signal?: AbortSignal,
): Promise<ManagedStorageObjectPage> {
  const raw = await request<unknown>({
    method: "GET",
    path: `${storageRoot(scope.projectId)}/objects`,
    scope,
    query: { cursor, limit },
    ...(signal ? { signal } : {}),
  });
  const result = parseWire(managedStorageObjectPageWireSchema, raw, {
    endpoint: "listManagedStorageObjects",
  });
  if (
    result.project_id !== scope.projectId ||
    result.items.some((item) => item.project_id !== scope.projectId)
  ) {
    throw contractMismatch("存储对象响应与当前项目不匹配。");
  }
  return result;
}

export const capacityQueryKeys = {
  snapshot: (projectId: string) =>
    makeQueryKey("storage", "capacity", { projectId }),
  inventory: (
    projectId: string,
    snapshotId: string,
    cursor: string | undefined,
    limit: number,
  ) =>
    makeQueryKey("storage", "capacity-inventory", {
      projectId,
      snapshotId,
      cursor,
      limit,
    }),
  history: (
    projectId: string,
    from: string | undefined,
    to: string | undefined,
  ) => makeQueryKey("storage", "capacity-history", { projectId, from, to }),
  portfolio: (projectId: string, projectIds: readonly string[]) =>
    makeQueryKey("storage", "capacity-portfolio", {
      projectId,
      projects: projectIds.join(","),
    }),
  objects: (projectId: string, cursor: string | undefined, limit: number) =>
    makeQueryKey("storage", "managed-objects", {
      projectId,
      cursor,
      limit,
    }),
} as const;

export function useCapacitySnapshot(
  scope: CapacityScope | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: scope
      ? capacityQueryKeys.snapshot(scope.projectId)
      : ["storage", "capacity", "disabled"],
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
    queryKey:
      scope && snapshotId
        ? capacityQueryKeys.inventory(
            scope.projectId,
            snapshotId,
            cursor,
            limit,
          )
        : ["storage", "capacity-inventory", "disabled"],
    queryFn: ({ signal }) =>
      getCapacityInventory(scope!, snapshotId!, cursor, limit, signal),
    enabled: enabled && scope !== null && snapshotId !== undefined,
  });
}

export function useCapacityHistory(
  scope: CapacityScope | null,
  window: Readonly<{ from?: string; to?: string }>,
  enabled: boolean,
) {
  return useQuery({
    queryKey: scope
      ? capacityQueryKeys.history(scope.projectId, window.from, window.to)
      : ["storage", "capacity-history", "disabled"],
    queryFn: ({ signal }) => getCapacityHistory(scope!, window, signal),
    enabled: enabled && scope !== null,
    staleTime: 30_000,
  });
}

export function useCapacityPortfolio(
  scope: CapacityScope | null,
  projectIds: readonly string[],
  enabled: boolean,
) {
  return useQuery({
    queryKey: scope
      ? capacityQueryKeys.portfolio(scope.projectId, projectIds)
      : ["storage", "capacity-portfolio", "disabled"],
    queryFn: ({ signal }) => getCapacityPortfolio(scope!, projectIds, signal),
    enabled: enabled && scope !== null && projectIds.length > 0,
    staleTime: 30_000,
  });
}

export function useManagedStorageObjects(
  scope: CapacityScope | null,
  cursor: string | undefined,
  limit: number,
  enabled: boolean,
) {
  return useQuery({
    queryKey: scope
      ? capacityQueryKeys.objects(scope.projectId, cursor, limit)
      : ["storage", "managed-objects", "disabled"],
    queryFn: ({ signal }) =>
      getManagedStorageObjects(scope!, cursor, limit, signal),
    enabled: enabled && scope !== null,
  });
}

export interface StorageObjectMutationIntent {
  readonly objectId: string;
  readonly etag: string;
  readonly idempotencyKey: string;
  readonly reason: string;
}

function useInvalidateStorageObjects() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["storage"] });
}

export function useAuthorizeStorageObjectDownload() {
  const scope = useShellStore((state) =>
    state.scope?.projectId ? (state.scope as CapacityScope) : null,
  );
  return useMutation({
    mutationFn: async ({
      objectId,
      idempotencyKey,
    }: StorageObjectMutationIntent) => {
      const raw = await request<unknown>({
        method: "POST",
        path: `${storageRoot(scope!.projectId)}/objects/${encodeURIComponent(objectId)}:download`,
        scope: scope!,
        idempotencyKey,
        cache: "no-store",
      });
      const value = parseWire(storageObjectDownloadGrantWireSchema, raw, {
        endpoint: "authorizeManagedStorageObjectDownload",
      });
      if (value.object_id !== objectId) {
        throw contractMismatch("下载授权与所选存储对象不匹配。");
      }
      return value;
    },
  });
}

function useStorageObjectMutation(action: "trash" | "restore" | "transition") {
  const scope = useShellStore((state) =>
    state.scope?.projectId ? (state.scope as CapacityScope) : null,
  );
  const invalidate = useInvalidateStorageObjects();
  return useMutation({
    mutationFn: async (
      intent: StorageObjectMutationIntent & {
        readonly transitionAction?: "ARCHIVE" | "TRANSITION_TO_COLD";
      },
    ) => {
      const body =
        action === "transition"
          ? { action: intent.transitionAction, reason: intent.reason }
          : { reason: intent.reason };
      const raw = await request<unknown>({
        method: "POST",
        path: `${storageRoot(scope!.projectId)}/objects/${encodeURIComponent(intent.objectId)}:${action}`,
        scope: scope!,
        body,
        ifMatch: intent.etag,
        idempotencyKey: intent.idempotencyKey,
      });
      const value = parseWire(managedStorageObjectWireSchema, raw, {
        endpoint: `${action}ManagedStorageObject`,
      });
      if (
        value.project_id !== scope!.projectId ||
        value.object_id !== intent.objectId
      ) {
        throw contractMismatch("存储对象操作响应与当前对象不匹配。");
      }
      return value;
    },
    onSuccess: invalidate,
  });
}

export function useTrashStorageObject() {
  return useStorageObjectMutation("trash");
}

export function useRestoreStorageObject() {
  return useStorageObjectMutation("restore");
}

export function useTransitionStorageObject() {
  return useStorageObjectMutation("transition");
}
