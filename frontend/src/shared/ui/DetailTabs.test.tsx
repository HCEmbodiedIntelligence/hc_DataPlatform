// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DetailTabs } from "./DetailTabs";

afterEach(cleanup);

describe("DetailTabs", () => {
  it("does not claim a tabpanel relationship unless the page supplies one", () => {
    const tabs = [
      { id: "overview", label: "概览" },
      { id: "history", label: "历史" },
    ] as const;
    const { rerender } = render(
      <DetailTabs tabs={tabs} activeTab="overview" onChange={vi.fn()} />,
    );

    expect(screen.getByRole("tab", { name: "概览" })).not.toHaveAttribute(
      "aria-controls",
    );
    expect(screen.getByRole("tab", { name: "历史" })).not.toHaveAttribute(
      "aria-controls",
    );

    rerender(
      <DetailTabs
        tabs={tabs}
        activeTab="overview"
        onChange={vi.fn()}
        panelIdForTab={(tabId) => `panel-${tabId}`}
      />,
    );

    expect(screen.getByRole("tab", { name: "概览" })).toHaveAttribute(
      "aria-controls",
      "panel-overview",
    );
    expect(screen.getByRole("tab", { name: "历史" })).toHaveAttribute(
      "aria-controls",
      "panel-history",
    );
  });
});
