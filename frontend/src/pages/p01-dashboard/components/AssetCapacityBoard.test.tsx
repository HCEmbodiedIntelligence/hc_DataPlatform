// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import {
  adaptDashboardPendingPage,
  adaptDashboardSnapshot,
} from "../../../features/dashboard/api/adapter";
import {
  dashboardEmptyFixtures,
  dashboardPendingFixture,
  dashboardSnapshotFixture,
} from "../../../mocks/fixtures/dashboard";
import { AssetCapacityBoard } from "./AssetCapacityBoard";

afterEach(cleanup);

describe("AssetCapacityBoard", () => {
  it("presents the fixed eight-stage signal rail, QC evidence, and publication lineage", () => {
    render(
      <MemoryRouter>
        <AssetCapacityBoard
          snapshot={adaptDashboardSnapshot(dashboardSnapshotFixture)}
          pending={adaptDashboardPendingPage(dashboardPendingFixture)}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "信号轨道" })).toBeVisible();
    expect(
      screen.getByRole("list", { name: "采集到发布的固定八阶段" }),
    ).toBeVisible();
    expect(screen.getAllByRole("listitem")).toHaveLength(8);
    expect(screen.getByText("SAVED")).toBeVisible();
    expect(screen.getByText("RECEIVED")).toBeVisible();
    expect(screen.getByText("30 Hz 对齐")).toBeVisible();
    expect(screen.getByText("数据标注")).toBeVisible();
    expect(screen.getByText("当前窗口异常 1")).toBeVisible();
    expect(screen.getByRole("link", { name: "查看 Raw 诊断" })).toHaveAttribute(
      "href",
      "/datasets/dataset-1/rollouts/rollout-1",
    );
    expect(screen.getByText("已发布 3")).toBeVisible();
    expect(screen.getByText("血缘 8")).toBeVisible();
    expect(screen.queryByText(/清洗/)).not.toBeInTheDocument();
    expect(screen.queryByText(/物理占用/)).not.toBeInTheDocument();
  });

  it("renders truthful empty lineage counts without removing the rail", () => {
    render(
      <MemoryRouter>
        <AssetCapacityBoard
          snapshot={adaptDashboardSnapshot(dashboardEmptyFixtures.snapshot)}
        />
      </MemoryRouter>,
    );

    expect(screen.getAllByRole("listitem")).toHaveLength(8);
    expect(screen.getByText("已发布 0")).toBeVisible();
    expect(screen.getByText("血缘 0")).toBeVisible();
    expect(screen.getByLabelText("状态：EMPTY")).toBeVisible();
  });
});
