import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EpisodePackageBinding } from "../../features/datasets/api/episode-package-wire";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import { createDatasetPackageWindowSource } from "./package-window-source";

const scope = {
  organizationId: "org-a",
  projectId: "project-a",
  regionCode: "region-a",
};
const baseNs = 1750000000000000001n;
const binding: EpisodePackageBinding = {
  data: {
    identity: {
      organization_id: scope.organizationId,
      project_id: scope.projectId,
      region_code: scope.regionCode,
      dataset_id: "dataset_test",
      episode_id: "episode_test",
      rollout_id: "explicit-rollout",
    },
    data_version_id: "dv_test",
    version: 3,
    content_hash: "a".repeat(64),
    storage_format: "lerobot_v3",
    schema_snapshot_id: "schema-test",
    package_prefix: "test/pinned/",
    frame_count: 5000,
  },
  rollout_id: "explicit-rollout",
  feature_key: "observation.state",
  modality_key: "/joint_states",
  value_kind: "VECTOR",
  start_step: 0,
  end_step: 5000,
};
const input = {
  scope,
  datasetId: binding.data.identity.dataset_id,
  binding,
  streamStartNs: String(baseNs),
  streamEndNs: String(baseNs + 50000n),
};
function viewerWindow(start: number, end: number) {
  return {
    startNs: String(baseNs + BigInt(start * 10)),
    endNs: String(baseNs + BigInt(end * 10)),
    lod: 0,
  };
}
function page(start: number, end: number) {
  return {
    data: binding.data,
    start_step: start,
    end_step: end,
    steps: Array.from({ length: end - start }, (_, index) => ({
      schema_version: "1",
      rollout_id: binding.rollout_id,
      step_index: start + index,
      timestamp_ns: String(baseNs + BigInt((start + index) * 10)),
      modalities: {
        [binding.modality_key]: {
          names: ["elbow"],
          positions: [start + index],
          units: ["rad"],
        },
      },
      source_timestamps_ns: { [binding.modality_key]: [] },
      time_error_ns: { [binding.modality_key]: 0 },
      valid: { [binding.modality_key]: true },
      repeated: { [binding.modality_key]: false },
      sample_valid: false,
    })),
  };
}
function json(value: unknown) {
  return new Response(JSON.stringify(value), { status: 200 });
}

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "/api/v1",
    sseBaseUrl: "/api/v1",
    buildVersion: "test",
    releaseEnv: "test",
  });
  const shell = useShellStore.getState();
  shell.finishScopeChange();
  shell.setSession(
    { actorId: "actor-test", displayName: "Test", roleIds: [] },
    "test-token",
  );
  shell.setScope(scope);
});
afterEach(() => {
  vi.unstubAllGlobals();
  resetRuntimeConfigForTests();
});

describe("pinned Episode package numeric source through the scoped HTTP client", () => {
  it("pages a long range within reader limits, preserves ns/joint units and uses declared runtime routes", async () => {
    const fetch = vi.fn(async (raw: string, init: RequestInit) => {
      const url = new URL(raw, "http://localhost");
      expect(url.pathname).toBe(
        "/api/v1/projects/project-a/regions/region-a/datasets/dataset_test/episodes/episode_test/data-versions/dv_test/steps",
      );
      expect(url.searchParams.getAll("columns")).toEqual(["/joint_states"]);
      expect(url.searchParams.has("version")).toBe(false);
      const headers = new Headers(init.headers);
      expect(headers.get("Authorization")).toBe("Bearer test-token");
      expect(headers.get("X-Organization-Id")).toBe(scope.organizationId);
      expect(headers.get("X-Project-Id")).toBe(scope.projectId);
      expect(headers.get("X-Region-Code")).toBe(scope.regionCode);
      expect(init.cache).toBe("no-store");
      const start = Number(url.searchParams.get("start_step"));
      const end = Number(url.searchParams.get("end_step"));
      expect(end - start).toBeLessThanOrEqual(4096);
      return json(page(start, end));
    });
    vi.stubGlobal("fetch", fetch);
    const payload = await createDatasetPackageWindowSource(input).loadWindow(
      viewerWindow(0, 5000),
      new AbortController().signal,
    );
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(payload.timestampsNs[0]).toBe(String(baseNs));
    expect(payload.timestampsNs.at(-1)).toBe(String(baseNs + 49990n));
    expect(payload.values?.at(-1)).toEqual([4999]);
    expect(payload.series).toEqual([
      { id: "series-1", displayName: "elbow", unit: "rad" },
    ]);
  });

  it.each([
    "organization_id",
    "project_id",
    "region_code",
    "dataset_id",
    "episode_id",
    "rollout_id",
  ] as const)("rejects response identity changes to %s", async (key) => {
    const response = page(0, 2);
    response.data = {
      ...response.data,
      identity: { ...response.data.identity, [key]: "foreign" },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json(response)),
    );
    await expect(
      createDatasetPackageWindowSource(input).loadWindow(
        viewerWindow(0, 2),
        new AbortController().signal,
      ),
    ).rejects.toMatchObject({ problemCode: "P06_WINDOW_IDENTITY_MISMATCH" });
  });

  it.each([
    { data_version_id: "dv_other" },
    { version: 4 },
    { content_hash: "b".repeat(64) },
    { schema_snapshot_id: "other-schema" },
    { package_prefix: "other/" },
    { frame_count: 5001 },
  ])("rejects changed immutable version fields %j", async (changed) => {
    const response = page(0, 2);
    response.data = { ...response.data, ...changed };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json(response)),
    );
    await expect(
      createDatasetPackageWindowSource(input).loadWindow(
        viewerWindow(0, 2),
        new AbortController().signal,
      ),
    ).rejects.toMatchObject({ problemCode: "P06_WINDOW_IDENTITY_MISMATCH" });
  });

  it.each([
    "missing-row",
    "duplicate-step",
    "out-of-order-time",
    "foreign-rollout",
    "missing-validity",
    "missing-channel",
  ])("rejects corrupt or incomplete records: %s", async (mode) => {
    const response = page(0, 2);
    const second = response.steps[1]!;
    if (mode === "missing-row") response.steps.pop();
    if (mode === "duplicate-step") second.step_index = 0;
    if (mode === "out-of-order-time") second.timestamp_ns = String(baseNs);
    if (mode === "foreign-rollout") second.rollout_id = "another-rollout";
    if (mode === "missing-validity") second.valid = {};
    if (mode === "missing-channel") second.modalities = {};
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json(response)),
    );
    await expect(
      createDatasetPackageWindowSource(input).loadWindow(
        viewerWindow(0, 2),
        new AbortController().signal,
      ),
    ).rejects.toMatchObject({ problemCode: "P06_WINDOW_RECORD_MISMATCH" });
  });

  it("skips invalid channel samples without using aggregate row validity", async () => {
    const response = page(0, 2);
    response.steps[1]!.valid[binding.modality_key] = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json(response)),
    );
    const payload = await createDatasetPackageWindowSource(input).loadWindow(
      viewerWindow(0, 2),
      new AbortController().signal,
    );
    expect(payload.timestampsNs).toEqual([String(baseNs)]);
    expect(payload.values).toEqual([[0]]);
  });

  it("cancels before another page and refuses bindings outside the selected scope", async () => {
    const controller = new AbortController();
    const fetch = vi.fn(async () => {
      controller.abort();
      return json(page(0, 4096));
    });
    vi.stubGlobal("fetch", fetch);
    await expect(
      createDatasetPackageWindowSource(input).loadWindow(
        viewerWindow(0, 5000),
        controller.signal,
      ),
    ).rejects.toThrow();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(() =>
      createDatasetPackageWindowSource({
        ...input,
        scope: { ...scope, regionCode: "other" },
      }),
    ).toThrow("数据绑定与当前作用域或数值通道不一致");
  });
});
