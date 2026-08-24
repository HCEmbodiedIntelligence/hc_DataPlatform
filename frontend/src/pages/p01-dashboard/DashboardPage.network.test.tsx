// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { AuthorizationSnapshot } from "../../entities/capability";
import { makeScopeKey } from "../../entities/scope";
import {
  getDashboardActivity,
  getDashboardSnapshot,
  listDashboardPendingItems,
} from "../../features/dashboard/api/client";
import {
  dashboardActivityFixture,
  dashboardPendingFixture,
  dashboardSnapshotFixture,
} from "../../mocks/fixtures/dashboard";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import { DashboardPage } from "./page";

const scope = {
  organizationId: "org_fx_01",
  projectId: "prj_fx_01",
  regionCode: "cn-shanghai",
} as const;
const dashboardScope = { ...scope, timezone: "Asia/Shanghai" } as const;
const dashboardWindow = {
  from: "2026-08-04T08:00:00Z",
  to: "2026-08-05T08:00:00Z",
} as const;
const dashboardAuthorization = {
  scopeKey: makeScopeKey(scope),
  roleVersion: "role-p01",
  capabilities: ["dashboard.read"],
  fetchedAt: "2026-08-05T08:00:00Z",
  expiresAt: "2099-08-05T08:00:00Z",
} as unknown as AuthorizationSnapshot;

function json(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
}

function responseFor(url: string): Response {
  if (url.includes("/dashboard/activity?"))
    return json(dashboardActivityFixture);
  if (url.includes("/dashboard/snapshot?"))
    return json(dashboardSnapshotFixture);
  if (url.includes("/dashboard/pending-items?"))
    return json(dashboardPendingFixture);
  return json({ title: "Unexpected request", status: 404 }, { status: 404 });
}

describe("Dashboard production client path", () => {
  beforeEach(() => {
    configureRuntime({
      apiBaseUrl: "/api/v1",
      sseBaseUrl: "/api/v1",
      buildVersion: "p01-network-test",
      releaseEnv: "test",
    });
    useShellStore.setState({
      principal: {
        actorId: "actor-p01",
        displayName: "工作台测试用户",
        roleIds: [],
      },
      sessionToken: "p01-real-client-token",
      scope,
      scopeKey: makeScopeKey(scope),
      scopeChanging: false,
      // dashboard.read is formal in the runtime Dashboard contract but remains
      // reserved in the shared UI catalogue; P01 must still fail closed on it.
      authorization: dashboardAuthorization,
      authorizationLoading: false,
      authorizationFailed: false,
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    resetRuntimeConfigForTests();
  });

  it("issues only the three visible-region HTTP reads with no Browser Mock or MSW fallback", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) =>
      responseFor(String(input)),
    );
    vi.stubGlobal("fetch", fetchMock);

    const [activity, snapshot, pending] = await Promise.all([
      getDashboardActivity(dashboardScope, dashboardWindow),
      getDashboardSnapshot(dashboardScope, dashboardWindow),
      listDashboardPendingItems(dashboardScope, dashboardWindow, { limit: 5 }),
    ]);

    expect(activity.items).toHaveLength(2);
    expect(snapshot.signalPipeline.stages).toHaveLength(8);
    expect(snapshot.signalPipeline.stageCounts.PUBLISHED).toBe(8);
    expect(pending.items).toHaveLength(4);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const urls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(
      urls
        .map((url) => new URL(url, "https://frontend.invalid").pathname)
        .sort(),
    ).toEqual([
      "/api/v1/projects/prj_fx_01/dashboard/activity",
      "/api/v1/projects/prj_fx_01/dashboard/pending-items",
      "/api/v1/projects/prj_fx_01/dashboard/snapshot",
    ]);
    for (const url of urls) {
      const parsed = new URL(url, "https://frontend.invalid");
      expect(parsed.searchParams.get("region_code")).toBe("cn-shanghai");
      expect(parsed.searchParams.get("timezone")).toBe("Asia/Shanghai");
      expect(parsed.searchParams.get("from")).toBe(dashboardWindow.from);
      expect(parsed.searchParams.get("to")).toBe(dashboardWindow.to);
    }
  });

  it("renders only the remaining cards and never requests coverage", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) =>
      responseFor(String(input)),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/dashboard"]}>
          <DashboardPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(
      await screen.findByRole("heading", { name: "最近活动" }),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: "我的待办" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "局部状态" })).toBeNull();
    expect(screen.queryByText("采集覆盖率")).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/dashboard/coverage"),
      ),
    ).toBe(false);
  });

  it("fails closed on 403 authorization without issuing Dashboard reads", async () => {
    useShellStore.setState({
      authorization: {
        scopeKey: makeScopeKey(scope),
        roleVersion: "role-p01-forbidden",
        capabilities: [],
        fetchedAt: "2026-08-05T08:00:00Z",
        expiresAt: "2099-08-05T08:00:00Z",
      },
    });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/dashboard"]}>
          <DashboardPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("无权访问")).toBeVisible();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
