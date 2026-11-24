import { mockSavePicker } from "../../shared/lib/test-save-picker";
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { useShellStore } from "../../shared/scope/shell-store";
import { exportToOss } from "../../features/exports/oss-export";
vi.mock("../../features/exports/oss-export", () => ({ exportToOss: vi.fn() }));
import { clearEpisodeBundleExport } from "../../features/exports/episode-bundle-export";
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
  clearEpisodeBundleExport();
  useShellStore.getState().clearSensitiveState();
  window.localStorage.clear();
  vi.restoreAllMocks();
  vi.resetAllMocks();
  vi.unstubAllGlobals();
});
function mount(annotated = false, canDownload = true, filter?: PackageExportFilter,
  datasets?: { id: string; name: string }[]) {
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
  const { close, picker } = mockSavePicker();
  vi.stubGlobal("fetch", vi.fn(async () => new Response(new Uint8Array(f.job.size_bytes!))));
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
        datasets={datasets ?? [{ id: f.data.identity.dataset_id, name: "演示数据" }]}
        filter={filter}
        annotated={annotated}
        canReadAnnotations={true}
        canCreate={true}
        canDownload={canDownload}
      />
    </QueryClientProvider>,
  );
  return { ...f, client, close, picker, unmount: rendered.unmount };
}
it("publishes the ready collection to the chosen destination and displays the OSS URI", async () => {
  const f = mount();
  const user = userEvent.setup();
  useShellStore.getState().setSession({ actorId: "exporter", displayName: "Exporter", roleIds: [] }, "test-session");
  vi.mocked(listPackageJobs).mockResolvedValue([f.job]);
  const uri = "oss://managed/exports/project/lerobot.zip";
  vi.mocked(createPackageBatch).mockResolvedValue(f.job);
  vi.mocked(exportToOss).mockResolvedValue({ kind: "oss", uri, bytes: 75 });
  await user.click(await screen.findByRole("checkbox", { name: new RegExp(f.data.identity.episode_id) }));
  await user.click(screen.getByRole("radio", { name: "导出到 OSS" }).closest("label")!);
  await user.click(screen.getByRole("button", { name: "导出" }));
  expect(await screen.findByText(uri)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "关闭导出提示" }));
  expect(screen.queryByText("已导出到 OSS")).toBeNull();
  await user.click(await screen.findByRole("button", { name: "复制 OSS 地址" }));
  expect(await navigator.clipboard.readText()).toBe(uri);
  expect(exportToOss).toHaveBeenCalledWith(f.scope, { kind: "job", job_id: f.job.job_id },
    expect.any(AbortSignal), expect.any(Function));
  expect(downloadPackageJob).not.toHaveBeenCalled();
});

it.each([false, true])("shows only the latest QC OSS receipt (earlier bundle still running: %s)", async (overlap) => {
  const filter = { stage: "qc_passed" as const, batch_numbers: [882] };
  vi.mocked(loadExportFormats).mockResolvedValueOnce([{
    id: "holobrain", title: "HoloBrain", archive_format: "holobrain_collection_zip/8",
    supports_annotations: true, requires_originals: false,
  }]);
  const { data } = packageDeliveryFixture();
  const second = { ...data, data_version_id: "dv_" + "2".repeat(64),
    identity: { ...data.identity, episode_id: "episode-second" } };
  vi.mocked(loadUploadCandidates).mockResolvedValueOnce({
    total: 2, next_after: null, items: [data, second].map(item => ({ data: item, batch_numbers: [882] })),
  });
  const f = mount(false, true, filter);
  const bundleUri = "oss://managed/Episodes.zip";
  const jobUri = "oss://managed/HoloBrain.zip";
  const receipt = { kind: "oss" as const, uri: bundleUri, bytes: 10 };
  let finish!: () => void;
  vi.mocked(downloadEpisodeBundle).mockImplementationOnce(
    () => new Promise(resolve => { finish = () => resolve(receipt); }),
  ).mockResolvedValue(receipt);
  vi.mocked(createPackageBatch).mockResolvedValue(f.job);
  vi.mocked(exportToOss).mockResolvedValue({ ...receipt, uri: jobUri });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("checkbox", { name: "全选筛选结果" }));
  await user.click(screen.getByRole("radio", { name: "导出到 OSS" }).closest("label")!);
  const submit = screen.getByRole("button", { name: "导出" });
  await user.click(submit);
  if (!overlap) {
    await act(async () => finish());
    expect(await screen.findByText(bundleUri)).toBeVisible();
  }
  await user.click(screen.getByRole("radio", { name: "HoloBrain" }).closest("label")!);
  await user.click(submit);
  if (overlap) {
    await waitFor(() => expect(createPackageBatch).toHaveBeenCalledOnce());
    await act(async () => finish());
  }
  expect(await screen.findByText(jobUri)).toBeVisible();
  expect(screen.queryByText(bundleUri)).toBeNull();
  expect(screen.getAllByText("已导出到 OSS")).toHaveLength(1);
  expect(createPackageBatch).toHaveBeenCalledTimes(1);
  const [, members, mode] = vi.mocked(createPackageBatch).mock.calls[0]!;
  expect(mode).toBe("holobrain");
  expect(members).toHaveLength(2);
  expect(exportToOss).toHaveBeenCalledTimes(1);
  expect(downloadEpisodeBundle).toHaveBeenCalledTimes(1);

  await user.click(screen.getByRole("radio", { name: /MCAP/ }).closest("label")!);
  await user.click(submit);
  expect(await screen.findByText(bundleUri)).toBeVisible();
  expect(screen.queryByText(jobUri)).toBeNull();
  expect(downloadEpisodeBundle).toHaveBeenCalledTimes(2);
});

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
  expect(await screen.findByText(/下载完成 · 1 条 Episode/)).toBeVisible();
  for (const [, members, mode] of vi.mocked(downloadEpisodeBundle).mock.calls) {
    expect(members).toEqual([f.data]);
    expect(mode).toBe("mcap");
  }
});

it("continues the same Episode bundle across page visits and retains the finished ZIP", async () => {
  const filter = { stage: "qc_passed" as const, batch_numbers: [882] };
  const first = mount(false, true, filter);
  const user = userEvent.setup();
  let finish!: (result: Awaited<ReturnType<typeof downloadEpisodeBundle>>) => void;
  vi.mocked(downloadEpisodeBundle).mockImplementationOnce(
    () => new Promise(resolve => { finish = resolve; }),
  );
  vi.stubGlobal("URL", class extends URL { static revokeObjectURL = vi.fn(); });
  try {
    await user.click(await screen.findByRole("checkbox", { name: /全选筛选结果/ }));
    await user.click(screen.getByRole("radio", { name: /MCAP/ }).closest("label")!);
    await user.click(screen.getByRole("button", { name: "导出" }));
    const [, members, mode, , signal, report] = vi.mocked(downloadEpisodeBundle).mock.calls[0]!;
    expect(members).toEqual([first.data]);
    expect(mode).toBe("mcap");
    first.unmount();
    expect(signal.aborted).toBe(false);
    report("准备 Episode 1/1，已复用 0 条");

    const second = mount(false, true, { stage: "qc_passed" });
    expect(await screen.findByText("准备 Episode 1/1，已复用 0 条")).toBeVisible();
    await user.click(await screen.findByRole("checkbox", { name: new RegExp(first.data.identity.episode_id) }));
    expect(screen.getByRole("button", { name: /导出$/ })).toBeEnabled();
    second.unmount();
    await act(async () => finish({ kind: "saved", filename: "Episodes.zip", bytes: 2048 }));

    mount(false, true, filter);
    expect(await screen.findByText(/下载完成 · 1 条 Episode · Episodes.zip/)).toBeVisible();
    expect(screen.queryByRole("link", { name: "下载 ZIP" })).toBeNull();
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    expect(downloadEpisodeBundle).toHaveBeenCalledTimes(1);
    act(clearEpisodeBundleExport);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
  } finally {
    clearEpisodeBundleExport();
    vi.unstubAllGlobals();
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
    screen.queryByRole("radio", { name: /独立标准包/ }),
  ).not.toBeInTheDocument();
});
it("selects across datasets and pages without a second dataset picker", async () => {
  const f = packageDeliveryFixture(true);
  const other = { ...f.data, data_version_id: "other-version",
    identity: { ...f.data.identity, dataset_id: "other-dataset", episode_id: "other-episode" } };
  const later = { ...other, data_version_id: "later-version",
    identity: { ...other.identity, episode_id: "later-episode" } };
  const second = { ...f.candidates, dataset_id: other.identity.dataset_id,
    collection_version_id: "cv_other", total: 2 };
  vi.mocked(loadPackageCandidates)
    .mockResolvedValueOnce(f.candidates)
    .mockResolvedValueOnce({ ...second, items: [other], next_offset: 1 })
    .mockResolvedValueOnce({ ...second, items: [later] });
  mount(true, true, undefined, [
    { id: f.data.identity.dataset_id, name: "第一数据集" },
    { id: other.identity.dataset_id, name: "第二数据集" },
  ]);
  const user = userEvent.setup();
  expect(await screen.findByText("标注完成 3 条 · 已选 0 条")).toBeVisible();
  expect(screen.queryByRole("combobox", { name: "标准包数据集" })).toBeNull();
  await user.click(screen.getByRole("checkbox", { name: /第一数据集/ }));
  await user.click(screen.getByRole("button", { name: "加载更多数据" }));
  await user.click(await screen.findByRole("checkbox", { name: /later-episode/ }));
  expect(loadPackageCandidates).toHaveBeenCalledTimes(3);
  expect(loadPackageCandidates).toHaveBeenLastCalledWith(f.scope, "other-dataset",
    expect.any(AbortSignal), { collectionId: "cv_other", offset: 1 }, "annotated");
  expect(screen.getByText("标注完成 3 条 · 已选 2 条")).toBeVisible();
  const create = screen.getByRole("button", { name: "导出" });
  await waitFor(() => expect(create).toBeEnabled());
  vi.mocked(createPackageBatch).mockResolvedValue(f.job);
  await user.click(create);
  await waitFor(() => expect(createPackageBatch).toHaveBeenCalledTimes(1));
  expect(vi.mocked(createPackageBatch).mock.calls[0]![1].map(member => member.data))
    .toEqual(expect.arrayContaining([f.data, later]));
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
it("reads job status without fetching download candidates when download is missing", () => {
  mount(false, false);
  expect(loadPackageCandidates).not.toHaveBeenCalled();
  expect(listPackageJobs).toHaveBeenCalled();
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
  expect(button).toBeEnabled();
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
  expect(f.close).toHaveBeenCalledTimes(1);
  expect(
    await screen.findByText(
      /已保存到所选位置：.+\.zip/,
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

it("does not queue, authorize, or download when the file picker is cancelled", async () => {
  const f = mount();
  const user = userEvent.setup();
  f.picker.mockRejectedValueOnce(new DOMException("cancel", "AbortError"));
  await user.click(await screen.findByRole("checkbox", { name: new RegExp(f.data.identity.episode_id) }));
  await user.click(screen.getByRole("button", { name: "导出" }));
  expect(f.picker).toHaveBeenCalledOnce();
  expect(createPackageBatch).not.toHaveBeenCalled();
  expect(downloadPackageJob).not.toHaveBeenCalled();
  expect(f.close).not.toHaveBeenCalled();
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
    await screen.findByText(/下载授权暂时失败/),
  ).toBeVisible();
  expect(f.close).not.toHaveBeenCalled();
  await user.click(await screen.findByRole("button", { name: "重新下载" }));
  await waitFor(() => expect(f.close).toHaveBeenCalledTimes(1));
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
    expect(f.close).not.toHaveBeenCalled();
  },
);

it("continues a selected download when leaving the page", async () => {
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
  await waitFor(() => expect(f.close).toHaveBeenCalledOnce());
  expect(downloadPackageJob).toHaveBeenCalledOnce();
});
it.each(["success", "failure"])("starts a second selection before the first settles (%s), keeping its format and destination", async (outcome) => {
  const f = mount(false, true, { stage: "qc_passed", all_batches: true });
  const user = userEvent.setup();
  const started: string[] = [];
  let finish!: () => void;
  const first = new Promise<void>(resolve => { finish = resolve; });
  vi.mocked(downloadEpisodeBundle).mockImplementation(async (_scope, _members, mode, _filter, _signal, _report, dest, _datasets, enter) => {
    await enter!();
    started.push(`${mode}/${dest}`);
    if (started.length === 1) {
      await first;
      if (outcome === "failure") throw new Error("第一项失败");
    }
    return { kind: "saved", bytes: 4, filename: `${mode}.zip` };
  });
  await user.click(await screen.findByRole("checkbox", { name: new RegExp(f.data.identity.episode_id) }));
  await user.click(screen.getByRole("button", { name: "导出" }));
  await user.click(screen.getByRole("radio", { name: /MCAP/ }).closest("label")!);
  await user.click(screen.getByRole("button", { name: "导出" }));
  await waitFor(() => expect(started).toEqual(["rgbd/zip", "mcap/zip"]));
  expect(await screen.findByText(/下载完成 · 1 条 Episode · mcap.zip/)).toBeVisible();
  await act(async () => finish());
  await waitFor(() => expect(started).toEqual(["rgbd/zip", "mcap/zip"]));
  expect(screen.queryByText("等待前面的导出完成")).toBeNull();
});
