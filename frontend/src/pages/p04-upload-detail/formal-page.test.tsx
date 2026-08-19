// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { createDomainError } from "../../shared/api/domain-error";
import type { FormalUploadDetail } from "./formal-detail-client";
import FormalUploadDetailPage from "./formal-page";

const { loadDetailMock } = vi.hoisted(() => ({
  loadDetailMock: vi.fn(),
}));

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
  const actual = await importOriginal<typeof import("./formal-detail-client")>();
  return { ...actual, loadFormalUploadDetail: loadDetailMock };
});

vi.mock("../../features/viewer", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../features/viewer")>();
  return {
    ...actual,
    RawDiagnosticWorkbench: (props: {
      readonly manifest: { readonly cameras: readonly unknown[] };
      readonly findings: readonly unknown[];
    }) => (
      <section
        aria-label="Raw 诊断测试适配器"
        data-camera-count={props.manifest.cameras.length}
        data-finding-count={props.findings.length}
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
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  loadDetailMock.mockResolvedValue(detailFixture("PASS", 4));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("P04 formal upload detail page", () => {
  it("keeps a direct deep link query/hash and shows PASS without a false Raw error banner", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "上传详情" })).toBeVisible();
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/ingest/uploads/session-a?from=pending#quality",
    );
    expect(screen.getByText("自动质检通过")).toBeVisible();
    expect(screen.getByText("未发现需要诊断的异常")).toBeVisible();
    expect(screen.queryByText("Raw 诊断正式入口")).not.toBeInTheDocument();
    expect(loadDetailMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: "project-a",
        regionCode: "cn-test",
      }),
      "session-a",
      expect.any(AbortSignal),
    );
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
      expect(workbench).toHaveAttribute("data-finding-count", "1");
      expect(screen.getByText("自动质检：RISK")).toBeVisible();
    },
  );

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
