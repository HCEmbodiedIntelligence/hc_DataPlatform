// @vitest-environment jsdom

import { mockSavePicker } from "../../shared/lib/test-save-picker";
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { datasetIds } from "../../mocks/fixtures/datasets/core";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { ProviderHarness } from "../../app/providers";
import {
  datasetHandlers,
  resetDatasetHandlerState,
} from "../../mocks/handlers/datasets.handlers";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import { DataExportPage } from "./page";
import { packageDeliveryFixture } from "./testing/package-delivery-fixture";
import RawDataExportPanel from "./RawDataExportPanel";

const server = setupServer(...datasetHandlers);
const postedExports: { episode_ids: string[] }[] = [];
vi.mock("../../features/collection-tasks/api", () => ({
  collectionTaskGateway: {
    list: async () => ({
      items: [
        {
          collection_task_id: "assembly",
          dataset_id: "dataset_fx_01",
          name: "Assembly task",
          task_code: "00000012",
        },
        {
          collection_task_id: "other",
          task_code: "00000013",
          dataset_id: "dataset_fx_01",
          name: "Other task",
        },
      ],
      next_cursor: null,
    }),
  },
}));

function rawDownloadSession() {
  const store = useShellStore.getState();
  const { authorization, scope } = store;
  store.setSession(
    { actorId: "reader", displayName: "Reader", roleIds: [] },
    "test-session",
  );
  store.setSessionScopes(
    [
      {
        organizationId: scope!.organizationId,
        projectId: scope!.projectId!,
        regionCodes: [scope!.regionCode!],
        projectWide: false,
        capabilities: [],
      },
    ],
    1,
  );
  store.setScope(scope!);
  store.setAuthorization(authorization!);
  return scope!;
}

beforeAll(() => {
  configureRuntime({
    apiBaseUrl: "http://localhost/api/v1",
    sseBaseUrl: "http://localhost/api/v1/events",
    buildVersion: "p21-page-test",
    releaseEnv: "test",
  });
  server.listen({ onUnhandledRequest: "error" });
});

beforeEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  postedExports.length = 0;
  server.use(
    http.get("*/episode-deliveries/formats", () => HttpResponse.json([])),
    http.get("*/episode-deliveries/upload-batches", () => HttpResponse.json([])),
  );
  const store = useShellStore.getState();
  store.setScope({
    organizationId: "org_fx_01",
    projectId: "prj_fx_01",
    regionCode: "cn-shanghai",
  });
  store.setAuthorization({
    scopeKey: useShellStore.getState().scopeKey,
    roleVersion: "p21-page-test-v1",
    capabilities: [
      "dataset.read",
      "dataset_version.read",
      "episode.read",
      "export.read",
      "export.create",
      "export.download",
      "dataset_version.download_raw",
      "upload.read",
    ],
    fetchedAt: "2026-08-26T00:00:00Z",
  });
  const interceptedFetch = globalThis.fetch;
  // jsdom and Node expose separately branded AbortSignals; MSW needs Node's.
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST" && String(input).endsWith("/exports")) {
      postedExports.push(JSON.parse(String(init.body)));
    }
    return interceptedFetch(input, { ...init, signal: undefined });
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getComputedStyle(element),
  );
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
  resetDatasetHandlerState();
  useShellStore.getState().clearSensitiveState();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

afterAll(() => {
  server.close();
  resetRuntimeConfigForTests();
});

function SelectedBatchQuery() {
  const values = new URLSearchParams(useLocation().search).getAll("uploadBatch");
  return <output data-testid="selected-batches">{values.sort().join(",")}</output>;
}

describe("P21 data export page", () => {
  it("shows delivery history with only the export status permission", async () => {
    rawDownloadSession();
    const store = useShellStore.getState();
    store.setAuthorization({ ...store.authorization!, capabilities: ["export.read"] });
    render(<ProviderHarness><MemoryRouter initialEntries={["/exports"]}><DataExportPage /></MemoryRouter></ProviderHarness>);
    expect(await screen.findByText("导出记录")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "导出数据和格式" })).toBeNull();
  });

  it("adds and removes all batches for a Shanghai upload date", async () => {
    rawDownloadSession();
    const user = userEvent.setup();
    server.use(http.get("*/episode-deliveries/upload-batches", () => HttpResponse.json([
      { batch_number: 3, channel: "oss", created_at: "2026-09-02T16:01:00Z" },
      { batch_number: 2, channel: "web", created_at: "2026-09-01T16:01:00Z" },
      { batch_number: 1, channel: "web", created_at: "2026-09-02T15:59:00Z" },
    ])));
    render(<ProviderHarness><MemoryRouter initialEntries={["/exports?uploadBatch=3"]}>
      <DataExportPage /><SelectedBatchQuery />
    </MemoryRouter></ProviderHarness>);
    const picker = await screen.findByRole("combobox", { name: "按上传日期选择批次（北京时间）" });
    await user.click(picker);
    const option = () => screen.getByText("2026-09-02 · 2 个批次", { selector: ".ant-select-item-option-content" });
    await waitFor(() => expect(option()).toBeVisible());
    await user.click(option());
    await waitFor(() => expect(screen.getByTestId("selected-batches")).toHaveTextContent("1,2,3"));
    await user.click(option());
    await waitFor(() => expect(screen.getByTestId("selected-batches")).toHaveTextContent(/^3$/));
    await user.click(screen.getByText("2026-09-03 · 1 个批次", { selector: ".ant-select-item-option-content" }));
    expect(await screen.findByText("请选择要纳入的上传日期或批次。")).toBeVisible();
  });

  it("selects raw batches across pages and downloads all selected files in one archive request", async () => {
    const scope = rawDownloadSession();
    const batches = Array.from({ length: 22 }, (_, i) => ({
      import_id: `raw-${i}`,
      dataset_id: datasetIds.dataset,
      episode_count: 0,
      file_count: 3,
      total_bytes: 120,
      source_format: "MCAP",
      status: "RAW_COMMITTED",
      updated_at: "2026-10-16T00:00:00Z",
    }));
    const archives: unknown[] = [];
    server.use(
      http.get("*/lerobot-imports", () => HttpResponse.json(batches)),
      http.post("*/lerobot-imports/archive", async ({ request }) => {
        archives.push(await request.json());
        return new HttpResponse(new Uint8Array([80, 75, 3, 4]), {
          headers: { "Content-Type": "application/zip" },
        });
      }),
    );
    const { write: save, close: download } = mockSavePicker();
    const user = userEvent.setup();
    render(
      <ProviderHarness>
        <RawDataExportPanel
          scope={scope}
          datasetIds={[datasetIds.dataset]}
          taskIds={[]}
          hasFilter
          canRead
          canCreate
          canDownload
        />
      </ProviderHarness>,
    );
    await screen.findByText(/共 22 个原始批次/u);
    const button = screen.getByRole("button", { name: "导出" });
    expect(button).toBeDisabled();
    const table = screen.getByRole("table", { name: "原始数据批次" });
    await user.click(within(table).getAllByRole("checkbox")[1]!);
    await user.click(within(table).getAllByRole("checkbox")[2]!);
    await user.click(screen.getByTitle("2"));
    await user.click(within(table).getAllByRole("checkbox")[1]!);
    expect(screen.getByText(/已选 3 个批次 · 9 个文件/u)).toBeVisible();
    await user.click(button);
    await waitFor(() => expect(download).toHaveBeenCalledOnce());
    expect(archives).toEqual([{ import_ids: ["raw-0", "raw-1", "raw-20"], download_name: expect.stringMatching(/^original_.+_\d{8}_\d{4}\.zip$/) }]);
    expect(Array.from(save.mock.calls[0]![0] as Uint8Array)).toEqual([80, 75, 3, 4]);
    expect(await screen.findByText(/ZIP 已保存到所选位置 · original_/)).toBeVisible();
    expect(archives).toHaveLength(1);
    const ossUri = "oss://managed/exports/project/original-data.zip";
    const published = vi.fn();
    server.use(http.post("*/episode-deliveries/oss", async ({ request }) => {
      published(await request.json());
      return new HttpResponse('{"uploaded_bytes":2}\n' + JSON.stringify({ uploaded_bytes: 4, oss_uri: ossUri }) + '\n',
        { headers: { "Content-Type": "application/x-ndjson" } });
    }));
    await user.click(screen.getByRole("radio", { name: "导出到 OSS" }).closest("label")!);
    await user.click(button);
    expect(await screen.findByText(ossUri)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "关闭导出提示" }));
    expect(screen.queryByText("已导出到 OSS")).toBeNull();
    await user.click(screen.getByRole("button", { name: "复制 OSS 地址" }));
    expect(await navigator.clipboard.readText()).toBe(ossUri);
    const firstRecord = screen.getAllByText("查看原始批次明细（3）")[0]!.closest("details")!;
    await user.click(within(firstRecord).getByText("查看原始批次明细（3）"));
    await waitFor(() => expect(firstRecord.textContent).toContain("raw-20"));
    expect(firstRecord.textContent).toContain("raw-0");
    expect(firstRecord.textContent).toContain("raw-1");
    expect(published).toHaveBeenCalledWith({ kind: "raw", import_ids: ["raw-0", "raw-1", "raw-20"], download_name: expect.stringMatching(/^original_.+_\d{8}_\d{4}\.zip$/) });
    expect(archives).toHaveLength(1);
    expect(download).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByText("所选原始数据已下载为 ZIP"),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "全选筛选结果" }));
    expect(screen.getByText(/已选 22 个批次 · 66 个文件/u)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "清空选择" }));
    expect(button).toBeDisabled();
    expect(postedExports).toHaveLength(0);
  });

  it("resets raw selection when task filters change and disables downloads without permission", async () => {
    const scope = rawDownloadSession();
    server.use(
      http.get("*/lerobot-imports", () =>
        HttpResponse.json(
          ["assembly", "other"].map((task) => ({
            import_id: task,
            collection_task_id: task,
            dataset_id: datasetIds.dataset,
            episode_count: 0,
            file_count: 1,
            total_bytes: 10,
            source_format: "MCAP",
            status: "RAW_COMMITTED",
            updated_at: "2026-10-16T00:00:00Z",
          })),
        ),
      ),
    );
    const panel = (taskIds: string[], canDownload = true) => (
      <ProviderHarness>
        <RawDataExportPanel
          scope={scope}
          datasetIds={[datasetIds.dataset]}
          taskIds={taskIds}
          hasFilter
          canRead
          canCreate
          canDownload={canDownload}
        />
      </ProviderHarness>
    );
    const user = userEvent.setup();
    const view = render(panel([]));
    await screen.findByText(/共 2 个原始批次/u);
    await user.click(screen.getByRole("button", { name: "全选筛选结果" }));
    expect(screen.getByText(/已选 2 个批次/u)).toBeVisible();
    view.rerender(panel(["assembly"]));
    await screen.findByText(/共 1 个原始批次/u);
    expect(screen.getByRole("button", { name: "导出" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "全选筛选结果" }));
    expect(screen.getByText(/已选 1 个批次/u)).toBeVisible();
    view.rerender(panel(["assembly"], false));
    expect(screen.getByRole("button", { name: "导出" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "全选筛选结果" })).toBeDisabled();
    expect(
      screen
        .getAllByRole("checkbox")
        .every((input) => (input as HTMLInputElement).disabled),
    ).toBe(true);
  });

  it("shows and selects only batches from the first through the last upload minute", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("*/lerobot-imports", () => HttpResponse.json([])),
      http.get("*/episode-deliveries/upload-batches", () => HttpResponse.json([
        { batch_number: 4, channel: "oss", created_at: "2026-09-01T12:06:00Z" },
        { batch_number: 3, channel: "oss", created_at: "2026-09-01T12:05:59Z" },
        { batch_number: 2, channel: "web", created_at: "2026-09-01T12:00:00Z" },
        { batch_number: 1, channel: "web", created_at: "2026-09-01T11:59:59Z" },
      ])),
    );
    render(<ProviderHarness><MemoryRouter initialEntries={[
      "/exports?dataStage=raw&uploadTime=2026-09-01T12:00:00Z&uploadTime=2026-09-01T12:05:00Z",
    ]}><DataExportPage /></MemoryRouter></ProviderHarness>);
    const toggle = await screen.findByRole("checkbox", { name: "全选上传批次" });
    await waitFor(() => expect(toggle).toBeChecked());
    await user.click(screen.getByRole("combobox", { name: "上传批次" }));
    const options = document.querySelectorAll(".ant-select-item-option-content");
    expect(Array.from(options, e => e.textContent)).toEqual([
      expect.stringContaining("第 3 次上传"), expect.stringContaining("第 2 次上传"),
    ]);
    await user.keyboard("{Escape}");
    await user.click(toggle);
    expect(await screen.findByText("请选择要纳入的上传日期或批次。")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "清空筛选" }));
    await user.click(screen.getByRole("combobox", { name: "上传批次" }));
    expect(document.querySelectorAll(".ant-select-item-option-content")).toHaveLength(4);
  });

  it("uses the full ending minute for a time-only raw export and clears the range", async () => {
    const queries: URLSearchParams[] = [];
    server.use(http.get("*/lerobot-imports", ({ request }) => {
      queries.push(new URL(request.url).searchParams);
      return HttpResponse.json([]);
    }));
    const user = userEvent.setup();
    render(<ProviderHarness><MemoryRouter initialEntries={[
      "/exports?dataStage=raw&uploadTime=2026-09-01T12:00:00Z&uploadTime=2026-09-01T12:05:00Z",
    ]}><DataExportPage /></MemoryRouter></ProviderHarness>);
    await screen.findByText(/共 0 个原始批次/);
    expect(queries[0]?.get("uploaded_from")).toBe("2026-09-01T12:00:00.000Z");
    expect(queries[0]?.get("uploaded_before")).toBe("2026-09-01T12:06:00.000Z");
    expect(queries[0]?.has("dataset_id")).toBe(false);
    await user.click(screen.getByRole("button", { name: "清空筛选" }));
    expect(screen.getByLabelText("上传时间")).toHaveValue("");
    expect(await screen.findByText("请先选择上传时间、上传批次、任务或数据集")).toBeVisible();
  });

  it("can look up original files by upload batch alone", async () => {
    const queries: URLSearchParams[] = [];
    server.use(http.get("*/lerobot-imports", ({ request }) => {
      queries.push(new URL(request.url).searchParams);
      return HttpResponse.json([]);
    }));
    render(<ProviderHarness><MemoryRouter initialEntries={["/exports?dataStage=raw&uploadBatch=7"]}>
      <DataExportPage />
    </MemoryRouter></ProviderHarness>);
    await screen.findByText(/共 0 个原始批次/);
    expect(queries.some(q => q.get("batch_number") === "7" && !q.has("dataset_id"))).toBe(true);
  });

  it.each(["zip", "oss"])("exports the selected QC bundle to %s after navigating away", async destination => {
    const ossUri = "oss://managed/exports/episodes.zip";
    const first = packageDeliveryFixture().data;
    const second = packageDeliveryFixture();
    const timeFilter = { uploaded_from: "2026-09-01T00:00:00.000Z", uploaded_before: "2026-09-03T00:00:00.000Z" };
    Object.assign(second.data.identity, {
      organization_id: "org_fx_01", project_id: "prj_fx_01", region_code: "cn-shanghai",
      dataset_id: datasetIds.dataset, episode_id: "batch-two-episode",
    });
    Object.assign(first.identity, { ...second.data.identity, episode_id: "batch-one-episode" });
    first.data_version_id = "first-version";
    const queued = vi.fn();
    const archived = vi.fn();
    let releaseFirstPart!: () => void;
    const firstPart = new Promise<void>(resolve => { releaseFirstPart = resolve; });
    rawDownloadSession();
    const { close: download } = mockSavePicker();
    server.use(
      http.get("*/episode-deliveries/upload-batches", () => HttpResponse.json([
        { batch_number: 2, channel: "oss", created_at: "2026-09-02T12:00:00Z" },
        { batch_number: 1, channel: "web", created_at: "2026-09-01T12:00:00Z" },
      ])),
      http.get("*/episode-deliveries/upload-candidates", ({ request }) => {
        const query = new URL(request.url).searchParams;
        if (query.get("stage") !== "qc_passed" || query.has("dataset_id") ||
            query.get("uploaded_from") !== timeFilter.uploaded_from || query.get("uploaded_before") !== timeFilter.uploaded_before)
          return HttpResponse.json({ items: [], total: 0, next_after: null });
        const both = query.get("all_batches") === "true" || query.getAll("batch_number").length === 2;
        const next = Boolean(query.get("after"));
        return HttpResponse.json({ total: both ? 2 : 1, next_after: both && !next ? "page2" : null,
          items: [{ data: next ? second.data : first, batch_numbers: [next ? 2 : 1] }] });
      }),
      http.get("*/episode-deliveries", () => HttpResponse.json([])),
      http.post("*/episode-deliveries/bundle-parts", async ({ request }) => {
        const body = await request.json() as { data: Array<typeof first> };
        queued(body);
        const stream = new ReadableStream({
          async start(controller) {
            const send = (index: number, reused: boolean) => controller.enqueue(new TextEncoder().encode(
              JSON.stringify({index, reused, part_id: (body.data[index]!.data_version_id === "first-version" ? "a" : "b").repeat(64)}) + "\n"));
            send(1, false);
            await firstPart;
            send(0, true);
            controller.close();
          },
        });
        return new HttpResponse(stream, {headers: {"Content-Type":"application/x-ndjson"}});
      }),
      http.post("*/episode-deliveries/oss", async ({ request }) => {
        archived(await request.json());
        return new HttpResponse(JSON.stringify({ uploaded_bytes: 4, oss_uri: ossUri }) + "\n",
          { headers: { "Content-Type": "application/x-ndjson" } });
      }),
      http.post("*/episode-deliveries/bundle", async ({ request }) => {
        archived(await request.json());
        return new HttpResponse(new Uint8Array([80,75,3,4]), { headers: { "Content-Type": "application/zip" } });
      }),
    );
    const user = userEvent.setup();
    render(<ProviderHarness><MemoryRouter initialEntries={["/exports?dataStage=unannotated&uploadBatch=1&uploadTime=2026-09-01T00:00:00Z&uploadTime=2026-09-02T23:59:00Z"]}>
      <Routes>
        <Route path="/exports" element={<><Link to="/datasets">数据集列表</Link><DataExportPage /></>} />
        <Route path="/datasets" element={<Link to="/exports">返回数据导出</Link>} />
      </Routes>
    </MemoryRouter></ProviderHarness>);
    expect(await screen.findByRole("radio", { name: "质检通过且已入库的数据" })).toBeChecked();
    await user.click(await screen.findByRole("checkbox", { name: /batch-one-episode/ }));
    await user.click(screen.getByRole("combobox", { name: "上传批次" }));
    await user.click(await screen.findByText(/第 2 次上传 · OSS/));
    expect(await screen.findByRole("checkbox", {name: "全选上传批次"})).toBeChecked();
    await user.keyboard("{Escape}");
    await user.click(await screen.findByRole("checkbox", {name: "全选筛选结果"}));
    await waitFor(() => expect(screen.getByText(/查看已选数据（2）/)).toBeVisible());
    expect(screen.getByRole("radio", { name: "LeRobot RGB-D 集合" })).toBeChecked();
    if (destination === "oss") await user.click(screen.getByRole("radio", { name: "导出到 OSS" }).closest("label")!);
    await user.click(screen.getByRole("button", { name: "导出" }));
    await waitFor(() => expect(queued).toHaveBeenCalledTimes(1));
    expect(archived).not.toHaveBeenCalled();
    expect(await screen.findByText("已准备 Episode 1/2，已复用 0 条", {}, { timeout: 4000 })).toBeVisible();
    await user.click(screen.getByRole("link", { name: "数据集列表" }));
    expect(screen.queryByRole("button", { name: /导出$/ })).toBeNull();
    releaseFirstPart();
    await waitFor(() => expect(queued).toHaveBeenCalledTimes(1));
    expect(queued).toHaveBeenCalledWith({data: [second.data, first], mode:"rgbd", export_filter:{stage:"qc_passed", all_batches:true, ...timeFilter}});
    await waitFor(() => expect(archived).toHaveBeenCalledWith({
      ...(destination === "oss" && { kind: "bundle" }),
      part_ids: ["b".repeat(64), "a".repeat(64)],
      download_name: expect.stringMatching(/^lerobot_rgbd_.+_\d{8}_\d{4}\.zip$/),
      export_filter: { stage: "qc_passed", all_batches: true, ...timeFilter },
    }));
    if (destination === "zip") await waitFor(() => expect(download).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("link", { name: "返回数据导出" }));
    if (destination === "oss") {
      expect(await screen.findByText(ossUri)).toBeVisible();
      await user.click(screen.getByRole("button", { name: "关闭导出提示" }));
      await user.click(screen.getByRole("link", { name: "数据集列表" }));
      await user.click(screen.getByRole("link", { name: "返回数据导出" }));
      const copy = await screen.findByRole("button", { name: "复制 OSS 地址" });
      expect(screen.queryByText("已导出到 OSS")).toBeNull();
      expect(screen.getByText("LeRobot（含深度） · 2 条 Episode")).toBeVisible();
      const contents = screen.getByText("查看 Episode 明细（2）").closest("details")!;
      await user.click(within(contents).getByText("查看 Episode 明细（2）"));
      expect(await within(contents).findByText("batch-one-episode")).toBeVisible();
      expect(within(contents).getByText("batch-two-episode")).toBeVisible();
      expect(contents.textContent).toContain("上传批次 2");
      await user.click(copy);
      expect(await navigator.clipboard.readText()).toBe(ossUri);
      expect(archived).toHaveBeenCalledTimes(1);
      expect(download).not.toHaveBeenCalled();
      return;
    }
    expect(await screen.findByText(/下载完成 · 2 条 Episode/)).toBeVisible();
    expect(screen.queryByRole("link", { name: "下载 ZIP" })).toBeNull();
  });

  it("opens older bookmarks on the annotated standard export page", async () => {
    server.use(http.get("*/episode-deliveries", () => HttpResponse.json([])));
    render(
      <ProviderHarness>
        <MemoryRouter initialEntries={["/exports?delivery=legacy"]}>
          <DataExportPage />
        </MemoryRouter>
      </ProviderHarness>,
    );
    expect(await screen.findByRole("radio", { name: "标注完成数据" })).toBeChecked();
    expect(screen.getByLabelText("标准包交付格式")).toBeVisible();
    expect(screen.queryByRole("radio", { name: "历史格式" })).not.toBeInTheDocument();
  });

  it.each(["", "&delivery=legacy"])("opens a retired dataset link on QC data with its dataset filter %s", async (delivery) => {
    const queries: URLSearchParams[] = [];
    server.use(
      http.get("*/episode-deliveries/upload-candidates", ({ request }) => {
        queries.push(new URL(request.url).searchParams);
        return HttpResponse.json({ items: [], total: 0, next_after: null });
      }),
      http.get("*/episode-deliveries", () => HttpResponse.json([])),
    );
    render(
      <ProviderHarness>
        <MemoryRouter initialEntries={[`/exports?datasetId=${datasetIds.dataset}&dataStage=dataset${delivery}`]}>
          <DataExportPage />
        </MemoryRouter>
      </ProviderHarness>,
    );
    expect(await screen.findByRole("radio", { name: "质检通过且已入库的数据" })).toBeChecked();
    expect(screen.queryByRole("radio", { name: "数据集数据" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /独立标准包/ })).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "LeRobot RGB-D 集合" })).toBeChecked();
    await waitFor(() => expect(queries.some(query =>
      query.get("stage") === "qc_passed" && query.get("dataset_id") === datasetIds.dataset
    )).toBe(true));
  });
  it("downloads original files without requiring processed or approved Episodes", async () => {
    const store = useShellStore.getState();
    const authorization = store.authorization!;
    const scope = store.scope!;
    store.setSession(
      { actorId: "reader", displayName: "Reader", roleIds: [] },
      "test-session",
    );
    store.setSessionScopes(
      [
        {
          organizationId: "org_fx_01",
          projectId: "prj_fx_01",
          regionCodes: ["cn-shanghai"],
          projectWide: false,
          capabilities: [],
        },
      ],
      1,
    );
    store.setScope(scope);
    store.setAuthorization(authorization);
    server.use(
      http.get(
        "*/projects/:projectId/regions/:regionCode/lerobot-imports",
        () =>
          HttpResponse.json([
            {
              import_id: "raw-1",
              dataset_id: datasetIds.dataset,
              collection_task_id: "assembly",
              episode_count: 154,
              file_count: 1,
              source_format: "LEROBOT_V3",
              status: "RAW_COMMITTED",
            },
          ]),
      ),
      http.get("*/lerobot-imports/:importId/directory", () =>
        HttpResponse.json({
          import_id: "raw-1",
          offset: 0,
          path: "",
          storage_prefix: "raw/source",
          local_path: null,
          file_count: 1,
          total_bytes: 10,
          entries: [
            {
              name: "info.json",
              path: "info.json",
              kind: "FILE",
              size: 10,
              file_count: 1,
              sha256: "a".repeat(64),
              text_preview: true,
            },
          ],
          total: 1,
          episode_count: 154,
          source_format: "LEROBOT_V3",
        }),
      ),
      http.get("*/lerobot-imports/:importId/assets:read", () =>
        HttpResponse.json({
          path: "info.json",
          url: "https://example.test/original-info.json",
          expires_at: new Date(Date.now() + 60_000).toISOString(),
        }),
      ),
    );
    const { close: download, files } = mockSavePicker();
    server.use(http.get("https://example.test/original-info.json", () => new HttpResponse(new Uint8Array(10))));
    const user = userEvent.setup();
    render(
      <ProviderHarness>
        <MemoryRouter
          initialEntries={[
            `/exports?delivery=legacy&datasetId=${datasetIds.dataset}&dataStage=raw`,
          ]}
        >
          <DataExportPage />
        </MemoryRouter>
      </ProviderHarness>,
    );
    expect(await screen.findByText(/154 条原始 Episode/u)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /创建 .* 个导出任务/u }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看文件" }));
    await user.click(
      await screen.findByRole("button", { name: "下载 info.json" }),
    );
    await waitFor(() => expect(download).toHaveBeenCalledOnce());
    expect(files.has("info.json")).toBe(true);
    expect(postedExports).toHaveLength(0);
  });

});
