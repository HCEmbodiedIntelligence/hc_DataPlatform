// @vitest-environment jsdom

import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProviderHarness } from "../../app/providers";
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
  project_id: scope.projectId,
  task_code: "00000001",
  name: "透明件抓取采集",
  type: "抓取采集",
  scenario: "透明工件工位",
  description: "覆盖反光与遮挡条件。",
  target: { package_count: 20 },
  quality_threshold: 0.9,
  status: "ACTIVE",
};

const progress: CollectionTaskProgress = {
  schema_version: "1",
  collection_task_id: task.collection_task_id,
  project_id: scope.projectId,
  status: "ACTIVE",
  as_of: "2026-08-18T05:30:00Z",
  received_package_count: 8,
  qc: {
    evaluated_count: 7,
    pass_count: 6,
    risk_count: 1,
    reject_count: 0,
    pending_count: 1,
    pass_rate: { numerator: 6, denominator: 7, value: 6 / 7 },
  },
  observed_sources: {
    device_ids: [],
    camera_ids: [],
    topic_names: [],
  },
};

function makeGateway() {
  return {
    list: vi.fn(async () => ({ items: [task], next_cursor: null })),
    progress: vi.fn(async () => progress),
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
  } satisfies CollectionTaskGateway;
}

function renderPage(
  gateway: CollectionTaskGateway,
  capabilityOverride: "manage" | "read-only" | null = "manage",
) {
  return render(
    <ProviderHarness>
      <MemoryRouter initialEntries={["/collection-tasks"]}>
        <CollectionTaskPage
          {...(capabilityOverride === null ? {} : { capabilityOverride })}
          gateway={gateway}
        />
      </MemoryRouter>
    </ProviderHarness>,
  );
}

async function fillMinimumCreateForm(user: ReturnType<typeof userEvent.setup>) {
  const drawer = screen.getByRole("dialog", { name: "新建采集任务" });
  await user.type(within(drawer).getByLabelText("任务名称"), "夜班工位采集");
  await user.type(within(drawer).getByLabelText("采集类型"), "抓取采集");
  await user.type(within(drawer).getByLabelText("采集场景"), "夜间灯光条件");
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

    await fillMinimumCreateForm(user);
    await user.dblClick(screen.getByRole("button", { name: "创建任务" }));
    await waitFor(() => expect(gateway.create).toHaveBeenCalledTimes(1));
    expect(gateway.create).toHaveBeenCalledWith(
      scope,
      {
        name: "夜班工位采集",
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
    await user.click(screen.getByRole("button", { name: "编辑任务" }));
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

    await user.click(screen.getByRole("button", { name: "编辑任务" }));
    await screen.findByRole("dialog", { name: "编辑采集任务" });
    await waitFor(() => expect(gateway.detail).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole("button", { name: "关闭任务抽屉" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "编辑采集任务" }),
      ).not.toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: "关闭任务" }));
    const closeDialog = screen
      .getByText("关闭采集任务")
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
    expect(gateway.list).toHaveBeenCalledTimes(1);
  });
});
