import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import {
  fetchDatasetFacets,
  fetchDatasets,
  fetchDatasetsPageCapabilities,
  fetchDatasetSummary,
} from "../../features/datasets/api/queries";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  datasetFacetsEnvelopeWireSchema,
  datasetListEnvelopeWireSchema,
  datasetSummaryEnvelopeWireSchema,
  datasetsPageCapabilitiesEnvelopeWireSchema,
} from "../../features/datasets/api/wire-schemas";
import {
  datasetFacetsFixture,
  datasetListFixture,
  datasetSummaryFixture,
  datasetsPageCapabilitiesFixture,
} from "../fixtures/datasets/core";
import {
  getDatasetScenario,
  resetDatasetScenario,
} from "../scenarios/datasets";
import { datasetHandlers } from "./datasets.handlers";

const server = setupServer(...datasetHandlers);
const baseUrl = "http://localhost/api/v1/projects/prj_fx_01";
const headers = {
  "X-Client-Version": "test",
  "X-Organization-Id": "org_fx_01",
  "X-Project-Id": "prj_fx_01",
  "X-Region-Code": "cn-shanghai",
};

beforeAll(() => {
  configureRuntime({
    apiBaseUrl: "http://localhost/api/v1",
    sseBaseUrl: "http://localhost/api/v1/events",
    buildVersion: "test",
    releaseEnv: "test",
  });
  useShellStore.getState().setScope({
    organizationId: "org_fx_01",
    projectId: "prj_fx_01",
    regionCode: "cn-shanghai",
  });
  server.listen({ onUnhandledRequest: "error" });
});
afterEach(() => {
  server.resetHandlers();
  resetDatasetScenario();
});
afterAll(() => {
  server.close();
  resetRuntimeConfigForTests();
});

describe("dataset happy-scenario scope", () => {
  it.each([
    "/datasets:page-capabilities",
    "/datasets:summary",
    "/datasets:facets",
    "/datasets?sort=activity_at%3Adesc%2Cdataset_id%3Adesc&limit=20",
  ])("serves %s for the default project", async (path) => {
    const response = await fetch(`${baseUrl}${path}`, { headers });
    expect(response.status).toBe(200);
  });
});

describe("dataset happy-scenario contracts", () => {
  it.each([
    ["list", datasetListEnvelopeWireSchema.safeParse(datasetListFixture)],
    [
      "summary",
      datasetSummaryEnvelopeWireSchema.safeParse(datasetSummaryFixture),
    ],
    ["facets", datasetFacetsEnvelopeWireSchema.safeParse(datasetFacetsFixture)],
    [
      "page capabilities",
      datasetsPageCapabilitiesEnvelopeWireSchema.safeParse(
        datasetsPageCapabilitiesFixture,
      ),
    ],
  ])("matches the %s response schema", (_name, result) => {
    expect(result.success ? [] : result.error.issues).toEqual([]);
  });
});

describe("dataset page query pipeline", () => {
  it("parses every independent page region", async () => {
    const [list, summary, facets, capabilities] = await Promise.all([
      fetchDatasets({ sort: "activityDesc", limit: 20 }),
      fetchDatasetSummary({}),
      fetchDatasetFacets({}),
      fetchDatasetsPageCapabilities(),
    ]);
    expect(list.items).toHaveLength(2);
    expect(summary.datasetCount).toBe("2");
    expect(facets.tasks.length).toBeGreaterThan(0);
    expect(capabilities.allowedActions).toContain("CREATE_DATASET");
  });
});

describe("dataset mock scenario routing", () => {
  it("ignores a contract mismatch owned by another domain", () => {
    expect(getDatasetScenario("?mockScenario=ingest:contract-mismatch")).toBe(
      "happy",
    );
    expect(getDatasetScenario("?mockScenario=datasets:contract-mismatch")).toBe(
      "contract-mismatch",
    );
  });
});
