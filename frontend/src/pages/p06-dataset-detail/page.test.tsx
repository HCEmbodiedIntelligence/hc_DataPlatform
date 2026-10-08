// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
let workingVersionId: string | null = null;
const collectionId = `cv_${"a".repeat(64)}`;

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
  workingVersionId = null;
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
          working_version_id: workingVersionId,
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
  return router;
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

function authorizePublication() {
  const store = useShellStore.getState();
  const scope = store.scope!;
  const authorization = store.authorization!;
  store.setSession(
    { actorId: "publisher", displayName: "Publisher", roleIds: [] },
    "publisher-session",
  );
  store.setScope(scope);
  store.setSessionScopes(
    [
      {
        ...scope,
        projectId: scope.projectId!,
        regionCodes: [scope.regionCode!],
        projectWide: false,
        capabilities: [],
      },
    ],
    1,
  );
  store.setAuthorization({
    ...authorization,
    capabilities: [
      ...authorization.capabilities,
      "dataset_version.publish",
      "annotation_task.read",
    ],
  });
  workingVersionId = `version_${collectionId}`;
}

function releaseServer() {
  const submitted: Record<string, unknown>[] = [];
  const reads = vi.fn();
  const legacy = vi.fn();
  let firstFailure: "network" | "conflict" | null = null;
  let receipt: Record<string, unknown>;
  const path =
    "*/projects/:projectId/regions/:regionCode/datasets/:datasetId/collection-releases";
  const result = (body: Record<string, unknown>) => {
    const selected = (body.episode_ids ?? [
      "episode-a",
      "episode-b",
    ]) as string[];
    return {
      dataset_id: datasetIds.dataset,
      dataset_version: body.dataset_version,
      collection_version_id: collectionId,
      collection_manifest_sha256: "b".repeat(64),
      content_hash: "c".repeat(64),
      eligible_episode_ids: selected,
      excluded_episodes: ["episode-a", "episode-b"]
        .filter((id) => !selected.includes(id))
        .map((episode_id) => ({ episode_id, reason: "NOT_SELECTED" })),
    };
  };
  server.use(
    http.post(`${path}/preflight`, async ({ request }) =>
      HttpResponse.json({
        ...result((await request.json()) as Record<string, unknown>),
        annotation_task_options: {
          "episode-a": ["task-a"],
          "episode-b": ["task-b"],
        },
      }),
    ),
    http.post(path, async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      submitted.push(body);
      receipt = {
        ...result(body),
        format: "hc-collection-release/1",
        version_id: `version_release_${"d".repeat(32)}`,
        document_sha256: "e".repeat(64),
        document_size: 100,
        created_at: "2026-10-08T03:00:00Z",
      };
      if (firstFailure) {
        const failure = firstFailure;
        firstFailure = null;
        return failure === "network"
          ? HttpResponse.error()
          : HttpResponse.json(
              { code: "COLLECTION_RELEASE_CONFLICT", detail: "审核已经变化" },
              { status: 409 },
            );
      }
      return HttpResponse.json(receipt, { status: 201 });
    }),
    http.get(`${path}/:versionId`, ({ params }) => {
      reads(params.versionId);
      return HttpResponse.json(receipt);
    }),
    http.post("*/datasets/publications", () => {
      legacy();
      return HttpResponse.json({}, { status: 500 });
    }),
  );
  return {
    submitted,
    reads,
    legacy,
    failOnce: (failure: typeof firstFailure) => {
      firstFailure = failure;
    },
  };
}

async function openCollectionPublication() {
  authorizePublication();
  const router = mount("versions");
  await userEvent.click(
    await screen.findByRole("button", { name: "手动发布版本" }),
  );
  await userEvent.type(screen.getByRole("textbox", { name: "版本名称" }), "v1");
  await userEvent.click(screen.getByRole("button", { name: "预检发布内容" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "发布并定版" })).toBeEnabled(),
  );
  return router;
}

describe("P06 collection business publication entry", () => {
  it("publishes an explicitly selected subset, retries the identical reviewed request and navigates to the fixed receipt", async () => {
    const api = releaseServer();
    api.failOnce("network");
    const router = await openCollectionPublication();
    await userEvent.click(screen.getByRole("checkbox", { name: "episode-b" }));
    expect(screen.getByRole("button", { name: "发布并定版" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "预检发布内容" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "发布并定版" })).toBeEnabled(),
    );
    await userEvent.click(screen.getByRole("button", { name: "发布并定版" }));
    await screen.findByText("发布操作未完成");
    await userEvent.click(screen.getByRole("button", { name: "发布并定版" }));
    await waitFor(() =>
      expect(api.reads).toHaveBeenCalledWith(
        `version_release_${"d".repeat(32)}`,
      ),
    );
    expect(api.submitted).toHaveLength(2);
    expect(api.submitted[0]).toEqual(api.submitted[1]);
    expect(api.submitted[0]).toMatchObject({
      episode_ids: ["episode-a"],
      expected_content_hash: "c".repeat(64),
      collection_version_id: collectionId,
    });
    expect(api.legacy).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(router.state.location.search).toContain(
        `version_release_${"d".repeat(32)}`,
      ),
    );
  });
  it("invalidates a reviewed preview after an approval conflict", async () => {
    const api = releaseServer();
    api.failOnce("conflict");
    await openCollectionPublication();
    await userEvent.click(screen.getByRole("button", { name: "发布并定版" }));
    await screen.findByText("发布操作未完成");
    expect(screen.getByRole("button", { name: "发布并定版" })).toBeDisabled();
    expect(api.reads).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "预检发布内容" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "发布并定版" })).toBeEnabled(),
    );
  });
  it("closes the reviewed publication when publication permission is revoked", async () => {
    const api = releaseServer();
    await openCollectionPublication();
    act(() => {
      const auth = useShellStore.getState().authorization!;
      useShellStore
        .getState()
        .setAuthorization({
          ...auth,
          capabilities: auth.capabilities.filter(
            (capability) => capability !== "dataset_version.publish",
          ),
        });
    });
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "发布并定版" }),
      ).not.toBeInTheDocument(),
    );
    expect(api.submitted).toHaveLength(0);
  });
});
