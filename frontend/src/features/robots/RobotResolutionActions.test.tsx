// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RobotResolutionActions } from "./RobotResolutionActions";
import type { z } from "zod";
import type { resolutionPlanSchema } from "./api/resolution";

const state = vi.hoisted(() => ({
  plan: undefined as z.infer<typeof resolutionPlanSchema> | undefined,
  refetch: vi.fn(),
  mutateAsync: vi.fn(),
  isError: false,
  isFetching: false,
}));
vi.mock("./api/resolution", () => ({
  useRobotResolutionPlan: () => ({
    data: state.plan,
    isPending: !state.plan,
    isFetching: state.isFetching,
    isError: state.isError,
    error: new Error("暂不可用"),
    refetch: state.refetch,
  }),
  useResolveRobotAssociations: () => ({
    mutateAsync: state.mutateAsync,
    isPending: false,
  }),
}));
const refreshed = vi.fn();
function mount(canManage = true) {
  render(
    <RobotResolutionActions
      organizationId="org"
      robotId="source"
      canManage={canManage}
      disabled={false}
      onResolved={refreshed}
      onBusyChange={() => {}}
    />,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  state.isError = false;
  state.isFetching = false;
  state.mutateAsync.mockResolvedValue({ message: "新的关联已创建" });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
    })),
  });
  state.plan = {
    organization_id: "org",
    robot_id: "source",
    revision: "reviewed-snapshot",
    can_transfer: true,
    transfer_reason: null,
    targets: [{ id: "target", name: "接收机器人", serial_no: "SN-2" }],
    sources: [],
    tasks: [
      {
        project_id: "project",
        region_code: "region",
        task_id: "task",
        task_code: "00000014",
        name: "测试任务",
        etag: '"v1"',
        raw_count: 3,
        other_robot_raw_count: 1,
        can_delete: true,
        reason: null,
        retry_key: null,
      },
    ],
    can_clean_config: false,
    config_count: 1,
    cleanup_reason: "先删除关联数据。",
  };
});
afterEach(cleanup);

it("creates new associations only after choosing a destination and confirming", async () => {
  mount();
  expect(
    screen.getByRole("button", { name: "创建新关联并转移" }),
  ).toBeDisabled();
  await userEvent.click(
    screen.getByRole("combobox", { name: "接收关联的机器人实例" }),
  );
  await userEvent.click(await screen.findByText("接收机器人 · SN-2"));
  await userEvent.click(
    screen.getByRole("button", { name: "创建新关联并转移" }),
  );
  expect(state.mutateAsync).not.toHaveBeenCalled();
  await waitFor(() =>
    expect(screen.getByText(/系统会创建独立关联和配置副本/)).toBeVisible(),
  );
  await userEvent.click(screen.getByRole("button", { name: "确认执行" }));
  await waitFor(() =>
    expect(state.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "TRANSFER",
        target_robot_id: "target",
        revision: "reviewed-snapshot",
      }),
    ),
  );
  expect(refreshed).toHaveBeenCalledOnce();
  expect(state.refetch).toHaveBeenCalledOnce();
});

it("can delete the only linked source without a transfer target", async () => {
  state.plan = {
    ...state.plan!,
    targets: [],
    tasks: [],
    config_count: 0,
    sources: [
      {
        project_id: "p",
        region_code: "east",
        source_id: "src",
        name: "旧数据源",
        can_delete: true,
      },
    ],
  };
  mount();
  await userEvent.click(screen.getByRole("button", { name: "删除数据源" }));
  expect(screen.getByRole("button", { name: "确认永久删除" })).toBeDisabled();
  await userEvent.click(
    screen.getByRole("checkbox", { name: /我确认删除该数据源/ }),
  );
  await userEvent.click(screen.getByRole("button", { name: "确认永久删除" }));
  await waitFor(() =>
    expect(state.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "DELETE_SOURCE",
        revision: "reviewed-snapshot",
        source: { project_id: "p", region_code: "east", source_id: "src" },
      }),
    ),
  );
  expect(refreshed).toHaveBeenCalledOnce();
});

it.each(["ok", "storage failure", "changed plan"])(
  "purges reviewed files before deleting the source (%s)",
  async (outcome) => {
    const source = {
      project_id: "project",
      region_code: "region",
      source_id: "src",
    };
    state.plan!.tasks[0]!.other_robot_raw_count = 0;
    state.plan!.sources = [
      { ...source, name: "采集站", version: 3, can_delete: true },
    ];
    const next = { ...state.plan!, revision: "after-purge", tasks: [] };
    if (outcome === "changed plan")
      next.sources = [{ ...next.sources[0]!, version: 4 }];
    state.refetch.mockResolvedValue({ data: next });
    if (outcome === "storage failure")
      state.mutateAsync.mockRejectedValueOnce(new Error("存储清理失败"));
    mount();
    await userEvent.click(
      screen.getByRole("button", { name: "删除数据源及采集文件" }),
    );
    expect(screen.getByRole("dialog")).toHaveTextContent("00000014 · 测试任务");
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "确认永久删除" }));
    await waitFor(() => expect(refreshed).toHaveBeenCalled());
    expect(state.mutateAsync).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({
        action: "DELETE_TASK",
        task_id: "task",
        revision: "reviewed-snapshot",
      }),
    );
    if (outcome === "ok") {
      expect(state.mutateAsync).toHaveBeenNthCalledWith(
        2,
        expect.objectContaining({
          action: "DELETE_SOURCE",
          revision: "after-purge",
          source,
        }),
      );
    } else {
      expect(state.mutateAsync).toHaveBeenCalledTimes(1);
    }
  },
);

it("requires choosing tasks separately if multiple sources share their scope", () => {
  const source = {
    project_id: "project",
    region_code: "region",
    source_id: "src",
    name: "站点",
    can_delete: true,
  };
  state.plan!.tasks[0]!.other_robot_raw_count = 0;
  state.plan!.sources = [source, { ...source, source_id: "src2" }];
  mount();
  for (const button of screen.getAllByRole("button", {
    name: "删除数据源及采集文件",
  }))
    expect(button).toBeDisabled();
});

it("keeps a shared task's files out of robot deletion", () => {
  mount();
  expect(screen.getByRole("button", { name: "删除任务及数据" })).toBeDisabled();
});

it("requires explicit acknowledgement of task file deletion", async () => {
  state.plan!.tasks[0]!.other_robot_raw_count = 0;
  mount();
  await userEvent.click(screen.getByRole("button", { name: "删除任务及数据" }));
  const dialog = screen.getByRole("dialog");
  await waitFor(() =>
    expect(within(dialog).getByText(/当前共 3 条原始数据/)).toBeVisible(),
  );
  expect(screen.getByRole("button", { name: "确认永久删除" })).toBeDisabled();
  expect(state.mutateAsync).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: "确认永久删除" }));
  await waitFor(() =>
    expect(state.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "DELETE_TASK",
        project_id: "project",
        task_id: "task",
      }),
    ),
  );
});

it("reuses a durable cleanup key when resuming an interrupted deletion", async () => {
  state.plan!.tasks[0]!.other_robot_raw_count = 0;
  state.plan!.tasks[0]!.retry_key = "durable-key";
  mount();
  await userEvent.click(screen.getByRole("button", { name: "重试数据清理" }));
  await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: "确认永久删除" }));
  await waitFor(() =>
    expect(state.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ key: "durable-key" }),
    ),
  );
});

it("refreshes the plan and shows an actionable error after a rejected request", async () => {
  state.plan!.can_clean_config = true;
  state.mutateAsync.mockRejectedValue(new Error("关联记录已变化，请重新核对"));
  mount();
  await userEvent.click(screen.getByRole("button", { name: "清理配置关联" }));
  await userEvent.click(screen.getByRole("button", { name: "确认执行" }));
  expect(await screen.findByText("关联记录已变化，请重新核对")).toBeVisible();
  expect(state.refetch).toHaveBeenCalledOnce();
  expect(screen.queryByText("新的关联已创建")).not.toBeInTheDocument();
});

it("keeps operations disabled when the actor lacks management permission", () => {
  mount(false);
  expect(
    screen.getByRole("button", { name: "创建新关联并转移" }),
  ).toBeDisabled();
  expect(screen.getByRole("button", { name: "删除任务及数据" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "清理配置关联" })).toBeDisabled();
});

it("does not run a destructive operation when its confirmation is cancelled", async () => {
  state.plan!.tasks[0]!.other_robot_raw_count = 0;
  mount();
  await userEvent.click(screen.getByRole("button", { name: "删除任务及数据" }));
  await userEvent.click(screen.getByRole("button", { name: /取\s*消/ }));
  expect(state.mutateAsync).not.toHaveBeenCalled();
});
