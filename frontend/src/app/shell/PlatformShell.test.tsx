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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { ProviderHarness } from "../providers";
import { makeScopeKey } from "../../entities/scope";
import { useShellStore } from "../../shared/scope/shell-store";
import { PlatformShell, type ScopeOption } from "./PlatformShell";

const scope = {
  organizationId: "org-shell-test",
  projectId: "project-shell-test",
  regionCode: "cn-shanghai",
} as const;

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}</output>;
}

beforeEach(() => {
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getComputedStyle(element),
  );
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
      matches: query.includes("1360"),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  useShellStore.getState().setSession(
    {
      actorId: "actor-shell-test",
      displayName: "测试用户",
      roleIds: ["PROJECT_DEVELOPER"],
    },
    "session-shell-test",
  );
  useShellStore.getState().setScope(scope);
  useShellStore.getState().setAuthorization({
    scopeKey: makeScopeKey(scope),
    roleVersion: "role-shell-test",
    capabilities: [
      "upload.read",
      "annotation_task.read",
      "manual_issue.read",
      "robot.read",
    ],
    fetchedAt: "2026-08-17T00:00:00Z",
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
  useShellStore.setState({
    principal: null,
    sessionToken: null,
    scope: null,
    scopeKey: makeScopeKey({ organizationId: "unscoped" }),
    scopeChanging: false,
    authorization: null,
    authorizationLoading: false,
    authorizationFailed: false,
  });
});

const defaultScopeOptions = [
  {
    ...scope,
    organizationName: "杭叉集团",
    projectName: "双臂采集一期",
    regionName: "华东-01",
  },
] as const;

function renderShell(
  onLogout?: () => void,
  scopeOptions: readonly ScopeOption[] = defaultScopeOptions,
  initialPath = "/dashboard",
) {
  render(
    <ProviderHarness>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route
            path="/"
            element={
              <PlatformShell
                authorizationLoader={async (selectedScope) => ({
                  scopeKey: makeScopeKey(selectedScope),
                  roleVersion: "role-shell-test",
                  capabilities: [
                    "upload.read",
                    "annotation_task.read",
                    "manual_issue.read",
                    "robot.read",
                  ],
                  fetchedAt: "2026-08-17T00:00:00Z",
                })}
                pageAvailability={{
                  P01: true,
                  P03: true,
                  P08: true,
                  P09: true,
                }}
                {...(onLogout ? { onLogout } : {})}
                scopeOptions={scopeOptions}
              />
            }
          >
            <Route path="*" element={<LocationProbe />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ProviderHarness>,
  );
}

describe("PlatformShell", () => {
  it("does not report a failed project snapshot in platform-only account mode", () => {
    useShellStore.setState({
      scope: null,
      authorization: null,
      authorizationFailed: false,
      platformCapabilities: ["platform.account.read"],
    });

    renderShell();

    expect(
      screen.queryByText("授权状态不可用，当前作用域已按失败关闭处理。"),
    ).not.toBeInTheDocument();
  });

  it("uses the official fixed-size brand asset and consolidated function navigation", async () => {
    renderShell();

    const brandLink = screen.getByRole("link", {
      name: "杭叉集团 HC 数据平台工作台",
    });
    const logo = screen.getByRole("img", { name: "杭叉集团" });
    expect(brandLink).toHaveAttribute("href", "/");
    expect(logo).toHaveAttribute(
      "src",
      expect.stringContaining("hangcha-logo.png"),
    );
    expect(logo).toHaveAttribute("width", "106");
    expect(logo).toHaveAttribute("height", "62");
    expect(screen.getByRole("link", { name: "数据上传" })).toHaveAttribute(
      "href",
      "/ingest/uploads/new",
    );
    expect(screen.getByRole("link", { name: "数据标注" })).toHaveAttribute(
      "href",
      "/annotations/annotate",
    );
    expect(screen.getByText("采集与接收")).toBeVisible();
    expect(screen.getByText("数据生产")).toBeVisible();
    expect(screen.queryByText("数据清洗")).not.toBeInTheDocument();
    expect(screen.queryByText("清洗草稿")).not.toBeInTheDocument();
  });

  it("shows project and region context and exposes the account notification inbox", () => {
    renderShell();

    expect(screen.getByRole("combobox", { name: "当前项目" })).toBeEnabled();
    expect(screen.getByRole("combobox", { name: "当前区域" })).toBeEnabled();
    expect(screen.getByText(/双臂采集一期/u)).toBeVisible();
    expect(screen.getByText("华东-01")).toBeVisible();
    expect(screen.getByRole("button", { name: "全局搜索" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "通知" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "账户菜单" })).toBeEnabled();
  });

  it("switches duplicate project ids across organizations by their composite identity", async () => {
    const user = userEvent.setup();
    renderShell(undefined, [
      defaultScopeOptions[0],
      {
        organizationId: "org-shell-other",
        organizationName: "组织乙",
        projectId: scope.projectId,
        projectName: "双臂采集一期",
        regionCode: "eu-central",
        regionName: "欧洲-01",
      },
    ]);

    await user.click(screen.getByRole("combobox", { name: "当前项目" }));
    await user.click(await screen.findByText("组织乙 / 双臂采集一期"));

    await waitFor(() =>
      expect(useShellStore.getState().scope).toEqual({
        organizationId: "org-shell-other",
        projectId: scope.projectId,
        regionCode: "eu-central",
      }),
    );
  });

  it("lists only authorized projects and never restores a stale active project", async () => {
    const user = userEvent.setup();
    useShellStore.getState().setScope({
      organizationId: "stale-organization",
      projectId: "stale-private-project",
      regionCode: "stale-region",
    });
    renderShell();

    expect(
      screen.queryByText(/stale-private-project/u),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("combobox", { name: "当前项目" }));

    expect(
      await screen.findByRole("option", { name: /双臂采集一期/u }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/stale-private-project/u),
    ).not.toBeInTheDocument();
  });

  it("keeps the full personal shell usable without a project scope", async () => {
    useShellStore.setState({
      scope: null,
      authorization: null,
      authorizationLoading: false,
      authorizationFailed: false,
      sessionScopes: [],
      bootstrapLoaded: true,
    });
    const user = userEvent.setup();
    renderShell(vi.fn(), []);

    const scopeGroup = screen.getByRole("group", { name: "当前作用域" });
    const scopeSlots = scopeGroup.querySelectorAll("[data-scope-slot]");
    expect(scopeSlots).toHaveLength(2);
    expect(scopeSlots[0]).toHaveAttribute("data-scope-slot", "project");
    expect(scopeSlots[1]).toHaveAttribute("data-scope-slot", "region");
    expect(
      within(scopeGroup).getByLabelText("当前项目：尚未加入组织或项目"),
    ).toBeVisible();
    expect(
      screen.queryByRole("link", { name: "个人主页" }),
    ).not.toBeInTheDocument();
    expect(
      within(scopeGroup).getByRole("link", {
        name: "到账户设置管理",
      }),
    ).toHaveAttribute("href", "/account/settings?tab=memberships");
    expect(
      screen.queryByRole("combobox", { name: "当前项目" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "全局搜索" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "工作台" })).toBeVisible();
    expect(screen.getByRole("link", { name: "数据上传" })).toBeVisible();
    expect(screen.getByRole("link", { name: "数据标注" })).toBeVisible();
    expect(screen.getByRole("link", { name: "问题数据" })).toBeVisible();
    expect(
      screen.queryByRole("link", { name: "审计日志" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "通知" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "账户菜单" }));
    expect(
      await screen.findByRole("link", { name: "个人主页" }),
    ).toHaveAttribute("href", "/account");
    expect(screen.getByRole("link", { name: "账户设置" })).toHaveAttribute(
      "href",
      "/account/settings",
    );
    expect(
      await screen.findByRole("menuitem", { name: "退出登录" }),
    ).toBeEnabled();
  });

  it("opens the lazy global search dialog from the button and Ctrl+K", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "全局搜索" }));
    expect(
      await screen.findByRole("dialog", {}, { timeout: 3_000 }),
    ).toBeVisible();
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );

    await user.keyboard("{Control>}k{/Control}");
    expect(
      await screen.findByRole("dialog", {}, { timeout: 3_000 }),
    ).toBeVisible();
  });

  it("exposes the real logout action for authenticated runtime shells", async () => {
    const user = userEvent.setup();
    const onLogout = vi.fn();
    renderShell(onLogout);

    await user.click(screen.getByRole("button", { name: "账户菜单" }));
    const logout = await screen.findByRole("menuitem", { name: "退出登录" });
    expect(logout).toBeEnabled();
    await user.click(logout);

    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it("links authenticated users to their account settings", async () => {
    const user = userEvent.setup();
    renderShell();

    expect(
      screen.queryByRole("link", { name: "账户设置" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "账户菜单" }));

    expect(
      await screen.findByRole("link", { name: "账户设置" }),
    ).toHaveAttribute("href", "/account/settings");
  });

  it("keeps problem data selected on its Raw diagnostic route", () => {
    renderShell(
      undefined,
      defaultScopeOptions,
      "/manual/issues/raw-diagnostic/session-a",
    );

    expect(
      screen.getByRole("link", { name: "问题数据" }).closest("li"),
    ).toHaveClass("ant-menu-item-selected");
    expect(
      screen.getByRole("link", { name: "数据上传" }).closest("li"),
    ).not.toHaveClass("ant-menu-item-selected");
  });

  it("supports skip navigation and keyboard activation of navigation links", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.tab();
    const skipLink = screen.getByRole("link", { name: "跳到主要内容" });
    expect(skipLink).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("main")).toHaveFocus();

    const uploadLink = screen.getByRole("link", { name: "数据上传" });
    uploadLink.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/ingest/uploads/new",
    );
    await waitFor(() => expect(screen.getByRole("main")).toHaveFocus());
  });
});
