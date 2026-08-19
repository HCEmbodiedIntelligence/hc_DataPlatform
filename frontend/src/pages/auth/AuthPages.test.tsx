// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { ProviderHarness } from "../../app/providers";
import { makeScopeKey } from "../../entities/scope";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import { LoginRoute } from "./LoginRoute";
import { RegisterRoute } from "./RegisterRoute";
import { EmptyAccountRoute } from "./EmptyAccountRoute";

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}</output>;
}

function renderLogin() {
  render(
    <ProviderHarness>
      <MemoryRouter initialEntries={["/auth/login"]}>
        <Routes>
          <Route path="/auth/login" element={<LoginRoute />} />
          <Route path="/auth/session-expired" element={<LocationProbe />} />
          <Route path="/account/empty" element={<LocationProbe />} />
          <Route path="/" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </ProviderHarness>,
  );
}

function renderRegister() {
  render(
    <ProviderHarness>
      <MemoryRouter initialEntries={["/auth/register"]}>
        <Routes>
          <Route path="/auth/register" element={<RegisterRoute />} />
          <Route path="/auth/registered" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </ProviderHarness>,
  );
}

function renderEmptyAccount() {
  render(
    <ProviderHarness>
      <MemoryRouter initialEntries={["/account/empty"]}>
        <Routes>
          <Route path="/account/empty" element={<EmptyAccountRoute />} />
          <Route path="/" element={<LocationProbe />} />
          <Route path="/auth/login" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </ProviderHarness>,
  );
}

function problemResponse(status: number): Response {
  return new Response(
    JSON.stringify({
      type: "https://hc-data-platform.invalid/problems/auth",
      title: "Authentication failed",
      status,
      code: status === 429 ? "ABUSE_POLICY_LIMITED" : "AUTHENTICATION_REQUIRED",
      request_id: `req-${status}`,
      retryable: status === 429,
    }),
    { status, headers: { "Content-Type": "application/problem+json" } },
  );
}

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "/api/v1",
    sseBaseUrl: "/api/v1",
    buildVersion: "auth-test",
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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  resetRuntimeConfigForTests();
});

describe("E01 authentication pages", () => {
  it("provides visible labels, authentication autocomplete, and a keyboard password toggle", async () => {
    const user = userEvent.setup();
    renderLogin();

    const username = screen.getByLabelText("用户名");
    const password = screen.getByLabelText("密码");
    expect(username).toHaveAttribute("name", "username");
    expect(username).toHaveAttribute("autocomplete", "username");
    expect(username).toHaveAttribute("spellcheck", "false");
    expect(password).toHaveAttribute("name", "password");
    expect(password).toHaveAttribute("autocomplete", "current-password");
    expect(password).toHaveAttribute("type", "password");

    const toggle = screen.getByRole("button", { name: "显示密码" });
    toggle.focus();
    await user.keyboard("{Enter}");
    expect(password).toHaveAttribute("type", "text");
    expect(toggle).toHaveAttribute("aria-pressed", "true");
  });

  it.each([
    [401, "用户名或密码不正确"],
    [422, "登录信息未通过校验"],
    [429, "尝试次数过多"],
  ])(
    "renders a safe %s response without account enumeration",
    async (status, expected) => {
      const user = userEvent.setup();
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(problemResponse(status)),
      );
      renderLogin();

      await user.type(screen.getByLabelText("用户名"), "unknown-user");
      await user.type(screen.getByLabelText("密码"), "not-a-real-password");
      await user.click(screen.getByRole("button", { name: /登\s*录/u }));

      expect(await screen.findByText(new RegExp(expected, "u"))).toBeVisible();
      expect(screen.queryByText(/账户不存在/u)).not.toBeInTheDocument();
    },
  );

  it("submits with Enter, stores the opaque session in memory, and routes an empty account", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            access_token: "session-token",
            token_type: "Bearer",
            principal: {
              principal_id: "principal-1",
              username: "new-user",
              status: "ACTIVE",
              created_at: "2026-08-18T00:00:00Z",
            },
            capability_revision: 0,
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            principal: {
              principal_id: "principal-1",
              username: "new-user",
              status: "ACTIVE",
              created_at: "2026-08-18T00:00:00Z",
            },
            available_scopes: [],
            capability_revision: 0,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    renderLogin();

    await user.type(screen.getByLabelText("用户名"), "new-user");
    await user.type(screen.getByLabelText("密码"), "test-password");
    await user.keyboard("{Enter}");

    expect(await screen.findByTestId("location")).toHaveTextContent(
      "/account/empty",
    );
    expect(useShellStore.getState().sessionToken).toBe("session-token");
    const bootstrapHeaders = new Headers(
      (fetchMock.mock.calls[1]?.[1] as RequestInit | undefined)?.headers,
    );
    expect(bootstrapHeaders.get("Authorization")).toBe("Bearer session-token");
  });

  it("prevents duplicate login requests while the first submit is pending", async () => {
    const user = userEvent.setup();
    let resolveFetch: ((response: Response) => void) | undefined;
    const pendingResponse = new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    });
    const fetchMock = vi.fn().mockReturnValue(pendingResponse);
    vi.stubGlobal("fetch", fetchMock);
    renderLogin();

    await user.type(screen.getByLabelText("用户名"), "rate-user");
    await user.type(screen.getByLabelText("密码"), "test-password");
    const submit = screen.getByRole("button", { name: /登\s*录/u });
    await user.click(submit);
    await user.click(submit);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resolveFetch?.(problemResponse(429));
    expect(await screen.findByText(/尝试次数过多/u)).toBeVisible();
  });

  it("clears a newly issued token and routes to the expired-session state when bootstrap returns 401", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          new Response(
            JSON.stringify({
              access_token: "immediately-expired-token",
              token_type: "Bearer",
              principal: {
                principal_id: "principal-expired",
                username: "expired-user",
                status: "ACTIVE",
                created_at: "2026-08-18T00:00:00Z",
              },
              capability_revision: 0,
            }),
            { status: 201, headers: { "Content-Type": "application/json" } },
          ),
        )
        .mockResolvedValueOnce(problemResponse(401)),
    );
    renderLogin();

    await user.type(screen.getByLabelText("用户名"), "expired-user");
    await user.type(screen.getByLabelText("密码"), "test-password");
    await user.keyboard("{Enter}");

    expect(await screen.findByTestId("location")).toHaveTextContent(
      "/auth/session-expired",
    );
    expect(useShellStore.getState().sessionToken).toBeNull();
  });

  it("uses only username and new-password fields and gives weak-password feedback", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(problemResponse(422)));
    renderRegister();

    expect(screen.getByLabelText("用户名")).toHaveAttribute(
      "autocomplete",
      "username",
    );
    expect(screen.getByLabelText("密码")).toHaveAttribute(
      "autocomplete",
      "new-password",
    );
    expect(screen.getByLabelText("确认密码")).toHaveAttribute(
      "autocomplete",
      "new-password",
    );
    expect(
      screen.queryByLabelText(/邮箱|手机号|验证码/u),
    ).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("用户名"), "new-user");
    await user.type(screen.getByLabelText("密码"), "weak");
    await user.type(screen.getByLabelText("确认密码"), "weak");
    expect(screen.getByText(/密码强度较弱/u)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "创建账户" }));
    expect(await screen.findByText(/注册信息未通过校验/u)).toBeVisible();
  });

  it("submits membership and capability requests through the formal project endpoints", async () => {
    const user = userEvent.setup();
    useShellStore
      .getState()
      .setSession(
        { actorId: "principal-empty", displayName: "empty-user", roleIds: [] },
        "empty-session-token",
      );
    const membership = {
      request_id: "membership-request-a",
      project_id: "project-real-a",
      requester_id: "principal-empty",
      status: "PENDING",
      reason: "加入采集项目",
      created_at: "2026-08-18T00:00:00Z",
      updated_at: "2026-08-18T00:00:00Z",
      revision: 1,
    };
    const capability = {
      ...membership,
      request_id: "capability-request-a",
      capability_keys: ["collection.upload", "annotation.write"],
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(membership), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(capability), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    renderEmptyAccount();

    await user.click(screen.getByRole("button", { name: "申请加入项目" }));
    await user.type(screen.getByLabelText("项目 ID"), "project-real-a");
    await user.type(screen.getByLabelText("申请说明（选填）"), "加入采集项目");
    await user.click(screen.getByRole("button", { name: "提交加入申请" }));
    expect(await screen.findByText("项目加入申请已提交")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "申请权限" }));
    await user.type(
      screen.getByLabelText(/Capability（逗号或换行分隔）/u),
      "collection.upload, annotation.write, collection.upload",
    );
    await user.click(screen.getByRole("button", { name: "提交权限申请" }));
    expect(await screen.findByText("权限申请已提交")).toBeVisible();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/projects/project-real-a/membership-requests",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/v1/projects/project-real-a/capability-requests",
    );
    const membershipBody = JSON.parse(
      String((fetchMock.mock.calls[0]?.[1] as RequestInit).body),
    );
    const capabilityBody = JSON.parse(
      String((fetchMock.mock.calls[1]?.[1] as RequestInit).body),
    );
    expect(membershipBody).toEqual({ reason: "加入采集项目" });
    expect(capabilityBody).toEqual({
      capability_keys: ["collection.upload", "annotation.write"],
      reason: "加入采集项目",
    });
    for (const call of fetchMock.mock.calls) {
      const headers = new Headers((call[1] as RequestInit).headers);
      expect(headers.get("Authorization")).toBe("Bearer empty-session-token");
      expect(headers.get("Idempotency-Key")).toBeTruthy();
    }
  });

  it("refreshes approved membership before capability and enters the shell only after grants exist", async () => {
    const user = userEvent.setup();
    useShellStore
      .getState()
      .setSession(
        { actorId: "principal-empty", displayName: "empty-user", roleIds: [] },
        "empty-session-token",
      );
    const principal = {
      principal_id: "principal-empty",
      username: "empty-user",
      status: "ACTIVE",
      created_at: "2026-08-18T00:00:00Z",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            principal,
            available_scopes: [
              {
                project_id: "project-real-a",
                region_codes: [],
                project_wide: true,
                capabilities: [],
              },
            ],
            capability_revision: 1,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            principal,
            available_scopes: [
              {
                project_id: "project-real-a",
                region_codes: [],
                project_wide: true,
                capabilities: ["collection.upload", "annotation.write"],
              },
            ],
            capability_revision: 2,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    renderEmptyAccount();

    await user.click(screen.getByRole("button", { name: "申请加入项目" }));
    await user.click(screen.getByRole("button", { name: "审批后刷新" }));
    expect(await screen.findByRole("heading", { name: "申请项目权限" })).toBeVisible();
    expect(screen.getByText(/项目加入已生效/u)).toBeVisible();
    expect(useShellStore.getState().scope).toEqual({
      organizationId: "",
      projectId: "project-real-a",
    });

    await user.click(screen.getByRole("button", { name: "审批后刷新" }));
    expect(await screen.findByTestId("location")).toHaveTextContent("/");
    expect(useShellStore.getState().authorization?.capabilities).toEqual([
      "collection.upload",
      "annotation.write",
    ]);
  });
});
