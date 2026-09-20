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
import { makeScopeKey } from "../../entities/scope";
import { useShellStore } from "../../shared/scope/shell-store";
import UploadJobsPage from "./page";
import {
  resetUploadQueueStoreForTests,
  useUploadQueueStore,
  type UploadQueueItem,
} from "./upload-queue-store";
import { UploadMethodPanel } from "./components/UploadMethodPanel";
import { UploadQueuePanel } from "./components/UploadQueuePanel";

const {
  nativeUploadMock,
  createSessionMock,
  grantedCapabilities,
  ingestScopeState,
  listSessionsMock,
  preflightMock,
} = vi.hoisted(() => ({
  nativeUploadMock: vi.fn(),
  createSessionMock: vi.fn(),
  grantedCapabilities: new Set<string>(["upload.read", "upload.manage"]),
  ingestScopeState: {
    current: {
      organizationId: "org-e05",
      projectId: "project-e05",
      regionCode: "cn-shanghai",
    } as {
      organizationId: string;
      projectId: string;
      regionCode: string;
    } | null,
  },
  listSessionsMock: vi.fn(),
  preflightMock: vi.fn(),
}));

vi.mock("../../features/ingest/use-ingest-scope", () => ({
  useIngestScope: () => ingestScopeState.current,
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

vi.mock("../../features/datasets/api/hooks", () => ({
  useDatasetsQuery: () => ({
    data: { items: [{ datasetId: "dataset_alpha", name: "原始数据集" }] },
    isPending: false,
    isError: false,
  }),
}));
vi.mock("./lerobot-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./lerobot-client")>()),
  uploadNativeLeRobot: nativeUploadMock,
}));

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
  nativeUploadMock.mockReset().mockResolvedValue({ status: "RAW_COMMITTED" });
  grantedCapabilities.clear();
  grantedCapabilities.add("upload.read");
  grantedCapabilities.add("upload.manage");
  ingestScopeState.current = {
    organizationId: "org-e05",
    projectId: "project-e05",
    regionCode: "cn-shanghai",
  };
  resetUploadQueueStoreForTests();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
  useShellStore.getState().clearSensitiveState();
  useShellStore.setState({
    bootstrapLoaded: false,
    authorizationFailed: false,
  });
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
        [new Uint8Array(8)],
        "recording.mcap",
        "factory/episode-e05/recording.mcap",
        { type: "application/octet-stream" },
      ),
    ]);
    const dialog = await screen.findByRole("dialog", { name: "确认上传" });
    await user.click(
      within(dialog).getByRole("combobox", { name: "目标数据集 ID" }),
    );
    await user.click(await screen.findByText("原始数据集"));
    return dialog;
  }

  it("explains the missing storage scope instead of reporting a permission error", async () => {
    const user = userEvent.setup();
    ingestScopeState.current = null;
    grantedCapabilities.clear();
    useShellStore.setState({
      scope: null,
      scopeKey: makeScopeKey({ organizationId: "unscoped" }),
      authorization: null,
      authorizationLoading: false,
      authorizationFailed: false,
      bootstrapLoaded: true,
    });

    renderPage();

    expect(screen.getByRole("heading", { name: "数据上传" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "新建上传" })).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "选择采集文件夹" }),
    ).toBeVisible();
    expect(screen.getByText("上传队列为空")).toBeVisible();
    expect(screen.queryByText("暂无上传记录")).toBeNull();
    expect(screen.queryByText("无权访问")).toBeNull();

    await user.click(
      screen.getByText("选择采集文件夹", { selector: "strong" }),
    );

    const dialog = await screen.findByRole("dialog", {
      name: "上传前需要加入组织和项目",
    });
    await waitFor(() =>
      expect(
        within(dialog).getByText(/平台无法确定数据应写入哪个存储位置/u),
      ).toBeVisible(),
    );
    expect(
      within(dialog).getByRole("button", { name: "前往账户设置" }),
    ).toBeEnabled();
    expect(preflightMock).not.toHaveBeenCalled();
    expect(createSessionMock).not.toHaveBeenCalled();
  });

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

    expect(
      within(dialog).queryByText("浏览器本地检查"),
    ).not.toBeInTheDocument();
    expect(within(dialog).getByText("factory")).toBeInTheDocument();
    expect(within(dialog).getByText("project-e05")).toBeInTheDocument();
    expect(within(dialog).getByText("cn-shanghai")).toBeInTheDocument();
    expect(within(dialog).queryByText("task-e05")).toBeNull();
    expect(within(dialog).queryByText("robot-e05")).toBeNull();
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

  it("stores the original folder without invoking the legacy processing precheck", async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    const dialog = await chooseValidFolder(user, container);
    await user.click(within(dialog).getByRole("button", { name: "确认上传" }));
    expect(
      await screen.findByRole("heading", { name: "上传已完成" }),
    ).toBeVisible();
    expect(nativeUploadMock).toHaveBeenCalledTimes(1);
    const [, source, binding] = nativeUploadMock.mock.calls[0]!;
    expect(source.format).toBe("mcap");
    expect(source.sourceFiles).toHaveLength(1);
    expect(binding).toEqual({
      datasetId: "dataset_alpha",
      collectionTaskId: null,
      robotId: null,
      processingMode: "STORE_ONLY",
    });
    expect(preflightMock).not.toHaveBeenCalled();
    expect(createSessionMock).not.toHaveBeenCalled();
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


  it("accepts an MCAP without a platform Manifest", async () => {
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
    expect(within(dialog).queryByText("MANIFEST_FILE_MISSING")).toBeNull();
    await user.click(
      within(dialog).getByRole("combobox", { name: "目标数据集 ID" }),
    );
    await user.click(await screen.findByText("原始数据集"));
    expect(
      within(dialog).getByRole("button", { name: "确认上传" }),
    ).toBeEnabled();
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

  it("shows an interrupted LeRobot transfer in upload records", async () => {
    useUploadQueueStore.setState({
      scopeKey: "org-e05/project-e05/cn-shanghai",
      items: [
        {
          id: "lerobot-queue-1",
          scopeKey: "org-e05/project-e05/cn-shanghai",
          sessionId: "a".repeat(32),
          sourceType: "LEROBOT_NATIVE",
          fileName: "factory-run",
          dataPackageId: "dataset-a",
          totalBytes: 100,
          uploadedBytes: 40,
          completedParts: 4,
          totalParts: 10,
          speedBytesPerSecond: null,
          remainingSeconds: null,
          transferStatus: "failed",
          serverStatus: null,
          failedParts: [],
          failedPartTransfers: [],
          failureCode: "LEROBOT_UPLOAD_INTERRUPTED",
          failureMessage: "网络传输中断，可继续未完成上传。",
          requestId: null,
          createdAt: "2026-09-01T00:00:00Z",
          robotId: "robot-a",
          collectionTaskId: "task-a",
        },
      ],
    });

    renderPage("/ingest/uploads/records");

    expect(await screen.findByText("原始数据 · factory-run")).toBeVisible();
    expect(screen.getByText("Dataset dataset-a")).toBeVisible();
    expect(screen.getByText("传输失败")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "继续未完成上传" }),
    ).toBeEnabled();
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
        onContinueUpload={vi.fn()}
        onViewRecords={vi.fn()}
        onRecover={onRecover}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("服务端暂时不可用");
    expect(screen.getByText("req-recover-1")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "重新恢复队列" }));
    expect(onRecover).toHaveBeenCalledTimes(1);
  });

  it("does not offer queue recovery when the login session has expired", () => {
    render(
      <UploadQueuePanel
        items={[]}
        recovering={false}
        recoveryProblem={{
          title: "登录状态已失效",
          detail: "请重新登录后继续。",
          requestId: "req-expired-session",
          retryable: false,
          status: 401,
          problemCode: "AUTHENTICATION_REQUIRED",
        }}
        folderBatch={null}
        canManage
        onPause={vi.fn()}
        onResume={vi.fn()}
        onRetry={vi.fn()}
        onCancel={vi.fn()}
        onReattach={vi.fn()}
        onClearSettled={vi.fn()}
        onContinueUpload={vi.fn()}
        onViewRecords={vi.fn()}
        onRecover={vi.fn()}
      />,
    );

    expect(screen.getByText("登录状态已失效")).toBeVisible();
    expect(screen.getByText("暂时无法读取上传队列")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "重新恢复队列" }),
    ).not.toBeInTheDocument();
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
        onContinueUpload={vi.fn()}
        onViewRecords={vi.fn()}
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
        onContinueUpload={vi.fn()}
        onViewRecords={vi.fn()}
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
        onContinueUpload={vi.fn()}
        onViewRecords={vi.fn()}
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
        onContinueUpload={vi.fn()}
        onViewRecords={vi.fn()}
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
        onContinueUpload={vi.fn()}
        onViewRecords={vi.fn()}
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
    disabled: false,
    onFilesChange: vi.fn(),
  };

  it("names the browser package input and enables recursive directory selection", () => {
    const { container } = render(<UploadMethodPanel {...commonProps} />);

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
    expect(screen.getByText("保留原目录结构和文件内容")).toBeVisible();
  });

  it("does not expose a manual object-storage address", () => {
    render(<UploadMethodPanel {...commonProps} />);

    expect(screen.queryByText("授权对象地址")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("已授权对象地址")).not.toBeInTheDocument();
    expect(document.querySelector("#object-storage-uri")).toBeNull();
    expect(document.querySelector("#object-upload-manifest")).toBeNull();
  });
});
