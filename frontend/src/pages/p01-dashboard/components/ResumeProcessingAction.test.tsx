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
import ResumeProcessingAction from "./ResumeProcessingAction";

const permissions = vi.hoisted(() => ({ canManage: true }));
vi.mock("../../../shared/api/http-client", () => ({ request: vi.fn() }));
vi.mock("../../../shared/auth/use-capabilities", () => ({
  useCapabilities: () => ({ has: () => permissions.canManage }),
}));
const scope = {
  organizationId: "org",
  projectId: "project",
  regionCode: "region",
  timezone: "Asia/Shanghai",
};
const progress = {
  import_id: "import-a",
  dataset_id: "dataset-a",
  status: "PARTIALLY_FAILED",
  episode_count: 154,
  ready: 132,
  failed: 22,
  resume_required: 21,
  reprocessing_conflicts: 1,
  updated_at: "2026-09-21T03:10:12Z",
};
const clients: QueryClient[] = [];
function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <ResumeProcessingAction scope={scope} importId="import-a" />
    </QueryClientProvider>,
  );
  return client;
}
beforeEach(() => {
  permissions.canManage = true;
  vi.mocked(request).mockReset();
  vi.mocked(request).mockResolvedValue(progress);
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.restoreAllMocks();
});

it("retries in place and refreshes dashboard/import progress without navigation", async () => {
  vi.mocked(request).mockImplementation(async (input) =>
    input.method === "POST"
      ? {
          ...progress,
          status: "PENDING",
          failed: 0,
          updated_at: "2026-09-21T04:00:00Z",
        }
      : progress,
  );
  const client = setup();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const button = screen.getByRole("button", { name: "重试本批次未完成处理" });
  await waitFor(() => expect(button).toBeEnabled());
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      method: "GET",
      path: "/projects/project/regions/region/lerobot-imports/import-a/processing",
    }),
  );
  expect(
    vi.mocked(request).mock.calls.some(([input]) => input.method === "POST"),
  ).toBe(false);
  invalidate.mockClear();
  fireEvent.click(button);
  expect(await screen.findByText("已提交，正在继续处理")).toBeVisible();
  expect(button).toBeDisabled();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      method: "POST",
      path: "/projects/project/regions/region/lerobot-imports/import-a:retry",
      scope,
    }),
  );
  await waitFor(() =>
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["dashboard"] }),
  );
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["native-processing"] });
});

it("shows an actionable error without treating the failed submission as running", async () => {
  vi.mocked(request).mockImplementation(async (input) => {
    if (input.method === "POST") throw new Error("任务暂不可重试");
    return progress;
  });
  setup();
  const button = screen.getByRole("button", { name: "重试本批次未完成处理" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  expect(await screen.findByText("任务暂不可重试")).toBeVisible();
  expect(screen.queryByText("已提交，正在继续处理")).not.toBeInTheDocument();
  await waitFor(() => expect(button).toBeEnabled());
});

it("does not submit or load processing controls without permission", () => {
  permissions.canManage = false;
  setup();
  expect(
    screen.getByRole("button", { name: "重试本批次未完成处理" }),
  ).toBeDisabled();
  expect(request).not.toHaveBeenCalled();
});
