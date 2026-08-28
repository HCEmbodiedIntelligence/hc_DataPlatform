// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";
import { ProviderHarness } from "../../app/providers";
import { makeScopeKey } from "../../entities/scope";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import GlobalRobotSearchDialog from "./GlobalRobotSearchDialog";
import { searchDataSources, searchDatasets, searchRobots } from "./api";

const scope = {
  organizationId: "org-search",
  projectId: "project-search",
  regionCode: "cn-shanghai-01",
} as const;

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function page(ids: readonly string[], cursor: string | null = null) {
  return {
    query: "星舟",
    items: ids.map((id) => ({
      entity_type: "ROBOT",
      robot: {
        id,
        display_name: id === "robot-1" ? "星舟 017" : "星舟 018",
        serial_no: id === "robot-1" ? "SN-017" : "SN-018",
        lifecycle_status: "ACTIVE",
        connectivity: {
          state: "ONLINE",
          observed_at: "2026-08-21T08:00:00Z",
          source: "edge-agent",
          reason_code: null,
        },
      },
    })),
    page_info: {
      has_next_page: cursor !== null,
      has_previous_page: false,
      start_cursor: null,
      end_cursor: cursor,
    },
    snapshot_at: "2026-08-21T08:00:00Z",
    scope: { project_id: scope.projectId, region_code: scope.regionCode },
    request_id: "global-search-test",
    contract_version: "2026-08-21",
  };
}

function dataSourcePage(ids: readonly string[], cursor: string | null = null) {
  return {
    summary: {
      total_count: String(ids.length),
      online_count: String(ids.length),
      verified_bytes_today: "0",
      abnormal_count: "0",
      as_of: "2026-08-21T08:00:00Z",
      timezone: "Asia/Shanghai",
      definition_version: "p02-data-source-page/v1",
    },
    facets: {
      source_types: [],
      source_formats: [],
      robots: [],
      upload_policies: [],
      administrative_states: [],
      connectivity_states: [],
      credential_states: [],
      heartbeat_states: [],
    },
    items: ids.map((id) => ({
      id,
      scope: {
        organization_id: scope.organizationId,
        project_id: scope.projectId,
        region_code: scope.regionCode,
      },
      name: id === "source-1" ? "星舟边缘数据源" : "星舟导入数据源",
      source_type: "EDGE_AGENT",
      source_format: "MCAP",
      source_format_version: "1",
      adapter_version: "2026.08",
      binding: {
        kind: "EDGE_AGENT",
        agent_id: "edge-agent-1",
        display_name: "星舟边缘网关",
      },
      administrative_state: "ENABLED",
      credential: {
        kind: "TOKEN",
        state: "CONFIGURED",
        credential_ref: "credential-1",
        masked_hint: "…xxxx",
        version: "1",
        updated_at: "2026-08-21T08:00:00Z",
        expires_at: null,
        rotation_due_at: null,
      },
      connectivity: {
        state: "ONLINE",
        last_check_state: "SUCCEEDED",
        observed_config_version: "1",
        observed_credential_version: "1",
        checked_at: "2026-08-21T08:00:00Z",
        safe_error: null,
      },
      heartbeat: null,
      upload_policy: {
        code: "STANDARD",
        label: "标准策略",
        max_object_size_bytes: "1073741824",
      },
      last_upload: null,
      config_version: "1",
      credential_version: "1",
      etag: '"v1"',
      allowed_actions: ["VIEW"],
      blocked_reasons: [],
      created_at: "2026-08-21T08:00:00Z",
      updated_at: "2026-08-21T08:00:00Z",
    })),
    page_info: {
      has_next_page: cursor !== null,
      has_previous_page: false,
      start_cursor: null,
      end_cursor: cursor,
    },
    snapshot_at: "2026-08-21T08:00:00Z",
    allowed_actions: [],
    component_errors: [],
    scope: {
      organization_id: scope.organizationId,
      project_id: scope.projectId,
      region_code: scope.regionCode,
    },
    request_id: "data-source-search-test",
    contract_version: "ingest.v1alpha1",
  };
}

function datasetPage(ids: readonly string[], cursor: string | null = null) {
  return {
    items: ids.map((id) => ({
      scope: {
        organization_id: scope.organizationId,
        project_id: scope.projectId,
        region_code: scope.regionCode,
      },
      dataset_id: id,
      folder_path: ["星舟项目"],
      name: id === "dataset_search_1" ? "星舟装配数据集" : "星舟巡检数据集",
      availability: "AVAILABLE",
      dataset_created_at: "2026-08-21T08:00:00Z",
      dataset_activity_at: "2026-08-21T08:10:00Z",
      current_version: null,
      episode_count: id === "dataset_search_1" ? "12" : "4",
      pending_review_version_count: "0",
      returned_version_count: "0",
      actionable_draft_count: "0",
      allowed_actions: [
        { action: "OPEN_DATASET", allowed: true, blocked_reasons: [] },
      ],
    })),
    page_info: {
      after: cursor,
      before: null,
      has_next: cursor !== null,
      has_previous: false,
      limit: 20,
      total_count: String(ids.length),
    },
    snapshot_at: "2026-08-21T08:00:00Z",
    snapshot_id: "dataset-search-snapshot",
    scope: {
      organization_id: scope.organizationId,
      project_id: scope.projectId,
      region_code: scope.regionCode,
    },
    request_id: "dataset-search-test",
    contract_version: "dataset-version-review.v1alpha1",
  };
}

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
    </output>
  );
}

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "/api/v1",
    sseBaseUrl: "/api/v1",
    buildVersion: "global-search-test",
    releaseEnv: "test",
  });
  useShellStore
    .getState()
    .setSession(
      { actorId: "robot-reader", displayName: "检索用户", roleIds: [] },
      "search-token",
    );
  useShellStore.getState().setScope(scope);
  useShellStore.getState().setAuthorization({
    scopeKey: makeScopeKey(scope),
    roleVersion: "role-search",
    capabilities: ["robot.read"],
    fetchedAt: "2026-08-21T08:00:00Z",
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  resetRuntimeConfigForTests();
  useShellStore.getState().setSession(null, null);
  useShellStore.setState({
    scope: null,
    scopeKey: makeScopeKey({ organizationId: "unscoped" }),
    authorization: null,
  });
});

describe("global robot search", () => {
  it("uses the real scoped API, rejects mismatched scope responses, and keeps the query in the URL only", async () => {
    const fetchMock = vi.fn<typeof fetch>(() =>
      Promise.resolve(jsonResponse(page(["robot-1"]))),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(searchRobots(scope, "星舟", undefined)).resolves.toMatchObject(
      {
        query: "星舟",
        items: [{ robot: { id: "robot-1" } }],
      },
    );
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe(
      "/api/v1/projects/project-search/regions/cn-shanghai-01/search?q=%E6%98%9F%E8%88%9F&limit=10",
    );
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer search-token");
    expect(headers.get("X-Project-Id")).toBe(scope.projectId);
    expect(headers.get("X-Region-Code")).toBe(scope.regionCode);

    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        ...page(["robot-1"]),
        scope: { project_id: "foreign", region_code: scope.regionCode },
      }),
    );
    await expect(searchRobots(scope, "星舟", undefined)).rejects.toMatchObject({
      code: "CONTRACT_MISMATCH",
    });
  });

  it("uses the existing real scoped data-source API and rejects a mismatched organization response", async () => {
    const fetchMock = vi.fn<typeof fetch>(() =>
      Promise.resolve(jsonResponse(dataSourcePage(["source-1"]))),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      searchDataSources(scope, "星舟", undefined),
    ).resolves.toMatchObject({ items: [{ id: "source-1" }] });
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe(
      "/api/v1/projects/project-search/regions/cn-shanghai-01/data-sources/page?q=%E6%98%9F%E8%88%9F&sort=name%3Aasc%2Cid%3Aasc&limit=10",
    );
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer search-token");
    expect(headers.get("X-Organization-Id")).toBe(scope.organizationId);
    expect(headers.get("X-Project-Id")).toBe(scope.projectId);
    expect(headers.get("X-Region-Code")).toBe(scope.regionCode);

    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        ...dataSourcePage(["source-1"]),
        scope: {
          organization_id: "foreign-org",
          project_id: scope.projectId,
          region_code: scope.regionCode,
        },
      }),
    );
    await expect(searchDataSources(scope, "星舟", undefined)).rejects.toThrow(
      "SCOPE_MISMATCH",
    );
  });

  it("uses the existing real scoped dataset API and rejects a mismatched organization response", async () => {
    const fetchMock = vi.fn<typeof fetch>(() =>
      Promise.resolve(jsonResponse(datasetPage(["dataset_search_1"]))),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      searchDatasets(scope, "星舟", undefined),
    ).resolves.toMatchObject({ items: [{ datasetId: "dataset_search_1" }] });
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe(
      "/api/v1/projects/project-search/datasets?q=%E6%98%9F%E8%88%9F&sort=name%3Aasc%2Cdataset_id%3Aasc&limit=20",
    );
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer search-token");
    expect(headers.get("X-Organization-Id")).toBe(scope.organizationId);
    expect(headers.get("X-Project-Id")).toBe(scope.projectId);
    expect(headers.get("X-Region-Code")).toBe(scope.regionCode);

    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        ...datasetPage(["dataset_search_1"]),
        scope: {
          organization_id: "foreign-org",
          project_id: scope.projectId,
          region_code: scope.regionCode,
        },
      }),
    );
    await expect(
      searchDatasets(scope, "星舟", undefined),
    ).rejects.toMatchObject({
      code: "CONTRACT_MISMATCH",
    });
  });

  it("shows search states, loads the next cursor page, and navigates through the normal robot route", async () => {
    const user = userEvent.setup();
    const close = vi.fn();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(page(["robot-1"], "cursor-2")))
      .mockResolvedValueOnce(jsonResponse(page(["robot-2"])));
    vi.stubGlobal("fetch", fetchMock);
    render(
      <ProviderHarness>
        <MemoryRouter>
          <GlobalRobotSearchDialog open onClose={close} />
          <LocationProbe />
        </MemoryRouter>
      </ProviderHarness>,
    );

    expect(await screen.findByText("检索当前范围内的实体")).toBeVisible();
    await user.type(
      screen.getByRole("textbox", { name: "搜索机器人、数据源和数据集" }),
      "星舟",
    );
    await user.click(screen.getByRole("button", { name: "搜索" }));
    expect(await screen.findByText("星舟 017")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "加载更多机器人" }));
    expect(await screen.findByText("星舟 018")).toBeVisible();

    await user.click(screen.getByRole("button", { name: /星舟 017/ }));
    expect(close).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/settings/robots?robotId=robot-1",
    );

    const urls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(urls).toEqual([
      "/api/v1/projects/project-search/regions/cn-shanghai-01/search?q=%E6%98%9F%E8%88%9F&limit=10",
      "/api/v1/projects/project-search/regions/cn-shanghai-01/search?q=%E6%98%9F%E8%88%9F&cursor=cursor-2&limit=10",
    ]);
  });

  it("searches permitted entity lanes in parallel and opens the selected data source", async () => {
    useShellStore.getState().setAuthorization({
      scopeKey: makeScopeKey(scope),
      roleVersion: "role-search-both",
      capabilities: ["robot.read", "ingest_source.read", "datasets.read"],
      fetchedAt: "2026-08-21T08:00:00Z",
    });
    const user = userEvent.setup();
    const close = vi.fn();
    const fetchMock = vi.fn<typeof fetch>((input) => {
      const url = String(input);
      return Promise.resolve(
        jsonResponse(
          url.includes("/data-sources/page")
            ? dataSourcePage(["source-1"])
            : url.includes("/datasets")
              ? datasetPage(["dataset_search_1"])
              : page(["robot-1"]),
        ),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <ProviderHarness>
        <MemoryRouter>
          <GlobalRobotSearchDialog open onClose={close} />
          <LocationProbe />
        </MemoryRouter>
      </ProviderHarness>,
    );

    await user.type(
      screen.getByRole("textbox", { name: "搜索机器人、数据源和数据集" }),
      "星舟",
    );
    await user.click(screen.getByRole("button", { name: "搜索" }));

    expect(await screen.findByText("星舟 017")).toBeVisible();
    expect(await screen.findByText("星舟边缘数据源")).toBeVisible();
    expect(await screen.findByText("星舟装配数据集")).toBeVisible();
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual(
      expect.arrayContaining([
        "/api/v1/projects/project-search/regions/cn-shanghai-01/search?q=%E6%98%9F%E8%88%9F&limit=10",
        "/api/v1/projects/project-search/regions/cn-shanghai-01/data-sources/page?q=%E6%98%9F%E8%88%9F&sort=name%3Aasc%2Cid%3Aasc&limit=10",
        "/api/v1/projects/project-search/datasets?q=%E6%98%9F%E8%88%9F&sort=name%3Aasc%2Cdataset_id%3Aasc&limit=20",
      ]),
    );

    await user.click(screen.getByRole("button", { name: /星舟边缘数据源/ }));
    expect(close).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/ingest/sources?sourceId=source-1",
    );
  });

  it("paginates data sources without issuing an unauthorized robot search", async () => {
    useShellStore.getState().setAuthorization({
      scopeKey: makeScopeKey(scope),
      roleVersion: "role-search-sources-only",
      capabilities: ["ingest_source.read"],
      fetchedAt: "2026-08-21T08:00:00Z",
    });
    const user = userEvent.setup();
    const fetchMock = vi.fn<typeof fetch>((input) => {
      const url = String(input);
      if (!url.includes("/data-sources/page"))
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      return Promise.resolve(
        jsonResponse(
          url.includes("after=source-cursor")
            ? dataSourcePage(["source-2"])
            : dataSourcePage(["source-1"], "source-cursor"),
        ),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <ProviderHarness>
        <MemoryRouter>
          <GlobalRobotSearchDialog open onClose={vi.fn()} />
        </MemoryRouter>
      </ProviderHarness>,
    );

    await user.type(
      screen.getByRole("textbox", { name: "搜索机器人、数据源和数据集" }),
      "星舟",
    );
    await user.click(screen.getByRole("button", { name: "搜索" }));
    expect(await screen.findByText("星舟边缘数据源")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "加载更多数据源" }));
    expect(await screen.findByText("星舟导入数据源")).toBeVisible();
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/v1/projects/project-search/regions/cn-shanghai-01/data-sources/page?q=%E6%98%9F%E8%88%9F&sort=name%3Aasc%2Cid%3Aasc&limit=10",
      "/api/v1/projects/project-search/regions/cn-shanghai-01/data-sources/page?q=%E6%98%9F%E8%88%9F&sort=name%3Aasc%2Cid%3Aasc&after=source-cursor&limit=10",
    ]);
  });

  it("paginates datasets without querying unauthorized lanes and opens the normal detail route", async () => {
    useShellStore.getState().setAuthorization({
      scopeKey: makeScopeKey(scope),
      roleVersion: "role-search-datasets-only",
      capabilities: ["datasets.read"],
      fetchedAt: "2026-08-21T08:00:00Z",
    });
    const user = userEvent.setup();
    const close = vi.fn();
    const fetchMock = vi.fn<typeof fetch>((input) => {
      const url = String(input);
      if (!url.includes("/datasets"))
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      return Promise.resolve(
        jsonResponse(
          url.includes("after=dataset-cursor")
            ? datasetPage(["dataset_search_2"])
            : datasetPage(["dataset_search_1"], "dataset-cursor"),
        ),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <ProviderHarness>
        <MemoryRouter>
          <GlobalRobotSearchDialog open onClose={close} />
          <LocationProbe />
        </MemoryRouter>
      </ProviderHarness>,
    );

    await user.type(
      screen.getByRole("textbox", { name: "搜索机器人、数据源和数据集" }),
      "星舟",
    );
    await user.click(screen.getByRole("button", { name: "搜索" }));
    expect(await screen.findByText("星舟装配数据集")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "加载更多数据集" }));
    expect(await screen.findByText("星舟巡检数据集")).toBeVisible();

    await user.click(screen.getByRole("button", { name: /星舟装配数据集/ }));
    expect(close).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/datasets/dataset_search_1",
    );
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/v1/projects/project-search/datasets?q=%E6%98%9F%E8%88%9F&sort=name%3Aasc%2Cdataset_id%3Aasc&limit=20",
      "/api/v1/projects/project-search/datasets?q=%E6%98%9F%E8%88%9F&sort=name%3Aasc%2Cdataset_id%3Aasc&after=dataset-cursor&limit=20",
    ]);
  });

  it("shows a scoped permission state without sending a query", async () => {
    useShellStore.getState().setAuthorization({
      scopeKey: makeScopeKey(scope),
      roleVersion: "role-search-denied",
      capabilities: [],
      fetchedAt: "2026-08-21T08:00:00Z",
    });
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    render(
      <ProviderHarness>
        <MemoryRouter>
          <GlobalRobotSearchDialog open onClose={vi.fn()} />
        </MemoryRouter>
      </ProviderHarness>,
    );

    expect(await screen.findByText("无权访问")).toBeVisible();
    expect(
      screen.getByRole("textbox", { name: "搜索机器人、数据源和数据集" }),
    ).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
