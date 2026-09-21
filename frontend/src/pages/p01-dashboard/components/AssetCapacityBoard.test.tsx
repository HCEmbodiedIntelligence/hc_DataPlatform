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
import { MemoryRouter } from "react-router-dom";
import { adaptDashboardTaskStatus } from "../../../features/dashboard/api/adapter";
import {
  dashboardTaskStatusAllTasksFixture,
  dashboardTaskStatusEmptyFixture,
  dashboardTaskStatusFixture,
} from "../../../mocks/fixtures/dashboard";
import { AssetCapacityBoard } from "./AssetCapacityBoard";

vi.mock("./DuplicateIssueActions", () => ({
  default: () => <div>解决这条数据的处理冲突</div>,
}));
vi.mock("./ResumeProcessingAction", () => ({
  default: ({ importId }: { importId: string }) => (
    <button>直接重试 {importId}</button>
  ),
}));

vi.mock("../../p03-upload-jobs/components/OriginalSourceBrowser", () => ({
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

  it("includes source duplicates in the problem-data total", () => {
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

    const validation = within(screen.getByText("自动质检").closest("li")!);
    expect(
      validation.getByRole("button", { name: "质检问题 2" }),
    ).toBeVisible();
    expect(
      validation.getByRole("button", { name: "重复数据 1" }),
    ).toBeVisible();
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
    expect(screen.getByText("自动质检")).toBeVisible();
    expect(screen.getByText("已入库")).toBeVisible();
    expect(screen.getByText("数据标注")).toBeVisible();
    expect(screen.getByText("人工审核")).toBeVisible();
    expect(screen.getAllByText("12 个数据包")).toHaveLength(3);
    expect(screen.getByText("8 个数据包")).toBeVisible();
    expect(screen.getByText("3 个数据包")).toBeVisible();
    expect(screen.getAllByText("1 个数据包")).toHaveLength(3);
    expect(screen.getByText("当前 · 未质检 1")).toBeVisible();
    expect(screen.getByText("等待 2 · 运行 2")).toBeVisible();
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
