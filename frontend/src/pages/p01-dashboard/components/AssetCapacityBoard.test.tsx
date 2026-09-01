// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { adaptDashboardTaskStatus } from "../../../features/dashboard/api/adapter";
import {
  dashboardTaskStatusAllTasksFixture,
  dashboardTaskStatusEmptyFixture,
  dashboardTaskStatusFixture,
} from "../../../mocks/fixtures/dashboard";
import { AssetCapacityBoard } from "./AssetCapacityBoard";

afterEach(cleanup);

describe("AssetCapacityBoard", () => {
  it("shows an all-task aggregate by default instead of a zero-value unselected rail", () => {
    render(
      <MemoryRouter>
        <AssetCapacityBoard
          taskStatus={adaptDashboardTaskStatus(
            dashboardTaskStatusAllTasksFixture,
          )}
          onTaskChange={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByLabelText("筛选采集任务")).toBeVisible();
    expect(screen.getByText("全部任务（2）")).toBeVisible();
    expect(screen.getByLabelText("全部任务汇总")).toHaveTextContent(
      "2 个任务 · 14 个数据包",
    );
    expect(screen.getAllByText("14 个数据包")).toHaveLength(3);
    expect(screen.getByText("10 个数据包")).toBeVisible();
    expect(screen.queryByText(/请选择一个采集任务/)).not.toBeInTheDocument();
    expect(screen.queryByText(/当前任务 ·/)).not.toBeInTheDocument();
    expect(
      screen.getByText(
        /全部任务共有 3 个问题数据（质量风险或拒绝 3 个，重复 0 个）/,
      ),
    ).toBeVisible();
  });

  it("includes source duplicates in the problem-data total", () => {
    const withDuplicates = {
      ...dashboardTaskStatusAllTasksFixture,
      pipeline: {
        ...dashboardTaskStatusAllTasksFixture.pipeline,
        qc: {
          ...dashboardTaskStatusAllTasksFixture.pipeline.qc,
          risk: 1,
          rejected: 1,
          duplicate: 1,
        },
      },
    };

    render(
      <MemoryRouter>
        <AssetCapacityBoard
          taskStatus={adaptDashboardTaskStatus(withDuplicates)}
          onTaskChange={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(
      screen.getByText(
        /全部任务共有 3 个问题数据（质量风险或拒绝 2 个，重复 1 个）/,
      ),
    ).toBeVisible();
    expect(screen.getByText("自动质检").closest("li")).toHaveTextContent(
      "质检问题 2 · 重复数据 1",
    );
  });

  it("keeps the current task contract inside the eight-stage signal rail", () => {
    render(
      <MemoryRouter>
        <AssetCapacityBoard
          taskStatus={adaptDashboardTaskStatus(dashboardTaskStatusFixture)}
          onTaskChange={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "信号轨道" })).toBeVisible();
    expect(screen.getByText("当前任务 · 00000042")).toBeVisible();
    expect(screen.getByText("双臂装配采集")).toBeVisible();
    expect(screen.getByLabelText("状态：ACTIVE / 进行中")).toBeVisible();
    expect(screen.getByLabelText("状态：已达标")).toBeVisible();
    expect(
      screen.getByRole("list", { name: "采集到发布的固定八阶段" }),
    ).toBeVisible();
    expect(screen.getAllByRole("listitem")).toHaveLength(8);
    expect(screen.getByText("采集")).toBeVisible();
    expect(screen.getByText("登记上传")).toBeVisible();
    expect(screen.getByText("Raw 接收")).toBeVisible();
    expect(screen.getByText("自动质检")).toBeVisible();
    expect(screen.getByText("已入库")).toBeVisible();
    expect(screen.getByText("数据标注")).toBeVisible();
    expect(screen.getByText("人工审核")).toBeVisible();
    expect(screen.getAllByText("12 个数据包")).toHaveLength(3);
    expect(screen.getByText("8 个数据包")).toBeVisible();
    expect(screen.getByText("3 个数据包")).toBeVisible();
    expect(screen.getAllByText("1 个数据包")).toHaveLength(3);
    expect(screen.getByText("当前 · 未质检 1 · 质检问题 3")).toBeVisible();
    expect(
      screen.getByText("等待 2 · 运行 2 · 上游问题隔离 3 · 失败 2"),
    ).toBeVisible();
    expect(screen.getAllByText(/上游问题隔离 3/u)).toHaveLength(4);
    expect(screen.getAllByText(/质检问题 3/u)).toHaveLength(1);
    expect(screen.getByText(/当前任务有 2 项技术或结构阻塞/)).toBeVisible();
    expect(
      screen.getByText(/共 3 个问题数据（质量风险或拒绝 3 个，重复 0 个）/),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "查看问题数据" })).toHaveAttribute(
      "href",
      "/manual/issues?source=AUTO_QC",
    );
    expect(screen.queryByText("任务目标")).not.toBeInTheDocument();
    expect(screen.queryByText("CAPTURED 12")).not.toBeInTheDocument();
    expect(screen.queryByText("建议操作")).not.toBeInTheDocument();
  });

  it("keeps the rail visible for an empty task range", () => {
    render(
      <MemoryRouter>
        <AssetCapacityBoard
          taskStatus={adaptDashboardTaskStatus(dashboardTaskStatusEmptyFixture)}
          onTaskChange={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getAllByRole("listitem")).toHaveLength(8);
    expect(screen.getAllByText("0 个数据包")).toHaveLength(8);
    expect(screen.getByLabelText("状态：暂无数据")).toHaveAttribute(
      "data-status",
      "EMPTY",
    );
    expect(screen.queryByText(/当前任务 ·/)).not.toBeInTheDocument();
  });
});
