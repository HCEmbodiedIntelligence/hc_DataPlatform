// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ProviderHarness } from "../../app/providers";
import type { AccountSettings } from "../../features/account/api";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { makeScopeKey } from "../../entities/scope";
import { useShellStore } from "../../shared/scope/shell-store";
import { AccountSettingsPage } from "./page";

const principalId = "10277040-188f-4f9f-a59b-16dd83511c98";
const scope = {
  organizationId: "org-settings",
  projectId: "project-settings",
  regionCode: "region-settings",
};

const settings: AccountSettings = {
  profile: {
    principal_id: principalId,
    username: "settings-user",
    display_name: "设置用户",
    status: "ACTIVE",
    created_at: "2026-08-20T01:00:00Z",
    updated_at: "2026-08-20T01:00:00Z",
    password_changed_at: "2026-08-20T01:00:00Z",
    revision: 1,
    etag: '"v1"',
  },
  password_policy: {
    min_length: 12,
    max_length: 128,
    disallow_username: true,
    blocked_password_count: 8192,
  },
};

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: {
      "Content-Type":
        status >= 400 ? "application/problem+json" : "application/json",
    },
  });
}

function renderPage() {
  render(
    <ProviderHarness>
      <MemoryRouter initialEntries={["/account/settings"]}>
        <Routes>
          <Route path="/account/settings" element={<AccountSettingsPage />} />
          <Route
            path="/auth/session-expired"
            element={<h1>登录状态已失效</h1>}
          />
        </Routes>
      </MemoryRouter>
    </ProviderHarness>,
  );
}

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "/api/v1",
    sseBaseUrl: "/api/v1",
    buildVersion: "account-page-test",
    releaseEnv: "test",
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  vi.stubGlobal(
    "ResizeObserver",
    class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  if (!globalThis.crypto.randomUUID) {
    Object.defineProperty(globalThis.crypto, "randomUUID", {
      configurable: true,
      value: vi.fn(() => "00000000-0000-4000-8000-000000000001"),
    });
  }
  const store = useShellStore.getState();
  store.setSession(
    { actorId: principalId, displayName: "设置用户", roleIds: [] },
    "settings-session-token",
  );
  store.setScope(scope);
  store.setSessionScopes(
    [
      {
        organizationId: scope.organizationId,
        projectId: scope.projectId,
        regionCodes: [scope.regionCode],
        projectWide: false,
        capabilities: ["dataset.read"],
      },
    ],
    3,
  );
  store.setAuthorization({
    scopeKey: makeScopeKey(scope),
    roleVersion: "3",
    capabilities: ["dataset.read"],
    fetchedAt: "2026-08-20T00:00:00Z",
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  resetRuntimeConfigForTests();
  useShellStore.getState().setSession(null, null);
  window.sessionStorage.clear();
});

describe("account settings page", () => {
  it("loads real profile facts and saves display name without clearing authorization", async () => {
    const updated: AccountSettings = {
      ...settings,
      profile: {
        ...settings.profile,
        display_name: "新的显示名称",
        revision: 2,
        etag: '"v2"',
      },
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(settings))
      .mockResolvedValueOnce(response(updated));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderPage();

    expect(
      await screen.findByRole("heading", { level: 1, name: "账户设置" }),
    ).toBeVisible();
    const username = await screen.findByLabelText("登录用户名");
    expect(username).toHaveValue("settings-user");
    expect(username).toBeDisabled();

    const displayName = screen.getByLabelText("显示名称");
    await user.clear(displayName);
    await user.type(displayName, "新的显示名称");
    const save = screen.getByRole("button", { name: "保存基本资料" });
    await waitFor(() => expect(save).toBeEnabled());
    await user.click(save);

    expect(
      (await screen.findAllByText("基本资料已保存")).length,
    ).toBeGreaterThan(0);
    const init = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({
      display_name: "新的显示名称",
    });
    expect(useShellStore.getState().principal?.displayName).toBe(
      "新的显示名称",
    );
    expect(useShellStore.getState().sessionToken).toBe(
      "settings-session-token",
    );
    expect(useShellStore.getState().scope).toEqual(scope);
    expect(useShellStore.getState().authorization?.capabilities).toEqual([
      "dataset.read",
    ]);
  });

  it("validates confirmation locally and clears every password field after success", async () => {
    const result = {
      account: {
        ...settings,
        profile: {
          ...settings.profile,
          revision: 2,
          etag: '"v2"',
          password_changed_at: "2026-08-20T02:00:00Z",
        },
      },
      other_sessions_revoked: 2,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(settings))
      .mockResolvedValueOnce(response(result));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderPage();
    await screen.findByLabelText("当前密码");

    const current = screen.getByLabelText("当前密码");
    const next = screen.getByLabelText("新密码");
    const confirm = screen.getByLabelText("确认新密码");
    await user.type(current, "Current-password-9!");
    await user.type(next, "prefix-SETTINGS-USER-suffix");
    await user.type(confirm, "prefix-SETTINGS-USER-suffix");
    expect(await screen.findByText("新密码不能包含登录用户名。")).toBeVisible();
    expect(screen.getByRole("button", { name: "更新密码" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await user.clear(next);
    await user.clear(confirm);
    await user.type(next, "Next-password-10!");
    await user.type(confirm, "does-not-match");
    expect(screen.getByRole("button", { name: "更新密码" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await user.clear(confirm);
    await user.type(confirm, "Next-password-10!");
    const submit = screen.getByRole("button", { name: "更新密码" });
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);

    expect(
      await screen.findByText("密码已更新，其他 2 个登录会话已退出。"),
    ).toBeVisible();
    expect(current).toHaveValue("");
    expect(next).toHaveValue("");
    expect(confirm).toHaveValue("");
    const body = JSON.parse(
      String((fetchMock.mock.calls[1]?.[1] as RequestInit).body),
    );
    expect(body).toEqual({
      current_password: "Current-password-9!",
      new_password: "Next-password-10!",
    });
    expect(body).not.toHaveProperty("confirm_password");
  });

  it("verifies a recovery email through the authenticated account endpoints", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(settings))
      .mockResolvedValueOnce(response({ accepted: true }, 202))
      .mockResolvedValueOnce(
        response({ recovery_email_hint: "r***@example.com" }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderPage();
    const email = await screen.findByRole("textbox", { name: "恢复邮箱" });
    await user.type(email, "recovery@example.com");
    await user.click(screen.getByRole("button", { name: "发送验证邮件" }));

    expect(await screen.findByText("验证邮件已发送")).toBeVisible();
    const token = screen.getByLabelText("邮件验证码");
    await user.type(token, "hcer_single-use-token-123456");
    await user.click(screen.getByRole("button", { name: "确认恢复邮箱" }));

    expect(await screen.findByText("恢复邮箱已更新")).toBeVisible();
    expect(screen.getByText(/r\*\*\*@example\.com/u)).toBeVisible();

    const requestInit = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(JSON.parse(String(requestInit.body))).toEqual({
      recovery_email: "recovery@example.com",
    });
    const requestHeaders = new Headers(requestInit.headers);
    expect(requestHeaders.get("Authorization")).toBe(
      "Bearer settings-session-token",
    );
    expect(requestHeaders.get("X-Organization-Id")).toBeNull();
    expect(requestHeaders.get("X-Project-Id")).toBeNull();
    expect(requestHeaders.get("X-Region-Code")).toBeNull();

    const confirmInit = fetchMock.mock.calls[2]?.[1] as RequestInit;
    expect(JSON.parse(String(confirmInit.body))).toEqual({
      token: "hcer_single-use-token-123456",
    });
    expect(screen.getByRole("textbox", { name: "恢复邮箱" })).toHaveValue("");
    expect(screen.getByLabelText("邮件验证码")).toHaveValue("");
  });

  it("clears a rejected session and routes to the expired-session state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response(
          {
            type: "about:blank",
            title: "Session invalid",
            status: 401,
            code: "SESSION_INVALID",
            retryable: false,
          },
          401,
        ),
      ),
    );

    renderPage();

    expect(
      await screen.findByRole("heading", { name: "登录状态已失效" }),
    ).toBeVisible();
    expect(useShellStore.getState().sessionToken).toBeNull();
    expect(useShellStore.getState().principal).toBeNull();
  });
});
