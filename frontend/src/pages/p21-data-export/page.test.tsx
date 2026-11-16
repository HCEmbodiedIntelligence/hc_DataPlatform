// @vitest-environment jsdom

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
import { MemoryRouter, useLocation } from "react-router-dom";
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
    expect(await screen.findByText("已移除全部日期。请选择要纳入的上传日期或批次。")).toBeVisible();
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
    const save = vi.fn(() => "blob:raw-archive");
    vi.stubGlobal(
      "URL",
      class extends URL {
        static createObjectURL = save;
        static revokeObjectURL = vi.fn();
      },
    );
    const download = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    const user = userEvent.setup();
    render(
      <ProviderHarness>
        <RawDataExportPanel
          scope={scope}
          datasetIds={[datasetIds.dataset]}
          taskIds={[]}
          hasFilter
          canRead
          canDownload
        />
      </ProviderHarness>,
    );
    await screen.findByText(/共 22 个原始批次/u);
    const button = screen.getByRole("button", { name: "下载所选批次" });
    expect(button).toBeDisabled();
    const table = screen.getByRole("table", { name: "原始数据批次" });
    await user.click(within(table).getAllByRole("checkbox")[1]!);
    await user.click(within(table).getAllByRole("checkbox")[2]!);
    await user.click(screen.getByTitle("2"));
    await user.click(within(table).getAllByRole("checkbox")[1]!);
    expect(screen.getByText(/已选 3 个批次 · 9 个文件/u)).toBeVisible();
    await user.click(button);
    await waitFor(() => expect(download).toHaveBeenCalledOnce());
    expect(archives).toEqual([{ import_ids: ["raw-0", "raw-1", "raw-20"] }]);
    expect(save).toHaveBeenCalledWith(
      expect.objectContaining({ type: "application/zip", size: 4 }),
    );
    expect(
      await screen.findByText("ZIP 已准备好，已向浏览器请求下载"),
    ).toBeVisible();
    const retry = screen.getByRole("link", { name: "点击下载 ZIP" });
    expect(retry).toHaveAttribute("href", "blob:raw-archive");
    expect(retry).toHaveAttribute("download", expect.stringMatching(/\.zip$/));
    // An explicit click reuses the received bytes, without re-exporting them.
    retry.addEventListener("click", (event) => event.preventDefault(), {
      once: true,
    });
    await user.click(retry);
    expect(archives).toHaveLength(1);
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
    expect(screen.getByRole("button", { name: "下载所选批次" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "全选筛选结果" }));
    expect(screen.getByText(/已选 1 个批次/u)).toBeVisible();
    view.rerender(panel(["assembly"], false));
    expect(screen.getByRole("button", { name: "下载所选批次" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "全选筛选结果" })).toBeDisabled();
    expect(
      screen
        .getAllByRole("checkbox")
        .every((input) => (input as HTMLInputElement).disabled),
    ).toBe(true);
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

  it("unions batch tags, selects every matching page, and downloads one shared Episode bundle", async () => {
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
    rawDownloadSession();
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL = vi.fn(() => "blob:episodes");
      static revokeObjectURL = vi.fn();
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
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
        const body = await request.json() as { data: typeof first };
        queued(body);
        return HttpResponse.json({status: "ready", reused: true, part_id: (body.data.data_version_id === "first-version" ? "a" : "b").repeat(64)});
      }),
      http.post("*/episode-deliveries/bundle", async ({ request }) => {
        archived(await request.json());
        return new HttpResponse(new Uint8Array([80,75,3,4]), { headers: { "Content-Type": "application/zip" } });
      }),
    );
    const user = userEvent.setup();
    render(<ProviderHarness><MemoryRouter initialEntries={["/exports?dataStage=unannotated&uploadBatch=1&uploadTime=2026-09-01T00:00:00Z&uploadTime=2026-09-02T23:59:00Z"]}>
      <DataExportPage />
    </MemoryRouter></ProviderHarness>);
    expect(await screen.findByRole("radio", { name: "质检通过且已入库的数据" })).toBeChecked();
    await user.click(await screen.findByRole("checkbox", { name: /batch-one-episode/ }));
    await user.click(screen.getByRole("combobox", { name: "上传批次" }));
    await user.click(await screen.findByText(/第 2 次上传 · OSS/));
    expect(await screen.findByRole("checkbox", {name: "全选上传批次"})).toBeChecked();
    await user.keyboard("{Escape}");
    await user.click(await screen.findByRole("checkbox", {name: "全选筛选结果"}));
    await waitFor(() => expect(screen.getByText(/查看已选数据（2）/)).toBeVisible());
    await user.click(screen.getByRole("radio", { name: "LeRobot RGB-D 集合" }).closest("label")!);
    await user.click(screen.getByRole("button", { name: "导出" }));
    await waitFor(() => expect(queued).toHaveBeenCalledTimes(2));
    expect(queued).toHaveBeenCalledWith({data: second.data, mode:"rgbd", export_filter:{stage:"qc_passed", all_batches:true, ...timeFilter}});
    await waitFor(() => expect(archived).toHaveBeenCalledWith({
      part_ids: expect.arrayContaining(["a".repeat(64), "b".repeat(64)]),
      export_filter: { stage: "qc_passed", all_batches: true, ...timeFilter },
    }));
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

  it.each(["", "&delivery=legacy"])("exports a standard RGB subset from the dataset link %s", async (delivery) => {
    const f = packageDeliveryFixture();
    Object.assign(f.data.identity, {
      organization_id: "org_fx_01",
      project_id: "prj_fx_01",
      region_code: "cn-shanghai",
      dataset_id: datasetIds.dataset,
      episode_id: "standard-episode",
    });
    if (f.job.plan.format !== "hc-collection-delivery-plan/1")
      throw new Error("fixture");
    f.job.plan.modalities = "rgb";
    f.job.plan.members[0]!.selected_depth_profiles = {};
    f.job.plan.members[0]!.excluded_depth_features = [
      "observation.images.head_depth",
    ];
    const downloadClick = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    const legacyEligibility = vi.fn(),
      queued = vi.fn();
    server.use(
      http.get(
        "*/datasets/:datasetId/versions/:versionId/export-eligibility",
        () => {
          legacyEligibility();
          return HttpResponse.json({});
        },
      ),
      http.get("*/episode-deliveries/candidates", () =>
        HttpResponse.json({
          ...f.candidates,
          dataset_id: datasetIds.dataset,
        }),
      ),
      http.get("*/episode-deliveries", () => HttpResponse.json([])),
      http.get("*/episode-deliveries/:jobId", () => HttpResponse.json(f.job)),
      http.post("*/episode-deliveries/:jobId/download", () =>
        HttpResponse.json({
          job_id: f.job.job_id,
          artifact_sha256: f.job.artifact_sha256,
          size_bytes: f.job.size_bytes,
          url: "https://objects.example/rgb.zip",
          expires_seconds: 300,
        }),
      ),
      http.post("*/episode-deliveries/collections", async ({ request }) => {
        queued(await request.json());
        return HttpResponse.json({
          ...f.job,
          plan: {
            ...f.job.plan,
            modalities: "rgb",
            members: [
              {
                ...f.job.plan.members[0],
                selected_depth_profiles: {},
                excluded_depth_features: ["observation.images.head_depth"],
              },
            ],
          },
        });
      }),
    );
    const user = userEvent.setup();
    render(
      <ProviderHarness>
        <MemoryRouter
          initialEntries={[
            `/exports?datasetId=${datasetIds.dataset}&dataStage=dataset${delivery}`,
          ]}
        >
          <DataExportPage />
        </MemoryRouter>
      </ProviderHarness>,
    );
    await user.click(
      await screen.findByRole("checkbox", { name: /standard-episode/ }),
    );
    expect(screen.queryByRole("radio", { name: "历史格式" })).not.toBeInTheDocument();
    await user.click(
      screen
        .getByRole("radio", { name: "LeRobot RGB 子集（排除深度）" })
        .closest("label")!,
    );
    expect(
      screen.getByRole("radio", { name: "LeRobot RGB 子集（排除深度）" }),
    ).toBeChecked();
    await user.click(screen.getByRole("button", { name: "导出" }));
    await waitFor(() =>
      expect(queued).toHaveBeenCalledWith({
        modalities: "rgb",
        members: [{ data: f.data, annotation: null }],
      }),
    );
    expect(legacyEligibility).not.toHaveBeenCalled();
    expect(postedExports).toHaveLength(0);
    await waitFor(() => expect(downloadClick).toHaveBeenCalledOnce());
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
    const download = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
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
    expect(postedExports).toHaveLength(0);
  });

});
