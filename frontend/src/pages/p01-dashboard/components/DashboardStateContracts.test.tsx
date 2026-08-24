// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import {
  adaptDashboardActivity,
  adaptDashboardPendingPage,
} from "../../../features/dashboard/api/adapter";
import type { DashboardSection } from "../../../features/dashboard/types";
import {
  dashboardActivityFixture,
  dashboardPendingFixture,
} from "../../../mocks/fixtures/dashboard";
import { DashboardActivityList } from "./DashboardActivityList";
import { DashboardPendingList } from "./DashboardPendingList";
import {
  DashboardSectionNotice,
  sectionLabel,
} from "./DashboardSectionNotice";

afterEach(cleanup);

function section(
  status: DashboardSection["status"],
  retryable = false,
): DashboardSection {
  return {
    status,
    asOf: "2026-08-05T08:00:00Z",
    error:
      status === "READY" || status === "EMPTY"
        ? null
        : {
            code: `SECTION_${status}`,
            message: `${status} 的真实服务端说明`,
            retryable,
            needs_product_confirmation: status === "BLOCKED",
          },
  };
}

describe("Dashboard section states", () => {
  it.each([
    ["READY", "正常"],
    ["EMPTY", "暂无数据"],
    ["PARTIAL", "数据不完整"],
    ["STALE", "数据可能已过期"],
    ["ERROR", "加载失败"],
    ["BLOCKED", "暂时无法计算"],
  ] as const)("labels %s as %s for users", (status, label) => {
    expect(sectionLabel(status)).toBe(label);
  });

  it.each(["PARTIAL", "STALE", "ERROR", "BLOCKED"] as const)(
    "keeps %s as an explicit local state",
    (status) => {
      render(
        <DashboardSectionNotice
          section={section(status, status === "ERROR")}
          label="最近活动"
          onRetry={vi.fn()}
        />,
      );

      expect(
        screen.getByText(`最近活动 · ${sectionLabel(status)}`),
      ).toBeVisible();
      expect(screen.getByText(`${status} 的真实服务端说明`)).toBeVisible();
      expect(screen.queryAllByRole("button", { name: "重试" })).toHaveLength(
        status === "ERROR" ? 1 : 0,
      );
    },
  );

  it("does not add a banner for READY and keeps BLOCKED explicit", () => {
    const { rerender } = render(
      <DashboardSectionNotice section={section("READY")} label="信号轨道" />,
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    rerender(
      <DashboardSectionNotice section={section("BLOCKED")} label="信号轨道" />,
    );
    expect(screen.getByText("信号轨道 · 暂时无法计算")).toBeVisible();
    expect(screen.getByRole("alert")).toHaveAttribute(
      "data-dashboard-section-status",
      "BLOCKED",
    );
  });

  it("shows direct Chinese copy for the signal formula blocker", () => {
    const blocked = section("BLOCKED");
    render(
      <DashboardSectionNotice
        section={{
          ...blocked,
          error: {
            ...blocked.error!,
            code: "P01_SIGNAL_FORMULA_UNCONFIRMED",
            message: "English server fallback",
          },
        }}
        label="信号轨道"
      />,
    );

    expect(
      screen.getByText(
        "信号轨道的统计规则还没配置完成，因此暂时不能显示各阶段数量。",
      ),
    ).toBeVisible();
    expect(
      screen.queryByText("English server fallback"),
    ).not.toBeInTheDocument();
  });

  it.each([
    [
      "P01_COVERAGE_DENOMINATOR_MISSING",
      "缺少采集计划、机器人分组、任务目录或目标总量，因此暂时无法计算覆盖率。",
    ],
    [
      "COVERAGE_PRODUCT_DECISION_REQUIRED",
      "覆盖率的统计规则和目标总量还没配置完成，因此暂时无法计算。",
    ],
  ] as const)("keeps coverage blocker %s explicit", (code, message) => {
    const blocked = section("BLOCKED");
    render(
      <DashboardSectionNotice
        section={{
          ...blocked,
          error: { ...blocked.error!, code, message: "English server fallback" },
        }}
        label="采集覆盖率"
      />,
    );

    expect(screen.getByText("采集覆盖率 · 暂时无法计算")).toBeVisible();
    expect(screen.getByText(message)).toBeVisible();
    expect(screen.getByRole("alert")).toHaveAttribute(
      "data-dashboard-section-status",
      "BLOCKED",
    );
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
    expect(screen.queryByText("English server fallback")).not.toBeInTheDocument();
  });
});

describe("Dashboard activity states", () => {
  it("shows source states in Chinese and retains the raw values as data", () => {
    render(
      <MemoryRouter>
        <DashboardActivityList
          activity={adaptDashboardActivity(dashboardActivityFixture)}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText("上传已提交 · 原始数据已提交")).toHaveAttribute(
      "data-source-state",
      "RAW_COMMITTED",
    );
    expect(screen.getByText("数据集已发布 · 已发布")).toHaveAttribute(
      "data-source-state",
      "PUBLISHED",
    );
  });
});

describe("Dashboard pending actions", () => {
  it("uses the four formal kinds and the fixed publication action copy", () => {
    const pending = adaptDashboardPendingPage(dashboardPendingFixture);
    render(
      <MemoryRouter>
        <DashboardPendingList items={pending.items} />
      </MemoryRouter>,
    );

    expect(screen.getByText("上传失败")).toBeVisible();
    expect(screen.getByText("自动质检异常")).toBeVisible();
    expect(screen.getByText("待审核")).toBeVisible();
    expect(screen.getByText("待冻结发布")).toBeVisible();
    expect(screen.getByRole("link", { name: /进入发布/ })).toHaveAttribute(
      "href",
      "/datasets/dataset-1/versions/1",
    );
    expect(screen.getAllByLabelText("状态：高")).toHaveLength(4);
    expect(screen.getByText("失败 · upload-1")).toHaveAttribute(
      "data-source-state",
      "FAILED",
    );
    expect(screen.getByText("有风险 · rollout-1")).toHaveAttribute(
      "data-source-state",
      "RISK",
    );
  });
});
