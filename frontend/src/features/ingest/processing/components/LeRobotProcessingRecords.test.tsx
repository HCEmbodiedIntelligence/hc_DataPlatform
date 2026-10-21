// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "../../../../shared/api/http-client";
import { LeRobotProcessingRecords } from "./LeRobotProcessingRecords";

vi.mock("../../../../shared/api/http-client", () => ({ request: vi.fn() }));
vi.mock("../../../../shared/auth/use-capabilities", () => ({
  useCapabilities: () => ({ has: () => true }),
}));
vi.mock("../../../../shared/lib/resource-labels", () => ({
  ResourceLabel: () => <span>Task 014</span>,
}));
vi.mock("./ProcessingLabels", () => ({
  useProcessingConfiguration: () => ({ data: null }),
  ProcessingLabels: () => null,
}));
let linked = true;
const scope = {
  organizationId: "org",
  projectId: "project",
  regionCode: "global",
};
beforeEach(() => {
  linked = true;
  vi.mocked(request).mockReset();
  vi.mocked(request).mockImplementation(async (options) => {
    if (options.path.endsWith("/mcap-processing"))
      return {
        same_recording: linked
          ? { episode_id: "preserved", evidence: {} }
          : null,
        files: [
          {
            processing_id: "old",
            path: "simulation.mcap",
            status: "FAILED",
            error: "MCAP_REQUIRED_RGB_MISSING",
            robot_id: "robot",
            configuration: { state_topic: "/state" },
            config_reference: null,
          },
        ],
        history: [],
      };
    return [
      {
        import_id: "raw",
        dataset_id: "dataset",
        source_format: "MCAP",
        status: "FAILED",
        episode_count: 0,
        ready: 0,
        failed: 0,
        last_error_code: "MCAP_REQUIRED_RGB_MISSING",
        last_error_detail: "缺少 observation.images.head（头部相机视频）",
        updated_at: "2026-10-19T00:00:00Z",
      },
    ];
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <LeRobotProcessingRecords scope={scope} canManage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
});
afterEach(cleanup);
it("presents the confirmed same-source receipt and keeps historical failure details without a duplicate retry action", async () => {
  const user = userEvent.setup();
  await screen.findByText("同次录制 · 已关联成功结果");
  expect(
    screen.queryByRole("button", { name: "准备录制 / 重试" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "重试未完成处理" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText("MCAP_REQUIRED_RGB_MISSING"),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("缺少 observation.images.head（头部相机视频）")).not.toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "管理实例采集方案" }),
  ).toHaveAttribute("href", expect.stringContaining("robot=robot"));
  await user.click(screen.getByText("查看此前错误与当时配置"));
  expect(await screen.findByText("MCAP_REQUIRED_RGB_MISSING")).toBeVisible();
  expect(screen.getByRole("button", { name: "重试处理" })).toBeDisabled();
  expect(request).not.toHaveBeenCalledWith(
    expect.objectContaining({ method: "POST" }),
  );
});
it("retains retry controls for an unresolved ordinary failure", async () => {
  linked = false;
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "重试未完成处理" }),
    ).toBeEnabled(),
  );
  expect(screen.getByRole("button", { name: "准备录制 / 重试" })).toBeEnabled();
  expect(screen.getByText("缺少 observation.images.head（头部相机视频）")).toBeVisible();
});
