import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EpisodePackageBinding } from "../../features/datasets/api/episode-package-wire";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import { createDatasetPackageMediaSource } from "../../features/datasets/episodes/package-media-source";
import { adaptP06ViewerStreams } from "./viewer-stream-adapter";

const scope = {
  organizationId: "org-a",
  projectId: "project-a",
  regionCode: "region-a",
};
const binding: EpisodePackageBinding = {
  data: {
    identity: {
      organization_id: "org-a",
      project_id: "project-a",
      region_code: "region-a",
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
    frame_count: 30,
  },
  rollout_id: "explicit-rollout",
  feature_key: "observation.images.head_depth",
  modality_key: "/depth",
  value_kind: null,
  start_step: 5,
  end_step: 20,
};
function grant() {
  return {
    data: binding.data,
    feature: binding.feature_key,
    profile_id: "rs-2.58.4-dynamic-jet-h264-v1",
    kind: "depth-preview",
    source_reused: false,
    source_video_sha256: "b".repeat(64),
    media_sha256: "c".repeat(64),
    frame_count: 30,
    fps: 30,
    width: 640,
    height: 480,
    depth_min_m: 0.01,
    depth_max_m: 10,
    colorizer: {
      implementation: "pyrealsense2.colorizer",
      sdk_version: "2.58.4.10922",
      visual_preset: "Dynamic",
      color_scheme: "Jet",
      histogram_equalization_enabled: true,
      distance_color_scale: "per_frame_histogram",
      invalid_depth: 0,
      display_depth_units_m: 10 / 65535,
      display_input: "reconstructed_z16_in_memory_only",
    },
    media_url: "https://media.example.test/preview.mp4?temporary=fixture",
    expires_at: "2099-01-01T00:00:00Z",
  };
}
const input = {
  scope,
  datasetId: "dataset_test",
  binding,
  modality: "depth" as const,
};
function respond(body: unknown) {
  const fetch = vi.fn(
    async () => new Response(JSON.stringify(body), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
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

describe("fixed Episode RGB-D preview authorization", () => {
  it("lazily obtains and refreshes a scoped official-depth preview grant with the explicit window", async () => {
    const fetch = respond(grant());
    const source = createDatasetPackageMediaSource(input);
    expect(fetch).not.toHaveBeenCalled();
    const signal = new AbortController().signal;
    const expected = {
      url: grant().media_url,
      kind: "depth-preview",
      expiresAt: grant().expires_at,
      mediaStartSeconds: 5 / 30,
      mediaEndSeconds: 20 / 30,
    };
    expect(await source.authorize(signal)).toEqual(expected);
    expect(await source.refresh(signal)).toEqual(expected);
    expect(fetch).toHaveBeenCalledTimes(2);
    for (const call of fetch.mock.calls as unknown as [string, RequestInit][]) {
      expect(call[0]).toBe(
        "/api/v1/projects/project-a/regions/region-a/datasets/dataset_test/episodes/episode_test/data-versions/dv_test/media/observation.images.head_depth/authorize",
      );
      expect(call[1].method).toBe("POST");
      expect(call[1].cache).toBe("no-store");
      const headers = new Headers(call[1].headers);
      expect(headers.get("X-Organization-Id")).toBe("org-a");
      expect(headers.get("Authorization")).toBe("Bearer test-token");
    }
  });

  it.each([
    { feature: "other" },
    { data: { ...binding.data, data_version_id: "dv_other" } },
    { kind: "rgb-video" },
    { source_reused: true },
    { frame_count: 31 },
    { colorizer: null },
    { depth_min_m: 11 },
    { media_url: "javascript:alert(1)" },
    { expires_at: "2020-01-01T00:00:00Z" },
  ])("rejects a mismatched or unsafe depth grant %j", async (changed) => {
    respond({ ...grant(), ...changed });
    await expect(
      createDatasetPackageMediaSource(input).authorize(
        new AbortController().signal,
      ),
    ).rejects.toMatchObject({ problemCode: "P06_MEDIA_IDENTITY_MISMATCH" });
  });

  it("routes both RGB and official depth through the actual viewer adapter without Lance IDs", () => {
    const streams = adaptP06ViewerStreams(
      {
        scope: {
          organization_id: "org-a",
          project_id: "project-a",
          region_code: "region-a",
        },
        dataset_id: "dataset_test",
        version_id: "version_collection",
        episode_id: "episode_test",
        revision_id: "revision_test",
        ordinal: 0,
        content_sha256: "a".repeat(64),
        started_at_ns: "0",
        duration_ns: "1000000000",
        streams: ["RGB_VIDEO", "DEPTH"].map((kind, index) => ({
          episode_stream_id: `stream_${index}`,
          kind,
          channel_path: `camera-${index}`,
          t_start_ns: "0",
          t_end_ns: "1000000000",
          aligned_media_binding: null,
          data_binding: null,
          package_binding: binding,
        })),
      },
      "dataset_test",
    );
    expect(streams.map((s) => s.modality)).toEqual(["rgb", "depth"]);
    expect(
      streams.every(
        (s) => s.mediaSource !== undefined && s.windowSource === undefined,
      ),
    ).toBe(true);
    expect(streams[1]!.accessibleSummary).toContain("RealSense 官方动态着色");
    expect(streams[1]!.accessibleSummary).toContain("颜色不是固定距离刻度");
  });
});
