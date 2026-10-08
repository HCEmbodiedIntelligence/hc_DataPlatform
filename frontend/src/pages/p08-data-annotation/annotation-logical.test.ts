import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import { useShellStore } from "../../shared/scope/shell-store";
import { loadAnnotationLogicalData } from "../../features/annotation/runtime/logical-data";
import {
  createVisualAnnotationBundle,
  visualAnnotationScope as scope,
} from "./testing/annotation-fixture";
import {
  bundleLogicalFixture,
  installAnnotationSession,
} from "./testing/annotation-logical-fixture";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
const mocked = vi.mocked(request);
const bundle = () =>
  createVisualAnnotationBundle({ mode: "annotation", cameraCount: 2 });
beforeEach(() => installAnnotationSession(scope));
afterEach(() => {
  vi.resetAllMocks();
  useShellStore.getState().clearSensitiveState();
});

it("uses the server task mapping with explicit scope and no latest lookup", async () => {
  const fixture = bundle();
  const wire = bundleLogicalFixture(fixture, scope);
  mocked.mockResolvedValue(wire as never);
  const logical = await loadAnnotationLogicalData(scope, fixture.task);
  expect(logical.selection).toMatchObject({
    versionId: wire.snapshot.selection.version_id,
    revisionId: wire.snapshot.selection.revision_id,
    snapshotToken: wire.snapshot_token,
  });
  expect(logical.streams).toHaveLength(3);
  expect(mocked).toHaveBeenCalledExactlyOnceWith(
    expect.objectContaining({
      path: `/projects/${scope.projectId}/regions/${scope.regionCode}/annotation-tasks/${fixture.task.task_id}/logical`,
      scopeMode: "explicit",
      scope,
      cache: "no-store",
    }),
  );
});
it.each([
  "task",
  "organization",
  "project",
  "region",
  "dataset",
  "version",
  "count",
  "duplicate",
  "hash",
  "episode",
])("rejects mismatched %s before exposing streams", async (kind) => {
  const fixture = bundle();
  const task = fixture.task;
  const wire = bundleLogicalFixture(fixture, scope);
  const fixed = wire.snapshot.selection;
  if (kind === "task") wire.task_id = "different-task";
  if (kind === "organization") fixed.organization_id = "different";
  if (kind === "project") fixed.project_id = "different";
  if (kind === "region") fixed.region_code = "different";
  if (kind === "dataset") fixed.dataset_id = "different";
  if (kind === "version") fixed.version_id = "version_lance_999";
  if (kind === "count") wire.snapshot.streams[0]!.sample_count--;
  if (kind === "duplicate")
    wire.snapshot.streams.push(wire.snapshot.streams[0]!);
  if (kind === "hash" || kind === "episode") {
    task.base_lance_version = null;
    task.data_ref = {
      identity: {
        organization_id: scope.organizationId,
        project_id: scope.projectId,
        region_code: scope.regionCode,
        dataset_id: task.dataset_id,
        episode_id: "episode-fixed",
        rollout_id: task.rollout_id,
      },
      data_version_id: `dv_${"a".repeat(64)}`,
      version: 1,
      storage_format: "lerobot_v3",
      content_hash: "b".repeat(64),
      schema_snapshot_id: "schema-fixed",
      package_prefix: "fixed/package",
      frame_count: task.base_step_count!,
    };
    if (kind === "hash") wire.snapshot.content_sha256 = "c".repeat(64);
    else wire.snapshot.episode_id = "different";
  }
  mocked.mockResolvedValue(wire as never);
  await expect(loadAnnotationLogicalData(scope, task)).rejects.toMatchObject({
    code: "CONTRACT_MISMATCH",
  });
});
it("propagates missing fixed mapping without trying legacy media or latest data", async () => {
  mocked.mockRejectedValue({
    httpStatus: 409,
    code: "ANNOTATION_LOGICAL_BINDING_UNAVAILABLE",
  });
  await expect(
    loadAnnotationLogicalData(scope, bundle().task),
  ).rejects.toMatchObject({ httpStatus: 409 });
  expect(mocked).toHaveBeenCalledTimes(1);
});
it.each(["logout", "revoke"])(
  "discards an outstanding response after %s",
  async (kind) => {
    const fixture = bundle();
    mocked.mockImplementation(async (options) => {
      if (kind === "logout") useShellStore.getState().clearSensitiveState();
      else useShellStore.getState().setSessionScopes([], 2);
      expect(options.signal?.aborted).toBe(true);
      return bundleLogicalFixture(fixture, scope) as never;
    });
    await expect(
      loadAnnotationLogicalData(scope, fixture.task),
    ).rejects.toBeDefined();
  },
);

it("rebases nonzero source time for zero-based annotation ranges while preserving fixed sample reads", async () => {
  const fixture = bundle();
  const wire = bundleLogicalFixture(fixture, scope);
  const origin = 9_000_000_000n;
  for (const stream of wire.snapshot.streams) {
    stream.start_ns = String(origin);
    stream.end_ns = String(origin + BigInt(stream.end_ns!));
  }
  mocked.mockResolvedValueOnce(wire as never);
  const logical = await loadAnnotationLogicalData(scope, fixture.task);
  expect(logical.streams.every((stream) => stream.startNs === "0")).toBe(true);
  mocked.mockImplementation(async (options) => {
    const start = Number(options.query?.start),
      end = Number(options.query?.end);
    expect(options.query).toMatchObject({
      snapshot_id: wire.snapshot_id,
      snapshot_token: wire.snapshot_token,
      start: 0,
    });
    return {
      snapshot_id: wire.snapshot_id,
      stream_id: "joint-state",
      start,
      end,
      samples: Array.from({ length: end - start }, (_, i) => ({
        sample_id: `sample-${i}`,
        sample_index: start + i,
        timestamp_ns: String(
          origin + (BigInt(start + i) * 1_000_000_000n) / 30n,
        ),
        value: [i],
        valid: true,
        repeated: false,
        source_timestamps_ns: [],
        time_error_ns: "0",
      })),
    } as never;
  });
  const joint = logical.streams.find(
    (stream) => stream.modality === "joint_state",
  )!;
  const values = await joint.windowSource!.loadWindow(
    { startNs: "0", endNs: "66666667", lod: 0 },
    new AbortController().signal,
  );
  expect(values.timestampsNs).toEqual(["0", "33333333", "66666666"]);
  expect(values.values).toEqual([[0], [1], [2]]);
});
