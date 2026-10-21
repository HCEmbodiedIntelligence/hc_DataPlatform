// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { RecordingProcessingDetails } from "./RecordingProcessingDetails";

const { requestMock, taskMock } = vi.hoisted(() => ({
  requestMock: vi.fn(),
  taskMock: vi.fn(),
}));
vi.mock("../../shared/api/http-client", () => ({ request: requestMock }));
vi.mock("../collection-tasks/api", () => ({
  collectionTaskGateway: { detail: taskMock },
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

function show(sourceFails = false, discarded = false) {
  requestMock.mockImplementation(async ({ path }: { path: string }) => {
    if (path.endsWith("/episodes"))
      return {
        items: [
          {
            episode_id: "episode_0001",
            start_offset_ns: "700000000",
            end_offset_ns: "1700000000",
            status: discarded ? "DISCARDED" : "QC_FAILED",
            failure_code: discarded ? "ALIGNMENT_FAILED" : "QC_REJECTED",
            dispatch_error_code: discarded
              ? "INGEST_WORKFLOW_PLAN_BLOCKED"
              : null,
          },
        ],
        qc_reports: [
          {
            episode_id: "episode_0001",
            status: "REJECT",
            rules: [],
            finding_codes: ["OPENARM_CAPTURE_VECTOR_INVALID"],
            quality_report: { findings: [] },
          },
        ],
      };
    if (sourceFails) throw new Error("source unavailable");
    return {
      data: {
        collection_task_id: "task-3",
        device_id: "openarmx_01",
        capture_started_at: "2026-10-10T06:45:35Z",
      },
      current_slice_revision: null,
    };
  });
  taskMock.mockResolvedValue({
    task: { task_code: "00000003", name: "主相机时间轴联调" },
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <RecordingProcessingDetails
          scope={{
            organizationId: "01",
            projectId: "01",
            regionCode: "global",
          }}
          recordingId="recording-original"
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("identifies the task, original recording and slice next to the rejection reasons", async () => {
  show();
  expect(await screen.findByText("00000003 · 主相机时间轴联调")).toBeVisible();
  expect(screen.getByText(/episode_0001 · 0.700～1.700 秒/)).toBeVisible();
  expect(screen.getByText("recording-original")).toBeVisible();
  expect(
    screen.getByRole("link", { name: "查看原始录制与文件" }),
  ).toHaveAttribute("href", "/recordings/recording-original/slice");
  expect(screen.getByText(/关节或动作数据包含缺失值/)).toBeVisible();
  expect(screen.queryByText("QC_REJECTED")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("逐规则结果统计")).not.toBeInTheDocument();
});
it("retains the recording ID and failure reasons when source lookup fails", async () => {
  show(true);
  expect(
    await screen.findByText(/录制来源读取失败，仍可通过录制 ID/),
  ).toBeVisible();
  expect(screen.getByText("recording-original")).toBeVisible();
  expect(screen.getByText(/关节或动作数据包含缺失值/)).toBeVisible();
});
it("keeps deletion history without showing earlier processing and QC failures", async () => {
  show(false, true);
  expect(await screen.findByText("已删除")).toBeVisible();
  expect(screen.getByText("recording-original")).toBeVisible();
  expect(
    screen.queryByText(/关节或动作数据包含缺失值/),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("ALIGNMENT_FAILED")).not.toBeInTheDocument();
  expect(screen.queryByText("处理尚未启动")).not.toBeInTheDocument();
  expect(screen.queryByText("等待处理配置")).not.toBeInTheDocument();
});
