import { adaptLogicalViewerStreams } from "../../features/datasets/episodes/logical-viewer";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  installAnnotationSession,
  annotationLogicalFixture,
  logicalMediaFixture,
} from "./testing/annotation-logical-fixture";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
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
beforeEach(() => {
  installAnnotationSession(scope);
  configureRuntime({
    apiBaseUrl: "/api/v1",
    sseBaseUrl: "/api/v1/events",
    buildVersion: "test",
    releaseEnv: "test",
  });
});
afterEach(() => {
  useShellStore.getState().clearSensitiveState();
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
  const logical = annotationLogicalFixture(task, scope, [
    { camera_id: "color", topic: "/camera/rgb" },
    { camera_id: "depth", topic: "/camera/depth" },
  ]);
  logical.snapshot.selection.version_id = "version-fixed-collection";
  logical.snapshot.streams[1]!.kind = "DEPTH";
  requestMock.mockImplementation(async (options) => {
    if (options.path.endsWith("/logical")) return logical as never;
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
  return { task, data, metadata, history, logical };
}

it("loads fixed package metadata and exposes RGB plus official depth preview without a Lance lookup", async () => {
  const { task, data, logical } = install();
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
    ...logicalMediaFixture(
      logical,
      "depth",
      "https://media.invalid/official-preview.mp4",
    ),
    kind: "depth-preview",
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
      path: expect.stringContaining("/logical/streams/depth/media/authorize"),
    }),
  );
});

it("reads joint values from the fixed logical snapshot and rejects a changed identity", async () => {
  const { task, logical } = install();
  const bundle = await loadRuntimeAnnotationBundle(
    scope,
    task.task_id,
    "annotation",
  );
  let changed = false;
  requestMock.mockImplementation(async (options) => {
    expect(options.path).toContain("/logical/streams/joint-state/samples");
    expect(options.query).toMatchObject({
      snapshot_id: logical.snapshot_id,
      snapshot_token: logical.snapshot_token,
    });
    const start = Number(options.query?.start),
      end = Number(options.query?.end);
    return {
      snapshot_id: changed ? `snapshot_${"e".repeat(64)}` : logical.snapshot_id,
      stream_id: "joint-state",
      start,
      end,
      samples: Array.from({ length: end - start }, (_, i) => ({
        sample_id: `sample-${start + i}`,
        sample_index: start + i,
        timestamp_ns: String((BigInt(start + i) * 1_000_000_000n) / 30n),
        value: [1, 2],
        valid: true,
        repeated: false,
        source_timestamps_ns: [],
        time_error_ns: "0",
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
  const fresh = buildRuntimeJointAngleStream({
    bundle: {
      ...bundle,
      logical: {
        ...bundle.logical!,
        streams: adaptLogicalViewerStreams(
          bundle.logical!.selection,
          bundle.logical!.view,
        ),
      },
    },
    scope,
  })!;
  await expect(
    fresh.windowSource!.loadWindow(window, new AbortController().signal),
  ).rejects.toMatchObject({
    problemCode: "LOGICAL_IDENTITY_MISMATCH",
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
