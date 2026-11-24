// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { AuthorizationSnapshot } from "../../entities/capability";
import { makeScopeKey } from "../../entities/scope";
import {
  getDashboardActivity,
  getDashboardTaskStatus,
  listDashboardPendingItems,
} from "../../features/dashboard/api/client";
import {
  dashboardActivityFixture,
  dashboardPendingFixture,
  dashboardTaskStatusAllTasksFixture,
  dashboardTaskStatusFixture,
} from "../../mocks/fixtures/dashboard";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import { DashboardPage } from "./page";
import { dashboardQueryKeys } from "../../features/dashboard/api/query-keys";

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
  if (url.includes("/dashboard/analytics?"))
    return json({
      project_id: scope.projectId,
      region_code: scope.regionCode,
      as_of: dashboardWindow.to,
      analytics: {
        buckets: [{ start: dashboardWindow.from, episodes: 2, frames: 360, seconds: 12 }],
        affected_packages: 2, reports: { recording: 4, episode: 3 },
        outcomes: { PASS: 3, REJECT: 1 },
        issues: {
          CAPTURE_INTERRUPTION_TOO_LONG: 1,
          CAPTURE_VALID_RATIO_LOW: 1,
          QC_MODALITY_OFFSET: 1,
        },
      },
    });
  if (url.includes("/dashboard/activity?"))
    return json(dashboardActivityFixture);
  if (url.includes("/dashboard/task-status?")) {
    const parsed = new URL(url, "https://frontend.invalid");
    return json(
      parsed.searchParams.has("task_id")
        ? { ...dashboardTaskStatusFixture, tasks: dashboardTaskStatusAllTasksFixture.tasks }
        : dashboardTaskStatusAllTasksFixture,
    );
  }
  if (url.includes("/dashboard/pending-items?"))
    return json(dashboardPendingFixture);
  return json({ title: "Unexpected request", status: 404 }, { status: 404 });
}

describe("Dashboard production client path", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({
        matches: false,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    const getComputedStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
      getComputedStyle(element),
    );
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
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    resetRuntimeConfigForTests();
  });

  it("keeps active details during refresh but removes them when the issue is closed", async () => {
    const fixture = {
      ...dashboardTaskStatusAllTasksFixture,
      pipeline: {
        ...dashboardTaskStatusAllTasksFixture.pipeline,
        issues: [
          {
            task_id: "task-assembly-01",
            rollout_id: "episode-failed",
            data_package_id: "package-failed",
            category: "TECHNICAL",
            stage: "STANDARDIZATION",
            reason_code: "ALIGNMENT_FAILED",
            label: "对齐失败",
            description: "需要保留的失败详情",
            source_episode_index: 0,
            source_import_id: null,
            alignment_attempt_id: null,
            qc_status: null,
            lance_ready: false,
            findings: [],
          },
        ],
      },
    };
    let nextRead: Promise<Response> | undefined;
    let failReads = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (!String(input).includes("/dashboard/task-status?"))
          return responseFor(String(input));
        if (nextRead) {
          const read = nextRead;
          nextRead = undefined;
          return read;
        }
        return failReads
          ? json({ message: "暂时离线" }, { status: 503 })
          : json(fixture);
      }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retryDelay: 0 } },
    });
    const queryKey = dashboardQueryKeys.taskStatus(dashboardScope);
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DashboardPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "处理失败 2" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "查看 Episode 0 详情" }),
    );
    const detail = await screen.findByText("需要保留的失败详情");
    for (const fail of [false, true, false]) {
      failReads = fail;
      let resolve!: (response: Response) => void;
      nextRead = new Promise<Response>((done) => {
        resolve = done;
      });
      let refresh!: Promise<void>;
      await act(async () => {
        refresh = client.refetchQueries({ queryKey });
      });
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "刷新工作台" })).toHaveClass(
          "ant-btn-loading",
        ),
      );
      expect(detail).toBeVisible();
      expect(screen.getByText("需要保留的失败详情")).toBe(detail);
      await act(async () => {
        resolve(
          fail ? json({ message: "暂时离线" }, { status: 503 }) : json(fixture),
        );
        await refresh;
      });
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "刷新工作台" }),
        ).not.toHaveClass("ant-btn-loading"),
      );
      expect(screen.getByText("需要保留的失败详情")).toBe(detail);
    }
    fixture.pipeline.issues = [];
    await act(async () => {
      await client.refetchQueries({ queryKey });
    });
    await waitFor(() =>
      expect(screen.queryByText("需要保留的失败详情")).not.toBeInTheDocument(),
    );
    expect(screen.queryByText("package-failed")).not.toBeInTheDocument();
    client.clear();
  });

  it("issues only the three visible-region HTTP reads with no Browser Mock or MSW fallback", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) =>
      responseFor(String(input)),
    );
    vi.stubGlobal("fetch", fetchMock);

    const [activity, taskStatus, pending] = await Promise.all([
      getDashboardActivity(dashboardScope, dashboardWindow),
      getDashboardTaskStatus(dashboardScope, "task-assembly-01"),
      listDashboardPendingItems(dashboardScope, dashboardWindow, { limit: 5 }),
    ]);

    expect(activity.items).toHaveLength(2);
    expect(taskStatus.selected?.stages).toHaveLength(8);
    expect(taskStatus.selected?.task.lifecycle).toBe("ACTIVE");
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
      "/api/v1/projects/prj_fx_01/dashboard/task-status",
    ]);
    for (const url of urls) {
      const parsed = new URL(url, "https://frontend.invalid");
      expect(parsed.searchParams.get("region_code")).toBe("cn-shanghai");
      if (!parsed.pathname.endsWith("/task-status")) {
        expect(parsed.searchParams.get("timezone")).toBe("Asia/Shanghai");
        expect(parsed.searchParams.get("from")).toBe(dashboardWindow.from);
        expect(parsed.searchParams.get("to")).toBe(dashboardWindow.to);
      } else {
        expect(parsed.searchParams.get("task_id")).toBe("task-assembly-01");
      }
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
      await screen.findByRole("heading", { name: "质检结果占比" }),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: "入库 Episode 趋势" })).toBeVisible();
    expect(await screen.findByText("连续无效采集行超过拒绝阈值")).toBeVisible();
    expect(screen.getByText("有效采集行比例过低")).toBeVisible();
    expect(screen.getByText("传感器时间不同步")).toBeVisible();
    expect(screen.getByText("全部任务（2）")).toBeVisible();
    expect(screen.getByLabelText("全部任务汇总")).toHaveTextContent(
      "2 个任务 · 14 个数据包",
    );
    expect(screen.queryByRole("heading", { name: "局部状态" })).toBeNull();
    expect(screen.queryByText("采集覆盖率")).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/dashboard/coverage"),
      ),
    ).toBe(false);
  });

  it("switches the analytics scope with the task selector and restores all tasks", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "https://frontend.invalid");
      const response = responseFor(String(input));
      if (url.pathname.endsWith("/analytics") && url.searchParams.get("task_id") === "task-assembly-01") {
        const body = await response.json();
        return json({ ...body, analytics: { buckets: [], issues: {}, outcomes: {}, reports: {}, affected_packages: 0 } });
      }
      return response;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<QueryClientProvider client={new QueryClient()}><MemoryRouter initialEntries={["/dashboard"]}><DashboardPage /></MemoryRouter></QueryClientProvider>);
    const bar = await screen.findByLabelText(/2 条 · 360 帧 · 12 秒/);
    fireEvent.mouseEnter(bar);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("2 条 · 360 帧 · 12 秒");
    expect(screen.getByText("有问题的数据包 2 个")).toBeVisible();
    fireEvent.mouseDown(screen.getByRole("combobox", {name:"筛选采集任务"}));
    fireEvent.click(await screen.findByText("00000042 · 双臂装配采集", { selector: ".ant-select-item-option-content" }));
    const zeroBuckets = await screen.findAllByLabelText(/0 条 · 0 帧 · 0 秒/);
    expect(zeroBuckets).toHaveLength(24);
    fireEvent.mouseLeave(bar);
    fireEvent.focus(zeroBuckets[0]!);
    await waitFor(() => expect(screen.getByRole("tooltip")).toHaveTextContent("0 条 · 0 帧 · 0 秒"));
    expect(screen.getByText("有问题的数据包 0 个")).toBeVisible();
    expect(screen.getByRole("img", { name: "质检问题分类占比图，0 类问题" })).toBeVisible();
    expect(screen.getByText("录制报告 0 份 · Episode 报告 0 份")).toBeVisible();
    fireEvent.mouseDown(screen.getByRole("combobox", {name:"筛选采集任务"}));
    fireEvent.click(await screen.findByText("全部任务（2）", { selector: ".ant-select-item-option-content" }));
    expect(await screen.findByLabelText(/2 条 · 360 帧 · 12 秒/)).toBeVisible();
  });

  it("requests a new analytics interval from the time picker", async () => {
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
    await screen.findByRole("heading", { name: "质检结果占比" });
    fireEvent.click(screen.getByPlaceholderText("开始时间"));
    fireEvent.click(await screen.findByText("最近 7 天"));
    await waitFor(() => {
      const requests = fetchMock.mock.calls.map(
        ([input]) => new URL(String(input), "https://frontend.invalid"),
      );
      expect(
        requests.some(
          (url) =>
            url.pathname.endsWith("/analytics") &&
            Date.parse(url.searchParams.get("to")!) -
              Date.parse(url.searchParams.get("from")!) ===
              7 * 86400000,
        ),
      ).toBe(true);
    });
    expect(screen.queryByRole("heading", { name: "我的待办" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "最近活动" })).toBeNull();
  });

  it("renders the Dashboard and issues both reads for platform.admin", async () => {
    useShellStore.setState({
      authorization: {
        scopeKey: makeScopeKey(scope),
        roleVersion: "role-p01-platform-admin",
        capabilities: ["platform.admin"],
        fetchedAt: "2026-08-05T08:00:00Z",
        expiresAt: "2099-08-05T08:00:00Z",
      },
    });
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
      await screen.findByRole("heading", { name: "质检结果占比" }),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: "入库 Episode 趋势" })).toBeVisible();
    expect(screen.queryByText("无权访问")).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(
      fetchMock.mock.calls
        .map(
          ([input]) =>
            new URL(String(input), "https://frontend.invalid").pathname,
        )
        .sort(),
    ).toEqual([
      "/api/v1/projects/prj_fx_01/dashboard/analytics",
      "/api/v1/projects/prj_fx_01/dashboard/task-status",
    ]);
  });

  it("renders the complete zero-result Dashboard without tenant reads", () => {
    useShellStore.setState({
      scope: null,
      scopeKey: makeScopeKey({ organizationId: "unscoped" }),
      authorization: null,
      authorizationLoading: false,
      authorizationFailed: false,
      bootstrapLoaded: true,
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

    expect(screen.getByRole("heading", { name: "工作台" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "信号轨道" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "入库 Episode 趋势" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "质检结果占比" })).toBeVisible();
    expect(screen.getByText("当前范围没有采集任务")).toBeVisible();
    expect(screen.queryByText("能力尚未开放")).toBeNull();
    expect(screen.getByRole("button", { name: "刷新工作台" })).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();
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
