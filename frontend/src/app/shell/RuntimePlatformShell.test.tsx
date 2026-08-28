// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { makeScopeKey } from "../../entities/scope";
import * as authApi from "../../pages/auth/api";
import { useShellStore } from "../../shared/scope/shell-store";
import { ProviderHarness } from "../providers";
import { RuntimePlatformShell } from "./RuntimePlatformShell";

const scope = {
  organizationId: "runtime-shell-organization",
  projectId: "runtime-shell-project",
  regionCode: "runtime-shell-region",
} as const;

function installAuthenticatedSession(): void {
  const store = useShellStore.getState();
  store.setSession(
    {
      actorId: "runtime-shell-user",
      displayName: "Runtime Shell User",
      roleIds: [],
    },
    "hcs_runtime-shell-session",
  );
  store.setScope(scope);
  store.setSessionScopes(
    [
      {
        organizationId: scope.organizationId,
        projectId: scope.projectId,
        regionCodes: [scope.regionCode],
        projectWide: false,
        capabilities: [],
      },
    ],
    1,
  );
  store.setAuthorization({
    scopeKey: makeScopeKey(scope),
    roleVersion: "runtime-shell-role-v1",
    capabilities: [],
    fetchedAt: "2026-08-20T00:00:00Z",
  });
}

function renderAuthenticatedShell() {
  render(
    <ProviderHarness>
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Routes>
          <Route
            path="/"
            element={<RuntimePlatformShell pageAvailability={{ P01: true }} />}
          >
            <Route path="*" element={<h1>受保护页面</h1>} />
          </Route>
          <Route path="/auth/login" element={<h1>登录</h1>} />
          <Route path="/auth/session-expired" element={<h1>会话已失效</h1>} />
        </Routes>
      </MemoryRouter>
    </ProviderHarness>,
  );
}

describe("RuntimePlatformShell", () => {
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
    window.sessionStorage.clear();
    useShellStore.getState().setSession(null, null);
  });

  afterEach(() => {
    cleanup();
    useShellStore.getState().setSession(null, null);
    window.sessionStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("redirects an unauthenticated business route to the login page", async () => {
    render(
      <ProviderHarness>
        <MemoryRouter initialEntries={["/dashboard"]}>
          <Routes>
            <Route
              path="/dashboard"
              element={<RuntimePlatformShell pageAvailability={{}} />}
            />
            <Route path="/auth/login" element={<h1>登录</h1>} />
          </Routes>
        </MemoryRouter>
      </ProviderHarness>,
    );

    expect(await screen.findByRole("heading", { name: "登录" })).toBeVisible();
  });

  it("never renders a stale persisted project and corrects it from authorized session scopes", async () => {
    const store = useShellStore.getState();
    store.setSession(
      {
        actorId: "runtime-shell-user",
        displayName: "Runtime Shell User",
        roleIds: [],
      },
      "hcs_runtime-shell-session",
    );
    store.setScope({
      organizationId: "stale-organization",
      projectId: "stale-private-project",
      regionCode: "stale-region",
    });
    vi.spyOn(authApi, "getSessionBootstrap").mockResolvedValue({
      principal: {
        principal_id: "runtime-shell-user",
        username: "runtime-shell-user",
        display_name: "Runtime Shell User",
        status: "ACTIVE",
        created_at: "2026-08-24T00:00:00Z",
      },
      available_scopes: [
        {
          organization_id: scope.organizationId,
          project_id: scope.projectId,
          region_codes: [scope.regionCode],
          project_wide: false,
          capabilities: [],
        },
      ],
      platform_capabilities: [],
      capability_revision: 2,
    });

    renderAuthenticatedShell();

    expect(
      screen.queryByText(/stale-private-project/u),
    ).not.toBeInTheDocument();
    expect(
      await screen.findByText(`${scope.organizationId} / ${scope.projectId}`),
    ).toBeVisible();
    await waitFor(() => expect(useShellStore.getState().scope).toEqual(scope));
    expect(
      screen.queryByText(/stale-private-project/u),
    ).not.toBeInTheDocument();
  });

  it("loads an empty bootstrap once and keeps the personal shell usable", async () => {
    const store = useShellStore.getState();
    store.setSession(
      {
        actorId: "runtime-shell-user",
        displayName: "Runtime Shell User",
        roleIds: [],
      },
      "hcs_runtime-shell-session",
    );
    const bootstrapSpy = vi
      .spyOn(authApi, "getSessionBootstrap")
      .mockResolvedValue({
        principal: {
          principal_id: "runtime-shell-user",
          username: "runtime-shell-user",
          display_name: "Runtime Shell User",
          status: "ACTIVE",
          created_at: "2026-08-24T00:00:00Z",
        },
        available_organizations: [],
        available_scopes: [],
        platform_capabilities: [],
        capability_revision: 2,
      });

    renderAuthenticatedShell();

    expect(await screen.findByText("尚未加入组织或项目")).toBeVisible();
    expect(
      screen.queryByRole("link", { name: "个人主页" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "通知" })).toBeEnabled();
    expect(screen.getByRole("link", { name: "工作台" })).toBeVisible();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "账户菜单" }));
    expect(
      await screen.findByRole("link", { name: "个人主页" }),
    ).toHaveAttribute("href", "/account");
    expect(screen.getByRole("link", { name: "账户设置" })).toHaveAttribute(
      "href",
      "/account/settings",
    );
    await waitFor(() => expect(bootstrapSpy).toHaveBeenCalledTimes(1));
    expect(useShellStore.getState().authorization).toBeNull();
    expect(useShellStore.getState().bootstrapLoaded).toBe(true);
  });

  it("revokes the server session, clears local credentials, and redirects to login", async () => {
    installAuthenticatedSession();
    const logoutSpy = vi
      .spyOn(authApi, "logoutSession")
      .mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderAuthenticatedShell();

    await user.click(screen.getByRole("button", { name: "账户菜单" }));
    await user.click(await screen.findByRole("menuitem", { name: "退出登录" }));

    await waitFor(() => expect(logoutSpy).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(useShellStore.getState().sessionToken).toBeNull(),
    );
    expect(await screen.findByRole("heading", { name: "登录" })).toBeVisible();
    expect(useShellStore.getState().sessionToken).toBeNull();
    expect(useShellStore.getState().principal).toBeNull();
    expect(
      window.sessionStorage.getItem("hc-platform-current-session"),
    ).toBeNull();
  });

  it("keeps the session and reports an explicit error when revocation fails", async () => {
    installAuthenticatedSession();
    vi.spyOn(authApi, "logoutSession").mockRejectedValue(
      new Error("network unavailable"),
    );
    const user = userEvent.setup();
    renderAuthenticatedShell();

    await user.click(screen.getByRole("button", { name: "账户菜单" }));
    await user.click(await screen.findByRole("menuitem", { name: "退出登录" }));

    expect(await screen.findByText("退出未完成")).toBeVisible();
    expect(screen.getByRole("heading", { name: "受保护页面" })).toBeVisible();
    await waitFor(() =>
      expect(useShellStore.getState().sessionToken).toBe(
        "hcs_runtime-shell-session",
      ),
    );
    expect(
      window.sessionStorage.getItem("hc-platform-current-session"),
    ).toContain("hcs_runtime-shell-session");
  });
});
