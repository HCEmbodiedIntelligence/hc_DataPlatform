// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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

const {
  createSessionMock,
  grantedCapabilities,
  listSessionsMock,
  preflightMock,
} = vi.hoisted(() => ({
  createSessionMock: vi.fn(),
  grantedCapabilities: new Set<string>(["upload.read", "upload.manage"]),
  listSessionsMock: vi.fn(),
  preflightMock: vi.fn(),
}));

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
    createFormalUploadSession: createSessionMock,
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

const uploadGrant = {
  session: {
    session_id: "upload-session-e05",
    project_id: manifest.project_id,
    region_code: "cn-shanghai",
    data_package_id: manifest.data_package_id,
    rollout_id: manifest.rollout_id,
    source_type: "BROWSER_MULTIPART" as const,
    status: "RAW_COMMITTED" as const,
    manifest_fingerprint: preflight.manifest_fingerprint,
    object_key: "raw/recording.mcap",
    multipart_upload_id: "multipart-e05",
    expected_size: manifest.file_size,
    expected_sha256: manifest.sha256,
    expected_crc64: manifest.crc64,
    failure_code: null,
    etag: "etag-e05",
    completed_at: "2026-08-18T01:00:20Z",
  },
  parts: [],
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

function nestedFolderFile(
  contents: BlobPart[],
  name: string,
  relativePath: string,
  options?: FilePropertyBag,
): File {
  const file = new File(contents, name, options);
  Object.defineProperty(file, "webkitRelativePath", {
    configurable: true,
    value: relativePath,
  });
  return file;
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
  createSessionMock.mockResolvedValue(uploadGrant);
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

describe("P03 serial data upload page", () => {
  async function chooseValidFolder(
    user: ReturnType<typeof userEvent.setup>,
    container: HTMLElement,
  ) {
    const folder = container.querySelector<HTMLInputElement>(
      "#browser-upload-folder",
    );
    expect(folder).not.toBeNull();
    await user.upload(folder!, [
      nestedFolderFile(
        [JSON.stringify(manifest)],
        "rollout_manifest.json",
        "factory/episode-e05/rollout_manifest.json",
        { type: "application/json" },
      ),
      nestedFolderFile(
        [new Uint8Array(8)],
        "recording.mcap",
        "factory/episode-e05/recording.mcap",
        { type: "application/octet-stream" },
      ),
    ]);
    return screen.findByRole("dialog", { name: "确认上传" });
  }

  it("starts with only the folder gate and no precheck or processing progress", () => {
    renderPage();
    expect(
      screen.getByRole("heading", { name: "选择采集文件夹" }),
    ).toBeVisible();
    expect(screen.getByText("上传队列为空")).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "上传前预检" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(preflightMock).not.toHaveBeenCalled();
    expect(createSessionMock).not.toHaveBeenCalled();
  });

  it("shows a server recovery failure instead of masking it with the idle state", async () => {
    listSessionsMock.mockRejectedValueOnce(new Error("恢复服务暂不可用"));
    renderPage();

    expect(
      await screen.findByRole("heading", { name: /上传队列/u }),
    ).toBeVisible();
    expect(screen.getByText("上传操作未完成")).toBeVisible();
    expect(screen.getByRole("button", { name: "重新恢复队列" })).toBeEnabled();
    expect(
      screen.queryByRole("heading", { name: "选择采集文件夹" }),
    ).not.toBeInTheDocument();
  });

  it("shows only target confirmation after local folder enumeration", async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    const dialog = await chooseValidFolder(user, container);

    expect(within(dialog).getByText("浏览器本地检查")).toBeInTheDocument();
    expect(within(dialog).getByText("factory")).toBeInTheDocument();
    expect(within(dialog).getByText("project-e05")).toBeInTheDocument();
    expect(within(dialog).getByText("cn-shanghai")).toBeInTheDocument();
    expect(within(dialog).getByText("task-e05")).toBeInTheDocument();
    expect(within(dialog).getByText("robot-e05")).toBeInTheDocument();
    expect(preflightMock).not.toHaveBeenCalled();
    expect(createSessionMock).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("heading", { name: "上传前预检" }),
    ).not.toBeInTheDocument();
  });

  it("cancels confirmation without creating a task or starting precheck", async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    const dialog = await chooseValidFolder(user, container);
    await user.click(within(dialog).getByRole("button", { name: /取\s*消/u }));

    expect(
      screen.getByRole("heading", { name: "选择采集文件夹" }),
    ).toBeVisible();
    expect(preflightMock).not.toHaveBeenCalled();
    expect(createSessionMock).not.toHaveBeenCalled();
  });

  it("hides confirmation and shows server precheck only after confirmation", async () => {
    let resolvePreflight!: (value: ManifestPreflight) => void;
    preflightMock.mockReturnValueOnce(
      new Promise<ManifestPreflight>((resolve) => {
        resolvePreflight = resolve;
      }),
    );
    const user = userEvent.setup();
    const { container } = renderPage();
    const dialog = await chooseValidFolder(user, container);
    await user.click(within(dialog).getByRole("button", { name: "确认上传" }));

    expect(
      await screen.findByRole("heading", { name: "上传前预检" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("dialog", { name: "确认上传" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("上传队列为空")).not.toBeInTheDocument();
    expect(preflightMock).toHaveBeenCalledTimes(1);
    expect(createSessionMock).not.toHaveBeenCalled();

    resolvePreflight(preflight);
    expect(await screen.findByText("Raw 已提交")).toBeVisible();
  });

  it("reflects queue creation from the real pending request without fake percentages", async () => {
    let resolveCreate!: (value: typeof uploadGrant) => void;
    createSessionMock.mockReturnValueOnce(
      new Promise<typeof uploadGrant>((resolve) => {
        resolveCreate = resolve;
      }),
    );
    const user = userEvent.setup();
    const { container } = renderPage();
    const dialog = await chooseValidFolder(user, container);
    await user.click(within(dialog).getByRole("button", { name: "确认上传" }));

    await waitFor(() => expect(createSessionMock).toHaveBeenCalledTimes(1));
    expect(
      screen.getByText("创建上传任务与分片队列").closest("li"),
    ).toHaveAttribute("data-state", "current");
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();

    resolveCreate(uploadGrant);
    expect(await screen.findByText("Raw 已提交")).toBeVisible();
  });

  it("stays on the error result and never enters the queue after precheck failure", async () => {
    preflightMock.mockRejectedValueOnce(
      createDomainError({
        code: "VALIDATION_ERROR",
        problemCode: "MANIFEST_PATH_TRAVERSAL",
        message: "files/0/path 包含路径穿越，数据包已拒绝。",
        fieldErrors: [],
        operationErrors: [],
        blockedReasons: [],
        requestId: "req-e05-422",
        retryable: false,
        httpStatus: 422,
      }),
    );
    const user = userEvent.setup();
    const { container } = renderPage();
    const dialog = await chooseValidFolder(user, container);
    await user.click(within(dialog).getByRole("button", { name: "确认上传" }));

    expect(await screen.findByText("Manifest 预检失败")).toBeVisible();
    expect(screen.getByText("MANIFEST_PATH_TRAVERSAL")).toBeVisible();
    expect(screen.getByRole("button", { name: "重新检查" })).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "返回重新选择文件夹" }),
    ).toBeEnabled();
    expect(createSessionMock).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("heading", { name: /上传队列/ }),
    ).not.toBeInTheDocument();
  });

  it("keeps task-creation failures out of the formal upload queue", async () => {
    createSessionMock.mockRejectedValueOnce(
      createDomainError({
        code: "SERVER_ERROR",
        problemCode: "UPLOAD_SESSION_CREATE_FAILED",
        message: "上传任务创建失败，请稍后重试。",
        fieldErrors: [],
        operationErrors: [],
        blockedReasons: [],
        requestId: "req-create-e05",
        retryable: true,
        httpStatus: 503,
      }),
    );
    const user = userEvent.setup();
    const { container } = renderPage();
    const dialog = await chooseValidFolder(user, container);
    await user.click(within(dialog).getByRole("button", { name: "确认上传" }));

    expect(await screen.findByText("上传操作未完成")).toBeVisible();
    expect(screen.getByText("UPLOAD_SESSION_CREATE_FAILED")).toBeVisible();
    expect(screen.getByText("req-create-e05")).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: /上传队列/ }),
    ).not.toBeInTheDocument();
  });

  it("reuses already-created batch tasks when queue creation is retried", async () => {
    const secondManifest = {
      ...preflight.manifest,
      rollout_id: "rollout-e05-2",
      data_package_id: "package-e05-2",
      sequence_no: 2,
      files: [
        {
          ...manifest.files[0],
          path: "recording-2.mcap",
          sha256: "c".repeat(64),
        },
      ],
      sha256: "c".repeat(64),
    };
    const secondPreflight: ManifestPreflight = {
      ...preflight,
      manifest_fingerprint: "d".repeat(64),
      identifiers: {
        ...preflight.identifiers,
        data_package_id: secondManifest.data_package_id,
      },
      files: [...secondManifest.files],
      manifest: secondManifest,
    };
    const secondGrant = {
      ...uploadGrant,
      session: {
        ...uploadGrant.session,
        session_id: "upload-session-e05-2",
        data_package_id: secondManifest.data_package_id,
        rollout_id: secondManifest.rollout_id,
        manifest_fingerprint: secondPreflight.manifest_fingerprint,
      },
    };
    preflightMock.mockImplementation(
      (_scope, submitted: typeof manifest | typeof secondManifest) =>
        Promise.resolve(
          submitted.data_package_id === secondManifest.data_package_id
            ? secondPreflight
            : preflight,
        ),
    );
    createSessionMock
      .mockResolvedValueOnce(uploadGrant)
      .mockRejectedValueOnce(
        createDomainError({
          code: "SERVER_ERROR",
          problemCode: "UPLOAD_SESSION_CREATE_FAILED",
          message: "第二个任务创建失败。",
          fieldErrors: [],
          operationErrors: [],
          blockedReasons: [],
          requestId: "req-create-batch",
          retryable: true,
          httpStatus: 503,
        }),
      )
      .mockResolvedValueOnce(secondGrant);

    const user = userEvent.setup();
    const { container } = renderPage();
    const folder = container.querySelector<HTMLInputElement>(
      "#browser-upload-folder",
    );
    await user.upload(folder!, [
      nestedFolderFile(
        [JSON.stringify(manifest)],
        "rollout_manifest.json",
        "factory/episode-e05/rollout_manifest.json",
        { type: "application/json" },
      ),
      nestedFolderFile(
        [new Uint8Array(8)],
        "recording.mcap",
        "factory/episode-e05/recording.mcap",
      ),
      nestedFolderFile(
        [JSON.stringify(secondManifest)],
        "rollout_manifest.json",
        "factory/episode-e05-2/rollout_manifest.json",
        { type: "application/json" },
      ),
      nestedFolderFile(
        [new Uint8Array(8)],
        "recording-2.mcap",
        "factory/episode-e05-2/recording-2.mcap",
      ),
    ]);
    const dialog = await screen.findByRole("dialog", { name: "确认上传" });
    await user.click(within(dialog).getByRole("button", { name: "确认上传" }));

    expect(await screen.findByText(/已有 1 个任务完成创建/u)).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: /上传队列/ }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新检查" }));

    await waitFor(() => expect(createSessionMock).toHaveBeenCalledTimes(3));
    expect(
      await screen.findByRole("heading", { name: /上传队列/u }),
    ).toBeVisible();
    expect(screen.getAllByText("Raw 已提交")).toHaveLength(2);
  });

  it("disables confirmation without upload.manage but allows local inspection", async () => {
    grantedCapabilities.delete("upload.manage");
    const user = userEvent.setup();
    const { container } = renderPage();
    const dialog = await chooseValidFolder(user, container);

    expect(within(dialog).getByText("缺少上传权限")).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "确认上传" }),
    ).toBeDisabled();
    expect(preflightMock).not.toHaveBeenCalled();
    expect(createSessionMock).not.toHaveBeenCalled();
  });

  it("allows a project admin to confirm in line with the backend role gate", async () => {
    grantedCapabilities.delete("upload.read");
    grantedCapabilities.delete("upload.manage");
    grantedCapabilities.add("project.access.manage");
    const user = userEvent.setup();
    const { container } = renderPage();
    const dialog = await chooseValidFolder(user, container);

    expect(
      within(dialog).getByRole("button", { name: "确认上传" }),
    ).toBeEnabled();
  });

  it("blocks confirmation when the local folder has no Manifest", async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    const folder = container.querySelector<HTMLInputElement>(
      "#browser-upload-folder",
    );
    await user.upload(folder!, [
      nestedFolderFile(
        [new Uint8Array(8)],
        "recording.mcap",
        "factory/episode-e05/recording.mcap",
      ),
    ]);

    const dialog = await screen.findByRole("dialog", { name: "确认上传" });
    expect(
      within(dialog).getByText("MANIFEST_FILE_MISSING"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "确认上传" }),
    ).toBeDisabled();
    expect(preflightMock).not.toHaveBeenCalled();
  });

  it("loads formal records and keeps route tabs keyboard accessible", async () => {
    const user = userEvent.setup();
    renderPage();
    const newUpload = screen.getByRole("tab", { name: "新建上传" });
    newUpload.focus();
    await user.keyboard("{ArrowRight}");
    await waitFor(() => expect(listSessionsMock).toHaveBeenCalled());
    expect(screen.getByText("还没有上传记录")).toBeVisible();
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "新建上传" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});

describe("P03 upload queue copy", () => {
  it("shows a recoverable queue-loading error instead of silently dropping it", async () => {
    const user = userEvent.setup();
    const onRecover = vi.fn();
    render(
      <UploadQueuePanel
        items={[]}
        recovering={false}
        recoveryProblem={{
          title: "上传队列恢复失败",
          detail: "服务端暂时不可用。",
          requestId: "req-recover-1",
          retryable: true,
          status: 503,
          problemCode: "RECOVERY_UNAVAILABLE",
        }}
        folderBatch={null}
        canManage
        onPause={vi.fn()}
        onResume={vi.fn()}
        onRetry={vi.fn()}
        onCancel={vi.fn()}
        onReattach={vi.fn()}
        onClearSettled={vi.fn()}
        onRecover={onRecover}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("服务端暂时不可用");
    expect(screen.getByText("req-recover-1")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "重新恢复队列" }));
    expect(onRecover).toHaveBeenCalledTimes(1);
  });

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
        recoveryProblem={null}
        folderBatch={null}
        canManage
        onPause={vi.fn()}
        onResume={vi.fn()}
        onRetry={vi.fn()}
        onCancel={vi.fn()}
        onReattach={vi.fn()}
        onClearSettled={vi.fn()}
        onRecover={vi.fn()}
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
        recoveryProblem={null}
        folderBatch={null}
        canManage
        onPause={vi.fn()}
        onResume={vi.fn()}
        onRetry={vi.fn()}
        onCancel={vi.fn()}
        onReattach={vi.fn()}
        onClearSettled={vi.fn()}
        onRecover={vi.fn()}
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
        recoveryProblem={null}
        folderBatch={null}
        canManage
        onPause={vi.fn()}
        onResume={vi.fn()}
        onRetry={vi.fn()}
        onCancel={vi.fn()}
        onReattach={vi.fn()}
        onClearSettled={vi.fn()}
        onRecover={vi.fn()}
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
        recoveryProblem={null}
        folderBatch={null}
        canManage
        onPause={vi.fn()}
        onResume={vi.fn()}
        onRetry={vi.fn()}
        onCancel={vi.fn()}
        onReattach={vi.fn()}
        onClearSettled={vi.fn()}
        onRecover={vi.fn()}
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

  it("binds pause and cancel to only the selected queue record", async () => {
    const user = userEvent.setup();
    const onPause = vi.fn();
    const onCancel = vi.fn();
    const uploading: UploadQueueItem = {
      id: "queue-a",
      scopeKey: "org/project/region",
      sessionId: "session-a",
      sourceType: "BROWSER_MULTIPART",
      fileName: "a.mcap",
      dataPackageId: "package-a",
      totalBytes: 10,
      uploadedBytes: 2,
      completedParts: 0,
      totalParts: 2,
      speedBytesPerSecond: 2,
      remainingSeconds: 4,
      transferStatus: "uploading",
      serverStatus: "UPLOADING",
      failedParts: [],
      failedPartTransfers: [],
      failureCode: null,
      failureMessage: null,
      requestId: null,
      createdAt: "2026-08-18T00:00:00Z",
    };
    render(
      <UploadQueuePanel
        items={[
          uploading,
          {
            ...uploading,
            id: "queue-b",
            sessionId: "session-b",
            fileName: "b.mcap",
            dataPackageId: "package-b",
            transferStatus: "waiting",
          },
        ]}
        recovering={false}
        recoveryProblem={null}
        folderBatch={null}
        canManage
        onPause={onPause}
        onResume={vi.fn()}
        onRetry={vi.fn()}
        onCancel={onCancel}
        onReattach={vi.fn()}
        onClearSettled={vi.fn()}
        onRecover={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "暂停传输" }));
    expect(onPause).toHaveBeenCalledWith("queue-a");
    expect(onPause).not.toHaveBeenCalledWith("queue-b");

    const waitingCard = screen
      .getByLabelText("package-b 上传传输")
      .closest("article");
    await user.click(
      within(waitingCard!).getByRole("button", { name: "取消" }),
    );
    await user.click(await screen.findByRole("button", { name: "取消上传" }));
    expect(onCancel).toHaveBeenCalledWith("queue-b");
    expect(onCancel).not.toHaveBeenCalledWith("queue-a");
  });
});

describe("P03 upload method form semantics", () => {
  const commonProps = {
    browserSelectionMode: "package" as const,
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

  it("names the browser package input and enables recursive directory selection", () => {
    const { container } = render(
      <UploadMethodPanel {...commonProps} sourceType="BROWSER_MULTIPART" />,
    );

    expect(container.querySelector("#browser-upload-package")).toHaveAttribute(
      "name",
      "browser-upload-package",
    );
    expect(container.querySelector("#browser-upload-folder")).toHaveAttribute(
      "name",
      "browser-upload-folder",
    );
    expect(container.querySelector("#browser-upload-folder")).toHaveAttribute(
      "webkitdirectory",
      "",
    );
    expect(
      screen.getByText("支持包含多个独立 Manifest 数据包的多级目录"),
    ).toBeVisible();
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
    expect(
      screen.queryByRole("heading", { name: "目标信息" }),
    ).not.toBeInTheDocument();
  });
});
