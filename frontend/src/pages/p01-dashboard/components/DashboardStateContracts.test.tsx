// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { adaptDashboardPendingPage } from "../../../features/dashboard/api/adapter";
import type { DashboardSection } from "../../../features/dashboard/types";
import { dashboardPendingFixture } from "../../../mocks/fixtures/dashboard";
import { DashboardPendingList } from "./DashboardPendingList";
import { DashboardSectionNotice } from "./DashboardSectionNotice";

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

      expect(screen.getByText(`最近活动 · ${status}`)).toBeVisible();
      expect(screen.getByText(`${status} 的真实服务端说明`)).toBeVisible();
      expect(screen.queryAllByRole("button", { name: "重试" })).toHaveLength(
        status === "ERROR" ? 1 : 0,
      );
    },
  );

  it("does not add a banner for READY or turn BLOCKED into numeric zero", () => {
    const { rerender } = render(
      <DashboardSectionNotice section={section("READY")} label="覆盖率" />,
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    rerender(
      <DashboardSectionNotice section={section("BLOCKED")} label="覆盖率" />,
    );
    expect(screen.getByText("覆盖率 · BLOCKED")).toBeVisible();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
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
  });
});
