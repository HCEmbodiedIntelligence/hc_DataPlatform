// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "../../shared/api/generated/platform";
import { domainErrorFromResponse } from "../../shared/api/http-client";
import {
  LifecyclePolicyTable,
  LifecycleProtectionSummary,
} from "../p13-storage-lifecycle/page";
import {
  storageLifecycleQueryCodec,
  updateLifecycleSearch,
} from "../p13-storage-lifecycle/query-codec";
import {
  CapacityOverview,
  CapacityErrorState,
  CapacitySnapshotMeta,
  CapacityTrend,
  ProjectCapacityTable,
  type CapacitySnapshot,
} from "./page";
import type { CapacityHistory } from "../../features/storage-overview/capacity-api";

type LifecyclePolicy = components["schemas"]["LifecyclePolicy"];

afterEach(cleanup);

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal("ResizeObserver", ResizeObserverStub);
const getComputedStyle = window.getComputedStyle.bind(window);
vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
  getComputedStyle(element),
);

const snapshot: CapacitySnapshot = {
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

const policy: LifecyclePolicy = {
  policy_id: "policy-protected-raw",
  project_id: "project-a",
  name: "Raw 永久保留",
  business_category: "RAW",
  object_role: "RAW",
  action: "RETAIN",
  minimum_age_days: 0,
  priority: 1,
  state: "ENABLED",
  version: 3,
  etag: '"v3"',
  created_at: "2026-08-17T02:30:00Z",
  updated_at: "2026-08-17T02:30:00Z",
};

const history: CapacityHistory = {
  project_id: "project-a",
  window_start: "2026-08-10T00:00:00Z",
  window_end: "2026-08-17T23:59:59Z",
  items: [
    {
      snapshot_id: "snapshot-history-1",
      observed_at: "2026-08-10T02:30:00Z",
      physical_total_bytes: "250",
      candidate_business_total_bytes: "160",
    },
    {
      snapshot_id: "snapshot-history-2",
      observed_at: "2026-08-17T02:30:00Z",
      physical_total_bytes: "310",
      candidate_business_total_bytes: "200",
    },
  ],
  growth: {
    from_snapshot_id: "snapshot-history-1",
    from_observed_at: "2026-08-10T02:30:00Z",
    to_snapshot_id: "snapshot-history-2",
    to_observed_at: "2026-08-17T02:30:00Z",
    candidate_change_bytes: "40",
    elapsed_seconds: 604800,
    candidate_bytes_per_day: "5",
  },
};

describe("E10 capacity and lifecycle component contracts", () => {
  it("shows the first-inventory empty state for CAPACITY_SNAPSHOT_NOT_FOUND", () => {
    const error = domainErrorFromResponse(404, {
      title: "Capacity snapshot not found",
      status: 404,
      detail: "The requested scoped resource does not exist.",
      code: "CAPACITY_SNAPSHOT_NOT_FOUND",
      request_id: "request-capacity-missing",
    });

    render(<CapacityErrorState error={error} label="容量管理" />);

    expect(screen.getByText("暂无容量快照")).toBeVisible();
    expect(screen.getByText(/等待首次存储盘点/u)).toBeVisible();
    expect(screen.queryByText("资源不存在")).not.toBeInTheDocument();
  });

  it("keeps an ordinary HTTP NOT_FOUND as resource missing", () => {
    const error = domainErrorFromResponse(404, {
      title: "Storage object not found",
      status: 404,
      detail: "The object does not exist.",
      code: "STORAGE_OBJECT_NOT_FOUND",
      request_id: "request-object-missing",
    });

    render(<CapacityErrorState error={error} label="容量管理" />);

    expect(screen.getByText("资源不存在")).toBeVisible();
    expect(screen.queryByText("暂无容量快照")).not.toBeInTheDocument();
  });

  it("keeps physical and business totals separate and shows exactly four fixed categories", () => {
    render(<CapacityOverview snapshot={snapshot} />);

    const legend = screen.getByRole("list", { name: "容量四类精确值" });
    for (const label of ["Raw", "标注完成", "待标注", "问题数据"]) {
      expect(within(legend).getByText(label)).toBeVisible();
    }
    expect(screen.getByText("对象存储物理总量")).toBeVisible();
    expect(screen.getByText("候选业务口径总量")).toBeVisible();
    expect(screen.getByText("对账一致")).toBeVisible();
    expect(screen.getByLabelText("容量对账公式")).toHaveTextContent(
      "物理总量310 B=业务候选200 B+副本开销100 B+临时对象10 B",
    );
    expect(screen.getByText(/两套总量不得相加/u)).toBeVisible();
    expect(
      screen.queryByText(/费用|成本|热存储|冷存储|中存储/u),
    ).not.toBeInTheDocument();
  });

  it("renders server-history points and exact growth without inventing a trend", () => {
    const onRangeChange = vi.fn();
    render(
      <CapacityTrend
        history={history}
        days={30}
        isPending={false}
        error={null}
        onRetry={vi.fn()}
        onRangeChange={onRangeChange}
      />,
    );

    const figure = screen.getByRole("figure", { name: "增长趋势" });
    expect(figure).toHaveAccessibleDescription(/候选业务口径变化 \+40 B/u);
    const plot = within(figure).getByRole("list", { name: "近 30 天容量记录" });
    expect(within(plot).getByText("160 B")).toBeVisible();
    expect(within(plot).getByText("200 B")).toBeVisible();
    expect(within(figure).getByText("+5 B/日")).toBeVisible();
    expect(
      within(figure).queryByText("历史序列未开放"),
    ).not.toBeInTheDocument();
  });

  it("keeps the range control, loading, empty, and retry states actionable", async () => {
    const user = userEvent.setup();
    const onRangeChange = vi.fn();
    const onRetry = vi.fn();
    const { rerender } = render(
      <CapacityTrend
        history={undefined}
        days={30}
        isPending
        error={null}
        onRetry={onRetry}
        onRangeChange={onRangeChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "近 7 天" }));
    expect(onRangeChange).toHaveBeenCalledWith(7);
    expect(screen.getByRole("status")).toHaveTextContent("正在加载容量趋势");

    rerender(
      <CapacityTrend
        history={{ ...history, items: [], growth: null }}
        days={7}
        isPending={false}
        error={null}
        onRetry={onRetry}
        onRangeChange={onRangeChange}
      />,
    );
    expect(
      screen.getByText("所选时间范围内没有已封存的容量记录。"),
    ).toBeVisible();

    rerender(
      <CapacityTrend
        history={undefined}
        days={7}
        isPending={false}
        error={new Error("history request failed")}
        onRetry={onRetry}
        onRangeChange={onRangeChange}
      />,
    );
    await user.click(screen.getByRole("button", { name: /重\s*试/u }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("keeps a long project identity inside the authorized portfolio table", () => {
    const longProjectId = `project-${"robot-data-collection-".repeat(8)}east-01`;
    render(
      <ProjectCapacityTable
        snapshot={{ ...snapshot, project_id: longProjectId }}
      />,
    );

    expect(screen.getByLabelText("授权项目容量明细")).toBeInTheDocument();
    expect(screen.getByText(longProjectId)).toBeVisible();
    expect(screen.getByText(/已选 1 个授权项目/u)).toBeVisible();
    expect(screen.getByLabelText("选择容量汇总项目")).toBeInTheDocument();
  });

  it("labels client-cache stale state without claiming backend freshness", () => {
    render(<CapacitySnapshotMeta snapshot={snapshot} isStale />);

    expect(screen.getByText("客户端缓存待刷新")).toBeVisible();
    expect(
      screen.getByText("客户端缓存待刷新").closest("[data-cache-state]"),
    ).toHaveAttribute("data-cache-state", "stale");
  });

  it("keeps all protected objects visible and explains the independent approval gate", () => {
    render(<LifecycleProtectionSummary />);

    expect(screen.getByText("Raw")).toBeVisible();
    expect(screen.getByText("数据清单")).toBeVisible();
    expect(screen.getByText("已发布数据清单")).toBeVisible();
    expect(screen.getByText("生产执行需独立审批")).toBeVisible();
    expect(screen.getByText(/申请人不能审批自己的计划/u)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /模拟|执行/u }),
    ).not.toBeInTheDocument();
  });

  it("fails lifecycle actions closed for read-only permission while preserving protection state", () => {
    render(
      <LifecyclePolicyTable
        items={[policy]}
        canManage={false}
        refreshing={false}
        onEdit={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(screen.getByText("永久保护")).toBeVisible();
    expect(screen.getByRole("button", { name: "编辑策略" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "暂停策略" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "删除策略" })).toBeDisabled();
  });

  it("renders the policy empty state without fabricating rows", () => {
    render(
      <LifecyclePolicyTable
        items={[]}
        canManage={true}
        refreshing={false}
        onEdit={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(screen.getByText("暂无数据")).toBeVisible();
    expect(
      screen.queryByRole("row", { name: /永久保护/u }),
    ).not.toBeInTheDocument();
  });

  it("keeps filters shareable, migrates legacy tabs, and resets only invalidated cursors", () => {
    const legacy = storageLifecycleQueryCodec.parse(
      new URLSearchParams("tab=audit&cursor=audit-next"),
    );
    expect(legacy.auditCursor).toBe("audit-next");
    expect(legacy.policyCursor).toBeUndefined();

    const filtered = updateLifecycleSearch(
      {
        query: "",
        state: "ALL",
        role: "ALL",
        policyCursor: "policy-next",
        auditCursor: "audit-next",
        limit: 50,
      },
      { state: "ENABLED" },
    );
    expect(filtered.policyCursor).toBeUndefined();
    expect(filtered.auditCursor).toBe("audit-next");
    expect(storageLifecycleQueryCodec.build(filtered).toString()).toContain(
      "state=ENABLED",
    );
  });
});
