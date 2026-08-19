// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PageState } from "./PageState";

afterEach(cleanup);

describe("PageState layout contract", () => {
  it("keeps the dashboard geometry while the page is loading", () => {
    const { container } = render(
      <PageState state="loading" label="工作台" layout="dashboard" />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("正在加载工作台");
    expect(screen.getByLabelText("工作台加载中")).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(container.querySelector('[data-layout="dashboard"]')).not.toBeNull();
    expect(
      container.querySelectorAll('[data-skeleton-role="summary"]'),
    ).toHaveLength(4);
  });

  it("preserves successful content in a partial state and retries only the failed region", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(
      <PageState state="partial" label="工作台" onRetry={onRetry}>
        <p>已加载的列表</p>
      </PageState>,
    );

    expect(screen.getByText("已加载的列表")).toBeVisible();
    expect(screen.getByText("部分内容未能加载")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /重\s*试/u }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("retains the requested layout for forbidden and unavailable states", () => {
    const { container, rerender } = render(
      <PageState state="forbidden" label="工作台" layout="dashboard" />,
    );

    expect(screen.getByText("无权访问")).toBeVisible();
    expect(container.querySelector('[data-subdued="true"]')).not.toBeNull();

    rerender(
      <PageState
        state="feature-unavailable"
        label="工作台"
        layout="dashboard"
        action={<button type="button">不应出现的动作</button>}
      />,
    );
    expect(screen.getByText("能力尚未开放")).toBeVisible();
    expect(screen.queryByRole("button", { name: "不应出现的动作" })).toBeNull();
  });
});
