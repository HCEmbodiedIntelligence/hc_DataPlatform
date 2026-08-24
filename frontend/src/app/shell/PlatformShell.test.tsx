// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { ProviderHarness } from "../providers";
import { makeScopeKey } from "../../entities/scope";
import * as authApi from "../../pages/auth/api";
import { useShellStore } from "../../shared/scope/shell-store";
import { PlatformShell, type ScopeOption } from "./PlatformShell";
import ProjectMembershipRequestDialog from "./ProjectMembershipRequestDialog";

const scope = {
  organizationId: "org-shell-test",
  projectId: "project-shell-test",
  regionCode: "cn-shanghai",
} as const;

const membershipResult: Awaited<
  ReturnType<typeof authApi.requestProjectMembership>
> = {
  request_id: "membership-shell-request",
  organization_id: scope.organizationId,
  project_id: "project-admin-provided",
  requester_id: "actor-shell-test",
  status: "PENDING",
  reason: "需要参与标注",
  created_at: "2026-08-24T00:00:00Z",
  updated_at: "2026-08-24T00:00:00Z",
  revision: 1,
};

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
) {
  render(
    <ProviderHarness>
      <MemoryRouter initialEntries={["/dashboard"]}>
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
  it("renders the membership request dialog accessibly", async () => {
    render(
      <ProviderHarness>
        <ProjectMembershipRequestDialog
          afterClose={vi.fn()}
          open
          organizationId={scope.organizationId}
          onClose={vi.fn()}
        />
      </ProviderHarness>,
    );

    expect(await screen.findByText("申请加入项目")).toBeVisible();
    expect(screen.getByRole("dialog", { name: "申请加入项目" })).toBeVisible();
  });

  it("does not report a failed project snapshot in platform-only account mode", () => {
    useShellStore.setState({
      scope: null,
      authorization: null,
      authorizationFailed: false,
      platformCapabilities: ["platform.account.read"],
    });

    renderShell();

    expect(
      screen.queryByText("授权快照不可用，当前作用域已按失败关闭处理。"),
    ).not.toBeInTheDocument();
  });

  it("uses the official fixed-size brand asset and consolidated function navigation", async () => {
    renderShell();

    const brandLink = screen.getByRole("link", {
      name: "杭叉集团 HC 数据平台工作台",
    });
    const logo = screen.getByRole("img", { name: "杭叉集团" });
    expect(brandLink).toHaveAttribute("href", "/dashboard");
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

  it("opens the project membership form from the project dropdown and restores focus", async () => {
    const user = userEvent.setup();
    renderShell();
    const projectSelector = screen.getByRole("combobox", { name: "当前项目" });

    await user.click(projectSelector);
    const requestMembershipButton = await screen.findByRole("button", {
      name: "申请加入其他项目",
    });
    requestMembershipButton.focus();
    expect(requestMembershipButton).toHaveFocus();
    await user.keyboard("{Enter}");

    expect(await screen.findByText("申请加入项目")).toBeVisible();
    expect(screen.getByLabelText("当前组织 ID")).toHaveValue(
      scope.organizationId,
    );
    expect(screen.getByLabelText("当前组织 ID")).toHaveAttribute("readonly");
    expect(screen.getByLabelText("项目 ID")).toHaveFocus();
    expect(screen.getByRole("button", { name: "提交加入申请" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /取\s*消/u }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(projectSelector).toHaveFocus();
  });

  it("submits the formal membership request once with scope, reason, and idempotency", async () => {
    let resolveRequest: (
      value: Awaited<ReturnType<typeof authApi.requestProjectMembership>>,
    ) => void = () => undefined;
    const pendingRequest = new Promise<
      Awaited<ReturnType<typeof authApi.requestProjectMembership>>
    >((resolve) => {
      resolveRequest = resolve;
    });
    const requestSpy = vi
      .spyOn(authApi, "requestProjectMembership")
      .mockReturnValue(pendingRequest);
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("combobox", { name: "当前项目" }));
    await user.click(
      await screen.findByRole("button", { name: "申请加入其他项目" }),
    );
    await user.type(
      screen.getByLabelText("项目 ID"),
      " project-admin-provided ",
    );
    await user.type(screen.getByLabelText("申请说明（选填）"), "需要参与标注");
    const submit = screen.getByRole("button", { name: "提交加入申请" });
    await user.click(submit);
    await user.click(submit);

    expect(requestSpy).toHaveBeenCalledTimes(1);
    expect(requestSpy).toHaveBeenCalledWith(
      scope.organizationId,
      "project-admin-provided",
      "需要参与标注",
      expect.any(String),
    );
    expect(requestSpy.mock.calls[0]?.[3]).not.toHaveLength(0);
    expect(submit).toBeDisabled();

    resolveRequest(membershipResult);
    expect(await screen.findByText("项目加入申请已提交")).toBeVisible();
    expect(screen.getByText("membership-shell-request")).toBeVisible();
    expect(screen.getByText(/刷新会话或重新登录/u)).toBeVisible();
  });

  it("keeps the membership form open and reports a service failure", async () => {
    vi.spyOn(authApi, "requestProjectMembership").mockRejectedValue(
      new Error("membership service unavailable"),
    );
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("combobox", { name: "当前项目" }));
    await user.click(
      await screen.findByRole("button", { name: "申请加入其他项目" }),
    );
    await user.type(screen.getByLabelText("项目 ID"), "project-failure");
    await user.click(screen.getByRole("button", { name: "提交加入申请" }));

    expect(await screen.findByText("加入申请未提交")).toBeVisible();
    expect(screen.getByText("membership service unavailable")).toBeVisible();
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(screen.getByRole("button", { name: "提交加入申请" })).toBeEnabled();
  });

  it("exposes the same membership request entry in the mobile project selector", async () => {
    vi.mocked(window.matchMedia).mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "打开导航" }));
    await user.click(await screen.findByRole("combobox", { name: "当前项目" }));
    await user.click(
      await screen.findByRole("button", { name: "申请加入其他项目" }),
    );

    expect(await screen.findByText("申请加入项目")).toBeVisible();
    expect(screen.getByLabelText("当前组织 ID")).toHaveValue(
      scope.organizationId,
    );
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

    await user.click(screen.getByRole("button", { name: "账户菜单" }));

    expect(
      await screen.findByRole("link", { name: "账户设置" }),
    ).toHaveAttribute("href", "/account/settings");
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
