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
    value: vi
      .fn()
      .mockImplementation(() => ({
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

it("requires explicit acknowledgement of the whole task and other robots' data", async () => {
  mount();
  await userEvent.click(screen.getByRole("button", { name: "删除任务及数据" }));
  const dialog = screen.getByRole("dialog");
  await waitFor(() =>
    expect(
      within(dialog).getByText(/其中 1 条来自其他机器人，也将一起删除/),
    ).toBeVisible(),
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
  mount();
  await userEvent.click(screen.getByRole("button", { name: "删除任务及数据" }));
  await userEvent.click(screen.getByRole("button", { name: /取\s*消/ }));
  expect(state.mutateAsync).not.toHaveBeenCalled();
});
