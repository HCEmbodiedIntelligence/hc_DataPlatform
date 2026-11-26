// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as auth from "../../../shared/auth/use-capabilities";
import { MemoryRouter } from "react-router-dom";
import { adaptDashboardTaskStatus } from "../../../features/dashboard/api/adapter";
import {
  dashboardTaskStatusAllTasksFixture,
  dashboardTaskStatusEmptyFixture,
  dashboardTaskStatusFixture,
} from "../../../mocks/fixtures/dashboard";
import * as http from "../../../shared/api/http-client";
import TaskIssueDrawer from "./TaskIssueDrawer";
import { AssetCapacityBoard } from "./AssetCapacityBoard";

vi.mock("./DuplicateIssueActions", () => ({
  default: () => <div>解决这条数据的处理冲突</div>,
}));
vi.mock("./ResumeProcessingAction", () => ({
  default: ({ importId }: { importId: string }) => (
    <button>直接重试 {importId}</button>
  ),
}));

vi.mock("../../../features/ingest/browser/OriginalSourceBrowser", () => ({
  default: ({
    importId,
    initialEpisodeIndex,
  }: {
    importId: string;
    initialEpisodeIndex: number;
  }) => (
    <p>
      原始预览 {importId} / Episode {initialEpisodeIndex}
    </p>
  ),
}));
beforeEach(() => {
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
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("AssetCapacityBoard", () => {
  it("discards only confirmed recordings across pages and keeps failed selections for retry", async () => {
    vi.spyOn(auth, "useCapabilities").mockReturnValue({ has: () => true, loading: false, failed: false });
    const digest = "a".repeat(64);
    const post = vi.fn();
    vi.spyOn(http, "request").mockImplementation(async options => {
      if (options.method === "POST") {
        post(options);
        if (options.path.includes("/rec-1/")) throw new Error("质检报告已变更");
        return {};
      }
      return { data: {
        status: options.path.endsWith("/rec-2") ? "READY_FOR_SLICING" : "QC_FAILED",
        quality_summary: { report_id: "recording_qc_" + digest },
      } };
    });
    const scope = { organizationId: "org", projectId: "p", regionCode: "global", timezone: "Asia/Shanghai" };
    const issues = Array.from({ length: 12 }, (_, i) => ({
      task_id: "task", rollout_id: "roll-" + i, data_package_id: "pkg-" + i,
      recording_id: i === 4 ? null : "rec-" + i, category: "QUALITY" as const,
      stage: "AUTOMATIC_VALIDATION" as const, label: "质检拒绝", description: "质检问题",
      reason_code: "QC_REJECT", findings: [], lance_ready: false,
    }));
    render(<QueryClientProvider client={new QueryClient()}><MemoryRouter>
      <TaskIssueDrawer scope={scope} issues={issues} tasks={[]} title="质检问题" onClose={vi.fn()} />
    </MemoryRouter></QueryClientProvider>);
    const checkbox = (i: number) => within(screen.getByText("pkg-" + i).closest("tr")!).getByRole("checkbox");
    expect(screen.getByRole("button", { name: "批量丢弃当前录制" })).toBeDisabled();
    expect(checkbox(4)).toBeDisabled();
    fireEvent.click(checkbox(1));
    fireEvent.click(checkbox(2));
    fireEvent.click(screen.getByTitle("2"));
    fireEvent.click(checkbox(10));
    const open = screen.getByRole("button", { name: "批量丢弃当前录制（3）" });
    fireEvent.click(open);
    await waitFor(() => expect(screen.getByText(/pkg-2 · 跳过/)).toBeVisible());
    fireEvent.click(screen.getByRole("button", { name: /关\s*闭/ }));
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(open);
    const confirm = await screen.findByRole("button", { name: "确认丢弃（2）" });
    await waitFor(() => expect(confirm).toBeEnabled());
    fireEvent.click(confirm);
    expect(await screen.findByText("已丢弃 1 条录制，失败 1 条，跳过 1 条")).toBeVisible();
    expect(screen.getByText(/pkg-1 · 质检报告已变更/)).toBeVisible();
    expect(post.mock.calls.map(([o]) => o.path.split("/").slice(-2).join("/")).sort())
      .toEqual(["rec-1/quality:discard", "rec-10/quality:discard"]);
    for (const [o] of post.mock.calls) expect(o.body).toEqual({ report_sha256: digest });
    fireEvent.click(screen.getByRole("button", { name: /完\s*成/ }));
    expect(screen.getByRole("button", { name: "批量丢弃当前录制（2）" })).toBeEnabled();
  });
  it("opens processing failures with batch retry available only inside the drawer", async () => {
    vi.spyOn(auth, "useCapabilities").mockReturnValue({ has: () => true, loading: false, failed: false });
    const view = adaptDashboardTaskStatus(dashboardTaskStatusFixture);
    const taskId = view.tasks[0]!.taskId;
    const stage = "AUTOMATIC_VALIDATION" as const;
    render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><AssetCapacityBoard
      scope={{ organizationId: "org", projectId: "p", regionCode: "global", timezone: "Asia/Shanghai" }}
      onTaskChange={vi.fn()} taskStatus={{ ...view, pipeline: { ...view.pipeline,
        stages: view.pipeline.stages.map(s => s.stage === stage ? { ...s, failed: 1 } : s),
        issues: [{ task_id: taskId, rollout_id: "roll", data_package_id: "processing",
          category: "TECHNICAL", stage, label: "处理失败", description: "录制准备出错", lance_ready: false,
          findings: [], reason_code: "RAW_PREPARATION_FAILED" }],
      } }} /></MemoryRouter></QueryClientProvider>);
    const failure = screen.getByRole("button", { name: "处理失败 1" });
    expect(screen.queryByRole("button", { name: "批量重试失败项" })).not.toBeInTheDocument();
    fireEvent.click(failure);
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByRole("button", { name: "批量重试失败项" })).toBeVisible();
  });

  it("shows an all-task aggregate by default instead of a zero-value unselected rail", () => {
    render(
      <MemoryRouter>
        <AssetCapacityBoard
          taskStatus={adaptDashboardTaskStatus(
            dashboardTaskStatusAllTasksFixture,
          )}
          onTaskChange={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByLabelText("筛选采集任务")).toBeVisible();
    expect(screen.getByText("全部任务（2）")).toBeVisible();
    expect(screen.getByLabelText("全部任务汇总")).toHaveTextContent(
      "2 个任务 · 14 个数据包",
    );
    expect(screen.getAllByText("14 个数据包")).toHaveLength(3);
    expect(screen.getByText("10 个数据包")).toBeVisible();
    expect(screen.queryByText(/请选择一个采集任务/)).not.toBeInTheDocument();
    expect(screen.queryByText(/当前任务 ·/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "质检问题 3" })).toBeVisible();
    expect(screen.queryByText(/上游问题隔离/)).not.toBeInTheDocument();
  });

  it("keeps confirmed duplicates out of current problems and history summaries", () => {
    const withDuplicates = {
      ...dashboardTaskStatusAllTasksFixture,
      pipeline: {
        ...dashboardTaskStatusAllTasksFixture.pipeline,
        qc: {
          ...dashboardTaskStatusAllTasksFixture.pipeline.qc,
          risk: 1,
          rejected: 1,
          duplicate: 1,
        },
      },
    };

    render(
      <MemoryRouter>
        <AssetCapacityBoard
          taskStatus={adaptDashboardTaskStatus(withDuplicates)}
          onTaskChange={vi.fn()}
        />
      </MemoryRouter>,
    );

    const validation = within(screen.getByText("自动校验").closest("li")!);
    expect(
      validation.getByRole("button", { name: "质检问题 2" }),
    ).toBeVisible();
    expect(
      validation.queryByRole("button", { name: "重复数据 1" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/历史处理：/)).not.toBeInTheDocument();
    expect(screen.queryByText(/已去重 1 个数据包/)).not.toBeInTheDocument();
  });

  it("keeps the current task contract inside the eight-stage signal rail", () => {
    render(
      <MemoryRouter>
        <AssetCapacityBoard
          taskStatus={adaptDashboardTaskStatus(dashboardTaskStatusFixture)}
          onTaskChange={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "信号轨道" })).toBeVisible();
    expect(screen.getByText("当前任务 · 00000042")).toBeVisible();
    expect(screen.getByText("双臂装配采集")).toBeVisible();
    expect(screen.getByLabelText("状态：ACTIVE / 进行中")).toBeVisible();
    expect(screen.getByLabelText("状态：已达标")).toBeVisible();
    expect(
      screen.getByRole("list", { name: "采集到发布的固定八阶段" }),
    ).toBeVisible();
    expect(screen.getAllByRole("listitem")).toHaveLength(8);
    expect(screen.getByText("采集")).toBeVisible();
    expect(screen.getByText("登记上传")).toBeVisible();
    expect(screen.getByText("Raw 接收")).toBeVisible();
    expect(screen.getByText("质检通过")).toBeVisible();
    expect(screen.getByText("已入库")).toBeVisible();
    expect(screen.getByText("数据标注")).toBeVisible();
    expect(screen.getByText("人工审核")).toBeVisible();
    expect(screen.getAllByText("12 个数据包")).toHaveLength(3);
    expect(screen.getByText("8 个数据包")).toBeVisible();
    expect(screen.getByText("3 个数据包")).toBeVisible();
    expect(screen.getAllByText("1 个数据包")).toHaveLength(3);
    expect(screen.getByText("当前 · 待质检 1")).toBeVisible();
    expect(screen.getByText("等待 1 · 运行 2")).toBeVisible();
    expect(screen.getByRole("button", { name: "处理失败 2" })).toBeVisible();
    expect(screen.getAllByRole("button", { name: "质检问题 3" })).toHaveLength(
      1,
    );
    expect(screen.queryByText(/上游问题隔离/u)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/当前任务有 .*技术或结构阻塞/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("任务目标")).not.toBeInTheDocument();
    expect(screen.queryByText("CAPTURED 12")).not.toBeInTheDocument();
    expect(screen.queryByText("建议操作")).not.toBeInTheDocument();
  });

  it.each([
    {
      fixture: dashboardTaskStatusAllTasksFixture,
      awaitingSlice: 3,
      waiting: 0,
    },
    { fixture: dashboardTaskStatusFixture, awaitingSlice: 2, waiting: 1 },
  ])(
    "separates recordings awaiting slice submission from the quality queue ($awaitingSlice / $waiting)",
    ({ fixture, awaitingSlice, waiting }) => {
      const taskStatus = adaptDashboardTaskStatus({
        ...fixture,
        pipeline: {
          ...fixture.pipeline,
          qc: {
            ...fixture.pipeline.qc,
            awaiting_slice: awaitingSlice,
            waiting,
          },
          stages: fixture.pipeline.stages.map((stage) =>
            stage.stage === "AUTOMATIC_VALIDATION"
              ? { ...stage, not_started: awaitingSlice, waiting }
              : stage,
          ),
        },
      });
      render(
        <MemoryRouter>
          <AssetCapacityBoard taskStatus={taskStatus} onTaskChange={vi.fn()} />
        </MemoryRouter>,
      );
      const validation = screen.getByText("自动校验").closest("li")!;
      expect(validation).toHaveTextContent(`待分片 ${awaitingSlice}`);
      if (waiting) {
        expect(validation).toHaveTextContent(`待质检 ${waiting}`);
      } else {
        expect(validation).not.toHaveTextContent("待质检");
      }
      expect(screen.queryByText(/未质检/u)).not.toBeInTheDocument();
      for (const item of screen.getAllByRole("listitem")) {
        if (item !== validation) expect(item).not.toHaveTextContent("待分片");
      }
    },
  );

  it("separates approved packages from upstream packages that have not reached review", () => {
    const taskStatus = adaptDashboardTaskStatus(dashboardTaskStatusFixture);
    render(
      <MemoryRouter>
        <AssetCapacityBoard
          taskStatus={{
            ...taskStatus,
            pipeline: {
              ...taskStatus.pipeline,
              stages: taskStatus.pipeline.stages.map((stage) =>
                stage.stage === "REVIEW"
                  ? { ...stage, succeeded: 1, waiting: 0, notStarted: 2 }
                  : stage.stage === "PUBLICATION"
                    ? { ...stage, succeeded: 0, waiting: 1, notStarted: 2 }
                    : stage,
              ),
            },
          }}
          onTaskChange={vi.fn()}
        />
      </MemoryRouter>,
    );
    const review = within(screen.getByText("人工审核").closest("li")!);
    expect(review.getByText("审核通过")).toBeVisible();
    expect(review.getByText("1 个数据包")).toBeVisible();
    expect(review.queryByText(/等待/)).not.toBeInTheDocument();
    const publication = within(
      screen.getByText("发布", { exact: true }).closest("li")!,
    );
    expect(publication.getByText("等待 1")).toBeVisible();
    expect(
      screen.getByText("按数据包统计；等待只包含已进入该阶段的数据。"),
    ).toBeVisible();
  });

  it("keeps the rail visible for an empty task range", () => {
    render(
      <MemoryRouter>
        <AssetCapacityBoard
          taskStatus={adaptDashboardTaskStatus(dashboardTaskStatusEmptyFixture)}
          onTaskChange={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getAllByRole("listitem")).toHaveLength(8);
    expect(screen.getAllByText("0 个数据包")).toHaveLength(8);
    expect(screen.getByLabelText("状态：暂无数据")).toHaveAttribute(
      "data-status",
      "EMPTY",
    );
    expect(screen.queryByText(/当前任务 ·/)).not.toBeInTheDocument();
  });

  it("opens the actual problem episode and original data from its originating stage", async () => {
    const taskStatus = adaptDashboardTaskStatus(dashboardTaskStatusFixture);
    render(
      <MemoryRouter>
        <AssetCapacityBoard
          scope={{
            organizationId: "org",
            projectId: "project",
            regionCode: "local",
            timezone: "Asia/Shanghai",
          }}
          taskStatus={{
            ...taskStatus,
            pipeline: {
              ...taskStatus.pipeline,
              qc: { ...taskStatus.pipeline.qc, reprocessingConflicts: 1 },
              issues: [
                {
                  task_id: taskStatus.tasks[0]!.taskId,
                  rollout_id: "episode-10",
                  data_package_id: "episode-10",
                  category: "PROCESSING_CONFLICT",
                  stage: "STANDARDIZATION",
                  reason_code: "ALIGNMENT_ATTEMPT_IMMUTABLE",
                  label: "处理结果冲突",
                  description:
                    "本次处理结果与历史结果不一致，此数据包尚未入库。",
                  source_import_id: "original-import",
                  source_episode_index: 10,
                  alignment_attempt_id: "old-attempt",
                  qc_status: "PASS",
                  lance_ready: false,
                  findings: [],
                },
              ],
            },
          }}
          onTaskChange={vi.fn()}
        />
      </MemoryRouter>,
    );
    const standardization = within(
      screen.getByText("标准化入库").closest("li")!,
    );
    fireEvent.click(
      standardization.getByRole("button", { name: "处理冲突 1" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "查看 Episode 10 详情" }),
    );
    expect(
      await screen.findByText(
        "本次处理结果与历史结果不一致，此数据包尚未入库。",
      ),
    ).toBeVisible();
    expect(screen.getByText("未入库")).toBeVisible();
    expect(screen.getByText("old-attempt")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "查看原始数据与视频" }));
    expect(
      await screen.findByText("原始预览 original-import / Episode 10"),
    ).toBeVisible();
  });

  it("shows stopped episodes separately from conflicts and opens the retry explanation", async () => {
    const taskStatus = adaptDashboardTaskStatus(dashboardTaskStatusFixture);
    const taskId = taskStatus.tasks[0]!.taskId;
    render(
      <MemoryRouter>
        <AssetCapacityBoard
          scope={{
            organizationId: "org",
            projectId: "project",
            regionCode: "region",
            timezone: "Asia/Shanghai",
          }}
          taskStatus={{
            ...taskStatus,
            pipeline: {
              ...taskStatus.pipeline,
              qc: {
                ...taskStatus.pipeline.qc,
                duplicate: 0,
                reprocessingConflicts: 1,
              },
              issues: Array.from({ length: 21 }, (_, index) => ({
                task_id: taskId,
                rollout_id: `stopped-${index}`,
                data_package_id: `stopped-${index}`,
                category: "RESUME_REQUIRED" as const,
                stage: "STANDARDIZATION" as const,
                reason_code: "PROCESSING_RESUME_REQUIRED",
                label: "质检已通过，待继续处理",
                description:
                  "质检结果更新不会自动恢复已停止的任务，请重试未完成处理。",
                source_episode_index: index,
                source_import_id: "resume-import",
                qc_status: "PASS",
                lance_ready: false,
                findings: [],
              })),
            },
          }}
          onTaskChange={vi.fn()}
        />
      </MemoryRouter>,
    );
    const stage = within(screen.getByText("标准化入库").closest("li")!);
    expect(stage.getByRole("button", { name: "处理冲突 1" })).toBeVisible();
    expect(
      stage.queryByRole("button", { name: /重复数据/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(stage.getByRole("button", { name: "待继续处理 21" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "查看 Episode 0 详情" }),
    );
    expect(screen.getByText("通过")).toBeVisible();
    expect(screen.getByText("未入库")).toBeVisible();
    expect(
      await screen.findByRole("button", { name: "直接重试 resume-import" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("link", { name: /重试/ }),
    ).not.toBeInTheDocument();
  });

  it("shows measured risk values and the affected camera before opening episode zero", async () => {
    const taskStatus = adaptDashboardTaskStatus(dashboardTaskStatusFixture);
    render(
      <MemoryRouter>
        <AssetCapacityBoard
          scope={{
            organizationId: "org",
            projectId: "project",
            regionCode: "local",
            timezone: "Asia/Shanghai",
          }}
          taskStatus={{
            ...taskStatus,
            pipeline: {
              ...taskStatus.pipeline,
              issues: [
                {
                  task_id: taskStatus.tasks[0]!.taskId,
                  rollout_id: "episode-zero",
                  data_package_id: "episode-zero",
                  category: "QUALITY",
                  stage: "AUTOMATIC_VALIDATION",
                  reason_code: "QC_RISK",
                  label: "质量风险",
                  description: "黑帧比例超标。",
                  source_import_id: "risk-import",
                  source_episode_index: 0,
                  qc_status: "RISK",
                  lance_ready: false,
                  findings: [
                    {
                      code: "QC_IMAGE_BLACK",
                      severity: "warning",
                      message: "black frames",
                      topic: "/camera/wrist_right/image",
                      observed: 0.03,
                      threshold: 0.02,
                    },
                  ],
                },
              ],
            },
          }}
          onTaskChange={vi.fn()}
        />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "质检问题 3" }));
    expect(await screen.findByText("黑帧比例超标")).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", { name: "查看 Episode 0 详情" }),
    );
    expect(await screen.findByText("3.00%")).toBeVisible();
    expect(screen.getByText("2.00%")).toBeVisible();
    expect(screen.getByText("/camera/wrist_right/image")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "查看原始数据与视频" }));
    await waitFor(() =>
      expect(
        screen.getByText("原始预览 risk-import / Episode 0"),
      ).toBeVisible(),
    );
  });
});
