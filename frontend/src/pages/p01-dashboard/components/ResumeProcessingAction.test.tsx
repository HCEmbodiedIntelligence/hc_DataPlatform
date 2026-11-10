// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import * as matchers from "@testing-library/jest-dom/matchers";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { request } from "../../../shared/api/http-client";
import ResumeProcessingAction from "./ResumeProcessingAction";

expect.extend(matchers);

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
      <MemoryRouter>
        <ResumeProcessingAction scope={scope} importId="import-a" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}
beforeEach(() => {
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getComputedStyle(element),
  );
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

it("retries the failed MCAP execution with its pinned plan instead of starting intake again", async () => {
  let retried = false;
  vi.mocked(request).mockImplementation(async (input) => {
    if (input.path.endsWith("/mcap-plan-versions")) return { items: [] };
    if (input.path.endsWith(":retry") && input.method === "POST") {
      retried = true;
      return {};
    }
    if (input.path.endsWith("/mcap-processing")) {
      return {
        files: [
          {
            processing_id: "mcap-a",
            path: "source.mcap",
            status: retried ? "RUNNING" : "FAILED",
            recording_id: null,
            robot_id: "robot-a",
            collection_task_id: "task-a",
            configuration: { state_topic: "/joint_states" },
            config_reference: { plan_revision: 3, scheme: { scheme_id: "x1", version: 4 } },
            channel_inventory: null,
            error: retried ? null : "MCAP_DEPTH_UNITS_REQUIRED: 请设置深度单位",
          },
        ],
        history: [],
        same_recording: null,
      };
    }
    return {
      ...progress,
      source_format: "MCAP",
      status: input.method === "POST" ? "PENDING" : "FAILED",
    };
  });
  setup();
  await screen.findByRole("button", { name: "重试处理" });
  const toggle = screen.queryByRole("button", { name: "source.mcap 详情" });
  if (toggle) fireEvent.click(toggle);
  await waitFor(() => expect(screen.getByText(/MCAP_DEPTH_UNITS_REQUIRED/)).toBeVisible());
  const button = await screen.findByRole("button", { name: "重试处理" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  fireEvent.click(await screen.findByRole("button", { name: "确认重试处理" }));
  expect(await screen.findByText(/准备录制/)).toBeVisible();
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      method: "POST",
      path: "/projects/project/regions/region/lerobot-imports/import-a:retry",
      body: undefined,
    }),
  );
  await waitFor(() => expect(screen.queryByRole("button", { name: "重试处理" })?.matches(":enabled")).toBeFalsy());
});

it("defaults to the current task plan when the intake configuration is unusable", async () => {
  vi.mocked(request).mockImplementation(async (input) => {
    if (input.path.endsWith("/mcap-plan-versions")) {
      return { items: [
        { revision: 7, scheme: { scheme_id: "x1", version: 3 } },
        { revision: 6, scheme: { scheme_id: "x1", version: 2 } },
      ] };
    }
    if (input.path.endsWith("/mcap-processing")) {
      return {
        files: [{
          processing_id: "mcap-a", path: "source.mcap", status: "FAILED",
          recording_id: null, robot_id: "robot-a", collection_task_id: "task-a",
          configuration: {}, config_reference: { error: "CAPTURE_PLAN_REQUIRED" },
          channel_inventory: null, error: "CAPTURE_MAPPING_UNVERIFIED",
        }],
        history: [], same_recording: null,
      };
    }
    return { ...progress, source_format: "MCAP", status: "FAILED" };
  });
  setup();
  fireEvent.click(await screen.findByRole("button", { name: "重试处理" }));
  const dialog = await screen.findByRole("dialog");
  await waitFor(() => expect(dialog).toHaveTextContent("任务方案 v7 · 采集方案 v3"));
  fireEvent.click(await screen.findByRole("button", { name: "确认重试处理" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith(expect.objectContaining({
    method: "POST",
    path: "/projects/project/regions/region/lerobot-imports/import-a:reprocess-mcap",
    body: { request_id: expect.any(String), plan_revision: 7 },
  })));
});

it.each([false, true])("offers a newer task plan for an already prepared MCAP (new plan: %s)", async (hasNew) => {
  vi.mocked(request).mockImplementation(async (input) => {
    if (input.path.endsWith("/mcap-plan-versions")) {
      return { items: (hasNew ? [3, 2] : [2]).map((revision) => ({
        revision, scheme: { scheme_id: "x1", version: revision + 1 },
      })) };
    }
    if (input.path.endsWith("/mcap-processing")) return {
      files: [{
        processing_id: "mcap-a", path: "source.mcap", status: "READY", recording_id: "rec",
        robot_id: "robot-a", collection_task_id: "task-a", configuration: { state_topic: "/state" },
        config_reference: { plan_revision: 2, scheme: { scheme_id: "x1", version: 3 } },
      }], history: [], same_recording: null,
    };
    return { ...progress, source_format: "MCAP", status: "SUCCEEDED" };
  });
  setup();
  fireEvent.click(await screen.findByRole("button", { name: "按新方案重处理" }));
  const confirm = await screen.findByRole("button", { name: "确认按新方案重处理" });
  if (!hasNew) {
    await waitFor(() => expect(screen.getByText("暂无比本次录制使用的任务方案 v2 更新的版本")).toBeVisible());
    expect(screen.getByRole("link", { name: "编辑采集方案" })).toHaveAttribute("href", "/settings/capture-schemes/x1?edit=1");
    expect(screen.getByRole("link", { name: "打开采集任务" })).toHaveAttribute("href", "/collection-tasks?taskId=task-a");
    expect(confirm).toBeDisabled();
    expect(request).not.toHaveBeenCalledWith(expect.objectContaining({ method: "POST" }));
    return;
  }
  await waitFor(() => expect(confirm).toBeEnabled());
  fireEvent.click(confirm);
  await waitFor(() => expect(request).toHaveBeenCalledWith(expect.objectContaining({
    method: "POST", path: expect.stringMatching(/:reprocess-mcap$/),
    body: { request_id: expect.any(String), plan_revision: 3 },
  })));
});
