// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import type { paths } from "../../shared/api/generated/platform";
import { parseWire } from "../../shared/api/validate";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  capacityInventoryFactWireSchema,
  capacityHistoryWireSchema,
  capacityPortfolioWireSchema,
  capacitySnapshotWireSchema,
  getCapacityHistory,
  getCapacityPortfolio,
  getCapacitySnapshot,
  getManagedStorageObjects,
  useTrashStorageObject,
  type CapacityHistory,
  type CapacitySnapshot,
} from "./capacity-api";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));

const requestMock = vi.mocked(request);
const scope = {
  organizationId: "",
  projectId: "project-a",
  regionCode: "cn-test",
} as const;

type LifecycleDryRunPath = Extract<
  keyof paths,
  "/api/v1/projects/{project_id}/storage/lifecycle-executions:dry-run"
>;
const runtimeContractHasLifecycleDryRun: LifecycleDryRunPath extends never
  ? false
  : true = true;

const validSnapshot: CapacitySnapshot = {
  snapshot_id: "snapshot-1",
  project_id: "project-a",
  observed_at: "2026-08-17T02:30:00Z",
  physical_total_bytes: "310",
  physical_instance_count: 6,
  candidate_business_total_bytes: "200",
  candidate_logical_object_count: 4,
  categories: [
    { category: "RAW", candidate_bytes: "100", logical_object_count: 1 },
    {
      category: "ANNOTATION_COMPLETE",
      candidate_bytes: "50",
      logical_object_count: 1,
    },
    {
      category: "PENDING_ANNOTATION",
      candidate_bytes: "30",
      logical_object_count: 1,
    },
    { category: "ISSUE_DATA", candidate_bytes: "20", logical_object_count: 1 },
  ],
  reconciliation: {
    replica_overhead_bytes: "100",
    replica_instance_count: 1,
    temporary_bytes: "10",
    temporary_instance_count: 1,
    duplicate_inventory_rows_ignored: 1,
    formula:
      "physical_total_bytes = candidate_business_total_bytes + replica_overhead_bytes + temporary_bytes",
    balanced: true,
  },
};

const validHistory: CapacityHistory = {
  project_id: "project-a",
  window_start: "2026-08-10T00:00:00Z",
  window_end: "2026-08-17T23:59:59Z",
  items: [
    {
      snapshot_id: "snapshot-1",
      observed_at: "2026-08-10T02:30:00Z",
      physical_total_bytes: "250",
      candidate_business_total_bytes: "160",
    },
    {
      snapshot_id: "snapshot-2",
      observed_at: "2026-08-17T02:30:00Z",
      physical_total_bytes: "310",
      candidate_business_total_bytes: "200",
    },
  ],
  growth: {
    from_snapshot_id: "snapshot-1",
    from_observed_at: "2026-08-10T02:30:00Z",
    to_snapshot_id: "snapshot-2",
    to_observed_at: "2026-08-17T02:30:00Z",
    candidate_change_bytes: "40",
    elapsed_seconds: 604800,
    candidate_bytes_per_day: "5",
  },
};

const validPortfolio = {
  project_ids: ["project-a"],
  physical_total_bytes: "310",
  candidate_business_total_bytes: "200",
  categories: validSnapshot.categories,
  items: [
    {
      project_id: "project-a",
      snapshot_id: "snapshot-1",
      observed_at: "2026-08-17T02:30:00Z",
      physical_total_bytes: "310",
      candidate_business_total_bytes: "200",
      categories: validSnapshot.categories,
      balanced: true,
    },
  ],
} as const;

const validObject = {
  object_id: "object-1",
  project_id: "project-a",
  display_key: "raw/2026-08/robot-run.bin",
  physical_bytes: "128",
  checksum_sha256: "a".repeat(64),
  business_category: "RAW",
  object_role: "RAW",
  storage_tier: "HOT",
  status: "ACTIVE",
  active_reference_count: 0,
  retention_until: null,
  legal_hold: false,
  governance_hold: false,
  rebuild_source_id: null,
  recoverable_until: null,
  version: 1,
  etag: '"object-1:1"',
  allowed_actions: ["DOWNLOAD", "TRASH", "TRANSITION_TO_COLD"],
  created_at: "2026-08-17T02:30:00Z",
  updated_at: "2026-08-17T02:30:00Z",
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

describe("formal storage capacity wire contract", () => {
  beforeEach(() => requestMock.mockReset());

  it("contains the real lifecycle dry-run path and accepts exact dual-metric reconciliation", () => {
    expect(runtimeContractHasLifecycleDryRun).toBe(true);
    expect(
      parseWire(capacitySnapshotWireSchema, validSnapshot, {
        endpoint: "test",
      }),
    ).toEqual(validSnapshot);
  });

  it("fails closed on category order, arithmetic, and temporary-sample classification drift", () => {
    expect(() =>
      parseWire(
        capacitySnapshotWireSchema,
        {
          ...validSnapshot,
          physical_total_bytes: "311",
          categories: [
            validSnapshot.categories[1],
            validSnapshot.categories[0],
            ...validSnapshot.categories.slice(2),
          ],
        },
        { endpoint: "test" },
      ),
    ).toThrow("服务端响应与当前合同不匹配");

    expect(() =>
      parseWire(
        capacityInventoryFactWireSchema,
        {
          snapshot_id: "snapshot-1",
          project_id: "project-a",
          physical_instance_id: "temp-1",
          logical_object_id: null,
          physical_bytes: "10",
          disposition: "TEMPORARY",
          business_category: "RAW",
          object_role: "OTHER",
          observed_at: "2026-08-17T02:30:00Z",
        },
        { endpoint: "test" },
      ),
    ).toThrow("服务端响应与当前合同不匹配");
  });

  it("binds the formal capacity read to one immutable project/region scope", async () => {
    requestMock.mockResolvedValue(validSnapshot);
    await expect(getCapacitySnapshot(scope)).resolves.toEqual(validSnapshot);
    expect(requestMock).toHaveBeenCalledWith({
      method: "GET",
      path: "/projects/project-a/storage/capacity",
      scope,
    });
  });

  it("reports a cross-project capacity response as a contract mismatch", async () => {
    requestMock.mockResolvedValue({
      ...validSnapshot,
      project_id: "project-b",
    });
    await expect(getCapacitySnapshot(scope)).rejects.toMatchObject({
      code: "CONTRACT_MISMATCH",
      retryable: false,
    });
  });

  it("reads a bounded real capacity-history window in the active project scope", async () => {
    requestMock.mockResolvedValue(validHistory);

    await expect(
      getCapacityHistory(scope, {
        from: "2026-08-10T00:00:00Z",
        to: "2026-08-17T23:59:59Z",
      }),
    ).resolves.toEqual(validHistory);

    expect(requestMock).toHaveBeenCalledWith({
      method: "GET",
      path: "/projects/project-a/storage/capacity/history",
      scope,
      query: {
        from: "2026-08-10T00:00:00Z",
        to: "2026-08-17T23:59:59Z",
      },
    });
  });

  it("fails closed for unordered or growth-incomplete history data", () => {
    expect(() =>
      parseWire(
        capacityHistoryWireSchema,
        {
          ...validHistory,
          items: [...validHistory.items].reverse(),
        },
        { endpoint: "test" },
      ),
    ).toThrow("服务端响应与当前合同不匹配");

    expect(() =>
      parseWire(
        capacityHistoryWireSchema,
        { ...validHistory, growth: null },
        { endpoint: "test" },
      ),
    ).toThrow("服务端响应与当前合同不匹配");
  });

  it("reads and reconciles an authorized cross-project capacity portfolio", async () => {
    requestMock.mockResolvedValue(validPortfolio);

    await expect(getCapacityPortfolio(scope, ["project-a"])).resolves.toEqual(
      validPortfolio,
    );
    expect(requestMock).toHaveBeenCalledWith({
      method: "GET",
      path: "/projects/project-a/storage/capacity/portfolio",
      scope,
      query: { projectId: ["project-a"] },
    });

    expect(() =>
      parseWire(
        capacityPortfolioWireSchema,
        { ...validPortfolio, physical_total_bytes: "311" },
        { endpoint: "test" },
      ),
    ).toThrow("服务端响应与当前合同不匹配");
  });

  it("rejects managed storage objects that escape the current project", async () => {
    requestMock.mockResolvedValue({
      project_id: "project-a",
      items: [{ ...validObject, project_id: "project-b" }],
      page_info: {
        has_next_page: false,
        has_previous_page: false,
        start_cursor: null,
        end_cursor: null,
      },
    });

    await expect(
      getManagedStorageObjects(scope, undefined, 25),
    ).rejects.toMatchObject({ code: "CONTRACT_MISMATCH" });
  });

  it("binds a recoverable trash mutation to object, ETag, and idempotency", async () => {
    useShellStore.setState({ scope });
    requestMock.mockResolvedValue({
      ...validObject,
      status: "TRASHED",
      recoverable_until: "2026-09-17T02:30:00Z",
      version: 2,
      etag: '"object-1:2"',
      allowed_actions: ["RESTORE"],
    });
    const { result } = renderHook(() => useTrashStorageObject(), {
      wrapper: createQueryWrapper(),
    });

    await act(async () => {
      await result.current.mutateAsync({
        objectId: "object-1",
        etag: '"object-1:1"',
        idempotencyKey: "trash-object-1",
        reason: "清理已确认不再使用的对象",
      });
    });

    expect(requestMock).toHaveBeenCalledWith({
      method: "POST",
      path: "/projects/project-a/storage/objects/object-1:trash",
      scope,
      body: { reason: "清理已确认不再使用的对象" },
      ifMatch: '"object-1:1"',
      idempotencyKey: "trash-object-1",
    });
  });
});
