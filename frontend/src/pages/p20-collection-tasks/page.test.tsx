// @vitest-environment jsdom

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProviderHarness } from "../../app/providers";
import { makeScopeKey } from "../../entities/scope";
import {
  createDomainError,
  type DomainErrorCode,
} from "../../shared/api/domain-error";
import { useShellStore } from "../../shared/scope/shell-store";
import type {
  CollectionTask,
  CollectionTaskGateway,
  CollectionTaskProgress,
} from "./api";
import { CollectionTaskPage } from "./page";

const scope = {
  organizationId: "org-test",
  projectId: "project-test",
  regionCode: "cn-test",
} as const;

const task: CollectionTask = {
  schema_version: "1",
  collection_task_id: "task-1",
  dataset_id: "dataset_task_test_1",
  organization_id: scope.organizationId,
  project_id: scope.projectId,
  task_code: "00000001",
  name: "透明件抓取采集",
  type: "抓取采集",
  scenario: "透明工件工位",
  description: "覆盖反光与遮挡条件。",
  target: { package_count: 20, duration_seconds: 1200 },
  quality_threshold: 0.9,
  status: "ACTIVE",
};

const progress: CollectionTaskProgress = {
  schema_version: "1",
  collection_task_id: task.collection_task_id,
  organization_id: scope.organizationId,
  project_id: scope.projectId,
  status: "ACTIVE",
  as_of: "2026-08-18T05:30:00Z",
  received_package_count: 8,
  captured_duration_seconds: 600,
  duration_observed_package_count: 8,
  duration_unknown_package_count: 2,
  qc: {
    evaluated_count: 7,
    pass_count: 6,
    risk_count: 1,
    reject_count: 0,
    pending_count: 1,
    pass_rate: { numerator: 6, denominator: 7, value: 6 / 7 },
  },
  attainment: {
    status: "IN_PROGRESS",
    package_count: {
      actual: 8,
      target: 20,
      progress: 0.4,
      status: "IN_PROGRESS",
    },
    duration_seconds: {
      actual: 600,
      target: 1200,
      progress: 0.5,
      status: "IN_PROGRESS",
    },
    quality_threshold: 0.9,
    quality_status: "PENDING_QC",
  },
  observed_sources: {
    device_ids: ["robot-7"],
    camera_ids: ["cam-a"],
    topic_names: [],
  },
};

function makeGateway() {
  return {
    listAssignableDatasets: vi.fn(async () => [
      {
        datasetId: "dataset_shared_night",
        name: "夜班采集",
        folderPath: ["机器人", "G1"],
      },
    ]),
    list: vi.fn(async () => ({ items: [task], next_cursor: null })),
    progress: vi.fn(async () => progress),
    packages: vi.fn(async () => ({
      schema_version: "1" as const,
      collection_task_id: task.collection_task_id,
      organization_id: task.organization_id,
      project_id: task.project_id,
      as_of: "2026-08-26T12:00:00Z",
      items: [],
    })),
    detail: vi.fn(async () => ({ task, etag: '"v1"' })),
    create: vi.fn(async (_scope, command, _idempotencyKey: string) => ({
      ...task,
      name: command.name,
      type: command.type,
      scenario: command.scenario,
      description: command.description,
      target: command.target ?? null,
      quality_threshold: command.quality_threshold ?? null,
    })),
    update: vi.fn(async (_scope, _taskId, command) => ({
      ...task,
      ...command,
    })),
    close: vi.fn(async () => ({ ...task, status: "CLOSED" as const })),
    cancel: vi.fn(async () => ({ ...task, status: "CANCELLED" as const })),
    reopen: vi.fn(async () => ({ ...task, status: "ACTIVE" as const })),
  } satisfies CollectionTaskGateway;
}

function renderPage(
  gateway: CollectionTaskGateway,
  capabilityOverride: "manage" | "read-only" | null = "manage",
  initialEntry = "/collection-tasks",
) {
  return render(
    <ProviderHarness>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route
            path="/collection-tasks"
            element={
              <CollectionTaskPage
                {...(capabilityOverride === null ? {} : { capabilityOverride })}
                gateway={gateway}
              />
            }
          />
          <Route path="*" element={null} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </ProviderHarness>,
  );
}

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
    </output>
  );
}

async function fillMinimumCreateForm(user: ReturnType<typeof userEvent.setup>) {
  const drawer = screen.getByRole("dialog", { name: "新建采集任务" });
  await user.type(within(drawer).getByLabelText("任务名称"), "夜班工位采集");
  await user.type(within(drawer).getByLabelText("采集类型"), "抓取采集");
  await user.type(within(drawer).getByLabelText("采集场景"), "夜间灯光条件");
}

async function chooseTaskAction(
  user: ReturnType<typeof userEvent.setup>,
  name: "编辑任务" | "关闭任务" | "取消任务" | "重新开启",
) {
  await user.click(screen.getByRole("button", { name: "更多操作" }));
  await user.click(await screen.findByRole("menuitem", { name }));
}

beforeEach(() => {
  useShellStore.getState().setScope(scope);
  useShellStore.setState({
    authorization: null,
    authorizationFailed: false,
    authorizationLoading: false,
    scopeChanging: false,
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
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getComputedStyle(element),
  );
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("P20 collection tasks", () => {
  it("renders the normal zero-row task table without tenant requests", () => {
    useShellStore.setState({
      scope: null,
      scopeKey: makeScopeKey({ organizationId: "unscoped" }),
      authorization: null,
      authorizationLoading: false,
      authorizationFailed: false,
      bootstrapLoaded: true,
    });
    const gateway = makeGateway();

    renderPage(gateway, null);

    expect(screen.getByRole("heading", { name: "采集任务" })).toBeVisible();
    expect(screen.getByLabelText("采集任务筛选")).toBeVisible();
    expect(
      screen.getByRole("columnheader", { name: "任务信息" }),
    ).toBeVisible();
    expect(screen.getByText("当前窗口 0 条")).toBeVisible();
    expect(screen.queryByText("无权访问")).toBeNull();
    expect(gateway.list).not.toHaveBeenCalled();
  });

  it("opens the task-scoped dataset route from the identity link", async () => {
    const user = userEvent.setup();
    renderPage(makeGateway());

    await screen.findByText("目标进行中");
    const link = await screen.findByRole("link", {
      name: `查看采集任务 ${task.name}（${task.task_code}）的数据集`,
    });
    expect(link).toHaveAttribute("href", `/datasets/${task.dataset_id}`);

    await user.click(link);
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/datasets/${task.dataset_id}`,
    );
  });

  it("renders the real receive, duration, QC, source, and threshold facts", async () => {
    renderPage(makeGateway());

    await screen.findByText("目标进行中");
    expect(screen.getByTitle("原始任务码：00000001")).toHaveTextContent(
      "0000 0001",
    );
    expect(screen.getByText("类型：抓取采集")).toBeVisible();
    expect(screen.getByText("场景：透明工件工位")).toBeVisible();
    expect(screen.getByText("8 包 / 20 包")).toBeVisible();
    expect(screen.getByText("10 分钟 / 20 分钟")).toBeVisible();
    expect(screen.getByText("2 包时长未知")).toBeVisible();
    expect(
      screen.getByLabelText("Pass 6，Risk 1，Reject 0，未出质检 1"),
    ).toBeVisible();
    expect(screen.getByText("通过率 85.7%")).toBeVisible();
    expect(screen.getByText("存在未出质检包")).toBeVisible();
    expect(screen.getByText("1 设备 · 1 相机")).toBeVisible();
    expect(
      screen.queryByText("搜索与类型仅筛选当前游标窗口"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("任务码用于数据归类，不是数据包 ID。"),
    ).not.toBeInTheDocument();
    expect(screen.getByText(task.dataset_id)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看数据集" })).toHaveAttribute(
      "href",
      `/datasets/${task.dataset_id}`,
    );
    expect(screen.queryByLabelText("当前项目")).not.toBeInTheDocument();
    expect(screen.queryByText(/SAVED/u)).not.toBeInTheDocument();
  });

  it("uses the API quality status for a low pass rate", async () => {
    const gateway = makeGateway();
    gateway.progress.mockResolvedValueOnce({
      ...progress,
      qc: {
        evaluated_count: 10,
        pass_count: 2,
        risk_count: 3,
        reject_count: 5,
        pending_count: 0,
        pass_rate: { numerator: 2, denominator: 10, value: 0.2 },
      },
      attainment: {
        ...progress.attainment,
        quality_status: "NOT_MET",
      },
    });
    renderPage(gateway);

    expect(await screen.findByText("通过率 20%")).toBeVisible();
    expect(screen.getByText("未达到阈值")).toBeVisible();
    expect(screen.queryByText("达到阈值")).not.toBeInTheDocument();
  });

  it("keeps window filters reproducible in the URL and resets the cursor", async () => {
    const user = userEvent.setup();
    renderPage(makeGateway(), "manage", "/collection-tasks?cursor=window-2");

    await screen.findByText(task.name);
    await user.type(
      screen.getByRole("textbox", {
        name: "当前窗口搜索任务名称或编号",
      }),
      "透明",
    );
    await waitFor(() => {
      const location = screen.getByTestId("location").textContent ?? "";
      expect(location).toContain("q=%E9%80%8F%E6%98%8E");
      expect(location).not.toContain("cursor=");
    });

    await user.type(
      screen.getByRole("textbox", { name: "当前窗口筛选采集类型" }),
      "抓取",
    );
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "type=%E6%8A%93%E5%8F%96",
      ),
    );

    const statusFilter = screen.getByRole("combobox", { name: "任务状态" });
    fireEvent.mouseDown(statusFilter);
    await user.click(
      await screen.findByText("已关闭", {
        selector: ".ant-select-item-option-content",
      }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent("status=CLOSED"),
    );

    await user.click(screen.getByRole("button", { name: "重置" }));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/collection-tasks",
      ),
    );
    expect(screen.getByTestId("location").textContent).not.toContain("?");
  });

  it("does not navigate when task lifecycle or edit actions are clicked", async () => {
    const user = userEvent.setup();
    renderPage(makeGateway());

    await screen.findByText(task.name);
    await chooseTaskAction(user, "编辑任务");
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/collection-tasks",
    );
    await user.click(screen.getByRole("button", { name: "关闭任务抽屉" }));

    expect(
      screen.queryByRole("menuitem", { name: "关闭任务" }),
    ).not.toBeInTheDocument();
    await chooseTaskAction(user, "关闭任务");
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/collection-tasks",
    );
    await user.click(screen.getByRole("button", { name: /^取\s*消$/u }));

    await chooseTaskAction(user, "取消任务");
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/collection-tasks",
    );
  });

  it("opens and dismisses the more-actions menu from the keyboard", async () => {
    const user = userEvent.setup();
    renderPage(makeGateway());

    await screen.findByText(task.name);
    const moreActions = screen.getByRole("button", { name: "更多操作" });
    moreActions.focus();
    await user.keyboard("{Enter}");
    expect(
      await screen.findByRole("menuitem", { name: "关闭任务" }),
    ).toBeVisible();

    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(
        screen.queryByRole("menuitem", { name: "关闭任务" }),
      ).not.toBeInTheDocument(),
    );

    moreActions.focus();
    await user.keyboard(" ");
    expect(
      await screen.findByRole("menuitem", { name: "取消任务" }),
    ).toBeVisible();
    await user.click(document.body);
    await waitFor(() =>
      expect(
        screen.queryByRole("menuitem", { name: "取消任务" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("creates once from the confirmed schema and renders no disabled fields", async () => {
    const user = userEvent.setup();
    const gateway = makeGateway();
    let resolveCreate: ((value: CollectionTask) => void) | undefined;
    gateway.create.mockImplementationOnce(
      () =>
        new Promise<CollectionTask>((resolve) => {
          resolveCreate = resolve;
        }),
    );
    renderPage(gateway);

    await screen.findByText(task.name);
    await user.click(screen.getByRole("button", { name: "新建采集任务" }));
    expect(screen.getByRole("dialog", { name: "新建采集任务" })).toBeVisible();
    const drawer = screen.getByRole("dialog", { name: "新建采集任务" });
    expect(within(drawer).getByLabelText("项目")).toHaveValue(scope.projectId);
    expect(within(drawer).getByLabelText("任务编号（自动生成）")).toHaveValue(
      "保存后由系统生成",
    );
    for (const forbidden of [
      "分派",
      "人员",
      "PICO",
      "机器人",
      "开始时间",
      "结束时间",
      "暂停",
      "继续",
      "模态",
      "Topic",
    ]) {
      expect(
        screen.queryByText(new RegExp(forbidden, "iu")),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByLabelText(new RegExp(forbidden, "iu")),
      ).not.toBeInTheDocument();
    }

    const datasetSelector = within(drawer).getByLabelText("关联数据集");
    await user.click(datasetSelector);
    await user.type(datasetSelector, "dataset_shared_night");
    await user.click(
      await screen.findByText("夜班采集 · dataset_shared_night"),
    );

    await fillMinimumCreateForm(user);
    await user.dblClick(screen.getByRole("button", { name: "创建任务" }));
    await waitFor(() => expect(gateway.create).toHaveBeenCalledTimes(1));
    expect(gateway.create).toHaveBeenCalledWith(
      scope,
      {
        name: "夜班工位采集",
        dataset_id: "dataset_shared_night",
        type: "抓取采集",
        scenario: "夜间灯光条件",
        description: "",
        target: null,
        quality_threshold: null,
      },
      expect.stringMatching(/^create-/u),
    );

    await act(async () => {
      resolveCreate?.({ ...task, name: "夜班工位采集" });
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "新建采集任务" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("edits with the current ETag and closes with a fresh detail precondition", async () => {
    const user = userEvent.setup();
    const gateway = makeGateway();
    renderPage(gateway);

    await screen.findByText(task.name);
    await chooseTaskAction(user, "编辑任务");
    const editDrawer = screen.getByRole("dialog", { name: "编辑采集任务" });
    expect(within(editDrawer).getByText("任务描述")).toBeVisible();
    const description = editDrawer.querySelector<HTMLTextAreaElement>(
      'textarea[name="description"]',
    );
    expect(description).not.toBeNull();
    if (!description) throw new Error("任务描述字段未渲染。");
    await user.clear(description);
    await user.type(description, "更新后的长线采集说明");
    await user.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(gateway.update).toHaveBeenCalledTimes(1));
    expect(gateway.update).toHaveBeenCalledWith(
      scope,
      task.collection_task_id,
      expect.objectContaining({ description: "更新后的长线采集说明" }),
      '"v1"',
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "编辑采集任务" }),
      ).not.toBeInTheDocument(),
    );

    await chooseTaskAction(user, "编辑任务");
    await screen.findByRole("dialog", { name: "编辑采集任务" });
    await waitFor(() => expect(gateway.detail).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole("button", { name: "关闭任务抽屉" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "编辑采集任务" }),
      ).not.toBeInTheDocument(),
    );

    await chooseTaskAction(user, "关闭任务");
    const closeDialog = screen
      .getByText("确认关闭任务？")
      .closest('[role="dialog"]');
    expect(closeDialog).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认关闭任务" }));
    await waitFor(() => expect(gateway.close).toHaveBeenCalledTimes(1));
    expect(gateway.close).toHaveBeenCalledWith(
      scope,
      task.collection_task_id,
      '"v1"',
      expect.stringMatching(/^close-/u),
    );
    expect(gateway.detail).toHaveBeenCalledTimes(3);
    expect(await screen.findByText("采集任务已关闭")).toBeVisible();
  });

  it("requires explicit confirmation to cancel and reopen without deriving status from progress", async () => {
    const user = userEvent.setup();
    const cancelGateway = makeGateway();
    renderPage(cancelGateway);

    await screen.findByText(task.name);
    expect(screen.getByText("目标进行中")).toBeVisible();
    await chooseTaskAction(user, "取消任务");
    expect(
      screen.getByText("确认取消任务？").closest('[role="dialog"]'),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认取消任务" }));
    await waitFor(() => expect(cancelGateway.cancel).toHaveBeenCalledTimes(1));
    expect(cancelGateway.cancel).toHaveBeenCalledWith(
      scope,
      task.collection_task_id,
      '"v1"',
      expect.stringMatching(/^cancel-/u),
    );
    expect(await screen.findByText("采集任务已取消")).toBeVisible();

    const cancelledTask: CollectionTask = { ...task, status: "CANCELLED" };
    const reopenGateway = makeGateway();
    reopenGateway.list.mockResolvedValueOnce({
      items: [cancelledTask],
      next_cursor: null,
    });
    reopenGateway.detail.mockResolvedValueOnce({
      task: cancelledTask,
      etag: '"v2"',
    });
    cleanup();
    renderPage(reopenGateway);

    await screen.findByText("已取消");
    await chooseTaskAction(user, "重新开启");
    expect(
      screen.getByText("重新开启采集任务").closest('[role="dialog"]'),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认重新开启" }));
    await waitFor(() => expect(reopenGateway.reopen).toHaveBeenCalledTimes(1));
    expect(reopenGateway.reopen).toHaveBeenCalledWith(
      scope,
      task.collection_task_id,
      '"v2"',
      expect.stringMatching(/^reopen-/u),
    );
  });

  it.each<{
    code: DomainErrorCode;
    status: number;
    title: string;
    fieldMessage?: string;
  }>([
    {
      code: "VERSION_CONFLICT",
      status: 409,
      title: "服务器事实已变化",
    },
    {
      code: "VALIDATION_ERROR",
      status: 422,
      title: "请检查任务字段",
      fieldMessage: "任务名称与现有任务冲突。",
    },
    {
      code: "RATE_LIMITED",
      status: 429,
      title: "请求频率受限",
    },
  ])(
    "keeps form values for $status responses",
    async ({ code, fieldMessage, status, title }) => {
      const user = userEvent.setup();
      const gateway = makeGateway();
      gateway.create.mockRejectedValueOnce(
        createDomainError({
          code,
          problemCode: `TEST_${status}`,
          message: `服务端返回 ${status}。请按提示处理。`,
          fieldErrors: fieldMessage
            ? [{ path: "/name", code: "INVALID", message: fieldMessage }]
            : [],
          operationErrors: [],
          blockedReasons: [],
          requestId: `request-${status}`,
          retryable: status === 429,
          httpStatus: status,
        }),
      );
      renderPage(gateway);

      await screen.findByText(task.name);
      await user.click(screen.getByRole("button", { name: "新建采集任务" }));
      await fillMinimumCreateForm(user);
      await user.click(screen.getByRole("button", { name: "创建任务" }));

      expect(await screen.findByText(title)).toBeVisible();
      expect(screen.getByText(`请求 ID：request-${status}`)).toBeVisible();
      expect(screen.getByText(`问题代码：TEST_${status}`)).toBeVisible();
      expect(
        screen.getByText(
          status === 429
            ? "服务端允许重试。"
            : "请先核对字段、作用域或资源版本。",
        ),
      ).toBeVisible();
      const drawer = screen.getByRole("dialog", { name: "新建采集任务" });
      expect(within(drawer).getByLabelText("任务名称")).toHaveValue(
        "夜班工位采集",
      );
      if (fieldMessage) expect(screen.getByText(fieldMessage)).toBeVisible();
      if (status === 429) {
        const firstKey = gateway.create.mock.calls[0]?.[2];
        gateway.create.mockResolvedValueOnce({
          ...task,
          name: "夜班工位采集",
        });
        await user.click(
          within(drawer).getByRole("button", { name: "创建任务" }),
        );
        await waitFor(() => expect(gateway.create).toHaveBeenCalledTimes(2));
        expect(gateway.create.mock.calls[1]?.[2]).toBe(firstKey);
      }
    },
  );

  it("keeps list and progress Problem Details visible instead of collapsing them", async () => {
    const user = userEvent.setup();
    const listGateway = makeGateway();
    listGateway.list.mockRejectedValueOnce(
      createDomainError({
        code: "FORBIDDEN",
        problemCode: "PROJECT_SCOPE_DENIED",
        message: "当前身份不能读取此项目的采集任务。",
        fieldErrors: [],
        operationErrors: [],
        blockedReasons: [],
        requestId: "request-list-forbidden",
        retryable: false,
        httpStatus: 403,
      }),
    );
    const first = renderPage(listGateway);

    expect(await screen.findByText("无权访问")).toBeVisible();
    expect(screen.getByText("问题代码：PROJECT_SCOPE_DENIED")).toBeVisible();
    expect(screen.getByText("request-list-forbidden")).toBeVisible();
    expect(screen.getByText("请先核对作用域或资源状态。")).toBeVisible();
    first.unmount();

    const progressGateway = makeGateway();
    progressGateway.progress.mockRejectedValueOnce(
      createDomainError({
        code: "RATE_LIMITED",
        problemCode: "P20_PROGRESS_RATE_LIMITED",
        message: "任务进度请求频率受限。",
        fieldErrors: [],
        operationErrors: [],
        blockedReasons: [],
        requestId: "request-progress-429",
        retryable: true,
        httpStatus: 429,
      }),
    );
    renderPage(progressGateway);

    expect(
      await screen.findByLabelText(
        /P20_PROGRESS_RATE_LIMITED.*request-progress-429.*服务端允许重试/u,
      ),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() =>
      expect(progressGateway.progress).toHaveBeenCalledTimes(2),
    );
    expect(await screen.findByText("8 包 / 20 包")).toBeVisible();
  });

  it("keeps long task content inspectable and honors read-only capability", async () => {
    const gateway = makeGateway();
    const longName = `长名称${"采".repeat(190)}`;
    gateway.list.mockResolvedValueOnce({
      items: [{ ...task, name: longName }],
      next_cursor: null,
    });
    useShellStore.getState().setAuthorization({
      scopeKey: useShellStore.getState().scopeKey,
      roleVersion: "read-only-v1",
      capabilities: ["upload.read"],
      fetchedAt: "2026-08-18T05:30:00Z",
    });
    renderPage(gateway, null);

    expect(await screen.findByText(longName)).toHaveAttribute(
      "title",
      longName,
    );
    expect(screen.getByText("只读")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "新建采集任务" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "编辑任务" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "关闭任务" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "更多操作" }),
    ).not.toBeInTheDocument();
    expect(gateway.list).toHaveBeenCalledTimes(1);
  });
});
