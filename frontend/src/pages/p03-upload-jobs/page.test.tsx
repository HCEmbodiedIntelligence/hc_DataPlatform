// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { createDomainError } from "../../shared/api/domain-error";
import type { ManifestPreflight } from "./formal-client";
import UploadJobsPage from "./page";
import {
  resetUploadQueueStoreForTests,
  type UploadQueueItem,
} from "./upload-queue-store";
import { UploadMethodPanel } from "./components/UploadMethodPanel";
import { UploadQueuePanel } from "./components/UploadQueuePanel";

const { grantedCapabilities, listSessionsMock, preflightMock } = vi.hoisted(
  () => ({
    grantedCapabilities: new Set<string>(["upload.read", "upload.manage"]),
    listSessionsMock: vi.fn(),
    preflightMock: vi.fn(),
  }),
);

vi.mock("../../features/ingest/use-ingest-scope", () => ({
  useIngestScope: () => ({
    organizationId: "org-e05",
    projectId: "project-e05",
    regionCode: "cn-shanghai",
  }),
}));

vi.mock("../../shared/auth/use-capabilities", () => ({
  useCapabilities: () => ({
    has: (capability: string) => grantedCapabilities.has(capability),
    loading: false,
    failed: false,
  }),
}));

vi.mock("./formal-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./formal-client")>();
  return {
    ...actual,
    listFormalUploadSessions: listSessionsMock,
    preflightUploadManifest: preflightMock,
    getFormalUploadManifest: vi.fn(),
    listFormalUploadParts: vi.fn(),
  };
});

const manifest = {
  schema_version: 1,
  project_id: "project-e05",
  task_id: "task-e05",
  collection_job_id: "job-e05",
  rollout_id: "rollout-e05",
  collection_session_id: "session-e05",
  recording_request_id: "request-e05",
  data_package_id: "package-e05",
  sequence_no: 1,
  robot_id: "robot-e05",
  start_time: "2026-08-18T01:00:00Z",
  end_time: "2026-08-18T01:00:10Z",
  cameras: [
    {
      camera_id: "front",
      topic: "/camera/front",
      frame_id: "front_link",
      encoding: "h264",
    },
  ],
  topics: [
    {
      name: "/camera/front",
      required: true,
      message_encoding: "cdr",
      schema_name: "sensor_msgs/Image",
    },
  ],
  expected_topics: ["/camera/front"],
  actual_topics: ["/camera/front"],
  files: [
    {
      path: "recording.mcap",
      size: 8,
      sha256: "a".repeat(64),
      crc64: "1",
      media_type: "application/octet-stream",
      role: "RAW_MCAP",
    },
  ],
  file_size: 8,
  sha256: "a".repeat(64),
  crc64: "1",
  compression: "none",
  recorder_version: "recorder/1.0",
} as const;

const preflight: ManifestPreflight = {
  schema_version: "manifest-preflight/v1",
  manifest_fingerprint: "b".repeat(64),
  identifiers: {
    collection_session_id: manifest.collection_session_id,
    recording_request_id: manifest.recording_request_id,
    data_package_id: manifest.data_package_id,
    robot_id: manifest.robot_id,
    pico_instance_id: null,
  },
  time_range: { start_time: manifest.start_time, end_time: manifest.end_time },
  files: [...manifest.files],
  total_file_size: 8,
  discovery: {
    source: "MANIFEST",
    read_only: true,
    cameras: [...manifest.cameras],
    topics: [...manifest.topics],
    missing_expected_topics: [],
  },
  manifest: {
    ...manifest,
    cameras: [...manifest.cameras],
    topics: [...manifest.topics],
    expected_topics: [...manifest.expected_topics],
    actual_topics: [...manifest.actual_topics],
    files: [...manifest.files],
  },
};

function renderPage(path = "/ingest/uploads/new") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <UploadJobsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  Object.defineProperty(window.navigator, "onLine", {
    configurable: true,
    value: true,
  });
  const computedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    computedStyle(element),
  );
  listSessionsMock.mockResolvedValue({ items: [], total: 0 });
  preflightMock.mockResolvedValue(preflight);
  grantedCapabilities.clear();
  grantedCapabilities.add("upload.read");
  grantedCapabilities.add("upload.manage");
  resetUploadQueueStoreForTests();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
  resetUploadQueueStoreForTests();
});

describe("P03 data upload page", () => {
  it("renders the two in-page tabs, 25/50/25 workflow and public-network safety facts", () => {
    const { container } = renderPage();
    expect(screen.getByRole("tab", { name: "新建上传" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "上传记录" })).toHaveAttribute(
      "href",
      "/ingest/uploads/records",
    );
    expect(
      screen.getByRole("heading", { name: "上传方式与目标" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: /Manifest 预检/ }),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: /上传队列/ })).toBeVisible();
    expect(screen.getByText(/Manifest ≤ 1 MiB/)).toBeVisible();
    expect(screen.getByText(/禁止上传凭据、密钥或个人隐私/)).toBeVisible();
    expect(
      container.querySelector('[class*="uploadWorkspace"]'),
    ).toBeInTheDocument();
  });

  it("automatically preflights a selected browser package and keeps discovered cameras/topics read-only", async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    const input = container.querySelector<HTMLInputElement>(
      "#browser-upload-package",
    );
    expect(input).not.toBeNull();
    const manifestFile = new File(
      [JSON.stringify(manifest)],
      "rollout_manifest.json",
      { type: "application/json" },
    );
    const rawFile = new File([new Uint8Array(8)], "recording.mcap", {
      type: "application/octet-stream",
    });
    await user.upload(input!, [manifestFile, rawFile]);

    await waitFor(() => expect(preflightMock).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("预检通过")).toBeVisible();
    expect(screen.getByText("front")).toBeVisible();
    expect(screen.getByText("/camera/front")).toBeVisible();
    expect(screen.getByText("来源：Manifest · 只读")).toBeVisible();
    expect(screen.getByRole("button", { name: "开始上传" })).toBeEnabled();
  });

  it("calls the formal record list on the 上传记录 tab", async () => {
    renderPage("/ingest/uploads/records");
    expect(screen.getByRole("tab", { name: "上传记录" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await waitFor(() => expect(listSessionsMock).toHaveBeenCalled());
    expect(screen.getByText("还没有上传记录")).toBeVisible();
  });

  it("keeps upload controls genuinely read-only without upload.manage", () => {
    grantedCapabilities.delete("upload.manage");
    const { container } = renderPage();
    expect(screen.getByText("当前为只读模式")).toBeVisible();
    expect(
      container.querySelector<HTMLInputElement>("#browser-upload-package"),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "开始上传" })).toBeDisabled();
  });

  it("fails closed when upload.read is absent", () => {
    grantedCapabilities.delete("upload.read");
    renderPage();
    expect(screen.getByText("无权访问")).toBeVisible();
    expect(screen.getByText("当前授权不允许读取此资源。")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "开始上传" }),
    ).not.toBeInTheDocument();
  });

  it("supports arrow-key navigation between the two route-backed tabs", async () => {
    const user = userEvent.setup();
    renderPage();
    const newUpload = screen.getByRole("tab", { name: "新建上传" });
    newUpload.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "上传记录" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "新建上传" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it.each([
    [403, "FORBIDDEN", "UPLOAD_FORBIDDEN", "当前授权不能上传"],
    [409, "VERSION_CONFLICT", "DATA_PACKAGE_CONFLICT", "数据包事实发生冲突"],
    [429, "RATE_LIMITED", "UPLOAD_RATE_LIMITED", "请求频率受限"],
    [422, "VALIDATION_ERROR", "MANIFEST_PATH_TRAVERSAL", "Manifest 预检失败"],
  ] as const)(
    "shows real preflight HTTP %s without falling back to Browser Mock",
    async (status, code, problemCode, title) => {
      const user = userEvent.setup();
      preflightMock.mockRejectedValueOnce(
        createDomainError({
          code,
          problemCode,
          message:
            status === 422
              ? "files/0/path 包含路径穿越，数据包已拒绝。"
              : `real-api-${status}`,
          fieldErrors: [],
          operationErrors: [],
          blockedReasons: [],
          requestId: `req-e05-${status}`,
          retryable: status === 429,
          httpStatus: status,
        }),
      );
      const { container } = renderPage();
      const input = container.querySelector<HTMLInputElement>(
        "#browser-upload-package",
      );
      await user.upload(input!, [
        new File([JSON.stringify(manifest)], "rollout_manifest.json", {
          type: "application/json",
        }),
        new File([new Uint8Array(8)], "recording.mcap", {
          type: "application/octet-stream",
        }),
      ]);

      expect(await screen.findByRole("alert")).toHaveTextContent(title);
      expect(screen.getByText(problemCode)).toBeVisible();
      expect(screen.getByText(new RegExp(`req-e05-${status}`))).toBeVisible();
      expect(screen.getByRole("button", { name: "开始上传" })).toBeDisabled();
    },
  );
});

describe("P03 upload queue copy", () => {
  it("distinguishes transfer pause from collection-task state and exposes failed-part retry", () => {
    const paused: UploadQueueItem = {
      id: "queue-1",
      scopeKey: "org/project/region",
      sessionId: "session-1",
      sourceType: "BROWSER_MULTIPART",
      fileName: "recording.mcap",
      dataPackageId: "package-1",
      totalBytes: 10,
      uploadedBytes: 5,
      completedParts: 1,
      totalParts: 2,
      speedBytesPerSecond: null,
      remainingSeconds: null,
      transferStatus: "paused",
      serverStatus: "PAUSED",
      failedParts: [],
      failedPartTransfers: [],
      failureCode: null,
      failureMessage: null,
      requestId: null,
      createdAt: "2026-08-18T00:00:00Z",
    };
    const { rerender } = render(
      <UploadQueuePanel
        items={[paused]}
        recovering={false}
        canManage
        onPause={vi.fn()}
        onResume={vi.fn()}
        onRetry={vi.fn()}
        onCancel={vi.fn()}
        onReattach={vi.fn()}
        onClearSettled={vi.fn()}
      />,
    );
    expect(
      screen.getByText("上传传输已暂停；采集任务状态没有改变。"),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "继续传输" })).toBeEnabled();
    expect(screen.queryByText("任务暂停")).not.toBeInTheDocument();

    rerender(
      <UploadQueuePanel
        items={[
          {
            ...paused,
            transferStatus: "failed",
            failedParts: [2],
            failedPartTransfers: [
              { partNumber: 2, failureCode: "NETWORK_ERROR" },
            ],
            failureCode: "NETWORK_ERROR",
            failureMessage: "分片 2 网络传输失败。",
          },
        ]}
        recovering={false}
        canManage
        onPause={vi.fn()}
        onResume={vi.fn()}
        onRetry={vi.fn()}
        onCancel={vi.fn()}
        onReattach={vi.fn()}
        onClearSettled={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "重试失败分片" })).toBeEnabled();
    expect(screen.getByText("#2")).toBeVisible();

    rerender(
      <UploadQueuePanel
        items={[
          {
            ...paused,
            transferStatus: "failed",
            failedParts: [2],
            failedPartTransfers: [
              { partNumber: 2, failureCode: "PART_TIMEOUT" },
            ],
            failureCode: "PART_TIMEOUT",
            failureMessage:
              "分片 #2 在 120 秒内未完成传输，本批其余请求已停止。可“重试失败分片”；服务端已确认分片不会重复上传。",
            requestId: null,
          },
        ]}
        recovering={false}
        canManage
        onPause={vi.fn()}
        onResume={vi.fn()}
        onRetry={vi.fn()}
        onCancel={vi.fn()}
        onReattach={vi.fn()}
        onClearSettled={vi.fn()}
      />,
    );
    expect(screen.getByText("PART_TIMEOUT")).toBeVisible();
    expect(screen.getByText(/分片 #2 在 120 秒内未完成传输/u)).toBeVisible();
    expect(screen.getByText(/已确认分片不会重复上传/u)).toBeVisible();
    expect(screen.queryByText(/请求 ID/u)).not.toBeInTheDocument();
  });

  it("uses a unique stable name for each dynamic reattach input", () => {
    const needsFile: UploadQueueItem = {
      id: "queue-reattach-1",
      scopeKey: "org/project/region",
      sessionId: "session-1",
      sourceType: "BROWSER_MULTIPART",
      fileName: "recording-1.mcap",
      dataPackageId: "package-1",
      totalBytes: 10,
      uploadedBytes: 5,
      completedParts: 1,
      totalParts: 2,
      speedBytesPerSecond: null,
      remainingSeconds: null,
      transferStatus: "needs-file",
      serverStatus: "PAUSED",
      failedParts: [],
      failedPartTransfers: [],
      failureCode: null,
      failureMessage: null,
      requestId: null,
      createdAt: "2026-08-18T00:00:00Z",
    };
    const { container } = render(
      <UploadQueuePanel
        items={[
          needsFile,
          {
            ...needsFile,
            id: "queue-reattach-2",
            sessionId: "session-2",
            fileName: "recording-2.mcap",
            dataPackageId: "package-2",
          },
        ]}
        recovering={false}
        canManage
        onPause={vi.fn()}
        onResume={vi.fn()}
        onRetry={vi.fn()}
        onCancel={vi.fn()}
        onReattach={vi.fn()}
        onClearSettled={vi.fn()}
      />,
    );

    const names = Array.from(
      container.querySelectorAll<HTMLInputElement>("input[type=file]"),
      (input) => input.name,
    );
    expect(names).toEqual([
      "reattach-upload-file-queue-reattach-1",
      "reattach-upload-file-queue-reattach-2",
    ]);
    expect(new Set(names).size).toBe(names.length);
  });
});

describe("P03 upload method form semantics", () => {
  const commonProps = {
    files: [] as const,
    objectStorageUri: "",
    projectId: "project-e05",
    regionCode: "cn-shanghai",
    preflight: null,
    disabled: false,
    onSourceTypeChange: vi.fn(),
    onFilesChange: vi.fn(),
    onObjectStorageUriChange: vi.fn(),
  };

  it("names the browser package file input", () => {
    const { container } = render(
      <UploadMethodPanel {...commonProps} sourceType="BROWSER_MULTIPART" />,
    );

    expect(container.querySelector("#browser-upload-package")).toHaveAttribute(
      "name",
      "browser-upload-package",
    );
  });

  it("names object-reference controls and uses autocomplete off and an ellipsis", () => {
    const { container } = render(
      <UploadMethodPanel
        {...commonProps}
        sourceType="OBJECT_STORAGE_REFERENCE"
      />,
    );

    expect(screen.getByLabelText("已授权对象地址")).toHaveAttribute(
      "name",
      "object-storage-uri",
    );
    expect(screen.getByLabelText("已授权对象地址")).toHaveAttribute(
      "autocomplete",
      "off",
    );
    expect(screen.getByLabelText("已授权对象地址")).toHaveAttribute(
      "placeholder",
      "s3://受管存储桶/raw/v1/…/recording.mcap",
    );
    expect(container.querySelector("#object-upload-manifest")).toHaveAttribute(
      "name",
      "object-upload-manifest",
    );
    expect(screen.getByText("相机与 Topic 上传后自动发现，只读")).toBeVisible();
  });
});
