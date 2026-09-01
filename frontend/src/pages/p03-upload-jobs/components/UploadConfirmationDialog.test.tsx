// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LocalUploadSelection } from "../upload-flow";
import { UploadConfirmationDialog } from "./UploadConfirmationDialog";

const { listActiveCollectionTasksMock, onConfirmMock } = vi.hoisted(() => ({
  listActiveCollectionTasksMock: vi.fn(),
  onConfirmMock: vi.fn(),
}));

vi.mock("../lerobot-targets", () => ({
  listActiveCollectionTasks: listActiveCollectionTasksMock,
}));

vi.mock("../../../features/ingest/api", () => ({
  useDataSourcesPage: () => ({
    data: {
      items: [
        {
          id: "source-alpha",
          name: "装配工位机器人",
          binding: {
            kind: "ROBOT",
            robotId: "robot-alpha",
            displayName: "装配机器人 A",
          },
        },
      ],
    },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
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
    info: { total_episodes: 1 },
    episodeCount: 1,
    sourceFiles: [],
    sourceBytes: 128,
  },
  totalLocalBytes: 128,
  declaredRawBytes: 128,
  objectStorageUri: "",
  problems: [],
};

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
  listActiveCollectionTasksMock.mockResolvedValue([
    {
      schema_version: "1",
      collection_task_id: "collection-task-alpha",
      organization_id: "org-alpha",
      project_id: "project-alpha",
      dataset_id: "dataset_alpha",
      task_code: "00000001",
      name: "透明件抓取采集",
      type: "抓取采集",
      scenario: "装配工位",
      description: "测试任务",
      target: null,
      quality_threshold: null,
      status: "ACTIVE",
    },
    {
      schema_version: "1",
      collection_task_id: "collection-task-beta",
      organization_id: "org-alpha",
      project_id: "project-alpha",
      dataset_id: "dataset_beta",
      task_code: "00000002",
      name: "搬运采集",
      type: "搬运采集",
      scenario: "仓储通道",
      description: "另一个数据集的任务",
      target: null,
      quality_threshold: null,
      status: "ACTIVE",
    },
  ]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("UploadConfirmationDialog LeRobot targets", () => {
  it("requires linked dropdown choices instead of accepting free-form IDs", async () => {
    const user = userEvent.setup();
    renderDialog();

    const confirm = screen.getByRole("button", { name: "确认上传" });
    expect(confirm).toBeDisabled();

    const dataset = await screen.findByRole("combobox", {
      name: "目标数据集 ID",
    });
    await user.click(dataset);
    await user.click(await screen.findByText("dataset_alpha · 1 个可用任务"));

    const collectionTask = screen.getByRole("combobox", {
      name: "采集任务 ID",
    });
    await waitFor(() => expect(collectionTask).toBeEnabled());
    await user.click(collectionTask);
    expect(
      await screen.findByRole("option", { name: /collection-task-alpha/u }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: /collection-task-beta/u }),
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByText("透明件抓取采集 · collection-task-alpha"),
    );

    const robot = screen.getByRole("combobox", { name: "目标机器人 ID" });
    await user.click(robot);
    await user.click(
      await screen.findByText("装配工位机器人 · 装配机器人 A · robot-alpha"),
    );

    await waitFor(() => expect(confirm).toBeEnabled());
    await user.click(confirm);

    expect(onConfirmMock).toHaveBeenCalledWith({
      datasetId: "dataset_alpha",
      collectionTaskId: "collection-task-alpha",
      robotId: "robot-alpha",
    });
  });
});
