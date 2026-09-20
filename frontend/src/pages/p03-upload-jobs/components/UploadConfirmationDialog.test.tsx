// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LocalUploadSelection } from "../upload-flow";
import { UploadConfirmationDialog } from "./UploadConfirmationDialog";

const { onConfirmMock } = vi.hoisted(() => ({ onConfirmMock: vi.fn() }));
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
});

afterEach(() => {
  cleanup();
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
    });
  });
});
