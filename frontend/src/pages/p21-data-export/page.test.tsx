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
      "export.read",
      "export.create",
      "export.download",
    ],
    fetchedAt: "2026-08-26T00:00:00Z",
  });
  const interceptedFetch = globalThis.fetch;
  // jsdom and Node expose separately branded AbortSignals; MSW needs Node's.
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) =>
    interceptedFetch(input, { ...init, signal: undefined }),
  );
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
  it("searches sources and creates independently tracked tasks for multiple formats", async () => {
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
    await user.click(screen.getByLabelText("任务"));
    await user.click(
      await screen.findByText("assembly (1)", {
        selector: ".ant-select-item-option-content",
      }),
    );
    await user.click(screen.getByLabelText("Tag"));
    const tagOptions = await screen.findAllByText("assembly (1)", {
      selector: ".ant-select-item-option-content",
    });
    await user.click(tagOptions.at(-1)!);
    await user.click(screen.getByLabelText("Tag"));
    await user.click(
      await screen.findByText("line-a (1)", {
        selector: ".ant-select-item-option-content",
      }),
    );
    await user.click(screen.getByLabelText("机器人"));
    await user.click(
      await screen.findByText("robot_fx_01 (1)", {
        selector: ".ant-select-item-option-content",
      }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("location-search")).toHaveTextContent(
        "task=assembly",
      ),
    );
    const locationSearch = screen.getByTestId("location-search");
    expect(locationSearch).toHaveTextContent("tag=assembly");
    expect(locationSearch).toHaveTextContent("tag=line-a");
    expect(locationSearch).toHaveTextContent("robotId=robot_fx_01");
    expect(locationSearch).toHaveTextContent("datasetId=");

    await user.click(
      await screen.findByRole("checkbox", { name: "选择 Assembly dataset" }),
    );
    await user.click(screen.getByRole("checkbox", { name: /LeRobot v3/u }));

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "创建 2 个导出任务" }),
      ).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "创建 2 个导出任务" }));

    expect(await screen.findByText("已创建 2 个导出任务")).toBeVisible();
    const taskTable = screen.getByRole("table", { name: "导出任务列表" });
    expect(within(taskTable).getAllByText("Assembly dataset")).toHaveLength(2);
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
  });
});
