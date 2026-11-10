// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { TaskUsage } from "../../features/storage-overview/task-usage-api";
import {
  TaskStorageDashboard,
  StorageFlow,
  LocationOverview,
  storageBytes,
} from "./TaskStoragePage";

vi.mock("../../features/storage-overview/task-usage-api", () => ({
  useTaskUsage: vi.fn(),
  useTaskFiles: () => ({
    isPending: false,
    isError: false,
    data: {
      items: [
        {
          file_id: "opaque-internal-file-id",
          category: "ANNOTATION",
          name: "标注结果 · 第 2 版.json",
          relative_path: "labels.json",
          record_name: "成片 001",
          robot_name: "X1",
          status: "标注文件",
          bytes: "2048",
          updated_at: "2026-10-28T02:00:00Z",
          related_task_names: ["抓取水瓶"],
          shared: false,
        },
      ],
      total_count: 1,
      total_bytes: "2048",
    },
  }),
}));
afterEach(cleanup);
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi
    .fn()
    .mockImplementation((query) => ({
      matches: false,
      media: query,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() {},
    })),
});
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);
const computed = window.getComputedStyle.bind(window);
vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
  computed(element),
);

const categories: TaskUsage["categories"] = [
  { category: "RAW", bytes: "1024", file_count: 1 },
  { category: "EPISODE", bytes: "4096", file_count: 2 },
  { category: "PROCESSING", bytes: "0", file_count: 0 },
  { category: "ANNOTATION", bytes: "2048", file_count: 1 },
  { category: "EXPORT", bytes: "8192", file_count: 1 },
  { category: "OTHER", bytes: "0", file_count: 0 },
];
const data: TaskUsage = {
  project_id: "project-a",
  observed_at: "2026-10-28T02:00:00Z",
  total_bytes: "15360",
  file_count: 5,
  categories,
  tasks: [
    {
      group_id: "task:opaque-task-id",
      task_code: "00000024",
      task_name: "抓取水瓶",
      total_bytes: "15360",
      file_count: 5,
      categories,
      raw_count: 1,
      episode_count: 2,
      annotation_complete_count: 1,
      export_count: 1,
      shared_bytes: "0",
    },
  ],
  disk: {
    total_bytes: "1048576",
    used_bytes: "524288",
    available_bytes: "500000",
    reserved_bytes: "24288",
  },
  database_bytes: "65536",
  storage_locations: [],
  warnings: [],
};

describe("task storage usage", () => {
  it("binds relationship nodes to file bytes and keeps annotation records separate", () => {
    render(<StorageFlow data={data} />);
    expect(screen.getByText("4 KiB")).toBeVisible();
    expect(screen.getByText("2 KiB")).toBeVisible();
    expect(screen.getByText("8 KiB")).toBeVisible();
    expect(screen.getByText(/不复制整份视频/)).toBeVisible();
    expect(storageBytes("1152921504606846976")).toBe("1024 PiB");
  });
  it("opens searchable files on the same page without exposing internal object codes", async () => {
    const before = window.location.href;
    render(
      <TaskStorageDashboard
        data={data}
        scope={{
          organizationId: "organization-a",
          projectId: "project-a",
          regionCode: "global",
        }}
      />,
    );
    expect(screen.getByText("1 / 2 条")).toBeVisible();
    expect(screen.getByText("1 条待完成")).toBeVisible();
    expect(screen.queryByText(/物理实例/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "查看文件" }));
    expect(
      await screen.findByRole("textbox", { name: "搜索任务文件" }),
    ).toBeVisible();
    expect(screen.getByText("标注结果 · 第 2 版.json")).toBeVisible();
    expect(
      screen.queryByText("opaque-internal-file-id"),
    ).not.toBeInTheDocument();
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(window.location.href).toBe(before);
  });
});

it("separates local and OSS purposes, keeps unknown usage distinct from zero", () => {
  render(<LocationOverview data={{...data, storage_locations: [
    {location: "LOCAL", label: "本地服务器", scope: "本地目录及数据库", total_bytes: "1024", complete: false,
      categories: [{category: "database", label: "当前版本数据库", bytes: "1024", retention: "KEEP", description: "含模型内容"},
        {category: "cache", label: "处理缓存", bytes: null, retention: "CONDITIONAL", description: "任务完成后回收"}], note: "不等于整盘占用"},
    {location: "OSS", label: "阿里云 OSS", scope: "oss://bucket/prefix/", total_bytes: "4096", complete: true,
      categories: [{category: "episodes", label: "正式入库数据", bytes: "4096", retention: "KEEP", description: "长期保留"}], note: "仅当前目录"}
  ]}} />);
  expect(screen.getByRole("region", {name: "本地服务器数据分类"})).toHaveTextContent("1 KiB");
  expect(screen.getByRole("region", {name: "阿里云 OSS数据分类"})).toHaveTextContent("4 KiB");
  expect(screen.getAllByText("未取得").length).toBeGreaterThan(0);
  expect(screen.queryByText("0 B")).not.toBeInTheDocument();
  expect(screen.queryByText("labels.json")).not.toBeInTheDocument();
});
