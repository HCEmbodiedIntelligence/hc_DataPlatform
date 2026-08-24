import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import type { Scope } from "../../../entities/scope";
import type { components } from "../../../shared/api/generated/platform";
import { createDomainError } from "../../../shared/api/domain-error";
import { request } from "../../../shared/api/http-client";
import { makeQueryKey } from "../../../shared/api/query-keys";
import { parseWire } from "../../../shared/api/validate";
import { useShellStore } from "../../../shared/scope/shell-store";

export type LifecyclePolicy = components["schemas"]["LifecyclePolicy"];
export type LifecyclePolicyPage = components["schemas"]["LifecyclePolicyPage"];
export type LifecycleAuditEvent = components["schemas"]["LifecycleAuditEvent"];
export type LifecycleAuditPage = components["schemas"]["LifecycleAuditPage"];
export type LifecyclePolicyCommand =
  components["schemas"]["CreateLifecyclePolicy"];
export type BusinessCapacityCategory =
  components["schemas"]["BusinessCapacityCategory"];
export type ObjectRole = components["schemas"]["ObjectRole"];
export type LifecyclePolicyAction =
  components["schemas"]["LifecyclePolicyAction"];
export type LifecycleExecution = components["schemas"]["LifecycleExecution"];
export type LifecycleExecutionPage =
  components["schemas"]["LifecycleExecutionPage"];
export type LifecycleExecutionLogPage =
  components["schemas"]["LifecycleExecutionLogPage"];
export type LifecycleSchedule = components["schemas"]["LifecycleSchedule"];
export type LifecycleSchedulePage =
  components["schemas"]["LifecycleSchedulePage"];
type LifecycleScope = Scope & { readonly projectId: string };

const id = z.string().min(1).max(256);
const instant = z.string().datetime({ offset: true });
const businessCategory = z.enum([
  "RAW",
  "ANNOTATION_COMPLETE",
  "PENDING_ANNOTATION",
  "ISSUE_DATA",
]);
const objectRole = z.enum([
  "RAW",
  "MANIFEST",
  "PUBLISHED_MANIFEST",
  "REBUILDABLE_DERIVATIVE",
  "OTHER",
]);
const policyAction = z.enum([
  "RETAIN",
  "REVIEW_EXPIRATION",
  "ARCHIVE",
  "TRANSITION_TO_COLD",
  "CLEAN_REBUILDABLE_CACHE",
]);
const policyState = z.enum(["DRAFT", "ENABLED", "PAUSED"]);
const executionStatus = z.enum([
  "DRY_RUN",
  "AWAITING_APPROVAL",
  "APPROVED",
  "QUEUED",
  "RUNNING",
  "BLOCKED",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
]);
const pageInfo = z
  .object({
    has_next_page: z.boolean(),
    has_previous_page: z.boolean(),
    start_cursor: z.string().nullable().optional(),
    end_cursor: z.string().nullable().optional(),
  })
  .strict();

export const lifecyclePolicyWireSchema: z.ZodType<LifecyclePolicy> = z
  .object({
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
  })
  .strict()
  .superRefine((value, context) => {
    if (
      value.action === "CLEAN_REBUILDABLE_CACHE" &&
      value.object_role !== "REBUILDABLE_DERIVATIVE"
    ) {
      context.addIssue({
        code: "custom",
        path: ["object_role"],
        message: "cleanup target is not rebuildable",
      });
    }
  });

export const lifecyclePolicyPageWireSchema: z.ZodType<LifecyclePolicyPage> = z
  .object({
    project_id: id,
    items: z.array(lifecyclePolicyWireSchema),
    page_info: pageInfo,
  })
  .strict();

export const lifecycleAuditEventWireSchema: z.ZodType<LifecycleAuditEvent> = z
  .object({
    audit_id: id,
    project_id: id,
    policy_id: id,
    actor_id: id,
    action: z.string().min(1),
    before_digest: z.string().nullable().optional(),
    after_digest: z.string().nullable().optional(),
    request_id: id,
    details: z
      .record(
        z.string(),
        z.union([z.string(), z.number(), z.boolean(), z.null()]),
      )
      .optional(),
    occurred_at: instant,
  })
  .strict();

export const lifecycleAuditPageWireSchema: z.ZodType<LifecycleAuditPage> = z
  .object({
    project_id: id,
    items: z.array(lifecycleAuditEventWireSchema),
    page_info: pageInfo,
  })
  .strict();

const lifecycleExecutionItemWireSchema = z
  .object({
    object_id: z.string().min(1),
    physical_instance_id: z.string().min(1),
    status: z.enum(["PENDING", "PROCESSED", "BLOCKED", "FAILED"]),
    attempt: z.number().int().nonnegative(),
    blocked_reasons: z.array(z.string()),
    last_error_code: z.string().nullable().optional(),
  })
  .strict();

export const lifecycleExecutionWireSchema: z.ZodType<LifecycleExecution> = z
  .object({
    execution_id: id,
    project_id: id,
    policy_id: id,
    policy_version: z.number().int().positive(),
    action: policyAction,
    status: executionStatus,
    dry_run: z.boolean(),
    plan_hash: z.string().regex(/^[0-9a-f]{64}$/u),
    approval_id: z.string().nullable().optional(),
    requested_by: id,
    approved_by: z.string().nullable().optional(),
    total_items: z.number().int().nonnegative(),
    processed_items: z.number().int().nonnegative(),
    blocked_items: z.number().int().nonnegative(),
    failed_items: z.number().int().nonnegative(),
    next_batch: z.number().int().nonnegative(),
    items: z.array(lifecycleExecutionItemWireSchema),
    created_at: instant,
    updated_at: instant,
  })
  .strict();

const lifecycleExecutionPageWireSchema: z.ZodType<LifecycleExecutionPage> = z
  .object({
    project_id: id,
    items: z.array(lifecycleExecutionWireSchema),
    page_info: pageInfo,
  })
  .strict();

const lifecycleExecutionLogWireSchema = z
  .object({
    sequence: z.number().int().positive(),
    project_id: id,
    execution_id: id,
    level: z.enum(["INFO", "WARNING", "ERROR"]),
    event: z.string().min(1),
    details: z
      .record(
        z.string(),
        z.union([z.string(), z.number(), z.boolean(), z.null()]),
      )
      .optional(),
    occurred_at: instant,
  })
  .strict();

const lifecycleExecutionLogPageWireSchema: z.ZodType<LifecycleExecutionLogPage> =
  z
    .object({
      project_id: id,
      execution_id: id,
      items: z.array(lifecycleExecutionLogWireSchema),
      page_info: pageInfo,
    })
    .strict();

export const lifecycleScheduleWireSchema: z.ZodType<LifecycleSchedule> = z
  .object({
    schedule_id: id,
    project_id: id,
    policy_id: id,
    interval_seconds: z.number().int().min(300).max(2_678_400),
    enabled: z.boolean(),
    next_run_at: instant,
    last_execution_id: z.string().nullable().optional(),
    version: z.number().int().positive(),
    etag: z.string().min(1),
    created_at: instant,
    updated_at: instant,
  })
  .strict();

const lifecycleSchedulePageWireSchema: z.ZodType<LifecycleSchedulePage> = z
  .object({
    project_id: id,
    items: z.array(lifecycleScheduleWireSchema),
    page_info: pageInfo,
  })
  .strict();

function useProjectScope(): LifecycleScope | null {
  return useShellStore((state) =>
    state.scope?.projectId ? (state.scope as LifecycleScope) : null,
  );
}

function storageRoot(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/storage`;
}

function ensureProject<T extends { readonly project_id: string }>(
  value: T,
  projectId: string,
): T {
  const items =
    "items" in value && Array.isArray(value.items)
      ? (value.items as readonly Record<string, unknown>[])
      : [];
  if (
    value.project_id !== projectId ||
    items.some((item) => "project_id" in item && item.project_id !== projectId)
  ) {
    throw createDomainError({
      code: "CONTRACT_MISMATCH",
      message: "生命周期响应与当前项目不匹配。",
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
    makeQueryKey("lifecycle", "policies", { projectId, cursor, limit }),
  audit: (projectId: string, cursor: string | undefined, limit: number) =>
    makeQueryKey("lifecycle", "audit", { projectId, cursor, limit }),
  executions: (projectId: string, cursor: string | undefined, limit: number) =>
    makeQueryKey("lifecycle", "executions", { projectId, cursor, limit }),
  executionLogs: (
    projectId: string,
    executionId: string,
    cursor: string | undefined,
    limit: number,
  ) =>
    makeQueryKey("lifecycle", "execution-logs", {
      projectId,
      executionId,
      cursor,
      limit,
    }),
  schedules: (projectId: string, cursor: string | undefined, limit: number) =>
    makeQueryKey("lifecycle", "schedules", { projectId, cursor, limit }),
} as const;

export function useLifecyclePolicies(
  cursor: string | undefined,
  limit: number,
  enabled = true,
) {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  return useQuery({
    queryKey: projectId
      ? lifecycleQueryKeys.policies(projectId, cursor, limit)
      : ["lifecycle", "policies", "disabled"],
    enabled: enabled && projectId !== null,
    queryFn: async ({ signal }) => {
      const endpoint = `${storageRoot(projectId!)}/lifecycle-policies`;
      const raw = await request<unknown>({
        method: "GET",
        path: endpoint,
        scope: scope!,
        query: { cursor, limit },
        signal,
      });
      return ensureProject(
        parseWire(lifecyclePolicyPageWireSchema, raw, {
          endpoint: "listLifecyclePolicies",
        }),
        projectId!,
      );
    },
  });
}

export function useLifecycleAudit(
  cursor: string | undefined,
  limit: number,
  enabled = true,
) {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  return useQuery({
    queryKey: projectId
      ? lifecycleQueryKeys.audit(projectId, cursor, limit)
      : ["lifecycle", "audit", "disabled"],
    enabled: enabled && projectId !== null,
    queryFn: async ({ signal }) => {
      const endpoint = `${storageRoot(projectId!)}/lifecycle-audit`;
      const raw = await request<unknown>({
        method: "GET",
        path: endpoint,
        scope: scope!,
        query: { cursor, limit },
        signal,
      });
      return ensureProject(
        parseWire(lifecycleAuditPageWireSchema, raw, {
          endpoint: "listLifecycleAudit",
        }),
        projectId!,
      );
    },
  });
}

export function useLifecycleExecutions(
  cursor: string | undefined,
  limit: number,
  enabled = true,
) {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  return useQuery({
    queryKey: projectId
      ? lifecycleQueryKeys.executions(projectId, cursor, limit)
      : ["lifecycle", "executions", "disabled"],
    enabled: enabled && projectId !== null,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: "GET",
        path: `${storageRoot(projectId!)}/lifecycle-executions`,
        scope: scope!,
        query: { cursor, limit },
        signal,
      });
      return ensureProject(
        parseWire(lifecycleExecutionPageWireSchema, raw, {
          endpoint: "listLifecycleExecutions",
        }),
        projectId!,
      );
    },
    refetchInterval: 5_000,
  });
}

export function useLifecycleExecutionLogs(
  executionId: string | null,
  cursor: string | undefined,
  limit: number,
  enabled = true,
) {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  return useQuery({
    queryKey:
      projectId && executionId
        ? lifecycleQueryKeys.executionLogs(
            projectId,
            executionId,
            cursor,
            limit,
          )
        : ["lifecycle", "execution-logs", "disabled"],
    enabled: enabled && projectId !== null && executionId !== null,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: "GET",
        path: `${storageRoot(projectId!)}/lifecycle-executions/${encodeURIComponent(executionId!)}/logs`,
        scope: scope!,
        query: { cursor, limit },
        signal,
      });
      const value = parseWire(lifecycleExecutionLogPageWireSchema, raw, {
        endpoint: "listLifecycleExecutionLogs",
      });
      if (
        value.project_id !== projectId ||
        value.execution_id !== executionId
      ) {
        throw createDomainError({
          code: "CONTRACT_MISMATCH",
          message: "执行日志与当前执行不匹配。",
          fieldErrors: [],
          operationErrors: [],
          blockedReasons: [],
          requestId: null,
          retryable: false,
          httpStatus: null,
        });
      }
      return value;
    },
    refetchInterval: 5_000,
  });
}

export function useLifecycleSchedules(
  cursor: string | undefined,
  limit: number,
  enabled = true,
) {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  return useQuery({
    queryKey: projectId
      ? lifecycleQueryKeys.schedules(projectId, cursor, limit)
      : ["lifecycle", "schedules", "disabled"],
    enabled: enabled && projectId !== null,
    queryFn: async ({ signal }) => {
      const raw = await request<unknown>({
        method: "GET",
        path: `${storageRoot(projectId!)}/lifecycle-schedules`,
        scope: scope!,
        query: { cursor, limit },
        signal,
      });
      return ensureProject(
        parseWire(lifecycleSchedulePageWireSchema, raw, {
          endpoint: "listLifecycleSchedules",
        }),
        projectId!,
      );
    },
  });
}

function useInvalidateLifecycle() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["lifecycle"] });
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
      const raw = await request<unknown>({
        method: "POST",
        path: endpoint,
        scope: scope!,
        body: command,
        idempotencyKey,
      });
      return ensureProject(
        parseWire(lifecyclePolicyWireSchema, raw, {
          endpoint: "createLifecyclePolicy",
        }),
        projectId!,
      );
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
    mutationFn: async ({
      policyId,
      etag,
      command,
      idempotencyKey,
    }: UpdatePolicyIntent) => {
      const endpoint = `${storageRoot(projectId!)}/lifecycle-policies/${encodeURIComponent(policyId)}`;
      const raw = await request<unknown>({
        method: "PUT",
        path: endpoint,
        scope: scope!,
        body: command,
        ifMatch: etag,
        idempotencyKey,
      });
      return ensureProject(
        parseWire(lifecyclePolicyWireSchema, raw, {
          endpoint: "updateLifecyclePolicy",
        }),
        projectId!,
      );
    },
    onSuccess: invalidate,
  });
}

export interface PolicyStateIntent {
  readonly policyId: string;
  readonly etag: string;
  readonly idempotencyKey: string;
}

function usePolicyStateMutation(action: "enable" | "pause") {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  const invalidate = useInvalidateLifecycle();
  return useMutation({
    mutationFn: async ({
      policyId,
      etag,
      idempotencyKey,
    }: PolicyStateIntent) => {
      const endpoint = `${storageRoot(projectId!)}/lifecycle-policies/${encodeURIComponent(policyId)}/${action}`;
      const raw = await request<unknown>({
        method: "POST",
        path: endpoint,
        scope: scope!,
        ifMatch: etag,
        idempotencyKey,
      });
      return ensureProject(
        parseWire(lifecyclePolicyWireSchema, raw, {
          endpoint:
            action === "enable"
              ? "enableLifecyclePolicy"
              : "pauseLifecyclePolicy",
        }),
        projectId!,
      );
    },
    onSuccess: invalidate,
  });
}

export function useEnableLifecyclePolicy() {
  return usePolicyStateMutation("enable");
}

export function usePauseLifecyclePolicy() {
  return usePolicyStateMutation("pause");
}

export function useDeleteLifecyclePolicy() {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  const invalidate = useInvalidateLifecycle();
  return useMutation({
    mutationFn: ({ policyId, etag, idempotencyKey }: PolicyStateIntent) =>
      request<void>({
        method: "DELETE",
        path: `${storageRoot(projectId!)}/lifecycle-policies/${encodeURIComponent(policyId)}`,
        scope: scope!,
        ifMatch: etag,
        idempotencyKey,
      }),
    onSuccess: invalidate,
  });
}

export interface DryRunIntent {
  readonly policyId: string;
  readonly policyEtag: string;
  readonly idempotencyKey: string;
}

function useExecutionMutation(
  action: "approve" | "start" | "cancel" | "retry",
) {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  const invalidate = useInvalidateLifecycle();
  return useMutation({
    mutationFn: async (intent: {
      readonly executionId: string;
      readonly idempotencyKey: string;
      readonly planHash: string;
      readonly approvalId?: string;
      readonly reason?: string;
      readonly justification?: string;
    }) => {
      const body =
        action === "approve"
          ? {
              plan_hash: intent.planHash,
              justification: intent.justification,
            }
          : action === "start"
            ? { plan_hash: intent.planHash, approval_id: intent.approvalId }
            : action === "retry"
              ? { plan_hash: intent.planHash, reason: intent.reason }
              : { reason: intent.reason };
      const raw = await request<unknown>({
        method: "POST",
        path: `${storageRoot(projectId!)}/lifecycle-executions/${encodeURIComponent(intent.executionId)}:${action}`,
        scope: scope!,
        body,
        idempotencyKey: intent.idempotencyKey,
      });
      return ensureProject(
        parseWire(lifecycleExecutionWireSchema, raw, {
          endpoint: `${action}LifecycleExecution`,
        }),
        projectId!,
      );
    },
    onSuccess: invalidate,
  });
}

export function useCreateLifecycleDryRun() {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  const invalidate = useInvalidateLifecycle();
  return useMutation({
    mutationFn: async ({
      policyId,
      policyEtag,
      idempotencyKey,
    }: DryRunIntent) => {
      const raw = await request<unknown>({
        method: "POST",
        path: `${storageRoot(projectId!)}/lifecycle-executions:dry-run`,
        scope: scope!,
        body: { policy_id: policyId, policy_etag: policyEtag },
        idempotencyKey,
      });
      return ensureProject(
        parseWire(lifecycleExecutionWireSchema, raw, {
          endpoint: "createLifecycleExecutionDryRun",
        }),
        projectId!,
      );
    },
    onSuccess: invalidate,
  });
}

export function useApproveLifecycleExecution() {
  return useExecutionMutation("approve");
}

export function useStartLifecycleExecution() {
  return useExecutionMutation("start");
}

export function useCancelLifecycleExecution() {
  return useExecutionMutation("cancel");
}

export function useRetryLifecycleExecution() {
  return useExecutionMutation("retry");
}

export interface CreateScheduleIntent {
  readonly policyId: string;
  readonly intervalSeconds: number;
  readonly firstRunAt: string;
  readonly idempotencyKey: string;
}

export interface UpdateScheduleIntent {
  readonly scheduleId: string;
  readonly intervalSeconds: number;
  readonly nextRunAt: string;
  readonly etag: string;
  readonly idempotencyKey: string;
}

export function useCreateLifecycleSchedule() {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  const invalidate = useInvalidateLifecycle();
  return useMutation({
    mutationFn: async (intent: CreateScheduleIntent) => {
      const raw = await request<unknown>({
        method: "POST",
        path: `${storageRoot(projectId!)}/lifecycle-schedules`,
        scope: scope!,
        body: {
          policy_id: intent.policyId,
          interval_seconds: intent.intervalSeconds,
          first_run_at: intent.firstRunAt,
        },
        idempotencyKey: intent.idempotencyKey,
      });
      return ensureProject(
        parseWire(lifecycleScheduleWireSchema, raw, {
          endpoint: "createLifecycleSchedule",
        }),
        projectId!,
      );
    },
    onSuccess: invalidate,
  });
}

export function useUpdateLifecycleSchedule() {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  const invalidate = useInvalidateLifecycle();
  return useMutation({
    mutationFn: async (intent: UpdateScheduleIntent) => {
      const raw = await request<unknown>({
        method: "PUT",
        path: `${storageRoot(projectId!)}/lifecycle-schedules/${encodeURIComponent(intent.scheduleId)}`,
        scope: scope!,
        body: {
          interval_seconds: intent.intervalSeconds,
          next_run_at: intent.nextRunAt,
        },
        ifMatch: intent.etag,
        idempotencyKey: intent.idempotencyKey,
      });
      return ensureProject(
        parseWire(lifecycleScheduleWireSchema, raw, {
          endpoint: "updateLifecycleSchedule",
        }),
        projectId!,
      );
    },
    onSuccess: invalidate,
  });
}

function useScheduleStateMutation(action: "enable" | "pause" | "delete") {
  const scope = useProjectScope();
  const projectId = scope?.projectId ?? null;
  const invalidate = useInvalidateLifecycle();
  return useMutation({
    mutationFn: async (intent: {
      readonly scheduleId: string;
      readonly etag: string;
      readonly idempotencyKey: string;
    }) => {
      if (action === "delete") {
        await request<void>({
          method: "DELETE",
          path: `${storageRoot(projectId!)}/lifecycle-schedules/${encodeURIComponent(intent.scheduleId)}`,
          scope: scope!,
          ifMatch: intent.etag,
          idempotencyKey: intent.idempotencyKey,
        });
        return null;
      }
      const raw = await request<unknown>({
        method: "POST",
        path: `${storageRoot(projectId!)}/lifecycle-schedules/${encodeURIComponent(intent.scheduleId)}/${action}`,
        scope: scope!,
        ifMatch: intent.etag,
        idempotencyKey: intent.idempotencyKey,
      });
      return ensureProject(
        parseWire(lifecycleScheduleWireSchema, raw, {
          endpoint: `${action}LifecycleSchedule`,
        }),
        projectId!,
      );
    },
    onSuccess: invalidate,
  });
}

export function useEnableLifecycleSchedule() {
  return useScheduleStateMutation("enable");
}

export function usePauseLifecycleSchedule() {
  return useScheduleStateMutation("pause");
}

export function useDeleteLifecycleSchedule() {
  return useScheduleStateMutation("delete");
}
