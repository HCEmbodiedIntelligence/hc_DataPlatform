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
import {
  datasetBootstrapFixture,
  datasetIds,
  datasetListFixture,
  episodePageFixture,
} from "../../mocks/fixtures/datasets/core";
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

const server = setupServer(...datasetHandlers);
const postedExports: { episode_ids: string[] }[] = [];
vi.mock("../p20-collection-tasks/api", () => ({
  collectionTaskGateway: {
    list: async () => ({
      items: [
        {
          collection_task_id: "assembly",
          dataset_id: "dataset_fx_01",
          name: "Assembly task",
        },
        {
          collection_task_id: "other",
          dataset_id: "dataset_fx_01",
          name: "Other task",
        },
      ],
      next_cursor: null,
    }),
  },
}));

function LocationSearch() {
  return <output data-testid="location-search">{useLocation().search}</output>;
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
    http.get(
      "*/datasets/:datasetId/versions/:versionId/export-eligibility",
      ({ params, request }) =>
        HttpResponse.json({
          project_id: "prj_fx_01",
          dataset_id: String(params.datasetId),
          dataset_version: String(params.versionId),
          data_stage:
            new URL(request.url).searchParams.get("data_stage") ?? "annotated",
          eligible_episode_ids: [
            datasetIds.episode,
            "episode_fx_02",
            "episode_fx_03",
          ],
        }),
    ),
    http.get("*/projects/:projectId/datasets/:datasetId/bootstrap", () =>
      HttpResponse.json({
        ...datasetBootstrapFixture,
        data: {
          ...datasetBootstrapFixture.data,
          dataset: { ...datasetBootstrapFixture.data.dataset, folder_path: [] },
        },
      }),
    ),
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

describe("P21 data export page", () => {
  it("switches processing stages, clears selections, and sends the selected stage", async () => {
    server.use(
      http.get(
        "*/datasets/:datasetId/versions/:versionId/export-eligibility",
        ({ params, request }) => {
          const stage = new URL(request.url).searchParams.get("data_stage");
          return HttpResponse.json({
            project_id: "prj_fx_01",
            dataset_id: String(params.datasetId),
            dataset_version: String(params.versionId),
            data_stage: stage,
            eligible_episode_ids:
              stage === "dataset"
                ? [datasetIds.episode, "episode_fx_02"]
                : [datasetIds.episode],
          });
        },
      ),
      http.get(
        "*/projects/:projectId/datasets/:datasetId/versions/:versionId/episodes",
        ({ params }) =>
          HttpResponse.json({
            ...episodePageFixture,
            items: [datasetIds.episode, "episode_fx_02"].map((id, ordinal) => ({
              ...episodePageFixture.items[0],
              episode_id: id,
              version_id: String(params.versionId),
              selected_revision: {
                ...episodePageFixture.items[0].selected_revision,
                episode_id: id,
                ordinal,
              },
            })),
          }),
      ),
    );
    const user = userEvent.setup();
    render(
      <ProviderHarness>
        <MemoryRouter
          initialEntries={[`/exports?datasetId=${datasetIds.dataset}`]}
        >
          <DataExportPage />
        </MemoryRouter>
      </ProviderHarness>,
    );
    expect(await screen.findByText("可导出 1 条已标注数据")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "全选可导出数据" }));
    await user.click(
      screen.getByRole("radio", { name: "数据集数据" }).closest("label")!,
    );
    expect(await screen.findByText("可导出 2 条数据集数据")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "创建 0 个导出任务" }),
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "全选可导出数据" }));
    await user.click(screen.getByRole("button", { name: "创建 1 个导出任务" }));
    expect(await screen.findByText("已创建 1 个导出任务")).toBeVisible();
    expect(postedExports[0]).toMatchObject({
      data_stage: "dataset",
      episode_ids: [datasetIds.episode, "episode_fx_02"],
    });
    await user.click(
      screen.getByRole("radio", { name: "标注完成数据" }).closest("label")!,
    );
    expect(await screen.findByText("可导出 1 条已标注数据")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "创建 0 个导出任务" }),
    ).toBeDisabled();
  });

  it("downloads original files without requiring processed or approved Episodes", async () => {
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
      http.get("*/lerobot-imports/:importId/files", () =>
        HttpResponse.json({
          files: [{ path: "meta/info.json", size: 10, sha256: "a".repeat(64) }],
          total: 1,
          episode_count: 154,
          source_format: "LEROBOT_V3",
        }),
      ),
      http.get("*/lerobot-imports/:importId/assets:read", () =>
        HttpResponse.json({
          url: "https://example.test/original-info.json",
          expires_at: "2026-09-22T00:00:00Z",
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
            `/exports?datasetId=${datasetIds.dataset}&dataStage=raw`,
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
    await user.click(screen.getByRole("button", { name: "选择原文件" }));
    await user.click(await screen.findByRole("button", { name: "下载原文件" }));
    await waitFor(() => expect(download).toHaveBeenCalledOnce());
    expect(postedExports).toHaveLength(0);
  });

  it.each([0, 1])(
    "shows only %i approved Episodes across all processing pages",
    async (approvedCount) => {
      const approvedId = "episode_fx_approved";
      server.use(
        http.get(
          "*/datasets/:datasetId/versions/:versionId/export-eligibility",
          ({ params }) =>
            HttpResponse.json({
              project_id: "prj_fx_01",
              dataset_id: String(params.datasetId),
              dataset_version: String(params.versionId),
              eligible_episode_ids: approvedCount ? [approvedId] : [],
            }),
        ),
        http.get(
          "*/projects/:projectId/datasets/:datasetId/versions/:versionId/episodes",
          ({ request, params }) => {
            const later = new URL(request.url).searchParams.has("after");
            const ids = later
              ? [approvedId]
              : Array.from({ length: 20 }, (_, i) => `episode_fx_draft_${i}`);
            return HttpResponse.json({
              ...episodePageFixture,
              items: ids.map((id, ordinal) => ({
                ...episodePageFixture.items[0],
                episode_id: id,
                version_id: String(params.versionId),
                success_state: "SUCCEEDED",
                selected_revision: {
                  ...episodePageFixture.items[0].selected_revision,
                  episode_id: id,
                  ordinal: later ? 131 : ordinal,
                },
              })),
              page_info: {
                ...episodePageFixture.page_info,
                has_next: !later,
                after: later ? null : "approved-page",
              },
            });
          },
        ),
      );
      const user = userEvent.setup();
      render(
        <ProviderHarness>
          <MemoryRouter
            initialEntries={[`/exports?datasetId=${datasetIds.dataset}`]}
          >
            <DataExportPage />
          </MemoryRouter>
        </ProviderHarness>,
      );
      expect(
        await screen.findByText(`可导出 ${approvedCount} 条已标注数据`),
      ).toBeVisible();
      const table = screen.getByRole("table", { name: "可导出 Episode 列表" });
      expect(within(table).queryByText("Episode 0")).not.toBeInTheDocument();
      expect(
        within(table).queryAllByRole("checkbox", { name: /^选择 Episode/u }),
      ).toHaveLength(approvedCount);
      if (!approvedCount) {
        expect(
          screen.getByText("所选范围暂无可导出的已标注数据"),
        ).toBeVisible();
        expect(
          screen.getByRole("button", { name: "创建 0 个导出任务" }),
        ).toBeDisabled();
        return;
      }
      await user.click(screen.getByRole("button", { name: "全选可导出数据" }));
      await user.click(
        screen.getByRole("button", { name: "创建 1 个导出任务" }),
      );
      expect(await screen.findByText("已创建 1 个导出任务")).toBeVisible();
      expect(postedExports[0]?.episode_ids).toEqual([approvedId]);
    },
  );

  it.each([false, true])(
    "exports current working data when a published version exists: %s",
    async (hasPublishedVersion) => {
      const workingVersion = "version_lance_132";
      const requestedVersions: string[] = [];
      server.use(
        http.get("*/projects/:projectId/datasets", () =>
          HttpResponse.json({
            ...datasetListFixture,
            items: [
              {
                ...datasetListFixture.items[0],
                current_version: hasPublishedVersion
                  ? datasetListFixture.items[0].current_version
                  : null,
              },
            ],
          }),
        ),
        http.get("*/projects/:projectId/datasets/:datasetId/bootstrap", () =>
          HttpResponse.json({
            ...datasetBootstrapFixture,
            data: {
              ...datasetBootstrapFixture.data,
              dataset: {
                ...datasetBootstrapFixture.data.dataset,
                folder_path: [],
              },
              current_ready_version: hasPublishedVersion
                ? datasetBootstrapFixture.data.current_ready_version
                : null,
              working_version_id: workingVersion,
            },
          }),
        ),
        http.get(
          "*/projects/:projectId/datasets/:datasetId/versions/:versionId/episodes",
          ({ params }) => {
            requestedVersions.push(String(params.versionId));
            return HttpResponse.json({
              ...episodePageFixture,
              items: [
                {
                  ...episodePageFixture.items[0],
                  version_id: String(params.versionId),
                  selected_revision: {
                    ...episodePageFixture.items[0].selected_revision,
                    ordinal: 104,
                  },
                },
              ],
            });
          },
        ),
        http.post(
          "*/datasets/:datasetId/versions/:versionId/exports",
          ({ params }) => {
            requestedVersions.push(String(params.versionId));
            return HttpResponse.json(
              {
                job_id: "export:v1:lance_snapshot:working-data",
                project_id: "prj_fx_01",
                dataset_id: datasetIds.dataset,
                dataset_version: String(params.versionId),
                format: "lance_snapshot",
                attempt_id: "working-data",
                status: "PENDING",
                stage: "pending",
                progress: {
                  phase: "pending",
                  completed_phases: 0,
                  total_phases: 3,
                },
                cancellation_requested: false,
                created_at: "2026-09-21T06:00:00Z",
                updated_at: "2026-09-21T06:00:00Z",
              },
              { status: 202 },
            );
          },
        ),
      );
      const user = userEvent.setup();
      render(
        <ProviderHarness>
          <MemoryRouter
            initialEntries={[`/exports?datasetId=${datasetIds.dataset}`]}
          >
            <DataExportPage />
          </MemoryRouter>
        </ProviderHarness>,
      );
      await user.click(
        await screen.findByRole("checkbox", {
          name: "选择 Episode 104 · Assembly dataset",
        }),
      );
      await user.click(
        screen.getByRole("button", { name: "创建 1 个导出任务" }),
      );
      expect(await screen.findByText("已创建 1 个导出任务")).toBeVisible();
      expect(postedExports).toEqual([
        {
          project_id: "prj_fx_01",
          format: "lance_snapshot",
          data_stage: "annotated",
          episode_ids: [datasetIds.episode],
        },
      ]);
      expect(requestedVersions).toEqual([workingVersion, workingVersion]);
    },
  );

  it.each(["task", "dataset"])(
    "exports exactly the Episodes selected through %s, including later pages",
    async (mode) => {
      server.use(
        http.get(
          "*/projects/:projectId/datasets/:datasetId/versions/:versionId/episodes",
          ({ request, params }) => {
            const later = new URL(request.url).searchParams.has("after");
            const ids = later
              ? ["episode_fx_03"]
              : [datasetIds.episode, "episode_fx_02"];
            return HttpResponse.json({
              ...episodePageFixture,
              items: ids.map((id) => ({
                ...episodePageFixture.items[0],
                episode_id: id,
                version_id: String(params.versionId),
                task: id === "episode_fx_02" ? "other" : "assembly",
                selected_revision: {
                  ...episodePageFixture.items[0].selected_revision,
                  episode_id: id,
                  ordinal:
                    id === datasetIds.episode ? 0 : id.endsWith("02") ? 1 : 2,
                },
              })),
              page_info: {
                ...episodePageFixture.page_info,
                after: later ? null : "next",
                has_next: !later,
              },
            });
          },
        ),
      );
      const user = userEvent.setup();
      let downloadedName: string | null = null;
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
        function captureDownloadName(this: HTMLAnchorElement) {
          downloadedName = this.download;
        },
      );
      render(
        <ProviderHarness>
          <MemoryRouter initialEntries={["/exports"]}>
            <DataExportPage />
            <LocationSearch />
          </MemoryRouter>
        </ProviderHarness>,
      );

      expect(
        await screen.findByRole("heading", { name: "数据导出" }),
      ).toBeVisible();

      await user.click(await screen.findByLabelText("数据集"));
      await user.click(
        await screen.findByText("Assembly dataset · v3", {
          selector: ".ant-select-item-option-content",
        }),
      );
      if (mode === "task") {
        await user.click(screen.getByLabelText("任务"));
        await user.click(
          await screen.findByText("Assembly task", {
            selector: ".ant-select-item-option-content",
          }),
        );
      }
      const selectAll = await screen.findByRole("button", {
        name: "全选可导出数据",
      });
      await waitFor(() => expect(selectAll).toBeEnabled());
      await user.click(selectAll);
      if (mode === "dataset") {
        await user.click(
          screen.getByRole("checkbox", {
            name: "选择 Episode 1 · Assembly dataset",
          }),
        );
      }
      await user.click(screen.getByRole("checkbox", { name: /LeRobot v3/u }));

      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "创建 2 个导出任务" }),
        ).toBeEnabled(),
      );
      await user.click(
        screen.getByRole("button", { name: "创建 2 个导出任务" }),
      );

      expect(await screen.findByText("已创建 2 个导出任务")).toBeVisible();
      expect(postedExports).toHaveLength(2);
      expect(
        postedExports.every(
          (body) =>
            JSON.stringify(body.episode_ids) ===
            JSON.stringify([datasetIds.episode, "episode_fx_03"]),
        ),
      ).toBe(true);
      const taskTable = screen.getByRole("table", { name: "导出任务列表" });
      expect(within(taskTable).getAllByText("Assembly dataset")).toHaveLength(
        2,
      );
      expect(within(taskTable).getByText("Lance 数据文件")).toBeVisible();
      expect(within(taskTable).getByText("LeRobot v3")).toBeVisible();
      expect(window.sessionStorage.length).toBe(1);

      const downloadButtons = await screen.findAllByRole(
        "button",
        { name: /下载 Assembly dataset .*压缩包/u },
        { timeout: 7_000 },
      );
      await user.click(downloadButtons[0]!);
      await waitFor(() => expect(downloadedName).toMatch(/\.zip$/u));
      expect(await screen.findByText("压缩包下载已开始")).toBeVisible();
    },
  );
});
