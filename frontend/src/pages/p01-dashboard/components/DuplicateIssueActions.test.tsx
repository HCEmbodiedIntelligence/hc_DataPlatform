// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "../../../shared/api/http-client";
import type { DashboardDataIssue } from "../../../features/dashboard/types";
import DuplicateIssueActions from "./DuplicateIssueActions";

vi.mock("../../../shared/api/http-client", () => ({ request: vi.fn() }));
vi.mock("../../../shared/auth/use-capabilities", () => ({
  useCapabilities: () => ({ has: () => true, loading: false, failed: false }),
}));
const scope = {
  organizationId: "org",
  projectId: "project",
  regionCode: "region",
  timezone: "Asia/Shanghai",
};
const issue: DashboardDataIssue = {
  task_id: "task",
  rollout_id: "episode-10",
  data_package_id: "episode-10",
  category: "PROCESSING_CONFLICT",
  stage: "STANDARDIZATION",
  reason_code: "ALIGNMENT_ATTEMPT_IMMUTABLE",
  label: "处理结果冲突",
  description: "处理结果冲突",
  source_import_id: "import",
  source_episode_index: 10,
  alignment_attempt_id: "old-attempt",
  lance_ready: false,
  findings: [],
};
const clients: QueryClient[] = [];
function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <DuplicateIssueActions scope={scope} issue={issue} />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.mocked(request).mockReset();
  vi.mocked(request).mockResolvedValue({
    status: "UNRESOLVED",
    available: true,
  });
  const original = window.getComputedStyle;
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    original(element),
  );
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.restoreAllMocks();
});

it("requires confirmation, allows cancellation, and sends only the selected episode", async () => {
  setup();
  const remove = screen.getByRole("button", { name: "移除这条数据" });
  await waitFor(() => expect(remove).toBeEnabled());
  fireEvent.click(remove);
  await waitFor(() =>
    expect(screen.getByText(/同批其他数据不受影响/)).toBeVisible(),
  );
  fireEvent.click(screen.getByRole("button", { name: /取\s*消/ }));
  expect(
    vi
      .mocked(request)
      .mock.calls.every(([options]) => options.method === "GET"),
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "重新处理并入库" }));
  vi.mocked(request).mockResolvedValue({ status: "PENDING", available: false });
  fireEvent.click(screen.getByRole("button", { name: "确认重新入库" }));
  await screen.findByText("已提交，等待重新处理");
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      method: "POST",
      path: "/projects/project/regions/region/lerobot-imports/import/episodes/10/resolution",
      scope,
      body: {
        action: "REPROCESS",
        expected_attempt_id: "old-attempt",
        request_id: expect.any(String),
      },
    }),
  );
  expect(remove).toBeDisabled();
});

it("shows a discard receipt instead of claiming data was ingested", async () => {
  setup();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "移除这条数据" })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "移除这条数据" }));
  vi.mocked(request).mockResolvedValue({
    status: "DISCARDED",
    available: false,
  });
  fireEvent.click(screen.getByRole("button", { name: "确认移除" }));
  expect(
    await screen.findByText(
      "已移除这条未入库记录，原始文件与历史处理结果已保留",
    ),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "重新处理并入库" })).toBeDisabled();
});

it("restores running progress after reopening and makes failures actionable", async () => {
  vi.mocked(request).mockResolvedValue({ status: "RUNNING", available: false });
  setup();
  await screen.findByText("正在重新处理并入库");
  expect(screen.getByRole("button", { name: "移除这条数据" })).toBeDisabled();
  vi.mocked(request).mockResolvedValue({
    status: "FAILED",
    available: true,
    error_code: "SOURCE_INVALID",
  });
  fireEvent.click(screen.getByRole("button", { name: "刷新处理状态" }));
  await screen.findByText("错误码：SOURCE_INVALID");
  expect(screen.getByRole("button", { name: "重新处理并入库" })).toBeEnabled();
});
