// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  fireEvent,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RobotDeletionDialog } from "./RobotDeletionDialog";
import { useShellStore } from "../../shared/scope/shell-store";
import type { z } from "zod";
import type { deletionCheckSchema } from "./api/deletion";

const state = vi.hoisted(() => ({
  data: undefined as z.infer<typeof deletionCheckSchema> | undefined,
  failed: false,
  refetch: vi.fn(),
  fetching: false,
}));
vi.mock("./api/deletion", () => ({
  useRobotDeletionCheck: () => ({
    data: state.data,
    isPending: !state.data && !state.failed,
    isFetching: state.fetching,
    isError: state.failed,
    error: new Error("检查服务暂不可用"),
    refetch: state.refetch,
  }),
}));
vi.mock("./RobotResolutionActions", () => ({
  RobotResolutionActions: () => <div>自动处理操作</div>,
}));
const onDelete = vi.fn();
const onCancel = vi.fn();
function mount() {
  render(
    <MemoryRouter>
      <RobotDeletionDialog
        organizationId="org"
        robot={{ id: "robot", displayName: "X1", serialNo: "SN" }}
        canManage
        pending={false}
        error={null}
        onDelete={onDelete}
        onCancel={onCancel}
      />
    </MemoryRouter>,
  );
  const tab = screen.queryByRole("tab", { name: "关联清单" });
  if (tab) fireEvent.click(tab);
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  state.failed = false;
  state.fetching = false;
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  useShellStore.getState().setScope({
    organizationId: "org",
    projectId: "project",
    regionCode: "region",
  });
  state.data = {
    organization_id: "org",
    robot_id: "robot",
    can_delete: false,
    blockers: [
      {
        kind: "raw",
        count: 1,
        label: "原始数据",
        resolution: "保留成果时停用；确认不需要的任务可以单独删除任务及数据。",
      },
    ],
    items: [
      {
        kind: "raw",
        id: "raw-014",
        name: "simulation.mcap",
        status: "READY",
        project_id: "project",
        region_code: "region",
        collection_task_id: "task-014",
        task_code: "00000014",
        task_name: "pick and place",
        raw_source_id: "raw-014",
        dataset_id: "dataset-014",
        scheme_id: null,
        version: null,
      },
    ],
    total: 1,
    restricted_count: 0,
    offset: 0,
    limit: 5,
  };
});
afterEach(cleanup);

it("shows the real file, task and scoped destinations before a destructive request", async () => {
  mount();
  await waitFor(() =>
    expect(screen.getByText("simulation.mcap")).toBeVisible(),
  );
  expect(screen.getByText("任务 00000014 · pick and place")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "有关联，暂不能删除" }),
  ).toBeDisabled();
  expect(screen.getByRole("link", { name: "查看所属任务" })).toHaveAttribute(
    "href",
    "/collection-tasks?taskId=task-014",
  );
  expect(screen.getByRole("link", { name: "查看上传与处理" })).toHaveAttribute(
    "href",
    "/ingest/uploads/records?rawSource=raw-014",
  );
  await userEvent.click(screen.getByRole("tab", { name: "处理操作" }));
  expect(screen.getByText("自动处理操作")).toBeVisible();
  expect(onDelete).not.toHaveBeenCalled();
});
it("does not navigate to another project using the current scope", async () => {
  state.data!.items[0]!.project_id = "different-project";
  mount();
  await waitFor(() =>
    expect(
      screen.getByText(/请先在顶部切换至项目 different-project/),
    ).toBeVisible(),
  );
  expect(
    screen.queryByRole("link", { name: "查看所属任务" }),
  ).not.toBeInTheDocument();
});
it("explains restricted references without pretending the robot is unused", async () => {
  state.data!.items = [];
  state.data!.total = 0;
  state.data!.restricted_count = 1;
  mount();
  await waitFor(() =>
    expect(
      screen.getByText("另有 1 条记录不在你的数据查看权限内"),
    ).toBeVisible(),
  );
  expect(
    screen.getByRole("button", { name: "有关联，暂不能删除" }),
  ).toBeDisabled();
});
it("fails closed while the check is loading or unavailable and offers retry", async () => {
  state.data = undefined;
  state.failed = true;
  mount();
  expect(screen.getByRole("button", { name: "确认删除机器人" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "重新检查" }));
  expect(state.refetch).toHaveBeenCalledOnce();
  expect(onDelete).not.toHaveBeenCalled();
});
it("only confirms removal when an unused robot has passed the check", async () => {
  state.data!.can_delete = true;
  state.data!.blockers = [];
  state.data!.items = [];
  state.data!.total = 0;
  mount();
  await userEvent.click(screen.getByRole("button", { name: "确认删除机器人" }));
  expect(onDelete).toHaveBeenCalledOnce();
});
it("closes the dialog when opening a reference, enabling browser back navigation", async () => {
  mount();
  await userEvent.click(screen.getByRole("link", { name: "查看上传与处理" }));
  expect(onCancel).toHaveBeenCalledOnce();
});
