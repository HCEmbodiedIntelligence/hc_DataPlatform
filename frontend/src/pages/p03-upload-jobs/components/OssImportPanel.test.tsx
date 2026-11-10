// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { OssImportPanel } from "./OssImportPanel";
const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }));
vi.mock("../../../shared/api/http-client", () => ({ request: requestMock }));
vi.mock("../../../features/ingest/upload/lerobot-capabilities", () => ({
  useLeRobotCapabilities: () => ({ data: undefined }),
}));
vi.mock("./UploadConfirmationDialog", () => ({
  OriginalDatasetField: (props: { onChange: (id: string) => void }) => (
    <button onClick={() => props.onChange("archive")}>选择原始数据集</button>
  ),
}));
vi.mock(
  "../../../features/ingest/processing/components/ProcessingTargetFields",
  () => ({
    ProcessingTargetFields: (props: {
      onDatasetIdChange: (id: string) => void;
      onCollectionTaskIdChange: (id: string) => void;
      onRobotIdChange: (id: string) => void;
    }) => (
      <button
        onClick={() => {
          props.onDatasetIdChange("dataset");
          props.onCollectionTaskIdChange("task");
          props.onRobotIdChange("robot");
        }}
      >
        选择处理目标
      </button>
    ),
  }),
);
const scope = { organizationId: "org", projectId: "project", regionCode: "cn" };
const preview = {
  source_uri: "oss://bucket/root/input/",
  source_format: "MCAP",
  info: {},
  inventory_sha256: "a".repeat(64),
  total_bytes: 4,
  files: [{ path: "one.mcap", size: 4 }],
};
afterEach(cleanup);
beforeEach(() => {
  requestMock.mockReset();
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});
function setup(disabled = false) {
  const onImported = vi.fn();
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <OssImportPanel
        scope={scope}
        disabled={disabled}
        onImported={onImported}
      />
    </QueryClientProvider>,
  );
  return onImported;
}
it("registers OSS references with the normal processing target without uploading files", async () => {
  requestMock
    .mockResolvedValueOnce(preview)
    .mockResolvedValueOnce({ import_id: "id" });
  const onImported = setup();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "从 OSS 导入" }));
  await user.type(
    screen.getByLabelText("OSS 文件或目录地址"),
    preview.source_uri,
  );
  await user.click(screen.getByRole("button", { name: "检查 OSS 数据" }));
  await screen.findByText(/已识别 MCAP/);
  expect(screen.getByRole("button", { name: "确认导入" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "选择处理目标" }));
  await user.click(screen.getByRole("button", { name: "确认导入" }));
  await waitFor(() => expect(onImported).toHaveBeenCalledOnce());
  expect(requestMock).toHaveBeenCalledTimes(2);
  expect(requestMock.mock.calls[1]?.[0]).toMatchObject({
    path: expect.stringContaining("/oss:commit"),
    body: {
      source_uri: preview.source_uri,
      inventory_sha256: preview.inventory_sha256,
      processing_mode: "PROCESS",
      dataset_id: "dataset",
      collection_task_id: "task",
      robot_id: "robot",
    },
  });
});
it("clears the checked inventory when the source address changes", async () => {
  requestMock.mockResolvedValue(preview);
  setup();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "从 OSS 导入" }));
  await user.type(
    screen.getByLabelText("OSS 文件或目录地址"),
    preview.source_uri,
  );
  await user.click(screen.getByRole("button", { name: "检查 OSS 数据" }));
  await screen.findByText(/已识别 MCAP/);
  await user.type(screen.getByLabelText("OSS 文件或目录地址"), "other/");
  expect(screen.queryByText(/已识别 MCAP/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "确认导入" })).toBeDisabled();
});
it("disables OSS import without upload permission or connectivity", () => {
  setup(true);
  expect(screen.getByRole("button", { name: "从 OSS 导入" })).toBeDisabled();
  expect(requestMock).not.toHaveBeenCalled();
});

it("shows task selection immediately and preserves it for archive-only imports", async () => {
  requestMock
    .mockResolvedValueOnce({ ...preview, archive: { path: "capture.zip" } })
    .mockResolvedValueOnce({ import_id: "id" });
  const onImported = setup();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "从 OSS 导入" }));
  await user.click(screen.getByRole("button", { name: "选择处理目标" }));
  await user.type(
    screen.getByLabelText("OSS 文件或目录地址"),
    "oss://bucket/root/capture.zip",
  );
  await user.click(screen.getByRole("button", { name: "检查 OSS 数据" }));
  await screen.findByText(/已识别 MCAP/);
  await user.click(screen.getByRole("radio", { name: "仅登记原始数据" }));
  expect(
    screen.getByRole("button", { name: "选择处理目标" }),
  ).toBeInTheDocument();
  expect(screen.getByText("ZIP 将自动解压导入")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "确认导入" }));
  await waitFor(() => expect(onImported).toHaveBeenCalledOnce());
  expect(requestMock.mock.calls[1]?.[0].body).toMatchObject({
    dataset_id: "dataset",
    collection_task_id: "task",
    robot_id: "robot",
    processing_mode: "STORE_ONLY",
  });
});

it("requires task association for ROS bag imports without processing support", async () => {
  requestMock.mockResolvedValueOnce({ ...preview, source_format: "ROSBAG" });
  setup();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "从 OSS 导入" }));
  await user.type(
    screen.getByLabelText("OSS 文件或目录地址"),
    preview.source_uri,
  );
  await user.click(screen.getByRole("button", { name: "检查 OSS 数据" }));
  await screen.findByText(/已识别 ROSBAG/);
  expect(screen.getByRole("button", { name: "确认导入" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "选择处理目标" }));
  expect(screen.getByRole("button", { name: "确认导入" })).toBeEnabled();
});
