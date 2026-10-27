// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import { CaptureProgressPanel } from "./CaptureProgressPanel";

const permission = vi.hoisted(() => ({ manage: true }));
vi.mock("../../shared/auth/use-capabilities", () => ({
  useCapabilities: () => ({ has: () => permission.manage }),
}));
vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
vi.mock("../../shared/lib/resource-labels", () => ({
  ResourceLabel: ({ id }: { id: string }) => <span>{id}</span>,
}));
vi.mock("../quality/RecordingProcessingDetails", () => ({
  RecordingProcessingDetails: () => null,
}));
vi.mock("../ingest/processing/ResumeProcessingAction", () => ({
  default: () => null,
}));
const mocked = vi.mocked(request);
const scope = {
  organizationId: "org",
  projectId: "project",
  regionCode: "global",
};
const cancelled = new Set<string>();
let failSecond = false;
const uploads = ["first", "second", "committed"];

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <CaptureProgressPanel scope={scope} taskId="task" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}

beforeEach(() => {
  permission.manage = true;
  failSecond = false;
  cancelled.clear();
  mocked.mockImplementation(async (options) => {
    if (options.method === "POST") {
      const id = options.path.includes("first:cancel") ? "first" : "second";
      if (id === "second" && failSecond)
        throw new Error("暂时无法清理上传分片");
      cancelled.add(id);
      return undefined as never;
    }
    const items = uploads
      .map((id) => ({
        upload_id: id,
        data_package_id: id,
        rollout_id: id,
        robot_id: "robot",
        dataset_id: "dataset",
        collection_task_id: "task",
        capture_mode: "CONTINUOUS",
        source_path: `${id}.mcap`,
        received: id === "committed",
        upload_state:
          id === "committed"
            ? "COMMITTED"
            : cancelled.has(id)
              ? "CANCELLED"
              : "UPLOADING",
        stage:
          id === "committed"
            ? "READY"
            : cancelled.has(id)
              ? "CANCELLED"
              : "UPLOADING",
        episode_count: 0,
        ready_episode_count: 0,
        failed_episode_count: 0,
        created_at: "2026-10-26T00:00:00Z",
      }))
      .filter(
        (item) =>
          !options.query?.unfinished_uploads_only ||
          item.upload_state === "UPLOADING",
      );
    return {
      items,
      total: items.length,
      counts: {
        upload_count: items.length,
        received_count: 1,
        awaiting_slice_count: 0,
        processing_count: 0,
        failed_count: 0,
        blocked_count: 0,
        episode_count: 0,
        ready_episode_count: 0,
        failed_episode_count: 0,
      },
    } as never;
  });
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("filters unfinished uploads and confirms a batch cancellation, then refreshes the rail", async () => {
  const client = setup();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  await screen.findByText("first.mcap");
  expect(screen.getAllByRole("button", { name: "取消上传" })).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "处理未完成上传" }));
  await waitFor(() => expect(screen.queryByText("committed.mcap")).toBeNull());
  fireEvent.click(
    await screen.findByRole("button", { name: "取消本页 2 条上传" }),
  );
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText("first.mcap")).toBeTruthy();
  expect(mocked.mock.calls.filter(([o]) => o.method === "POST")).toHaveLength(
    0,
  );
  fireEvent.click(within(dialog).getByRole("button", { name: "确认取消上传" }));
  await screen.findByText("当前没有未完成上传。");
  expect(cancelled.size).toBe(2);
  expect(mocked).toHaveBeenCalledWith(
    expect.objectContaining({
      method: "POST",
      scope,
      scopeMode: "explicit",
      path: "/projects/project/robot-ingest/uploads/first:cancel",
    }),
  );
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["dashboard"] });
});

it("keeps partial failures visible and retries only the failed upload", async () => {
  failSecond = true;
  setup();
  fireEvent.click(screen.getByRole("button", { name: "处理未完成上传" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "取消本页 2 条上传" }),
  );
  fireEvent.click(
    within(await screen.findByRole("dialog")).getByRole("button", {
      name: "确认取消上传",
    }),
  );
  await screen.findByText("部分上传未能取消");
  expect(cancelled.has("first")).toBe(true);
  expect(cancelled.has("second")).toBe(false);
  failSecond = false;
  fireEvent.click(screen.getByRole("button", { name: "重试取消" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    mocked.mock.calls.filter(([o]) => o.path.endsWith("first:cancel")),
  ).toHaveLength(1);
  expect(
    mocked.mock.calls.filter(([o]) => o.path.endsWith("second:cancel")),
  ).toHaveLength(2);
});

it("read-only users can filter but cannot cancel uploads", async () => {
  permission.manage = false;
  setup();
  await screen.findByText("first.mcap");
  fireEvent.click(screen.getByRole("button", { name: "处理未完成上传" }));
  await waitFor(() => expect(screen.queryByText("committed.mcap")).toBeNull());
  expect(screen.queryByRole("button", { name: "取消上传" })).toBeNull();
  expect(screen.queryByRole("button", { name: /取消本页/ })).toBeNull();
});
