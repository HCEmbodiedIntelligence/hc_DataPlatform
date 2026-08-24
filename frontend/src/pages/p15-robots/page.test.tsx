// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import RobotsPage from "./page";

vi.mock("../../shared/auth/use-capabilities", () => ({
  useCapabilities: () => ({
    has: (capability: string) => capability === "robot.manage",
    loading: false,
    failed: false,
  }),
}));

const {
  createRobotMutate,
  createMaintenanceMutate,
  createComponentMutate,
  updateComponentMutate,
  transitionComponentMutate,
  updateRobotMutate,
  transitionRobotMutate,
  useMaintenanceRecords,
} = vi.hoisted(() => ({
  createRobotMutate: vi.fn(),
  createMaintenanceMutate: vi.fn(),
  createComponentMutate: vi.fn(),
  updateComponentMutate: vi.fn(),
  transitionComponentMutate: vi.fn(),
  updateRobotMutate: vi.fn(),
  transitionRobotMutate: vi.fn(),
  useMaintenanceRecords: vi.fn(),
}));

const robot = {
  id: "robot-1",
  displayName: "XR-01",
  serialNo: "XR-01-SN",
  lifecycle: "ACTIVE",
  connectivity: {
    state: "ONLINE",
    observedAt: "2026-08-21T08:00:00Z",
    source: "edge-agent",
    reasonCode: null,
  },
  effectiveModelBinding: null,
  etag: '"robot-1:2"',
  topologyRevision: "topology-1",
  allowedActions: ["VIEW", "EDIT", "TRANSITION"],
} as const;

const robotRow = {
  id: robot.id,
  displayName: robot.displayName,
  serialNo: robot.serialNo,
  lifecycle: robot.lifecycle,
  connectivity: robot.connectivity.state,
} as const;

vi.mock("../../features/robots/api", () => ({
  useRobots: () => ({
    isPending: false,
    error: null,
    refetch: vi.fn(),
    data: {
      items: [robotRow],
      pageInfo: {
        start_cursor: null,
        end_cursor: null,
        has_previous_page: false,
        has_next_page: false,
      },
      snapshotAt: "2026-08-21T08:00:00Z",
    },
  }),
  useRobotBootstrap: () => ({ data: robot }),
  useRobotComponents: () => ({
    data: { items: [], topologyRevision: "topology-1" },
    isFetching: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useRobotMaintenanceRecords: useMaintenanceRecords,
  useComponentFrames: () => ({ data: { items: [] } }),
  useComponentChannels: () => ({ data: { items: [] } }),
  useCreateRobot: () => ({
    isPending: false,
    error: null,
    mutate: createRobotMutate,
  }),
  useCreateRobotComponent: () => ({
    isPending: false,
    error: null,
    mutate: createComponentMutate,
  }),
  useUpdateRobot: () => ({
    isPending: false,
    error: null,
    mutate: updateRobotMutate,
  }),
  useUpdateRobotComponent: () => ({
    isPending: false,
    error: null,
    mutate: updateComponentMutate,
  }),
  useTransitionRobotLifecycle: () => ({
    isPending: false,
    error: null,
    mutate: transitionRobotMutate,
  }),
  useTransitionRobotComponentLifecycle: () => ({
    isPending: false,
    error: null,
    mutate: transitionComponentMutate,
  }),
  useCreateRobotMaintenanceRecord: () => ({
    isPending: false,
    error: null,
    mutate: createMaintenanceMutate,
  }),
}));

function renderPage(path = "/settings/robots?robotId=robot-1") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <RobotsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  const computedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    computedStyle(element),
  );
  if (!globalThis.crypto.randomUUID) {
    Object.defineProperty(globalThis.crypto, "randomUUID", {
      configurable: true,
      value: () => "p15-generated-key",
    });
  }
  createRobotMutate.mockImplementation((intent, callbacks) =>
    callbacks?.onSuccess?.({ ...robot, id: "robot-created" }),
  );
  createMaintenanceMutate.mockImplementation((_intent, callbacks) =>
    callbacks?.onSuccess?.(),
  );
  createComponentMutate.mockImplementation((_intent, callbacks) =>
    callbacks?.onSuccess?.({
      component: { ...robot, id: "component-created", robotId: "robot-1" },
      robotEtag: '"robot-1:3"',
      topologyRevision: "topology-2",
    }),
  );
  updateComponentMutate.mockImplementation((_intent, callbacks) =>
    callbacks?.onSuccess?.(),
  );
  transitionComponentMutate.mockImplementation((_intent, callbacks) =>
    callbacks?.onSuccess?.(),
  );
  updateRobotMutate.mockImplementation((_intent, callbacks) =>
    callbacks?.onSuccess?.(),
  );
  transitionRobotMutate.mockImplementation((_intent, callbacks) =>
    callbacks?.onSuccess?.(),
  );
  useMaintenanceRecords.mockReturnValue({
    isPending: false,
    isError: false,
    refetch: vi.fn(),
    data: {
      items: [
        {
          id: "maintenance-1",
          robot_id: "robot-1",
          component_id: null,
          event_type: "MAINTENANCE",
          previous_lifecycle_status: null,
          lifecycle_status: null,
          summary: "更换润滑脂",
          details: null,
          actor_id: "robot-manager",
          occurred_at: "2026-08-21T08:10:00Z",
        },
      ],
    },
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("P15 robot management page", () => {
  it("submits a real create command and selects the returned robot", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("button", { name: "新建机器人" }));
    await user.type(screen.getByLabelText("显示名称"), "新机器人");
    await user.type(screen.getByLabelText("序列号"), "NEW-SN-01");
    await user.click(screen.getByRole("button", { name: "创建机器人" }));

    await waitFor(() => {
      expect(createRobotMutate).toHaveBeenCalledWith(
        expect.objectContaining({
          displayName: "新机器人",
          serialNo: "NEW-SN-01",
          lifecycleStatus: "DRAFT",
          connectivityState: "OFFLINE",
          idempotencyKey: expect.any(String),
        }),
        expect.any(Object),
      );
    });
    expect(
      screen.queryByRole("dialog", { name: "新建机器人" }),
    ).not.toBeInTheDocument();
  });

  it("shows persisted maintenance history and submits a maintenance record", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("tab", { name: "维护记录" }));
    expect(await screen.findByText("更换润滑脂")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "新增维护记录" }));
    await user.type(screen.getByLabelText("维护摘要"), "检查急停按钮");
    await user.click(screen.getByRole("button", { name: "保存记录" }));

    await waitFor(() => {
      expect(createMaintenanceMutate).toHaveBeenCalledWith(
        expect.objectContaining({
          robotId: "robot-1",
          summary: "检查急停按钮",
          idempotencyKey: expect.any(String),
        }),
        expect.any(Object),
      );
    });
  });

  it("defers the maintenance query until its tab is selected", async () => {
    const user = userEvent.setup();
    renderPage();

    expect(useMaintenanceRecords).toHaveBeenLastCalledWith(null);
    await user.click(screen.getByRole("tab", { name: "维护记录" }));
    expect(useMaintenanceRecords).toHaveBeenLastCalledWith("robot-1");
  });

  it("adds a component with the current robot ETag rather than a client topology revision", async () => {
    const user = userEvent.setup();
    renderPage();

    const addComponentButtons = screen.getAllByRole("button", {
      name: "添加组件",
    });
    await user.click(addComponentButtons.at(-1)!);
    await user.type(screen.getByLabelText("组件模型 ID"), "arm-model-01");
    await user.type(screen.getByLabelText("组件类型"), "ARM");
    await user.type(screen.getByLabelText("显示名称"), "主机械臂");
    await user.type(screen.getByLabelText("序列号"), "ARM-SN-01");
    await user.click(
      screen.getAllByRole("button", { name: "添加组件" }).at(-1)!,
    );

    await waitFor(() => {
      expect(createComponentMutate).toHaveBeenCalledWith(
        expect.objectContaining({
          robotId: "robot-1",
          etag: '"robot-1:2"',
          componentModelId: "arm-model-01",
          componentType: "ARM",
          displayName: "主机械臂",
          serialNo: "ARM-SN-01",
          lifecycleStatus: "DRAFT",
          sortOrder: 0,
          idempotencyKey: expect.any(String),
        }),
        expect.any(Object),
      );
    });
  });
});
