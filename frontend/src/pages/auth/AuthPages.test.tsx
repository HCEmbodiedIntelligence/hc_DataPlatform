// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { ProviderHarness } from "../../app/providers";
import { makeScopeKey } from "../../entities/scope";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import { LoginRoute } from "./LoginRoute";
import {
  PasswordRecoveryRequestRoute,
  PasswordResetRoute,
} from "./PasswordRecoveryRoutes";
import { RegisterRoute } from "./RegisterRoute";
import { EmptyAccountRoute } from "./EmptyAccountRoute";

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}</output>;
}

function RecoveryLocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="recovery-location">{`${location.pathname}${location.hash}`}</output>
  );
}

function LeaveRegistrationButton() {
  const navigate = useNavigate();
  return <button onClick={() => navigate("/away")}>离开注册页</button>;
}

function LeaveLoginButton() {
  const navigate = useNavigate();
  return <button onClick={() => navigate("/away")}>离开登录页</button>;
}

function ReturnToLoginButton() {
  const navigate = useNavigate();
  return <button onClick={() => navigate("/auth/login")}>返回登录页</button>;
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
  return render(
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

function renderPasswordRecovery(initialEntry = "/auth/recover-password") {
  render(
    <ProviderHarness>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route
            path="/auth/recover-password"
            element={<PasswordRecoveryRequestRoute />}
          />
          <Route
            path="/auth/reset-password"
            element={
              <>
                <PasswordResetRoute />
                <RecoveryLocationProbe />
              </>
            }
          />
          <Route path="/auth/login" element={<LocationProbe />} />
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

function sessionLimitResponse(retryAfterSeconds: number): Response {
  return new Response(
    JSON.stringify({
      type: "https://hc-data-platform.invalid/problems/session-limit-reached",
      title: "Active session limit reached",
      status: 429,
      detail: "This account already has the maximum number of active sessions.",
      code: "SESSION_LIMIT_REACHED",
      request_id: "req-session-limit",
      retryable: true,
      retry_after_seconds: retryAfterSeconds,
    }),
    {
      status: 429,
      headers: {
        "Content-Type": "application/problem+json",
        "Retry-After": String(retryAfterSeconds),
      },
    },
  );
}

function temporaryLockResponse(retryAfterSeconds: number): Response {
  return new Response(
    JSON.stringify({
      type: "https://hc-data-platform.invalid/problems/authentication-temporarily-limited",
      title: "Authentication temporarily limited",
      status: 429,
      detail: "Wait before attempting authentication again.",
      code: "AUTH_ACCOUNT_TEMPORARILY_LOCKED",
      request_id: "req-temporary-lock",
      retryable: true,
      retry_after_seconds: retryAfterSeconds,
    }),
    {
      status: 429,
      headers: {
        "Content-Type": "application/problem+json",
        "Retry-After": String(retryAfterSeconds),
      },
    },
  );
}

function challengeRequiredResponse(): Response {
  return new Response(
    JSON.stringify({
      type: "https://hc-data-platform.invalid/problems/auth-challenge-required",
      title: "Authentication challenge required",
      status: 403,
      detail: "Complete the authentication challenge before trying again.",
      code: "AUTH_CHALLENGE_REQUIRED",
      request_id: "req-challenge-required",
      retryable: false,
    }),
    { status: 403, headers: { "Content-Type": "application/problem+json" } },
  );
}

function authConfigResponse(
  minLength = 6,
  maxLength = 128,
  disallowUsername = true,
): Response {
  return new Response(
    JSON.stringify({
      password_policy: {
        min_length: minLength,
        max_length: maxLength,
        disallow_username: disallowUsername,
        blocked_password_count: 5,
      },
      challenge: null,
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

function turnstileAuthConfigResponse(): Response {
  return new Response(
    JSON.stringify({
      password_policy: {
        min_length: 6,
        max_length: 128,
        disallow_username: true,
        blocked_password_count: 5,
      },
      challenge: {
        provider: "TURNSTILE",
        site_key: "test-turnstile-site-key",
      },
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
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
  delete window.turnstile;
  document.getElementById("hc-turnstile-api")?.remove();
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
    expect(screen.queryByText(/记住我|暂未开放/u)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "忘记密码？" })).toHaveAttribute(
      "href",
      "/auth/recover-password",
    );
  });

  it("sends a non-enumerating recovery request through the configured challenge without tenant headers", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const path = String(url);
      if (path.endsWith("/auth/config")) {
        return Promise.resolve(turnstileAuthConfigResponse());
      }
      if (path.endsWith("/auth/password-recovery-requests")) {
        return Promise.resolve(
          new Response(JSON.stringify({ accepted: true }), {
            status: 202,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPasswordRecovery();

    const script = await waitFor(() => {
      const element = document.getElementById("hc-turnstile-api");
      expect(element).toBeInstanceOf(HTMLScriptElement);
      return element as HTMLScriptElement;
    });
    const renderWidget = vi.fn(
      (
        _container: HTMLElement,
        options: { callback: (token: string) => void; action: string },
      ) => {
        expect(options.action).toBe("password_recovery");
        options.callback("recovery-challenge-token");
        return "recovery-widget";
      },
    );
    window.turnstile = {
      render: renderWidget,
      reset: vi.fn(),
      remove: vi.fn(),
    };
    fireEvent.load(script);

    await user.type(screen.getByLabelText("用户名或恢复邮箱"), "recovery-user");
    const submit = screen.getByRole("button", { name: "发送恢复邮件" });
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);

    expect(await screen.findByText("恢复请求已受理")).toBeVisible();
    expect(screen.getByText(/不会显示账户是否存在/u)).toBeVisible();
    const recoveryCall = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/auth/password-recovery-requests"),
    );
    expect(recoveryCall).toBeDefined();
    const init = recoveryCall?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      identifier: "recovery-user",
    });
    const headers = new Headers(init.headers);
    expect(headers.get("X-Auth-Challenge-Response")).toBe(
      "recovery-challenge-token",
    );
    expect(headers.get("Authorization")).toBeNull();
    expect(headers.get("X-Organization-Id")).toBeNull();
    expect(headers.get("X-Project-Id")).toBeNull();
    expect(headers.get("X-Region-Code")).toBeNull();
  });

  it("consumes a fragment recovery token, applies the discovered policy, and reports revoked sessions", async () => {
    const user = userEvent.setup();
    const recoveryToken = "hcpr_single-use-recovery-token-123456";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(authConfigResponse(12, 64, true))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ sessions_revoked: 3 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    renderPasswordRecovery(
      `/auth/reset-password#token=${encodeURIComponent(recoveryToken)}`,
    );

    const token = await screen.findByLabelText("重置凭据");
    expect(token).toHaveValue(recoveryToken);
    await waitFor(() =>
      expect(screen.getByTestId("recovery-location")).toHaveTextContent(
        "/auth/reset-password",
      ),
    );
    expect(screen.getByTestId("recovery-location")).not.toHaveTextContent(
      "token=",
    );
    expect(
      await screen.findByText(/新密码长度须为 12–64 个字符/u),
    ).toBeVisible();

    await user.type(screen.getByLabelText("新密码"), "New-password-23!");
    await user.type(screen.getByLabelText("确认新密码"), "New-password-23!");
    await user.click(screen.getByRole("button", { name: "重置密码" }));

    expect(
      await screen.findByText("密码已更新，3 个已有登录会话已退出。"),
    ).toBeVisible();
    const init = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      token: recoveryToken,
      new_password: "New-password-23!",
    });
    expect(screen.queryByLabelText("重置凭据")).not.toBeInTheDocument();
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

  it("distinguishes an active-session admission limit from abuse throttling", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sessionLimitResponse(73)));
    renderLogin();

    await user.type(screen.getByLabelText("用户名"), "session-cap-user");
    await user.type(screen.getByLabelText("密码"), "valid-password");
    await user.click(screen.getByRole("button", { name: /登\s*录/u }));

    expect(
      await screen.findByText(/有效登录会话已达上限。请在约 73 秒后重试/u),
    ).toBeVisible();
    expect(screen.queryByText(/尝试次数过多/u)).not.toBeInTheDocument();
  });

  it("shows a temporary login limit without revealing account existence", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(temporaryLockResponse(73)),
    );
    renderLogin();

    await user.type(screen.getByLabelText("用户名"), "unknown-user");
    await user.type(screen.getByLabelText("密码"), "not-a-real-password");
    await user.click(screen.getByRole("button", { name: /登\s*录/u }));

    expect(await screen.findByText(/约 73 秒后再试/u)).toBeVisible();
    expect(screen.queryByText(/账户不存在/u)).not.toBeInTheDocument();
  });

  it("loads Turnstile only after the server requires it, resets its token, and sends no tenant headers", async () => {
    const user = userEvent.setup();
    let sessionCalls = 0;
    const principal = {
      principal_id: "challenge-principal",
      username: "challenge-user",
      display_name: "Challenge User",
      status: "ACTIVE",
      created_at: "2026-08-20T00:00:00Z",
    } as const;
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const path = String(url);
      if (path.endsWith("/auth/sessions")) {
        sessionCalls += 1;
        if (sessionCalls <= 3) return Promise.resolve(problemResponse(401));
        if (sessionCalls === 4)
          return Promise.resolve(challengeRequiredResponse());
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "challenge-session-token",
              token_type: "Bearer",
              principal,
              capability_revision: 0,
            }),
            { status: 201, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      if (path.endsWith("/auth/config"))
        return Promise.resolve(turnstileAuthConfigResponse());
      if (path.endsWith("/auth/session/bootstrap")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              principal,
              available_scopes: [],
              capability_revision: 0,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderLogin();

    await user.type(screen.getByLabelText("用户名"), "challenge-user");
    await user.type(screen.getByLabelText("密码"), "independent-password");
    const submit = screen.getByRole("button", { name: /^\s*登\s*录\s*$/u });
    for (let index = 0; index < 4; index += 1) {
      await user.click(submit);
      await screen.findByText(
        index === 3 ? /请完成安全验证后再次登录/u : /用户名或密码不正确/u,
      );
    }

    const script = await waitFor(() => {
      const element = document.getElementById("hc-turnstile-api");
      expect(element).toBeInstanceOf(HTMLScriptElement);
      return element as HTMLScriptElement;
    });
    const renderWidget = vi.fn(
      (
        _container: HTMLElement,
        options: { callback: (token: string) => void; action: string },
      ) => {
        expect(options.action).toBe("login");
        options.callback("passed-challenge-token");
        return "widget-id";
      },
    );
    window.turnstile = {
      render: renderWidget,
      reset: vi.fn(),
      remove: vi.fn(),
    };
    fireEvent.load(script);

    await waitFor(() => expect(renderWidget).toHaveBeenCalledOnce());
    expect(submit).toBeEnabled();
    await user.click(submit);
    expect(await screen.findByTestId("location")).toHaveTextContent(
      "/account/empty",
    );

    const challengeCall = fetchMock.mock.calls.find(([url, init]) => {
      const headers = new Headers((init as RequestInit).headers);
      return (
        String(url).endsWith("/auth/sessions") &&
        headers.get("X-Auth-Challenge-Response") === "passed-challenge-token"
      );
    });
    expect(challengeCall, JSON.stringify(fetchMock.mock.calls)).toBeDefined();
    const challengeHeaders = new Headers(
      (challengeCall?.[1] as RequestInit).headers,
    );
    expect(challengeHeaders.get("Authorization")).toBeNull();
    expect(challengeHeaders.get("X-Organization-Id")).toBeNull();
    expect(challengeHeaders.get("X-Project-Id")).toBeNull();
    expect(challengeHeaders.get("X-Region-Code")).toBeNull();
  });

  it("keeps challenge-required login blocked when the provider script fails and offers retry", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).endsWith("/auth/sessions")) {
        return Promise.resolve(challengeRequiredResponse());
      }
      if (String(url).endsWith("/auth/config")) {
        return Promise.resolve(turnstileAuthConfigResponse());
      }
      return Promise.reject(new Error(`Unexpected request: ${String(url)}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderLogin();

    await user.type(screen.getByLabelText("用户名"), "challenge-user");
    await user.type(screen.getByLabelText("密码"), "independent-password");
    await user.click(screen.getByRole("button", { name: /^\s*登\s*录\s*$/u }));
    expect(await screen.findByText(/请完成安全验证后再次登录/u)).toBeVisible();

    const firstScript = await waitFor(() => {
      const element = document.getElementById("hc-turnstile-api");
      expect(element).toBeInstanceOf(HTMLScriptElement);
      return element as HTMLScriptElement;
    });
    fireEvent.error(firstScript);
    expect(await screen.findByText("无法加载安全验证")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /^\s*登\s*录\s*$/u }),
    ).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "重新加载" }));
    await waitFor(() => {
      const nextScript = document.getElementById("hc-turnstile-api");
      expect(nextScript).toBeInstanceOf(HTMLScriptElement);
      expect(nextScript).not.toBe(firstScript);
    });
  });

  it("submits with Enter, stores the opaque session in memory, and routes an empty account", async () => {
    const user = userEvent.setup();
    useShellStore
      .getState()
      .setSession(
        { actorId: "principal-1", displayName: "Stale", roleIds: [] },
        "stale-session-token",
      );
    useShellStore.getState().setScope({
      organizationId: "stale-organization",
      projectId: "stale-project",
      regionCode: "stale-region",
    });
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
              display_name: "New User",
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
              display_name: "Current User",
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
    expect(useShellStore.getState().principal?.displayName).toBe(
      "Current User",
    );
    const loginHeaders = new Headers(
      (fetchMock.mock.calls[0]?.[1] as RequestInit | undefined)?.headers,
    );
    expect(loginHeaders.get("Authorization")).toBeNull();
    expect(loginHeaders.get("X-Organization-Id")).toBeNull();
    expect(loginHeaders.get("X-Project-Id")).toBeNull();
    expect(loginHeaders.get("X-Region-Code")).toBeNull();
    const bootstrapHeaders = new Headers(
      (fetchMock.mock.calls[1]?.[1] as RequestInit | undefined)?.headers,
    );
    expect(bootstrapHeaders.get("Authorization")).toBe("Bearer session-token");
    expect(bootstrapHeaders.get("X-Organization-Id")).toBeNull();
    expect(bootstrapHeaders.get("X-Project-Id")).toBeNull();
    expect(bootstrapHeaders.get("X-Region-Code")).toBeNull();
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
    const form = submit.closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form!);
    fireEvent.submit(form!);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    resolveFetch?.(problemResponse(429));
    expect(await screen.findByText(/尝试次数过多/u)).toBeVisible();
  });

  it("keeps a late abandoned login from overwriting or revoking a newer session", async () => {
    const user = userEvent.setup();
    let resolveLoginA: ((response: Response) => void) | undefined;
    const pendingLoginA = new Promise<Response>((resolve) => {
      resolveLoginA = resolve;
    });
    const principalB = {
      principal_id: "principal-b",
      username: "login-b",
      display_name: "Login B",
      status: "ACTIVE",
      created_at: "2026-08-18T00:00:00Z",
    } as const;
    const fetchMock = vi
      .fn()
      .mockImplementation((url: string, init: RequestInit) => {
        const path = String(url);
        if (path.endsWith("/auth/sessions")) {
          const body = JSON.parse(String(init.body)) as { username: string };
          if (body.username === "login-a") return pendingLoginA;
          if (body.username === "login-b") {
            return Promise.resolve(
              new Response(
                JSON.stringify({
                  access_token: "session-token-b",
                  token_type: "Bearer",
                  principal: principalB,
                  capability_revision: 0,
                }),
                {
                  status: 201,
                  headers: { "Content-Type": "application/json" },
                },
              ),
            );
          }
        }
        if (path.endsWith("/auth/session/bootstrap")) {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                principal: principalB,
                available_scopes: [],
                capability_revision: 0,
              }),
              { status: 200, headers: { "Content-Type": "application/json" } },
            ),
          );
        }
        if (path.endsWith("/auth/session:logout")) {
          return Promise.resolve(new Response(null, { status: 204 }));
        }
        return Promise.reject(new Error(`Unexpected auth request: ${path}`));
      });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <ProviderHarness>
        <MemoryRouter initialEntries={["/auth/login"]}>
          <Routes>
            <Route
              path="/auth/login"
              element={
                <>
                  <LoginRoute />
                  <LeaveLoginButton />
                </>
              }
            />
            <Route
              path="/away"
              element={
                <>
                  <LocationProbe />
                  <ReturnToLoginButton />
                </>
              }
            />
            <Route path="/account/empty" element={<LocationProbe />} />
            <Route path="/auth/session-expired" element={<LocationProbe />} />
            <Route path="/" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>
      </ProviderHarness>,
    );

    await user.type(screen.getByLabelText("用户名"), "login-a");
    await user.type(screen.getByLabelText("密码"), "password-a");
    await user.click(screen.getByRole("button", { name: /^\s*登\s*录\s*$/u }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "离开登录页" }));
    await user.click(screen.getByRole("button", { name: "返回登录页" }));

    await user.type(screen.getByLabelText("用户名"), "login-b");
    await user.type(screen.getByLabelText("密码"), "password-b");
    await user.click(screen.getByRole("button", { name: /^\s*登\s*录\s*$/u }));
    expect(await screen.findByTestId("location")).toHaveTextContent(
      "/account/empty",
    );
    expect(useShellStore.getState().sessionToken).toBe("session-token-b");

    await act(async () => {
      resolveLoginA?.(
        new Response(
          JSON.stringify({
            access_token: "session-token-a",
            token_type: "Bearer",
            principal: {
              principal_id: "principal-a",
              username: "login-a",
              display_name: "Login A",
              status: "ACTIVE",
              created_at: "2026-08-18T00:00:00Z",
            },
            capability_revision: 0,
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        ),
      );
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(([url]) =>
          String(url).endsWith("/auth/session:logout"),
        ),
      ).toHaveLength(1);
    });

    const bootstrapCalls = fetchMock.mock.calls.filter(([url]) =>
      String(url).endsWith("/auth/session/bootstrap"),
    );
    const logoutCalls = fetchMock.mock.calls.filter(([url]) =>
      String(url).endsWith("/auth/session:logout"),
    );
    expect(bootstrapCalls).toHaveLength(1);
    expect(logoutCalls).toHaveLength(1);
    expect(
      new Headers((bootstrapCalls[0]?.[1] as RequestInit).headers).get(
        "Authorization",
      ),
    ).toBe("Bearer session-token-b");
    expect(
      new Headers((logoutCalls[0]?.[1] as RequestInit).headers).get(
        "Authorization",
      ),
    ).toBe("Bearer session-token-a");
    expect(useShellStore.getState().sessionToken).toBe("session-token-b");
    expect(useShellStore.getState().principal?.actorId).toBe("principal-b");
    expect(screen.getByTestId("location")).toHaveTextContent("/account/empty");
  });

  it("revokes a safe token captured before a malformed session response is rejected", async () => {
    const user = userEvent.setup();
    useShellStore
      .getState()
      .setSession(
        { actorId: "principal-b", displayName: "Login B", roleIds: [] },
        "session-token-b",
      );
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const path = String(url);
      if (path.endsWith("/auth/sessions")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "malformed-session-token",
              token_type: "Bearer",
              principal: {
                principal_id: "principal-malformed",
                username: "malformed-user",
              },
              capability_revision: 0,
            }),
            { status: 201, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      if (path.endsWith("/auth/session:logout")) {
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      return Promise.reject(new Error(`Unexpected auth request: ${path}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderLogin();

    await user.type(screen.getByLabelText("用户名"), "malformed-user");
    await user.type(screen.getByLabelText("密码"), "password-a");
    await user.keyboard("{Enter}");

    expect(
      await screen.findByText(/服务响应与当前认证合同不一致/u),
    ).toBeVisible();
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(([url]) =>
          String(url).endsWith("/auth/session:logout"),
        ),
      ).toHaveLength(1);
    });
    expect(
      fetchMock.mock.calls.filter(([url]) =>
        String(url).endsWith("/auth/session/bootstrap"),
      ),
    ).toHaveLength(0);
    const logoutCall = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/auth/session:logout"),
    );
    expect(
      new Headers((logoutCall?.[1] as RequestInit).headers).get(
        "Authorization",
      ),
    ).toBe("Bearer malformed-session-token");
    expect(useShellStore.getState().sessionToken).toBe("session-token-b");
    expect(useShellStore.getState().principal?.actorId).toBe("principal-b");
  });

  it("rejects mismatched create and bootstrap principals without touching the active session", async () => {
    const user = userEvent.setup();
    useShellStore
      .getState()
      .setSession(
        { actorId: "principal-b", displayName: "Login B", roleIds: [] },
        "session-token-b",
      );
    const createdPrincipal = {
      principal_id: "principal-a",
      username: "login-a",
      display_name: "Login A",
      status: "ACTIVE",
      created_at: "2026-08-18T00:00:00Z",
    } as const;
    const bootstrapPrincipal = {
      ...createdPrincipal,
      principal_id: "principal-other",
      username: "other-user",
      display_name: "Other User",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            access_token: "session-token-a",
            token_type: "Bearer",
            principal: createdPrincipal,
            capability_revision: 0,
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            principal: bootstrapPrincipal,
            available_scopes: [],
            capability_revision: 0,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    renderLogin();

    await user.type(screen.getByLabelText("用户名"), "login-a");
    await user.type(screen.getByLabelText("密码"), "password-a");
    await user.keyboard("{Enter}");

    expect(
      await screen.findByText(/服务响应与当前认证合同不一致/u),
    ).toBeVisible();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const bootstrapHeaders = new Headers(
      (fetchMock.mock.calls[1]?.[1] as RequestInit).headers,
    );
    const logoutHeaders = new Headers(
      (fetchMock.mock.calls[2]?.[1] as RequestInit).headers,
    );
    expect(bootstrapHeaders.get("Authorization")).toBe(
      "Bearer session-token-a",
    );
    expect(logoutHeaders.get("Authorization")).toBe("Bearer session-token-a");
    expect(useShellStore.getState().sessionToken).toBe("session-token-b");
    expect(useShellStore.getState().principal?.actorId).toBe("principal-b");
  });

  it("prevents duplicate registration requests in the same render turn", async () => {
    const user = userEvent.setup();
    useShellStore
      .getState()
      .setSession(
        { actorId: "stale-principal", displayName: "Stale", roleIds: [] },
        "stale-session-token",
      );
    useShellStore.getState().setScope({
      organizationId: "stale-organization",
      projectId: "stale-project",
      regionCode: "stale-region",
    });
    let resolveFetch: ((response: Response) => void) | undefined;
    const pendingResponse = new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(authConfigResponse())
      .mockReturnValue(pendingResponse);
    vi.stubGlobal("fetch", fetchMock);
    renderRegister();

    expect(await screen.findByText(/密码需为 6–128 个字符/u)).toBeVisible();

    await user.type(screen.getByLabelText("用户名"), "register-once");
    await user.type(
      screen.getByLabelText("密码"),
      "Independent passphrase 2026!",
    );
    await user.type(
      screen.getByLabelText("确认密码"),
      "Independent passphrase 2026!",
    );
    const submit = screen.getByRole("button", { name: /创建账户/u });
    const form = submit.closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form!);
    fireEvent.submit(form!);
    await waitFor(() => {
      const registrationCalls = fetchMock.mock.calls.filter(([url]) =>
        String(url).endsWith("/auth/registrations"),
      );
      expect(registrationCalls).toHaveLength(1);
    });
    for (const call of fetchMock.mock.calls) {
      const headers = new Headers(
        (call[1] as RequestInit | undefined)?.headers,
      );
      expect(headers.get("Authorization")).toBeNull();
      expect(headers.get("X-Organization-Id")).toBeNull();
      expect(headers.get("X-Project-Id")).toBeNull();
      expect(headers.get("X-Region-Code")).toBeNull();
    }

    resolveFetch?.(problemResponse(429));
    expect(await screen.findByText(/尝试次数过多/u)).toBeVisible();
  });

  it("aborts public policy discovery when registration unmounts", async () => {
    const fetchMock = vi.fn().mockReturnValue(new Promise<Response>(() => {}));
    vi.stubGlobal("fetch", fetchMock);
    const rendered = renderRegister();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const signal = (fetchMock.mock.calls[0]?.[1] as RequestInit | undefined)
      ?.signal;
    expect(signal?.aborted).toBe(false);

    rendered.unmount();
    expect(signal?.aborted).toBe(true);
  });

  it("does not abort or redirect after an in-flight registration unmounts", async () => {
    const user = userEvent.setup();
    let resolveRegistration: ((response: Response) => void) | undefined;
    const pendingRegistration = new Promise<Response>((resolve) => {
      resolveRegistration = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(authConfigResponse())
      .mockReturnValueOnce(pendingRegistration);
    vi.stubGlobal("fetch", fetchMock);
    render(
      <ProviderHarness>
        <MemoryRouter initialEntries={["/auth/register"]}>
          <Routes>
            <Route
              path="/auth/register"
              element={
                <>
                  <RegisterRoute />
                  <LeaveRegistrationButton />
                </>
              }
            />
            <Route path="/away" element={<LocationProbe />} />
            <Route path="/auth/registered" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>
      </ProviderHarness>,
    );

    expect(await screen.findByText(/密码需为 6–128 个字符/u)).toBeVisible();
    await user.type(screen.getByLabelText("用户名"), "register-later");
    await user.type(
      screen.getByLabelText("密码"),
      "Independent passphrase 2026!",
    );
    await user.type(
      screen.getByLabelText("确认密码"),
      "Independent passphrase 2026!",
    );
    await user.click(screen.getByRole("button", { name: "创建账户" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const registrationSignal = (
      fetchMock.mock.calls[1]?.[1] as RequestInit | undefined
    )?.signal;

    await user.click(screen.getByRole("button", { name: "离开注册页" }));
    expect(await screen.findByTestId("location")).toHaveTextContent("/away");
    expect(registrationSignal?.aborted).toBe(false);

    await act(async () => {
      resolveRegistration?.(
        new Response(
          JSON.stringify({
            principal: {
              principal_id: "principal-later",
              username: "register-later",
              display_name: "register-later",
              status: "ACTIVE",
              created_at: "2026-08-18T00:00:00Z",
            },
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        ),
      );
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.getByTestId("location")).toHaveTextContent("/away");
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
                display_name: "Expired User",
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

  it("uses discovered length guidance and leaves Unicode username policy to the server", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(authConfigResponse(16, 20))
      .mockResolvedValue(problemResponse(422));
    vi.stubGlobal("fetch", fetchMock);
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
    expect(await screen.findByText(/密码需为 16–20 个字符/u)).toBeVisible();
    expect(screen.getByText(/不能包含用户名/u)).toBeVisible();
    expect(
      screen.queryByText(/至少 12|大小写|数字或符号/u),
    ).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("用户名"), "new-user");
    await user.type(screen.getByLabelText("密码"), "weak");
    await user.type(screen.getByLabelText("确认密码"), "weak");
    await user.click(screen.getByRole("button", { name: "创建账户" }));
    expect(await screen.findByText(/密码至少需要 16 个字符/u)).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await user.clear(screen.getByLabelText("密码"));
    await user.clear(screen.getByLabelText("确认密码"));
    await user.type(screen.getByLabelText("密码"), "123456789012345678901");
    await user.type(screen.getByLabelText("确认密码"), "123456789012345678901");
    await user.click(screen.getByRole("button", { name: "创建账户" }));
    expect(await screen.findByText(/密码不能超过 20 个字符/u)).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await user.clear(screen.getByLabelText("密码"));
    await user.clear(screen.getByLabelText("确认密码"));
    await user.type(screen.getByLabelText("密码"), "new-user-passphrase");
    await user.type(screen.getByLabelText("确认密码"), "new-user-passphrase");
    await user.click(screen.getByRole("button", { name: "创建账户" }));
    expect(await screen.findByText(/注册信息未通过校验/u)).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("keeps neutral server-validation guidance when policy discovery fails", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("network unavailable"))
      .mockResolvedValueOnce(problemResponse(422));
    vi.stubGlobal("fetch", fetchMock);
    renderRegister();

    expect(
      screen.getByText(/密码要求将在提交时由服务器安全校验/u),
    ).toBeVisible();
    await user.type(screen.getByLabelText("用户名"), "new-user");
    await user.type(screen.getByLabelText("密码"), "short");
    await user.type(screen.getByLabelText("确认密码"), "short");
    await user.click(screen.getByRole("button", { name: "创建账户" }));

    expect(await screen.findByText(/注册信息未通过校验/u)).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("rejects an impossible discovered policy and still submits to the server", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(authConfigResponse(20, 10))
      .mockResolvedValueOnce(problemResponse(422));
    vi.stubGlobal("fetch", fetchMock);
    renderRegister();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    await user.type(screen.getByLabelText("用户名"), "new-user");
    await user.type(screen.getByLabelText("密码"), "short");
    await user.type(screen.getByLabelText("确认密码"), "short");
    await user.click(screen.getByRole("button", { name: "创建账户" }));

    expect(await screen.findByText(/注册信息未通过校验/u)).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/20–10/u)).not.toBeInTheDocument();
  });

  it("logs out with only the opaque session when a stale project scope is persisted", async () => {
    const user = userEvent.setup();
    useShellStore
      .getState()
      .setSession(
        { actorId: "principal-empty", displayName: "Empty", roleIds: [] },
        "empty-session-token",
      );
    useShellStore.getState().setScope({
      organizationId: "stale-organization",
      projectId: "stale-project",
      regionCode: "stale-region",
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    renderEmptyAccount();

    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(await screen.findByTestId("location")).toHaveTextContent(
      "/auth/login",
    );

    const headers = new Headers(
      (fetchMock.mock.calls[0]?.[1] as RequestInit | undefined)?.headers,
    );
    expect(headers.get("Authorization")).toBe("Bearer empty-session-token");
    expect(headers.get("X-Organization-Id")).toBeNull();
    expect(headers.get("X-Project-Id")).toBeNull();
    expect(headers.get("X-Region-Code")).toBeNull();
  });

  it("submits membership and capability requests through the formal organization-project endpoints", async () => {
    const user = userEvent.setup();
    useShellStore
      .getState()
      .setSession(
        { actorId: "principal-empty", displayName: "empty-user", roleIds: [] },
        "empty-session-token",
      );
    const membership = {
      request_id: "membership-request-a",
      organization_id: "organization-real-a",
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
    await user.type(screen.getByLabelText("组织 ID"), "organization-real-a");
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
      "/api/v1/organizations/organization-real-a/projects/project-real-a/membership-requests",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/v1/organizations/organization-real-a/projects/project-real-a/capability-requests",
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
      expect(headers.get("X-Organization-Id")).toBeNull();
      expect(headers.get("X-Project-Id")).toBeNull();
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
      display_name: "Empty User",
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
                organization_id: "organization-real-a",
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
                organization_id: "organization-real-a",
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
    expect(
      await screen.findByRole("heading", { name: "申请项目权限" }),
    ).toBeVisible();
    expect(screen.getByText(/项目加入已生效/u)).toBeVisible();
    expect(useShellStore.getState().scope).toEqual({
      organizationId: "organization-real-a",
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
