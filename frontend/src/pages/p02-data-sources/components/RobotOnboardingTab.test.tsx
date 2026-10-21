// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RobotOnboardingTab } from "./RobotOnboardingTab";
import { parseRobotFilters } from "../onboarding-query";
import type { OnboardingRobot } from "../../../features/robot-ingest/onboarding";

const state = vi.hoisted(() => ({
  error: false,
  canDelete: true,
  deleteError: null as Error | null,
  deleteMutate: vi.fn(),
  online: null as number | null,
  robots: [] as OnboardingRobot[],
}));
vi.mock("../../../features/ingest/use-ingest-scope", () => ({
  useIngestScope: () => ({
    organizationId: "org-a",
    projectId: "project-a",
    regionCode: "global",
  }),
}));
vi.mock("../../../shared/auth/use-capabilities", () => ({
  useOrganizationCapabilities: () => ({
    loading: false,
    has: (capability: string) =>
      capability !== "robot.manage" || state.canDelete,
  }),
}));
vi.mock("../../../features/robots/api/deletion", () => ({
  useRobotDeletionCheck: () => ({
    data: {
      can_delete: true,
      blockers: [],
      items: [],
      total: 0,
      restricted_count: 0,
    },
    isPending: false,
    isFetching: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));
vi.mock("../../../features/robots/api/hooks", () => ({
  useScopedDeleteRobot: () => ({
    mutate: state.deleteMutate,
    reset: vi.fn(),
    isPending: false,
    error: state.deleteError,
  }),
}));
vi.mock("../../../shared/config/runtime", () => ({
  getRuntimeConfig: () => ({
    apiBaseUrl: "/api/v1",
    robotPlatformBaseUrl: "http://192.168.1.20:8088",
  }),
}));
vi.mock(
  "../../../features/robot-ingest/onboarding",
  async (importOriginal) => ({
    ...(await importOriginal<
      typeof import("../../../features/robot-ingest/onboarding")
    >()),
    useRobotOnboarding: () => ({
      data: {
        items: state.robots,
        total: 0,
        summary: {
          total_count: 31,
          online_count: state.online,
          pending_count: 4,
          attention_count: 2,
          unknown_count: 1,
        },
      },
      isPending: false,
      isError: state.error,
      error: new Error("服务连接失败"),
      refetch: vi.fn(),
    }),
  }),
);
vi.mock("./OnboardRobotDialog", () => ({ OnboardRobotDialog: () => null }));
vi.mock("./RobotIdentityConsole", () => ({ RobotIdentityConsole: () => null }));
// This test isolates onboarding filters and addresses. The shared configuration
// editor's requests and publication flow are covered by browser acceptance.
vi.mock("../../../features/capture-schemes/RobotSchemeAssociations", () => ({
  RobotSchemeAssociations: () => <div>配置编辑器测试标记</div>,
}));
function Location() {
  return <output aria-label="当前地址">{useLocation().search}</output>;
}
function mount(url: string) {
  render(
    <MemoryRouter initialEntries={[url]}>
      <RobotOnboardingTab />
      <Location />
    </MemoryRouter>,
  );
}
function query() {
  return new URLSearchParams(
    screen.getByLabelText("当前地址").textContent ?? "",
  );
}
beforeEach(() => {
  state.error = false;
  state.canDelete = true;
  state.deleteError = null;
  state.deleteMutate.mockReset();
  state.online = null;
  state.robots = [];
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
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
});
afterEach(cleanup);
function seedRobot() {
  state.robots = [
    {
      robot_id: "robot-a",
      display_name: "Robot A",
      serial_no: "serial-a",
      lifecycle_status: "ACTIVE",
      model_version_id: "model-a",
      updated_at: "2026-10-20T00:00:00Z",
      identity: null,
      credentials: [],
      legacy_sources: [],
      upload_permission: "NOT_ENABLED",
      credential_availability: "NOT_ISSUED",
      connection_state: "UNVERIFIED",
      last_communication_at: null,
      pending: true,
      faults: [],
    },
  ];
}
it("shows the robot-reachable platform address in the detail drawer", () => {
  seedRobot();
  mount("/ingest/sources?tab=robots&robot.detail=robot-a");
  expect(screen.getByText("平台地址")).toBeInTheDocument();
  expect(screen.getByText("http://192.168.1.20:8088/")).toBeInTheDocument();
  expect(screen.queryByText(/127\.0\.0\.1/)).not.toBeInTheDocument();
});
it("restores independent filters and card shortcuts clear business conditions while retaining sort and page size", async () => {
  const user = userEvent.setup();
  mount(
    "/ingest/sources?tab=robots&q=external-name&robot.q=serial-31&robot.permission=DISABLED&robot.credential=AVAILABLE&robot.page=3&robot.page_size=10&robot.sort=name",
  );
  expect(screen.getByLabelText("搜索机器人")).toHaveValue("serial-31");
  expect(
    within(screen.getByRole("button", { name: "机器人总数" })).getByText("31"),
  ).toBeVisible();
  expect(
    within(screen.getByRole("button", { name: "在线机器人" })).getByText(
      "未知",
    ),
  ).toBeVisible();
  expect(
    screen.getByText("有可用凭据", { selector: ".ant-tag" }),
  ).toBeVisible();
  await user.click(screen.getByRole("button", { name: "需处理设备" }));
  expect(parseRobotFilters(query())).toMatchObject({
    q: "",
    permission: "",
    credential: "",
    segment: "attention",
    page: 1,
    sort: "name",
    page_size: 10,
  });
  expect(query().get("q")).toBe("external-name");
  await user.type(screen.getByLabelText("搜索机器人"), "OpenArm");
  await user.click(screen.getByRole("button", { name: "应用筛选" }));
  expect(parseRobotFilters(query())).toMatchObject({
    q: "OpenArm",
    segment: "attention",
  });
  expect(screen.getByText("搜索：OpenArm")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "清除筛选" }));
  expect(parseRobotFilters(query())).toMatchObject({
    q: "",
    segment: "",
    sort: "name",
    page_size: 10,
  });
  expect(
    within(screen.getByRole("button", { name: "机器人总数" })).getByText("31"),
  ).toBeVisible();
});
it("query errors show unavailable cards even when old successful data is cached", () => {
  state.error = true;
  state.online = 5;
  mount("/ingest/sources");
  expect(screen.getByText("机器人接入暂不可用")).toBeVisible();
  for (const label of ["机器人总数", "在线机器人", "待完成接入", "需处理设备"])
    expect(
      within(screen.getByRole("button", { name: label })).getByText("暂不可用"),
    ).toBeVisible();
  expect(screen.getByRole("button", { name: /重\s*试/ })).toBeEnabled();
});

it("opens overview without the configuration editor and navigates to a separate configuration tab", async () => {
  const user = userEvent.setup();
  seedRobot();
  mount("/ingest/sources?tab=robots&robot.detail=robot-a");
  expect(screen.getByRole("tab", { name: "设备概览" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.queryByText("配置编辑器测试标记")).not.toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "采集方案" }));
  expect(screen.getByText("配置编辑器测试标记")).toBeVisible();
  expect(query().get("robot.section")).toBe("processing");
  await user.click(screen.getByRole("tab", { name: "设备概览" }));
  await waitFor(() =>
    expect(screen.queryByText("配置编辑器测试标记")).not.toBeInTheDocument(),
  );
});
it("deletes the robot ID from onboarding even after its upload identity was removed", async () => {
  const user = userEvent.setup();
  seedRobot();
  mount("/ingest/sources?tab=robots&robot.detail=robot-a");
  const drawer = screen.getByRole("dialog", { name: "Robot A" });
  await user.click(within(drawer).getByRole("button", { name: "删除机器人" }));
  const modal = screen.getByRole("dialog", { name: "删除机器人 · Robot A" });
  await waitFor(() =>
    expect(within(modal).getByText(/序列号：serial-a/)).toBeVisible(),
  );
  expect(state.deleteMutate).not.toHaveBeenCalled();
  state.deleteMutate.mockImplementation((_id, options) => {
    state.robots = [];
    options.onSuccess();
  });
  await user.click(
    within(modal).getByRole("button", { name: "确认删除机器人" }),
  );
  expect(state.deleteMutate).toHaveBeenCalledExactlyOnceWith(
    "robot-a",
    expect.any(Object),
  );
  expect(await screen.findByText("机器人“Robot A”已删除。")).toBeVisible();
  expect(query().has("robot.detail")).toBe(false);
  expect(query().has("robot.section")).toBe(false);
});
it("shows the actual rejection and never reports a historical robot as deleted", async () => {
  const user = userEvent.setup();
  seedRobot();
  state.deleteError = new Error("无法删除：上传记录 2 条");
  mount("/ingest/sources?tab=robots&robot.detail=robot-a");
  await user.click(
    within(screen.getByRole("dialog", { name: "Robot A" })).getByRole(
      "button",
      { name: "删除机器人" },
    ),
  );
  await waitFor(() =>
    expect(screen.getByText("无法删除：上传记录 2 条")).toBeVisible(),
  );
  expect(screen.queryByText("机器人“Robot A”已删除。")).not.toBeInTheDocument();
});
it("does not expose instance deletion to accounts with only upload management permission", () => {
  seedRobot();
  state.canDelete = false;
  mount("/ingest/sources?tab=robots&robot.detail=robot-a");
  expect(
    screen.queryByRole("button", { name: "删除机器人" }),
  ).not.toBeInTheDocument();
});
