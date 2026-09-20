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
  datasetIds,
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
        name:
          mode === "task"
            ? "全选所选任务的 Episode"
            : "全选所选数据集的 Episode",
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
