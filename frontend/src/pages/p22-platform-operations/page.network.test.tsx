// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { RouteCapabilityGuard } from "../../app/router/RouteCapabilityGuard";
import { makeScopeKey } from "../../entities/scope";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import { PlatformOperationsPage } from "./page";

const digest = `sha256:${"a".repeat(64)}`;
const reference = `id-hmac-sha256:${"b".repeat(64)}`;

const overview = {
  format_version: "hc-platform-operations-overview/v1",
  observed_at: "2026-08-29T08:00:00Z",
  environment_ref: `id-hmac-sha256:${"c".repeat(64)}`,
  release: {
    format_version: "hc-platform-release-identity/v1",
    release_id: "platform-v0.1.0-test.1",
    semantic_version: "0.1.0",
    git_commit: "1".repeat(40),
    chart_version: "0.1.0",
    release_manifest_digest: digest,
    migration_manifest_digest: digest,
    component: "api",
    component_image_digest: digest,
  },
  node_count: 4,
  nodes: ["frontend", "api", "worker", "media-worker"].map((role, index) => ({
    node_ref: `id-hmac-sha256:${String(index + 1).repeat(64)}`,
    role,
    release_id: "platform-v0.1.0-test.1",
    release_manifest_digest: digest,
    started_at: "2026-08-29T06:00:00Z",
    last_heartbeat_at: "2026-08-29T07:59:50Z",
    readiness: "ready",
    failed_checks: [],
    applied_config_revision: 18,
    stale: false,
  })),
  backup_count: 1,
  backups: [
    {
      backup_ref: reference,
      format_version: "hc-platform-backup/v1",
      mode: "portable",
      status: "RESTORE_VERIFIED",
      release_manifest_sha256: "a".repeat(64),
      backup_created_at: "2026-08-29T04:00:00Z",
      backup_completed_at: "2026-08-29T05:00:00Z",
      status_occurred_at: "2026-08-29T05:30:00Z",
    },
  ],
  upgrade_preflight: {
    status: "READY",
    checks: [
      ["RELEASE_IDENTITY", "RELEASE_IDENTITY_VERIFIED"],
      ["NODE_CONVERGENCE", "REQUIRED_ROLES_CONVERGED"],
      ["NODE_READINESS", "ACTIVE_NODES_READY"],
      ["CONFIG_CONVERGENCE", "CONFIG_REVISION_CONVERGED"],
      ["VERIFIED_BACKUP", "CURRENT_RELEASE_BACKUP_VERIFIED"],
      ["VERIFIED_RESTORE", "CURRENT_RELEASE_RESTORE_VERIFIED"],
      ["CENTRAL_LOG_SEARCH", "CENTRAL_LOG_SEARCH_CONFIGURED"],
    ].map(([code, reason_code]) => ({ code, status: "PASS", reason_code })),
  },
};

const emptyReleaseHistory = {
  format_version: "hc-platform-release-history/v1",
  count: 0,
  items: [],
};

const unconfiguredObjectStore = {
  format_version: "hc-object-store-config/v1",
  environment_id: "hc-local",
  revision: 0,
  source: "unconfigured",
  configured: false,
  provider: "oss",
  endpoint: "https://oss-cn-hangzhou.aliyuncs.com",
  public_endpoint: "https://oss-cn-hangzhou.aliyuncs.com",
  bucket: "",
  region: "cn-hangzhou",
  access_key_configured: false,
  access_key_hint: null,
  activation_required: false,
  updated_at: null,
} as const;

function releaseHistory(state = "AWAITING_APPROVAL") {
  return {
    format_version: "hc-platform-release-history/v1",
    count: 1,
    items: [
      {
        format_version: "hc-platform-release-run/v1",
        release_id: "platform-v0.1.1",
        source_version: "0.1.0",
        target_version: "0.1.1",
        manifest_sha256: "d".repeat(64),
        state,
        state_version: state === "APPROVED" ? 2 : 1,
        approval_required: true,
        approved: state === "APPROVED",
        created_at: "2026-08-29T07:30:00Z",
        updated_at: "2026-08-29T07:45:00Z",
        events: [
          {
            state_version: 1,
            event_kind: "PREFLIGHT_PASSED",
            state: "AWAITING_APPROVAL",
            actor_ref: reference,
            reason_code: "SIGNED_COMPATIBLE_RELEASE_VERIFIED",
            occurred_at: "2026-08-29T07:30:00Z",
          },
        ],
      },
    ],
  };
}

function json(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
}

function logResponse(url: string) {
  const parsed = new URL(url, "https://frontend.invalid");
  const occurredFrom = parsed.searchParams.get("occurred_from");
  const occurredTo = parsed.searchParams.get("occurred_to");
  if (!occurredFrom || !occurredTo) throw new Error("log window missing");
  return {
    format_version: "hc-platform-log-page/v1",
    observed_at: occurredTo,
    occurred_from: occurredFrom,
    occurred_to: occurredTo,
    count: 1,
    truncated: false,
    items: [
      {
        schema_version: "hc-platform-log-event/v1",
        timestamp: occurredTo,
        severity: "INFO",
        service: "hc-data-platform-api",
        role: "api",
        release_id: "platform-v0.1.0-test.1",
        event_code: "PLATFORM.READY",
        request_id: "request-safe-001",
        operation_id: null,
        workflow_id: null,
        duration_ms: 14,
        retry_count: 0,
        error_type: null,
        route: "/api/v1/platform/overview",
        http_method: "GET",
        status_code: 200,
      },
    ],
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/settings/platform-operations"]}>
        <Routes>
          <Route
            element={
              <RouteCapabilityGuard
                requiredCapabilities={["platform.operations.read"]}
              />
            }
          >
            <Route
              path="/settings/platform-operations"
              element={<PlatformOperationsPage />}
            />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("P22 platform operations production client path", () => {
  beforeEach(() => {
    configureRuntime({
      apiBaseUrl: "/api/v1",
      sseBaseUrl: "/api/v1",
      buildVersion: "p22-network-test",
      releaseEnv: "test",
    });
    useShellStore.setState({
      principal: {
        actorId: "ordinary-platform-admin",
        displayName: "普通平台管理员",
        roleIds: [],
      },
      sessionToken: "p22-session-token",
      scope: null,
      scopeKey: makeScopeKey({ organizationId: "unscoped" }),
      authorization: null,
      authorizationLoading: false,
      authorizationFailed: false,
      bootstrapLoaded: true,
      platformCapabilities: ["platform.admin"],
    });
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
    vi.stubGlobal(
      "ResizeObserver",
      class ResizeObserverStub {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    resetRuntimeConfigForTests();
    useShellStore.getState().clearSensitiveState();
  });

  it("renders version, nodes, backups, logs, and upgrade preflight with Mock-off HTTP", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, _init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/platform/overview")) return json(overview);
        if (url.includes("/platform/logs?")) return json(logResponse(url));
        if (url.includes("/platform/releases?"))
          return json(emptyReleaseHistory);
        if (url.includes("/platform/projects"))
          return json({
            format_version: "hc-platform-project-directory/v1",
            count: 0,
            items: [],
          });
        if (url.includes("/platform/object-store-config"))
          return json(unconfiguredObjectStore);
        return json(
          { title: "Unexpected request", status: 404 },
          { status: 404 },
        );
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    expect(
      await screen.findByRole("heading", { name: "升级准入链" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "platform-v0.1.0-test.1" }),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: "节点收敛" })).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "备份目录与验证" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "结构化运行日志" }),
    ).toBeVisible();
    expect(
      await screen.findByRole("heading", { name: "升级历史与人工批准" }),
    ).toBeVisible();
    expect(screen.getByText("允许进入升级窗口")).toBeVisible();
    expect(await screen.findByText("PLATFORM.READY")).toBeVisible();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    const calls = fetchMock.mock.calls;
    expect(
      calls
        .map(
          ([input]) =>
            new URL(String(input), "https://frontend.invalid").pathname,
        )
        .sort(),
    ).toEqual([
      "/api/v1/platform/logs",
      "/api/v1/platform/overview",
      "/api/v1/platform/projects",
      "/api/v1/platform/releases",
    ]);
    for (const [, init] of calls) {
      const headers = new Headers(init?.headers);
      expect(headers.get("Authorization")).toBe("Bearer p22-session-token");
      expect(headers.get("X-Organization-Id")).toBeNull();
      expect(headers.get("X-Project-Id")).toBeNull();
      expect(headers.get("X-Region-Code")).toBeNull();
    }

    const body = document.body.textContent?.toLowerCase() ?? "";
    for (const forbidden of [
      "node_name",
      "pod_name",
      "kubernetes_node_name",
      "repository_id",
      "manifest_uri",
      "object_key",
      "host-sensitive-sentinel",
      "secret-sensitive-sentinel",
    ]) {
      expect(body).not.toContain(forbidden);
    }
  });

  it("loads and saves encrypted OSS configuration only after the admin opens the panel", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/platform/overview")) return json(overview);
        if (url.includes("/platform/logs?")) return json(logResponse(url));
        if (url.includes("/platform/releases?"))
          return json(emptyReleaseHistory);
        if (url.includes("/platform/object-store-config")) {
          if (init?.method === "PUT") {
            return json({
              ...unconfiguredObjectStore,
              revision: 1,
              source: "database",
              configured: true,
              bucket: "hc-platform-test",
              access_key_configured: true,
              access_key_hint: "••••-key",
              activation_required: true,
              updated_at: "2026-08-29T08:01:00Z",
            });
          }
          return json(unconfiguredObjectStore);
        }
        return json({}, { status: 404 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("PLATFORM.READY");
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/platform/object-store-config"),
      ),
    ).toBe(false);

    await user.click(
      screen.getByRole("button", { name: "创建或编辑 OSS 地址" }),
    );
    await screen.findByText("尚未配置");
    await user.type(screen.getByLabelText("OSS Bucket"), "hc-platform-test");
    await user.type(
      screen.getByLabelText("OSS Access Key ID"),
      "sensitive-access-key",
    );
    await user.type(
      screen.getByLabelText("OSS Access Key Secret"),
      "sensitive-secret-key",
    );
    await user.click(screen.getByRole("button", { name: "加密保存配置" }));

    expect(await screen.findByText("等待重启生效")).toBeVisible();
    const updateCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).includes("/platform/object-store-config") &&
        init?.method === "PUT",
    );
    expect(updateCall).toBeDefined();
    expect(JSON.parse(String(updateCall?.[1]?.body))).toMatchObject({
      expected_revision: 0,
      provider: "oss",
      bucket: "hc-platform-test",
      access_key: "sensitive-access-key",
      secret_key: "sensitive-secret-key",
    });
    expect(screen.getByLabelText("OSS Access Key ID")).toHaveValue("");
    expect(screen.getByLabelText("OSS Access Key Secret")).toHaveValue("");
  });

  it("sends only allowlisted filters and never accepts a free-form LogQL field", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, _init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/platform/overview")) return json(overview);
        if (url.includes("/platform/logs?")) return json(logResponse(url));
        if (url.includes("/platform/releases?"))
          return json(emptyReleaseHistory);
        return json({}, { status: 404 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("PLATFORM.READY");
    await user.type(screen.getByLabelText("日志事件代码"), "backup.verify");
    await user.click(screen.getByRole("button", { name: "检索日志" }));

    await waitFor(() => {
      const logCalls = fetchMock.mock.calls.filter(([input]) =>
        String(input).includes("/platform/logs?"),
      );
      expect(logCalls).toHaveLength(2);
    });
    const latest = String(
      fetchMock.mock.calls
        .filter(([input]) => String(input).includes("/platform/logs?"))
        .at(-1)?.[0],
    );
    const params = new URL(latest, "https://frontend.invalid").searchParams;
    expect(params.get("event_code")).toBe("BACKUP.VERIFY");
    expect([...params.keys()].sort()).toEqual([
      "event_code",
      "limit",
      "occurred_from",
      "occurred_to",
    ]);
    expect(latest.toLowerCase()).not.toContain("logql");
    expect(latest).not.toContain("line_format");
  });

  it("records a distinct manual approval without sending deployment credentials", async () => {
    useShellStore.setState({
      platformCapabilities: [
        "platform.operations.read",
        "platform.release.operate",
      ],
    });
    let approved = false;
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, _init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/platform/overview")) return json(overview);
        if (url.includes("/platform/logs?")) return json(logResponse(url));
        if (url.includes(":approve")) {
          approved = true;
          return json(releaseHistory("APPROVED").items[0]);
        }
        if (url.includes("/platform/releases?"))
          return json(
            releaseHistory(approved ? "APPROVED" : "AWAITING_APPROVAL"),
          );
        return json({}, { status: 404 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();

    await user.type(
      await screen.findByLabelText("platform-v0.1.1 批准理由"),
      "第二位操作员已核验准入证据",
    );
    await user.click(screen.getByRole("button", { name: "人工批准" }));

    await waitFor(() => expect(screen.getByText("APPROVED")).toBeVisible());
    const approvalCall = fetchMock.mock.calls.find(([input]) =>
      String(input).includes(":approve"),
    );
    expect(approvalCall).toBeDefined();
    const [input, init] = approvalCall!;
    expect(String(input)).toContain(
      "/api/v1/platform/releases/platform-v0.1.1:approve",
    );
    expect(init?.method).toBe("POST");
    const requestBody = String(init?.body).toLowerCase();
    expect(requestBody).toContain("expected_state_version");
    for (const forbidden of [
      "kubeconfig",
      "cluster-admin",
      "bearer_token",
      "private_key",
      "imagepullsecret",
    ]) {
      expect(requestBody).not.toContain(forbidden);
    }
  });
});
