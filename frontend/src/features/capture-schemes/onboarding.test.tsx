// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { OnboardRobotDialog } from "../../pages/p02-data-sources/components/OnboardRobotDialog";
import { draftKey, saveDraft } from "./navigation";
const state = vi.hoisted(() => ({ save: vi.fn(), reset: vi.fn() }));
vi.mock("../../shared/auth/use-capabilities", () => ({
  useOrganizationCapabilities: () => ({ has: () => true }),
}));
vi.mock("../robot-ingest/onboarding", () => ({
  useOnboardRobot: () => ({
    mutateAsync: state.save,
    reset: state.reset,
    isPending: false,
    isError: false,
  }),
}));
vi.mock("../robot-ingest/ConnectionLink", () => ({
  ConnectionLink: () => <div>真实连接入口占位测试组件</div>,
}));
vi.mock("../robots/api/hooks", () => ({
  useScopedRobots: () => ({ data: { items: [] }, isPending: false }),
}));
vi.mock("../robot-models/api/hooks", () => ({
  useScopedRobotModels: () => ({ data: { items: [] }, isPending: false }),
}));
vi.mock("./RobotSchemeAssociations", () => ({
  RobotSchemeAssociations: ({ robotId }: { robotId: string }) => (
    <div>关联已保存实例 {robotId}</div>
  ),
}));
const scope = {
  organizationId: "org",
  projectId: "project",
  regionCode: "global",
};
const robot = {
  robot_id: "saved-robot",
  model_version_id: null,
  identity: { ingest_identity_id: "identity" },
  upload_permission: "ENABLED",
};
let client: QueryClient;
function mount() {
  client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <OnboardRobotDialog
          scope={scope}
          canCreate
          onClose={vi.fn()}
          onDone={vi.fn()}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  sessionStorage.clear();
  state.save.mockReset();
  state.reset.mockReset();
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: () => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  });
  const computed = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((e) => computed(e));
});
afterEach(() => {
  cleanup();
  client.clear();
  vi.restoreAllMocks();
});
it("retries onboarding with the same intent and restores receipt on return without recreating entities", async () => {
  const key = draftKey("onboarding:new", scope);
  saveDraft(key, {
    robot_id: "existing",
    allowed_formats: ["LEROBOT_V3"],
    idempotency_key: "fixed-intent",
    generate_credential: false,
  });
  state.save
    .mockRejectedValueOnce(new Error("响应丢失"))
    .mockResolvedValue({ robot });
  const user = userEvent.setup();
  const first = mount();
  await user.click(screen.getByRole("button", { name: "下一步" }));
  await user.click(screen.getByRole("button", { name: "保存并继续" }));
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(1));
  await user.click(screen.getByRole("button", { name: "保存并继续" }));
  await waitFor(() =>
    expect(screen.getByText("关联已保存实例 saved-robot")).toBeVisible(),
  );
  expect(state.save.mock.calls[0]![0]).toEqual(state.save.mock.calls[1]![0]);
  first.unmount();
  mount();
  await waitFor(() =>
    expect(screen.getByText("关联已保存实例 saved-robot")).toBeVisible(),
  );
  expect(state.save).toHaveBeenCalledTimes(2);
  await user.click(screen.getByRole("button", { name: "继续连接采集端" }));
  expect(screen.getByText("真实连接入口占位测试组件")).toBeVisible();
  expect(state.save).toHaveBeenCalledTimes(2);
});
