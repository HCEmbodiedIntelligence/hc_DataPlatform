// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { createDomainError } from "../../shared/api/domain-error";
import type { FormalUploadDetail } from "./formal-detail-client";
import FormalUploadDetailPage from "./formal-page";

const { loadDetailMock, loadRawMediaMock, loadWorkflowMock } = vi.hoisted(
  () => ({
    loadDetailMock: vi.fn(),
    loadRawMediaMock: vi.fn(),
    loadWorkflowMock: vi.fn(),
  }),
);

vi.mock("../../features/ingest/use-ingest-scope", () => ({
  useIngestScope: () => ({
    organizationId: "org-a",
    projectId: "project-a",
    regionCode: "cn-test",
  }),
}));

vi.mock("../../shared/auth/use-capabilities", () => ({
  useCapabilities: () => ({
    has: (capability: string) => capability === "upload.read",
    loading: false,
    failed: false,
  }),
}));

vi.mock("./formal-detail-client", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("./formal-detail-client")>();
  return {
    ...actual,
    loadFormalUploadDetail: loadDetailMock,
    getFormalUploadRawMedia: loadRawMediaMock,
    getFormalUploadProcessingStatus: loadWorkflowMock,
  };
});

vi.mock("../../features/viewer", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../features/viewer")>();
  return {
    ...actual,
    RawDiagnosticWorkbench: (props: {
      readonly manifest: { readonly cameras: readonly unknown[] };
      readonly findings: readonly unknown[];
      readonly mediaStreamsByTopic: Readonly<
        Record<
          string,
          { readonly availability?: string; readonly mediaSource?: unknown }
        >
      >;
    }) => (
      <section
        aria-label="Raw 诊断测试适配器"
        data-camera-count={props.manifest.cameras.length}
        data-finding-count={props.findings.length}
        data-stream-count={Object.keys(props.mediaStreamsByTopic).length}
        data-ready-count={
          Object.values(props.mediaStreamsByTopic).filter(
            (stream) =>
              stream.availability === "ready" && Boolean(stream.mediaSource),
          ).length
        }
      >
        Raw 诊断正式入口
      </section>
    ),
  };
});

function detailFixture(
  status: "PASS" | "RISK" | "REJECT",
  cameraCount: number,
): FormalUploadDetail {
  const cameras = Array.from({ length: cameraCount }, (_, index) => ({
    camera_id: `camera-${index + 1}`,
    topic: `/sensors/camera_${index + 1}/image`,
    encoding: "h264",
    frame_id: `camera_${index + 1}_optical`,
  }));
  const topics = cameras.map((camera) => ({
    name: camera.topic,
    required: true,
    schema_name: "sensor_msgs/Image",
    message_encoding: "cdr",
  }));
  return {
    session: {
      session_id: "session-a",
      project_id: "project-a",
      region_code: "cn-test",
      rollout_id: "rollout-a",
      data_package_id: "package-a",
      manifest_fingerprint: "fingerprint-a",
      expected_crc64: "1",
      expected_sha256: "source-a",
      expected_size: 2_048,
      object_key: "raw/package-a.mcap",
      source_type: "BROWSER_MULTIPART",
      status: "RAW_COMMITTED",
      workflow: {
        event_id: "a1111111-1111-4111-8111-111111111111",
        workflow_id: "ingest-rollout/project-a/rollout-a",
        status: "DISPATCHED",
        attempts: 1,
        updated_at: "2026-08-21T08:00:00Z",
      },
    },
    manifest: {
      schema_version: "manifest-preflight/v1",
      manifest_fingerprint: "fingerprint-a",
      identifiers: {
        collection_session_id: "collection-a",
        data_package_id: "package-a",
        recording_request_id: "recording-a",
        robot_id: "robot-a",
      },
      time_range: {
        start_time: "2026-08-18T01:00:00Z",
        end_time: "2026-08-18T01:00:10Z",
      },
      files: [],
      total_file_size: 2_048,
      discovery: {
        source: "MANIFEST",
        read_only: true,
        cameras,
        topics,
        missing_expected_topics: [],
      },
      manifest: {
        schema_version: 1,
        project_id: "project-a",
        task_id: "task-a",
        collection_job_id: "job-a",
        rollout_id: "rollout-a",
        collection_session_id: "collection-a",
        recording_request_id: "recording-a",
        data_package_id: "package-a",
        sequence_no: 1,
        robot_id: "robot-a",
        start_time: "2026-08-18T01:00:00Z",
        end_time: "2026-08-18T01:00:10Z",
        cameras,
        topics,
        expected_topics: topics.map((topic) => topic.name),
        actual_topics: topics.map((topic) => topic.name),
        files: [],
        file_size: 2_048,
        sha256: "source-a",
        crc64: "1",
        compression: "none",
        recorder_version: "recorder/1",
      },
    },
    quality: {
      schema_version: "qc-report/v1",
      rollout_id: "rollout-a",
      status,
      start_ns: 0,
      end_ns: 10_000_000_000,
      duration_ns: 10_000_000_000,
      findings:
        status === "PASS"
          ? []
          : [
              {
                code: "QC_FREQUENCY_LOW",
                topic: topics[0]?.name ?? "/joint_states",
                severity: status === "RISK" ? "warning" : "error",
                start_ns: 1_000_000_000,
                end_ns: 2_000_000_000,
                message: "采样频率低于自动质检阈值。",
                observed: 20,
                threshold: 28.5,
              },
            ],
      topic_metrics: [],
      content_sha256: "content-a",
      engine_version: "qc/1",
      profile_id: "profile-a",
      profile_sha256: "profile-sha-a",
      profile_version: 1,
      source_sha256: "source-a",
    },
  };
}

function workflowFixture(
  status: "PENDING" | "RUNNING" | "SUCCEEDED" | "TECHNICAL_FAILED",
) {
  return {
    schema_version: "upload-processing-status/v1",
    session_id: "session-a",
    rollout_id: "rollout-a",
    workflow_id: "ingest-rollout/project-a/rollout-a",
    status,
    stage: status === "SUCCEEDED" ? "succeeded" : "alignment",
    attempt: 1,
    aligned_media:
      status === "SUCCEEDED"
        ? {
            schema_version: "upload-aligned-media-target/v1",
            project_id: "project-a",
            dataset_id: "dataset_ingest_a",
            rollout_id: "rollout-a",
            dataset_version: 4,
            annotation_task_id: "annotation-a",
            fps: 30,
            start_step: 0,
            end_step: 300,
          }
        : null,
    viewer:
      status === "SUCCEEDED"
        ? {
            schema_version: "dataset-ingest-viewer-target/v1",
            dataset_id: "dataset_ingest_a",
            version_id: "version_lance_4",
            episode_id: "episode_ingest_a",
            revision_id: "revision_ingest_a",
          }
        : null,
    error_code: status === "TECHNICAL_FAILED" ? "ALIGNMENT_FAILED" : null,
    updated_at: "2026-08-21T08:01:00Z",
  };
}

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
      {location.hash}
    </output>
  );
}

function renderPage(path = "/ingest/uploads/session-a?from=pending#quality") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <LocationProbe />
        <Routes>
          <Route
            path="/ingest/uploads/:uploadId"
            element={<FormalUploadDetailPage />}
          />
          <Route
            path="/manual/issues/raw-diagnostic/:uploadId"
            element={<FormalUploadDetailPage />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  loadDetailMock.mockResolvedValue(detailFixture("PASS", 4));
  loadWorkflowMock.mockResolvedValue(workflowFixture("SUCCEEDED"));
  loadRawMediaMock.mockResolvedValue({
    schema_version: "raw-media-source/v1",
    format: "MCAP",
    media_type: "application/x-mcap",
    download_url: "https://object.example.test/raw/signed",
    expires_at: "2026-08-20T10:00:00Z",
    byte_length: 2_048,
    sha256: "source-a",
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("P04 formal upload detail page", () => {
  it("keeps a direct deep link/query/hash and exposes the authorized Raw source on PASS", async () => {
    renderPage();

    expect(
      await screen.findByRole("heading", { name: "上传详情" }),
    ).toBeVisible();
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/ingest/uploads/session-a?from=pending#quality",
    );
    expect(screen.getByText("自动质检通过")).toBeVisible();
    expect(screen.getByText("未发现需要诊断的异常")).toBeVisible();
    expect(screen.getByText("Raw 诊断正式入口")).toBeVisible();
    await waitFor(() =>
      expect(screen.getByLabelText("Raw 诊断测试适配器")).toHaveAttribute(
        "data-ready-count",
        "4",
      ),
    );
    expect(screen.getByText(/Canonical MP4 与 Lance 引用已提交/u)).toBeVisible();
    expect(
      screen.getByRole("link", { name: "打开完整数据视图" }),
    ).toHaveAttribute(
      "href",
      "/datasets/dataset_ingest_a/versions/version_lance_4/episodes/episode_ingest_a/view",
    );
    expect(
      await screen.findByRole("link", { name: "下载 Raw MCAP" }),
    ).toHaveAttribute("href", "https://object.example.test/raw/signed");
    expect(loadRawMediaMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: "project-a",
        regionCode: "cn-test",
      }),
      "session-a",
      expect.any(AbortSignal),
    );
    expect(loadDetailMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: "project-a",
        regionCode: "cn-test",
      }),
      "session-a",
      expect.any(AbortSignal),
    );
    expect(loadWorkflowMock).toHaveBeenCalledWith(
      expect.objectContaining({
        organizationId: "org-a",
        projectId: "project-a",
        regionCode: "cn-test",
      }),
      expect.objectContaining({ rollout_id: "rollout-a" }),
      expect.any(AbortSignal),
    );
  });

  it("keeps upload facts visible while the quality report is still being generated", async () => {
    loadDetailMock.mockResolvedValue({
      ...detailFixture("PASS", 1),
      quality: null,
    });
    loadWorkflowMock.mockResolvedValue(workflowFixture("RUNNING"));
    renderPage();

    expect(
      await screen.findByRole("heading", { name: "上传详情" }),
    ).toBeVisible();
    expect(screen.getByText("自动质检处理中")).toBeVisible();
    expect(screen.getByText("自动质检报告尚未生成")).toBeVisible();
    expect(await screen.findByText(/后台处理中 · alignment/u)).toBeVisible();
    expect(screen.getByText("数据清单发现")).toBeVisible();
    expect(screen.getByText("camera-1")).toBeVisible();
    expect(
      await screen.findByRole("link", { name: "下载 Raw MCAP" }),
    ).toBeVisible();
    expect(screen.queryByText("资源不存在")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "刷新质检状态" }));
    await waitFor(() => expect(loadDetailMock).toHaveBeenCalledTimes(2));
  });

  it.each([0, 1, 4, 8])(
    "enters read-only Raw diagnostics with %i Manifest cameras for a real RISK result",
    async (cameraCount) => {
      loadDetailMock.mockResolvedValue(detailFixture("RISK", cameraCount));
      renderPage();

      const workbench = await screen.findByLabelText("Raw 诊断测试适配器");
      expect(workbench).toHaveAttribute(
        "data-camera-count",
        String(cameraCount),
      );
      expect(workbench).toHaveAttribute(
        "data-stream-count",
        String(cameraCount),
      );
      expect(workbench).toHaveAttribute("data-finding-count", "1");
      expect(screen.getByText("自动质检：RISK")).toBeVisible();
      expect(
        screen.getByRole("link", { name: "返回问题数据" }),
      ).toHaveAttribute("href", "/manual/issues");
      expect(
        await screen.findByRole("link", { name: "下载 Raw MCAP" }),
      ).toHaveAttribute("href", "https://object.example.test/raw/signed");
      expect(loadRawMediaMock).toHaveBeenCalledWith(
        expect.objectContaining({
          projectId: "project-a",
          regionCode: "cn-test",
        }),
        "session-a",
        expect.any(AbortSignal),
      );
    },
  );

  it("returns a problem-data diagnostic to the exact filtered list", async () => {
    loadDetailMock.mockResolvedValue(detailFixture("REJECT", 3));
    renderPage(
      "/manual/issues/raw-diagnostic/session-a?returnTo=%2Fmanual%2Fissues%3Fsource%3DAUTO_QC%26severity%3DCRITICAL",
    );

    expect(
      await screen.findByRole("link", { name: "返回问题数据" }),
    ).toHaveAttribute(
      "href",
      "/manual/issues?source=AUTO_QC&severity=CRITICAL",
    );
  });

  it("keeps cameras in a generating state while the durable workflow is running", async () => {
    loadDetailMock.mockResolvedValue(detailFixture("PASS", 1));
    loadWorkflowMock.mockResolvedValue(workflowFixture("RUNNING"));
    renderPage();

    expect(await screen.findByText(/后台处理中 · alignment/u)).toBeVisible();
    const workbench = screen.getByLabelText("Raw 诊断测试适配器");
    expect(workbench).toHaveAttribute("data-ready-count", "0");
    expect(screen.queryByText(/不支持/u)).not.toBeInTheDocument();
  });

  it("shows a durable processing failure without hiding Raw evidence", async () => {
    loadDetailMock.mockResolvedValue(detailFixture("PASS", 1));
    loadWorkflowMock.mockResolvedValue(workflowFixture("TECHNICAL_FAILED"));
    renderPage();

    expect(await screen.findByText(/TECHNICAL_FAILED/u)).toBeVisible();
    expect(screen.getByText(/问题代码 ALIGNMENT_FAILED/u)).toBeVisible();
    expect(screen.getByLabelText("Raw 诊断测试适配器")).toHaveAttribute(
      "data-ready-count",
      "0",
    );
    expect(
      await screen.findByRole("link", { name: "下载 Raw MCAP" }),
    ).toBeVisible();
  });

  it("keeps Raw diagnostics visible when the authorized source fails and retries only that source", async () => {
    loadDetailMock.mockResolvedValue(detailFixture("RISK", 1));
    loadRawMediaMock.mockRejectedValue(
      createDomainError({
        code: "NETWORK_ERROR",
        message: "对象存储暂时不可用",
        fieldErrors: [],
        operationErrors: [],
        blockedReasons: [],
        requestId: "raw-source-request",
        retryable: true,
        httpStatus: 503,
      }),
    );
    renderPage();

    expect(await screen.findByLabelText("Raw 诊断测试适配器")).toBeVisible();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "对象存储暂时不可用",
    );
    fireEvent.click(screen.getByRole("button", { name: "重新获取受权链接" }));
    expect(loadRawMediaMock).toHaveBeenCalledTimes(2);
    expect(loadDetailMock).toHaveBeenCalledTimes(1);
  });

  it("preserves Problem Details and a retry action instead of converting an error to empty", async () => {
    loadDetailMock.mockRejectedValue(
      createDomainError({
        code: "RATE_LIMITED",
        problemCode: "QC_RATE_LIMITED",
        message: "自动质检读取频率受限",
        fieldErrors: [],
        operationErrors: [],
        blockedReasons: [],
        requestId: "request-p04-429",
        retryable: true,
        httpStatus: 429,
      }),
    );
    renderPage();

    expect(await screen.findByText("请求频率受限")).toBeVisible();
    expect(screen.getByText(/QC_RATE_LIMITED/u)).toBeVisible();
    expect(screen.getByText(/request-p04-429/u)).toBeVisible();
    expect(
      screen.getByRole("button", { name: "按服务端提示重试" }),
    ).toBeVisible();
    expect(screen.queryByText("暂无数据")).not.toBeInTheDocument();
  });

  it("rejects unstable aliases instead of inventing a current upload", () => {
    renderPage("/ingest/uploads/latest");
    expect(screen.getByText("资源不存在")).toBeVisible();
    expect(loadDetailMock).not.toHaveBeenCalled();
  });
});
