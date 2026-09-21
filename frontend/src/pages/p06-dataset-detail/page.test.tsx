// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
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
  datasetBootstrapFixture,
  datasetIds,
  datasetVersionCapacityFixture,
} from "../../mocks/fixtures/datasets/core";
import { datasetHandlers } from "../../mocks/handlers/datasets.handlers";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import { DatasetDetailPage } from "./page";

const server = setupServer(...datasetHandlers);
const capacityRequests = vi.fn();

beforeAll(() => {
  configureRuntime({
    apiBaseUrl: "http://localhost/api/v1",
    sseBaseUrl: "http://localhost/api/v1/events",
    buildVersion: "capacity-test",
    releaseEnv: "test",
  });
  server.listen({ onUnhandledRequest: "error" });
});
beforeEach(() => {
  useShellStore.getState().setScope({
    organizationId: "org_fx_01",
    projectId: "prj_fx_01",
    regionCode: "cn-shanghai",
  });
  useShellStore.getState().setAuthorization({
    scopeKey: useShellStore.getState().scopeKey,
    roleVersion: "capacity-test-v1",
    capabilities: [
      "dataset.read",
      "dataset_version.read",
      "episode.read",
      "storage.overview.read",
    ],
    fetchedAt: "2026-09-21T00:00:00Z",
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
  const computed = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    computed(element),
  );
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  server.use(
    http.get("*/datasets/:datasetId/bootstrap", () =>
      HttpResponse.json({
        ...datasetBootstrapFixture,
        data: {
          ...datasetBootstrapFixture.data,
          dataset: {
            ...datasetBootstrapFixture.data.dataset,
            folder_path: [],
          },
          summary: {
            ...datasetBootstrapFixture.data.summary,
            actual_oss_bytes: null,
            required_physical_bytes: "0",
            calculation_state: "PARTIAL",
          },
        },
      }),
    ),
  );
});
afterEach(() => {
  cleanup();
  server.resetHandlers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  capacityRequests.mockReset();
  useShellStore.getState().clearSensitiveState();
});
afterAll(() => {
  server.close();
  resetRuntimeConfigForTests();
});

function respondCapacity(state: "SETTLED" | "PARTIAL") {
  server.use(
    http.get(
      "*/datasets/:datasetId/versions/:versionId/capacity-facts",
      ({ params }) => {
        capacityRequests(params.versionId);
        return HttpResponse.json({
          ...datasetVersionCapacityFixture,
          data: {
            ...datasetVersionCapacityFixture.data,
            version_id: params.versionId,
            state,
            required_physical_bytes: state === "SETTLED" ? "5306813532" : null,
            actual_oss_bytes: state === "SETTLED" ? "5306813532" : null,
          },
        });
      },
    ),
  );
}
function mount(tab: string) {
  const router = createMemoryRouter(
    [{ path: "/datasets/:datasetId", element: <DatasetDetailPage /> }],
    {
      initialEntries: [
        `/datasets/${datasetIds.dataset}?tab=${tab}&versionId=${datasetIds.reviewing}`,
      ],
    },
  );
  render(
    <ProviderHarness>
      <RouterProvider router={router} />
    </ProviderHarness>,
  );
}

describe("Dataset capacity display", () => {
  it.each(["overview", "episodes"])(
    "shows measured version capacity on the %s tab",
    async (tab) => {
      respondCapacity("SETTLED");
      mount(tab);
      await waitFor(() =>
        expect(screen.getAllByText("4.9 GB").length).toBeGreaterThan(0),
      );
      expect(capacityRequests).toHaveBeenCalledWith(datasetIds.reviewing);
      expect(screen.queryByText("0 KB")).not.toBeInTheDocument();
    },
  );

  it("does not turn unavailable storage into zero bytes", async () => {
    respondCapacity("PARTIAL");
    mount("overview");
    await screen.findByRole("heading", { name: "概要" });
    await waitFor(() => expect(capacityRequests).toHaveBeenCalled());
    expect(screen.queryByText("0 KB")).not.toBeInTheDocument();
    expect(screen.queryByText("4.9 GB")).not.toBeInTheDocument();
  });
});
