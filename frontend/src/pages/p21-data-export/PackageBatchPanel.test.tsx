// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { PackageBatchPanel } from "./PackageBatchPanel";
import {
  createPackageBatch,
  downloadEpisodeBundle,
  loadUploadCandidates,
  type PackageExportFilter,
  downloadPackageJob,
  listPackageJobs,
  loadExportFormats,
  loadPackageCandidates,
  loadPackageJob,
  loadCollectionAnnotationHistory,
  type PackageJob,
} from "../../features/exports/package-deliveries";
import { loadEpisodeOverview } from "../../features/datasets/episodes/episode-detail";
import { episodeDetailFixture } from "../p06-dataset-detail/testing/episode-detail-fixture";
import { packageDeliveryFixture } from "./testing/package-delivery-fixture";

vi.mock("../../features/exports/package-deliveries", () => ({
  createPackageBatch: vi.fn(),
  listPackageJobs: vi.fn(),
  loadExportFormats: vi.fn(),
  loadPackageCandidates: vi.fn(),
  loadPackageJob: vi.fn(),
  commandPackageJob: vi.fn(),
  downloadEpisodeBundle: vi.fn(),
  loadUploadCandidates: vi.fn(),
  downloadPackageJob: vi.fn(),
  loadCollectionAnnotationHistory: vi.fn(),
}));
vi.mock("../../features/datasets/episodes/episode-detail", () => ({
  loadEpisodeOverview: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.resetAllMocks();
});
function mount(annotated = false, canDownload = true, filter?: PackageExportFilter) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockReturnValue({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  });
  const f = packageDeliveryFixture(annotated);
  vi.mocked(loadPackageCandidates).mockResolvedValue(f.candidates);
  vi.mocked(loadUploadCandidates).mockResolvedValue({
    total: 1,
    items: [{ data: f.data, batch_numbers: [882] }],
    next_after: null,
  });
  vi.mocked(listPackageJobs).mockResolvedValue([]);
  vi.mocked(loadExportFormats).mockResolvedValue([]);
  vi.mocked(loadPackageJob).mockResolvedValue(f.job);
  vi.mocked(downloadPackageJob).mockResolvedValue({
    job_id: f.job.job_id,
    artifact_sha256: f.job.artifact_sha256!,
    size_bytes: f.job.size_bytes!,
    url: "https://objects.example/export.zip",
    expires_seconds: 300,
  });
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  const overview = episodeDetailFixture().view;
  vi.mocked(loadEpisodeOverview).mockResolvedValue({
    ...overview,
    data: f.data,
    annotation: {
      ...overview.annotation,
      availability: "AVAILABLE",
      task_id: f.annotation.task.task_id,
    },
  });
  vi.mocked(loadCollectionAnnotationHistory).mockResolvedValue(
    { ...f.annotation.history, task: { ...f.annotation.history.task,
      status: "APPROVED", approved_revision: f.annotation.revision.revision } },
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rendered = render(
    <QueryClientProvider client={client}>
      <PackageBatchPanel
        scope={f.scope}
        datasets={[{ id: f.data.identity.dataset_id, name: "演示数据" }]}
        filter={filter}
        annotated={annotated}
        canReadAnnotations={true}
        canCreate={true}
        canDownload={canDownload}
      />
    </QueryClientProvider>,
  );
  return { ...f, client, click, unmount: rendered.unmount };
}
it("exports the entire QC selection as one HoloBrain collection", async () => {
  const { data, scope } = packageDeliveryFixture();
  const second = { ...data, data_version_id: "dv_" + "2".repeat(64),
    identity: { ...data.identity, episode_id: "episode-second" } };
  const filter = { stage: "qc_passed" as const, batch_numbers: [882] };
  vi.mocked(loadExportFormats).mockResolvedValueOnce([{
    id: "holobrain", title: "HoloBrain", archive_format: "holobrain_collection_zip/6",
    supports_annotations: true, requires_originals: false,
  }]);
  vi.mocked(loadUploadCandidates)
    .mockResolvedValueOnce({ total: 2, items: [{ data, batch_numbers: [882] }], next_after: "next" })
    .mockResolvedValueOnce({ total: 2, items: [{ data: second, batch_numbers: [882] }], next_after: null });
  mount(false, true, filter);
  const user = userEvent.setup();
  await user.click((await screen.findByRole("radio", { name: "HoloBrain" })).closest("label")!);
  await user.click(await screen.findByRole("checkbox", { name: "全选筛选结果" }));
  await user.click(screen.getByRole("button", { name: "导出" }));
  await waitFor(() => expect(createPackageBatch).toHaveBeenCalledTimes(1));
  const [selectedScope, members, mode, , , selectedFilter] = vi.mocked(createPackageBatch).mock.calls[0]!;
  expect([selectedScope, mode, selectedFilter]).toEqual([scope, "holobrain", filter]);
  expect(members.map((member) => member.data)).toEqual(expect.arrayContaining([data, second]));
  expect(members).toHaveLength(2);
  expect(downloadEpisodeBundle).not.toHaveBeenCalled();
});

it("preserves the QC selection when the save picker closes and reports transfer failures on retry", async () => {
  const f = mount(false, true, { stage: "qc_passed", batch_numbers: [882] });
  const user = userEvent.setup();
  const episode = await screen.findByRole("checkbox", { name: new RegExp(f.data.identity.episode_id) });
  await user.click(episode);
  await user.click(screen.getByRole("radio", { name: /MCAP/ }).closest("label")!);
  const button = screen.getByRole("button", { name: "导出" });
  vi.mocked(downloadEpisodeBundle)
    .mockResolvedValueOnce({ kind: "cancelled" })
    .mockRejectedValueOnce(new Error("连接中断"))
    .mockResolvedValueOnce({ kind: "saved", bytes: 1024, filename: "Episodes.zip" });
  await user.click(button);
  expect(await screen.findByText(/尚未选择保存位置/)).toBeVisible();
  expect(screen.queryByText("导出操作失败")).toBeNull();
  expect(episode).toBeChecked();
  await user.click(button);
  expect(await screen.findByText("连接中断")).toBeVisible();
  await user.click(button);
  expect(await screen.findByText("已完成 1 条 Episode 的打包下载")).toBeVisible();
  for (const [, members, mode] of vi.mocked(downloadEpisodeBundle).mock.calls) {
    expect(members).toEqual([f.data]);
    expect(mode).toBe("mcap");
  }
});

it("shows RGB depth exclusion and retains the key after an interrupted request", async () => {
  const f = mount();
  vi.mocked(createPackageBatch)
    .mockRejectedValueOnce(new Error("响应中断"))
    .mockResolvedValue(f.job);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("checkbox", {
      name: new RegExp(f.data.identity.episode_id),
    }),
  );
  await user.click(
    screen
      .getByRole("radio", { name: "LeRobot RGB 子集（排除深度）" })
      .closest("label")!,
  );
  expect(
    screen.getByRole("radio", { name: "LeRobot RGB 子集（排除深度）" }),
  ).toBeChecked();
  const create = screen.getByRole("button", { name: "导出" });
  await user.click(create);
  expect(await screen.findByText("响应中断")).toBeVisible();
  await user.click(create);
  await waitFor(() => expect(createPackageBatch).toHaveBeenCalledTimes(2));
  const calls = vi.mocked(createPackageBatch).mock.calls;
  expect(calls[0]![2]).toBe("rgb");
  expect(calls[0]![3]).toBe(calls[1]![3]);
  expect(calls[0]![1]).toEqual([{ data: f.data, annotation: null }]);
});
it("requests only completed candidates and exports approved annotations after selecting an Episode", async () => {
  const f = mount(true),
    user = userEvent.setup();
  await user.click(
    await screen.findByRole("checkbox", {
      name: new RegExp(f.data.identity.episode_id),
    }),
  );
  const create = screen.getByRole("button", { name: "导出" });
  await waitFor(() => expect(create).toBeEnabled());
  expect(loadPackageCandidates).toHaveBeenCalledWith(f.scope, f.data.identity.dataset_id, expect.any(AbortSignal), undefined, "annotated");
  expect(screen.getByText("标注完成 1 条 · 已选 1 条")).toBeVisible();
  vi.mocked(createPackageBatch).mockResolvedValue(f.job);
  await user.click(create);
  await waitFor(() =>
    expect(createPackageBatch).toHaveBeenCalledWith(
      f.scope,
      [
        {
          data: f.data,
          annotation: expect.objectContaining({
            task_id: f.annotation.task.task_id,
            revision: f.annotation.revision.revision,
            allow_unreviewed: false,
            track: expect.objectContaining({ annotation_ids: [] }),
          }),
        },
      ],
      "rgbd",
      expect.any(String),
      expect.any(AbortSignal),
      undefined,
    ),
  );
  expect(
    screen.getByRole("radio", { name: "独立标准包批量 ZIP（含已采集深度）" }),
  ).toBeDisabled();
});
it("keeps export disabled if the selected Episode's annotation cannot be loaded", async () => {
  const f = mount(true),
    user = userEvent.setup();
  vi.mocked(loadCollectionAnnotationHistory).mockRejectedValue(
    new Error("读取失败"),
  );
  await user.click(
    await screen.findByRole("checkbox", {
      name: new RegExp(f.data.identity.episode_id),
    }),
  );
  expect(await screen.findByText("标注读取失败")).toBeVisible();
  expect(screen.getByRole("button", { name: "导出" })).toBeDisabled();
  expect(createPackageBatch).not.toHaveBeenCalled();
});
it("does not fetch candidates or cached jobs without download permission", () => {
  mount(false, false);
  expect(loadPackageCandidates).not.toHaveBeenCalled();
  expect(listPackageJobs).not.toHaveBeenCalled();
  expect(loadPackageJob).not.toHaveBeenCalled();
});

function withoutDepth(job: PackageJob): PackageJob {
  if (job.plan.format !== "hc-collection-delivery-plan/1")
    throw new Error("fixture");
  return {
    ...job,
    job_id: "ed_" + "d".repeat(64),
    plan: {
      ...job.plan,
      modalities: "rgb",
      members: job.plan.members.map((member) => ({
        ...member,
        selected_depth_profiles: {},
        excluded_depth_features: Object.keys(member.selected_depth_profiles),
      })),
    },
  };
}

it("exports the current RGB selection and automatically downloads that job once, leaving old RGB-D archives alone", async () => {
  const f = mount(),
    user = userEvent.setup();
  const ready = withoutDepth(f.job);
  const queued: PackageJob = { ...ready, status: "PENDING", phase: "queued" };
  vi.mocked(listPackageJobs).mockResolvedValue([f.job]);
  vi.mocked(createPackageBatch).mockResolvedValue(queued);
  let complete!: (job: PackageJob) => void;
  vi.mocked(loadPackageJob).mockImplementation(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  await user.click(
    await screen.findByRole("checkbox", {
      name: new RegExp(f.data.identity.episode_id),
    }),
  );
  await user.click(
    screen
      .getByRole("radio", { name: "LeRobot RGB 子集（排除深度）" })
      .closest("label")!,
  );
  const button = screen.getByRole("button", { name: "导出" });
  await user.click(button);
  await waitFor(() => expect(loadPackageJob).toHaveBeenCalled());
  expect(button).toBeDisabled();
  expect(downloadPackageJob).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("button", { name: "下载 ZIP" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "开始导出" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText("已排除 1 项深度数据")).toBeVisible();
  await act(async () => {
    complete(ready);
  });
  await waitFor(() =>
    expect(downloadPackageJob).toHaveBeenCalledExactlyOnceWith(
      f.scope,
      ready,
      expect.any(AbortSignal),
      undefined,
    ),
  );
  expect(f.click).toHaveBeenCalledTimes(1);
  expect(
    await screen.findByText(
      "LeRobot RGB（不含深度）已生成，已向浏览器请求下载。",
    ),
  ).toBeVisible();
  await user.click(screen.getByRole("button", { name: "刷新状态" }));
  await waitFor(() => expect(button).toBeEnabled());
  expect(downloadPackageJob).toHaveBeenCalledTimes(1);
  expect(createPackageBatch).toHaveBeenCalledExactlyOnceWith(
    f.scope,
    [{ data: f.data, annotation: null }],
    "rgb",
    expect.any(String),
    expect.any(AbortSignal),
    undefined,
  );
});

it("keeps a native download link when automatic downloads are blocked, without claiming the file was saved", async () => {
  const f = mount(),
    user = userEvent.setup();
  vi.mocked(createPackageBatch).mockResolvedValue(f.job);
  await user.click(
    await screen.findByRole("checkbox", {
      name: new RegExp(f.data.identity.episode_id),
    }),
  );
  await user.click(screen.getByRole("button", { name: "导出" }));
  const link = await screen.findByRole("link", { name: "点击下载 ZIP" });
  expect(link).toHaveAttribute("href", "https://objects.example/export.zip");
  expect(link).toHaveAttribute("download", "");
  expect(link).toHaveAttribute("target", "_blank");
  expect(screen.getByText("若下载列表中没有记录，请点击下载：")).toBeVisible();
  expect(
    screen.queryByText(/文件已开始下载|下载完成|下载成功/),
  ).not.toBeInTheDocument();
  // The fallback is an ordinary link: a real click does not await another API call.
  await user.click(link);
  expect(downloadPackageJob).toHaveBeenCalledTimes(1);
  expect(createPackageBatch).toHaveBeenCalledTimes(1);
});

it("automatically downloads an already prepared export and offers a retry if download authorization fails", async () => {
  const f = mount(),
    user = userEvent.setup();
  vi.mocked(createPackageBatch).mockResolvedValue(f.job);
  vi.mocked(listPackageJobs).mockResolvedValue([f.job]);
  vi.mocked(downloadPackageJob).mockRejectedValueOnce(
    new Error("下载授权暂时失败"),
  );
  await user.click(
    await screen.findByRole("checkbox", {
      name: new RegExp(f.data.identity.episode_id),
    }),
  );
  await user.click(screen.getByRole("button", { name: "导出" }));
  expect(
    await screen.findByText("文件未能开始下载，请在导出记录中重新下载"),
  ).toBeVisible();
  expect(f.click).not.toHaveBeenCalled();
  await user.click(await screen.findByRole("button", { name: "重新下载" }));
  await waitFor(() => expect(f.click).toHaveBeenCalledTimes(1));
  expect(createPackageBatch).toHaveBeenCalledTimes(1);
  expect(downloadPackageJob).toHaveBeenCalledTimes(2);
});

it.each(["FAILED", "CANCELLED"] as const)(
  "does not download a %s export",
  async (status) => {
    const f = mount(),
      user = userEvent.setup();
    const queued: PackageJob = { ...f.job, status: "PENDING", phase: "queued" };
    const stopped: PackageJob = {
      ...f.job,
      status,
      phase: status === "FAILED" ? "failed" : "cancelled",
      error_code: status === "FAILED" ? "EXPORT_FAILED" : null,
    };
    vi.mocked(createPackageBatch).mockResolvedValue(queued);
    vi.mocked(loadPackageJob).mockResolvedValue(stopped);
    vi.mocked(listPackageJobs).mockResolvedValue([stopped]);
    await user.click(
      await screen.findByRole("checkbox", {
        name: new RegExp(f.data.identity.episode_id),
      }),
    );
    const button = screen.getByRole("button", { name: "导出" });
    await user.click(button);
    await waitFor(() => expect(loadPackageJob).toHaveBeenCalled());
    await waitFor(() => expect(button).toBeEnabled());
    expect(downloadPackageJob).not.toHaveBeenCalled();
    expect(f.click).not.toHaveBeenCalled();
  },
);

it("stops watching for automatic downloads when leaving the page", async () => {
  const f = mount(),
    user = userEvent.setup();
  vi.mocked(createPackageBatch).mockResolvedValue({
    ...f.job,
    status: "PENDING",
    phase: "queued",
  });
  let complete!: (job: PackageJob) => void;
  vi.mocked(loadPackageJob).mockImplementation(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  await user.click(
    await screen.findByRole("checkbox", {
      name: new RegExp(f.data.identity.episode_id),
    }),
  );
  await user.click(screen.getByRole("button", { name: "导出" }));
  await waitFor(() => expect(loadPackageJob).toHaveBeenCalled());
  f.unmount();
  await act(async () => {
    complete(f.job);
  });
  expect(downloadPackageJob).not.toHaveBeenCalled();
  expect(f.click).not.toHaveBeenCalled();
});
