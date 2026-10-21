// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LocalUploadSelection } from "../upload-flow";
import { UploadConfirmationDialog } from "./UploadConfirmationDialog";
import { useShellStore } from "../../../shared/scope/shell-store";
import type { LeRobotCapabilities } from "../../../features/ingest/upload/lerobot-capabilities";

const { onConfirmMock, requestMock } = vi.hoisted(() => ({
  onConfirmMock: vi.fn(),
  requestMock: vi.fn(),
}));
vi.mock("../../../shared/api/http-client", () => ({ request: requestMock }));
vi.mock("../../../features/ingest/processing/lerobot-targets", () => ({
  listActiveCollectionTasks: async () => [
    {
      collection_task_id: "task-a",
      dataset_id: "dataset_alpha",
      name: "合成采集",
    },
  ],
}));
vi.mock("../../../features/ingest/api/index", () => ({
  useDataSourcesPage: () => ({
    data: {
      items: [
        { name: "数据源", binding: { kind: "ROBOT", robotId: "robot-a" } },
      ],
    },
  }),
}));
vi.mock("../../../features/datasets/api/hooks", () => ({
  useDatasetsQuery: () => ({
    data: { items: [{ datasetId: "dataset_alpha", name: "原始数据集" }] },
    isPending: false,
    isError: false,
  }),
}));

const selection: LocalUploadSelection = {
  sourceType: "BROWSER_MULTIPART",
  browserSelectionMode: "folder",
  folderName: "lerobot-source",
  files: [],
  manifestFiles: [],
  rawFiles: [],
  units: [],
  lerobot: {
    format: "lerobot",
    version: "v3.0",
    robotType: "unitree_g1",
    rootDirectory: "lerobot-source",
    info: {
      total_episodes: 1,
      robot_type: "generic",
      features: {
        "observation.state": {
          dtype: "float64",
          names: ["x", "y"],
          shape: [2],
        },
        action: { dtype: "float64", names: ["x", "y"], shape: [2] },
      },
    },
    episodeCount: 1,
    sourceFiles: [],
    sourceBytes: 128,
  },
  totalLocalBytes: 128,
  declaredRawBytes: 128,
  objectStorageUri: "",
  problems: [],
};

const scope = {
  organizationId: "org-alpha",
  projectId: "project-alpha",
  regionCode: "cn-shanghai",
};
let capability: LeRobotCapabilities;
function renderDialog() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <UploadConfirmationDialog
        open
        selection={selection}
        scope={{
          organizationId: "org-alpha",
          projectId: "project-alpha",
          regionCode: "cn-shanghai",
        }}
        canManage
        online
        onCancel={vi.fn()}
        onConfirm={onConfirmMock}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  requestMock.mockReset();
  capability = {
    schema_version: "lerobot-capabilities/v1",
    capability_revision: "server-profile/7",
    info_sha256: "a".repeat(64),
    validation_scope: "DECLARED_METADATA",
    storage_supported: true,
    processing_supported: false,
    profile_id: null,
    output_storage_format: null,
    reasons: [
      {
        stage: "PROCESSING",
        code: "LEROBOT_PROFILE_UNSUPPORTED",
        detail: "当前处理器不支持此数据",
      },
    ],
  };
  requestMock.mockImplementation(async ({ path }: { path: string }) => {
    if (path.endsWith("/robot-options"))
      return [
        {
          robot_id: "robot-a",
          display_name: "采集机器人",
          serial_no: "serial-a",
        },
      ];
    if (path.endsWith(":capabilities")) return capability;
    if (path.endsWith("/configuration")) return { configured: false };
    throw new Error(`Unexpected request: ${path}`);
  });
  const store = useShellStore.getState();
  store.setSession(
    { actorId: "uploader", displayName: "Uploader", roleIds: [] },
    "test-session",
  );
  store.setSessionScopes(
    [
      {
        ...scope,
        regionCodes: [scope.regionCode],
        projectWide: false,
        capabilities: ["upload.manage"],
      },
    ],
    1,
  );
  store.setScope(scope);
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
  const computedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    computedStyle(element),
  );
});

afterEach(() => {
  cleanup();
  useShellStore.getState().clearSensitiveState();
  vi.clearAllMocks();
});

describe("UploadConfirmationDialog original storage", () => {
  it("accepts a dataset without a collection task, robot or processing configuration", async () => {
    const user = userEvent.setup();
    renderDialog();
    const confirm = screen.getByRole("button", { name: "确认上传" });
    expect(confirm).toBeDisabled();
    await user.click(
      await screen.findByRole("combobox", { name: "目标数据集 ID" }),
    );
    await user.click(await screen.findByText("原始数据集"));
    expect(screen.queryByRole("combobox", { name: "采集任务 ID" })).toBeNull();
    expect(
      screen.queryByRole("combobox", { name: "目标机器人 ID" }),
    ).toBeNull();
    await waitFor(() => expect(confirm).toBeEnabled());
    await user.click(confirm);
    expect(onConfirmMock).toHaveBeenCalledWith({
      datasetId: "dataset_alpha",
      collectionTaskId: null,
      robotId: null,
      processingMode: "STORE_ONLY",
      processingCapabilityRevision: "server-profile/7",
    });
  });

  it("uses a server-supported profile without requiring annotation and freezes its revision", async () => {
    capability = {
      ...capability,
      processing_supported: true,
      profile_id: "generic-2-axis",
      output_storage_format: "lerobot_v3",
      reasons: [],
    };
    const user = userEvent.setup();
    renderDialog();
    await waitFor(() => expect(screen.getByText("支持自动处理")).toBeVisible());
    expect(screen.getByRole("button", { name: "确认上传" })).toBeDisabled();
    await user.click(screen.getByRole("combobox", { name: "采集任务 ID" }));
    await user.click(await screen.findByText("合成采集 · task-a"));
    await user.click(screen.getByRole("combobox", { name: "目标机器人 ID" }));
    await user.click(
      await screen.findByText("采集机器人 · serial-a · robot-a"),
    );
    const confirm = screen.getByRole("button", { name: "确认上传" });
    await waitFor(() => expect(confirm).toBeEnabled());
    expect(screen.getByText(/标注待配置/)).toBeVisible();
    await user.click(confirm);
    expect(onConfirmMock).toHaveBeenCalledWith({
      datasetId: "dataset_alpha",
      collectionTaskId: "task-a",
      robotId: "robot-a",
      processingMode: "PROCESS",
      processingCapabilityRevision: "server-profile/7",
    });
    expect(requestMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "POST",
        path: "/projects/project-alpha/regions/cn-shanghai/lerobot-imports:capabilities",
        body: { info: selection.lerobot!.info },
        scope,
        scopeMode: "explicit",
      }),
    );
  });

  it("blocks unavailable capability checks and can retry without silently choosing storage", async () => {
    requestMock.mockRejectedValueOnce(new Error("检测服务不可用"));
    const user = userEvent.setup();
    renderDialog();
    await waitFor(() =>
      expect(screen.getByText("检测服务不可用")).toBeVisible(),
    );
    await user.click(screen.getByRole("combobox", { name: "目标数据集 ID" }));
    await user.click(await screen.findByText("原始数据集"));
    expect(screen.getByRole("button", { name: "确认上传" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "重试能力检查" }));
    expect(
      await screen.findByText("可保存原始文件，暂不支持自动处理"),
    ).toBeVisible();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "确认上传" })).toBeEnabled(),
    );
  });

  it("keeps unsupported storage blocked even after a dataset is selected", async () => {
    capability = { ...capability, storage_supported: false };
    const user = userEvent.setup();
    renderDialog();
    await waitFor(() =>
      expect(screen.getByText("暂不支持保存此格式")).toBeVisible(),
    );
    await user.click(screen.getByRole("combobox", { name: "目标数据集 ID" }));
    await user.click(await screen.findByText("原始数据集"));
    expect(screen.getByRole("button", { name: "确认上传" })).toBeDisabled();
    expect(onConfirmMock).not.toHaveBeenCalled();
  });
});
