// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../../../shared/api/http-client";
import { useShellStore } from "../../../shared/scope/shell-store";
import {
  useApproveLifecycleExecution,
  useCancelLifecycleExecution,
  useCreateLifecycleDryRun,
  useCreateLifecycleSchedule,
  useDeleteLifecycleSchedule,
  useLifecycleExecutionLogs,
  useLifecycleExecutions,
  useLifecycleSchedules,
  usePauseLifecycleSchedule,
  useRetryLifecycleExecution,
  useStartLifecycleExecution,
  useUpdateLifecycleSchedule,
} from ".";

vi.mock("../../../shared/api/http-client", () => ({ request: vi.fn() }));

const requestMock = vi.mocked(request);
const scope = {
  organizationId: "organization-a",
  projectId: "project-a",
  regionCode: "cn-test",
} as const;
const pageInfo = {
  has_next_page: false,
  has_previous_page: false,
  start_cursor: null,
  end_cursor: null,
} as const;
const execution = {
  execution_id: "execution-1",
  project_id: "project-a",
  policy_id: "policy-1",
  policy_version: 3,
  action: "CLEAN_REBUILDABLE_CACHE",
  status: "AWAITING_APPROVAL",
  dry_run: true,
  plan_hash: "a".repeat(64),
  approval_id: null,
  requested_by: "actor-requester",
  approved_by: null,
  total_items: 1,
  processed_items: 0,
  blocked_items: 0,
  failed_items: 0,
  next_batch: 0,
  items: [
    {
      object_id: "object-1",
      physical_instance_id: "physical-1",
      status: "PENDING",
      attempt: 0,
      blocked_reasons: [],
      last_error_code: null,
    },
  ],
  created_at: "2026-08-21T02:30:00Z",
  updated_at: "2026-08-21T02:30:00Z",
} as const;
const schedule = {
  schedule_id: "schedule-1",
  project_id: "project-a",
  policy_id: "policy-1",
  interval_seconds: 86_400,
  enabled: true,
  next_run_at: "2026-08-22T02:30:00Z",
  last_execution_id: null,
  version: 1,
  etag: '"schedule-1:1"',
  created_at: "2026-08-21T02:30:00Z",
  updated_at: "2026-08-21T02:30:00Z",
} as const;

function createQueryWrapper() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return function QueryWrapper({ children }: { readonly children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children);
  };
}

describe("lifecycle execution and schedule API", () => {
  beforeEach(() => {
    requestMock.mockReset();
    useShellStore.setState({ scope });
  });

  it("accepts execution items without mistaking their physical scope for a project leak", async () => {
    requestMock.mockImplementation(async (options) => {
      if (options.path.endsWith("/logs")) {
        return {
          project_id: "project-a",
          execution_id: "execution-1",
          items: [
            {
              sequence: 1,
              project_id: "project-a",
              execution_id: "execution-1",
              level: "INFO",
              event: "execution.dry_run.created",
              details: { total_items: 1 },
              occurred_at: "2026-08-21T02:30:00Z",
            },
          ],
          page_info: pageInfo,
        };
      }
      if (options.path.endsWith("/lifecycle-schedules")) {
        return {
          project_id: "project-a",
          items: [schedule],
          page_info: pageInfo,
        };
      }
      return {
        project_id: "project-a",
        items: [execution],
        page_info: pageInfo,
      };
    });
    const { result } = renderHook(
      () => ({
        executions: useLifecycleExecutions(undefined, 25),
        logs: useLifecycleExecutionLogs("execution-1", undefined, 50),
        schedules: useLifecycleSchedules(undefined, 25),
      }),
      { wrapper: createQueryWrapper() },
    );

    await waitFor(() => {
      expect(result.current.executions.data?.items).toHaveLength(1);
      expect(result.current.logs.data?.items).toHaveLength(1);
      expect(result.current.schedules.data?.items).toHaveLength(1);
    });
    expect(result.current.executions.data?.items[0]?.items[0]?.object_id).toBe(
      "object-1",
    );
  });

  it("binds dry-run, independent approval, and start to one immutable plan", async () => {
    requestMock
      .mockResolvedValueOnce(execution)
      .mockResolvedValueOnce({
        ...execution,
        status: "APPROVED",
        approval_id: "approval-1",
        approved_by: "actor-approver",
      })
      .mockResolvedValueOnce({
        ...execution,
        status: "QUEUED",
        dry_run: false,
        approval_id: "approval-1",
        approved_by: "actor-approver",
      });
    const { result } = renderHook(
      () => ({
        dryRun: useCreateLifecycleDryRun(),
        approve: useApproveLifecycleExecution(),
        start: useStartLifecycleExecution(),
      }),
      { wrapper: createQueryWrapper() },
    );

    await act(async () => {
      await result.current.dryRun.mutateAsync({
        policyId: "policy-1",
        policyEtag: '"policy-1:3"',
        idempotencyKey: "dry-run-1",
      });
      await result.current.approve.mutateAsync({
        executionId: "execution-1",
        planHash: "a".repeat(64),
        justification: "已由独立审批人核对执行范围",
        idempotencyKey: "approve-1",
      });
      await result.current.start.mutateAsync({
        executionId: "execution-1",
        planHash: "a".repeat(64),
        approvalId: "approval-1",
        idempotencyKey: "start-1",
      });
    });

    expect(requestMock).toHaveBeenNthCalledWith(1, {
      method: "POST",
      path: "/projects/project-a/storage/lifecycle-executions:dry-run",
      scope,
      body: { policy_id: "policy-1", policy_etag: '"policy-1:3"' },
      idempotencyKey: "dry-run-1",
    });
    expect(requestMock).toHaveBeenNthCalledWith(2, {
      method: "POST",
      path: "/projects/project-a/storage/lifecycle-executions/execution-1:approve",
      scope,
      body: {
        plan_hash: "a".repeat(64),
        justification: "已由独立审批人核对执行范围",
      },
      idempotencyKey: "approve-1",
    });
    expect(requestMock).toHaveBeenNthCalledWith(3, {
      method: "POST",
      path: "/projects/project-a/storage/lifecycle-executions/execution-1:start",
      scope,
      body: { plan_hash: "a".repeat(64), approval_id: "approval-1" },
      idempotencyKey: "start-1",
    });
  });

  it("sends explicit reasons for cancellation and retry", async () => {
    requestMock
      .mockResolvedValueOnce({ ...execution, status: "CANCELLED" })
      .mockResolvedValueOnce({
        ...execution,
        status: "QUEUED",
        dry_run: false,
        approval_id: "approval-1",
        approved_by: "actor-approver",
      });
    const { result } = renderHook(
      () => ({
        cancel: useCancelLifecycleExecution(),
        retry: useRetryLifecycleExecution(),
      }),
      { wrapper: createQueryWrapper() },
    );

    await act(async () => {
      await result.current.cancel.mutateAsync({
        executionId: "execution-1",
        planHash: "a".repeat(64),
        reason: "操作人确认需要取消当前执行",
        idempotencyKey: "cancel-1",
      });
      await result.current.retry.mutateAsync({
        executionId: "execution-1",
        planHash: "a".repeat(64),
        reason: "已排除临时存储故障后重试",
        idempotencyKey: "retry-1",
      });
    });

    expect(requestMock).toHaveBeenNthCalledWith(1, {
      method: "POST",
      path: "/projects/project-a/storage/lifecycle-executions/execution-1:cancel",
      scope,
      body: { reason: "操作人确认需要取消当前执行" },
      idempotencyKey: "cancel-1",
    });
    expect(requestMock).toHaveBeenNthCalledWith(2, {
      method: "POST",
      path: "/projects/project-a/storage/lifecycle-executions/execution-1:retry",
      scope,
      body: {
        plan_hash: "a".repeat(64),
        reason: "已排除临时存储故障后重试",
      },
      idempotencyKey: "retry-1",
    });
  });

  it("creates, updates, pauses, and deletes a schedule with concurrency guards", async () => {
    requestMock
      .mockResolvedValueOnce(schedule)
      .mockResolvedValueOnce({
        ...schedule,
        interval_seconds: 172_800,
        next_run_at: "2026-08-23T02:30:00Z",
        version: 2,
        etag: '"schedule-1:2"',
      })
      .mockResolvedValueOnce({
        ...schedule,
        enabled: false,
        version: 3,
        etag: '"schedule-1:3"',
      })
      .mockResolvedValueOnce(undefined);
    const { result } = renderHook(
      () => ({
        create: useCreateLifecycleSchedule(),
        update: useUpdateLifecycleSchedule(),
        pause: usePauseLifecycleSchedule(),
        remove: useDeleteLifecycleSchedule(),
      }),
      { wrapper: createQueryWrapper() },
    );

    await act(async () => {
      await result.current.create.mutateAsync({
        policyId: "policy-1",
        intervalSeconds: 86_400,
        firstRunAt: "2026-08-22T02:30:00Z",
        idempotencyKey: "schedule-create-1",
      });
      await result.current.update.mutateAsync({
        scheduleId: "schedule-1",
        intervalSeconds: 172_800,
        nextRunAt: "2026-08-23T02:30:00Z",
        etag: '"schedule-1:1"',
        idempotencyKey: "schedule-update-1",
      });
      await result.current.pause.mutateAsync({
        scheduleId: "schedule-1",
        etag: '"schedule-1:2"',
        idempotencyKey: "schedule-pause-1",
      });
      await result.current.remove.mutateAsync({
        scheduleId: "schedule-1",
        etag: '"schedule-1:3"',
        idempotencyKey: "schedule-delete-1",
      });
    });

    expect(requestMock).toHaveBeenNthCalledWith(1, {
      method: "POST",
      path: "/projects/project-a/storage/lifecycle-schedules",
      scope,
      body: {
        policy_id: "policy-1",
        interval_seconds: 86_400,
        first_run_at: "2026-08-22T02:30:00Z",
      },
      idempotencyKey: "schedule-create-1",
    });
    expect(requestMock).toHaveBeenNthCalledWith(2, {
      method: "PUT",
      path: "/projects/project-a/storage/lifecycle-schedules/schedule-1",
      scope,
      body: {
        interval_seconds: 172_800,
        next_run_at: "2026-08-23T02:30:00Z",
      },
      ifMatch: '"schedule-1:1"',
      idempotencyKey: "schedule-update-1",
    });
    expect(requestMock).toHaveBeenNthCalledWith(3, {
      method: "POST",
      path: "/projects/project-a/storage/lifecycle-schedules/schedule-1/pause",
      scope,
      ifMatch: '"schedule-1:2"',
      idempotencyKey: "schedule-pause-1",
    });
    expect(requestMock).toHaveBeenNthCalledWith(4, {
      method: "DELETE",
      path: "/projects/project-a/storage/lifecycle-schedules/schedule-1",
      scope,
      ifMatch: '"schedule-1:3"',
      idempotencyKey: "schedule-delete-1",
    });
  });
});
