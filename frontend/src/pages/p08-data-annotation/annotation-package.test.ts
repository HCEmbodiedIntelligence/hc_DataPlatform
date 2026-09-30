import { afterEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import { resetRuntimeConfigForTests } from "../../shared/config/runtime";
import { episodeDataReferenceWireSchema } from "../../features/datasets/api/episode-package-wire";
import {
  buildRuntimeCameraStreams,
  loadRuntimeAnnotationBundle,
} from "../../features/annotation/runtime/runtime-annotation-adapter";
import { buildRuntimeJointAngleStream } from "./joint-angle-stream";
import { checkedAnnotationRef } from "../../features/annotation/runtime/annotation-package";
import {
  createVisualAnnotationBundle,
  visualAnnotationScope as scope,
} from "./testing/annotation-fixture";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
const requestMock = vi.mocked(request);
afterEach(() => {
  vi.resetAllMocks();
  resetRuntimeConfigForTests();
});

function install() {
  const fixture = createVisualAnnotationBundle({
    mode: "annotation",
    cameraCount: 1,
  });
  const data = episodeDataReferenceWireSchema.parse({
    identity: {
      organization_id: scope.organizationId,
      project_id: scope.projectId,
      region_code: scope.regionCode,
      dataset_id: fixture.task.dataset_id,
      episode_id: "episode_fixed",
      rollout_id: fixture.task.rollout_id,
    },
    data_version_id: "dv_" + "a".repeat(64),
    version: 2,
    storage_format: "lerobot_v3",
    content_hash: "b".repeat(64),
    schema_snapshot_id: "schema-episode",
    package_prefix: "episodes/fixed/version",
    frame_count: fixture.task.base_step_count,
  });
  const binding = { base_lance_version: null, data_ref: data };
  const task = {
    ...fixture.task,
    ...binding,
    creation_source: "SYSTEM_EPISODE" as const,
  };
  const history = {
    ...fixture.history,
    task,
    revisions: fixture.history.revisions.map((r) => ({ ...r, ...binding })),
    submissions: fixture.history.submissions.map((s) => ({ ...s, ...binding })),
    reviews: fixture.history.reviews.map((r) => ({ ...r, data_ref: data })),
  };
  const metadata = {
    data,
    info: {
      fps: 30,
      total_frames: data.frame_count,
      features: {
        "observation.images.color": { dtype: "video" },
        "observation.images.depth": { dtype: "video" },
        "observation.state": { dtype: "float64" },
      },
      "hc.feature_mapping": {
        "/camera/rgb": "observation.images.color",
        "/camera/depth": "observation.images.depth",
        "/robot/joint_states": "observation.state",
      },
    },
    modalities: {
      depth: { "observation.images.depth": "hc/depth/depth/profile.json" },
    },
  };
  requestMock.mockImplementation(async (options) => {
    if (options.path.endsWith(`/annotation-tasks/${task.task_id}`))
      return task as never;
    if (options.path.endsWith("/annotation-tasks")) return [task] as never;
    if (options.path.endsWith("/history")) return history as never;
    if (options.path.endsWith("/draft"))
      return { ...fixture.draft, ...binding } as never;
    if (options.path.includes("/tag-schemas/")) return fixture.schema as never;
    if (options.path.endsWith(`/data-versions/${data.data_version_id}`))
      return metadata as never;
    throw new Error(`Unexpected ${options.path}`);
  });
  return { task, data, metadata, history };
}

it("loads fixed package metadata and exposes RGB plus official depth preview without a Lance lookup", async () => {
  const { task, data } = install();
  const bundle = await loadRuntimeAnnotationBundle(
    scope,
    task.task_id,
    "annotation",
  );
  expect(bundle.datasetVersion).toMatchObject({ data, frequency_hz: 30 });
  const streams = buildRuntimeCameraStreams({
    bundle,
    scope,
    mode: "annotation",
  });
  expect(streams.map((s) => s.modality)).toEqual(["rgb", "depth"]);
  expect(
    requestMock.mock.calls.some(([r]) =>
      /lance-versions|manifest-discovery/.test(r.path),
    ),
  ).toBe(false);
  requestMock.mockResolvedValue({
    data,
    feature: "observation.images.depth",
    profile_id: "rs-2.58.4-dynamic-jet-h264-v1",
    kind: "depth-preview",
    source_reused: false,
    source_video_sha256: "c".repeat(64),
    media_sha256: "d".repeat(64),
    frame_count: data.frame_count,
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
    media_url: "https://media.invalid/official-preview.mp4",
    expires_at: new Date(Date.now() + 60_000).toISOString(),
  } as never);
  const grant = await streams[1]!.mediaSource!.authorize(
    new AbortController().signal,
  );
  expect(grant.kind).toBe("depth-preview");
  expect(requestMock).toHaveBeenLastCalledWith(
    expect.objectContaining({
      method: "POST",
      path: expect.stringContaining(
        `/data-versions/${data.data_version_id}/media/observation.images.depth/authorize`,
      ),
    }),
  );
});

it("reads joint values from the immutable package and rejects a changed content hash", async () => {
  const { task, data } = install();
  const bundle = await loadRuntimeAnnotationBundle(
    scope,
    task.task_id,
    "annotation",
  );
  let changed = false;
  requestMock.mockImplementation(async (options) => {
    expect(options.path).toContain(
      `/data-versions/${data.data_version_id}/steps`,
    );
    expect(options.query).not.toHaveProperty("version");
    const start = Number(options.query?.startStep),
      end = Number(options.query?.endStep);
    return {
      data: changed ? { ...data, content_hash: "e".repeat(64) } : data,
      start_step: start,
      end_step: end,
      steps: Array.from({ length: end - start }, (_, offset) => ({
        schema_version: "1",
        rollout_id: task.rollout_id,
        step_index: start + offset,
        timestamp_ns: String((BigInt(start + offset) * 1_000_000_000n) / 30n),
        modalities: { "/robot/joint_states": [1, 2] },
        source_timestamps_ns: {},
        time_error_ns: {},
        valid: { "/robot/joint_states": true },
        repeated: {},
        sample_valid: true,
      })),
    } as never;
  });
  const window = { startNs: "0", endNs: "66666667", lod: 0 };
  const stream = buildRuntimeJointAngleStream({ bundle, scope })!;
  expect(
    (
      await stream.windowSource!.loadWindow(
        window,
        new AbortController().signal,
      )
    ).values?.[0],
  ).toEqual([1, 2]);
  changed = true;
  const fresh = buildRuntimeJointAngleStream({ bundle, scope })!;
  await expect(
    fresh.windowSource!.loadWindow(window, new AbortController().signal),
  ).rejects.toMatchObject({
    problemCode: "P08_JOINT_WINDOW_IDENTITY_MISMATCH",
  });
});

it("rejects cross-scope, mixed baselines and revisions bound to another package", async () => {
  const { task, data, history } = install();
  expect(() =>
    checkedAnnotationRef({ ...scope, organizationId: "another-org" }, task),
  ).toThrow();
  expect(() =>
    checkedAnnotationRef(scope, { ...task, base_lance_version: 1 }),
  ).toThrow();
  history.revisions[0]!.data_ref = { ...data, content_hash: "f".repeat(64) };
  await expect(
    loadRuntimeAnnotationBundle(scope, task.task_id, "annotation"),
  ).rejects.toMatchObject({ code: "CONTRACT_MISMATCH" });
});
