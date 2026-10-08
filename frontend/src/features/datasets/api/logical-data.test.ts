import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../../shared/config/runtime";
import { useShellStore } from "../../../shared/scope/shell-store";
import {
  fetchLogicalSnapshot,
  fetchLogicalSamples,
  authorizeLogicalMedia,
  type LogicalSelection,
  type LogicalSnapshot,
} from "./logical-data";
import { adaptLogicalViewerStreams } from "../episodes/logical-viewer";

const scope = {
  organizationId: "org-logical",
  projectId: "project-logical",
  regionCode: "local",
};
const selection: LogicalSelection = {
  scope,
  datasetId: "dataset-logical",
  versionId: "version-frozen",
  revisionId: "revision-fixed",
  episodeId: "episode-fixed",
  contentSha256: "a".repeat(64),
  snapshotToken: "opaque-fixed-snapshot-token",
};
const view: LogicalSnapshot = {
  snapshot_id: `snapshot_${"b".repeat(64)}`,
  snapshot: {
    format: "hc-logical-snapshot/1",
    selection: {
      organization_id: scope.organizationId,
      project_id: scope.projectId,
      region_code: scope.regionCode,
      dataset_id: selection.datasetId,
      version_id: selection.versionId,
      revision_id: selection.revisionId,
    },
    episode_id: selection.episodeId,
    content_sha256: selection.contentSha256,
    streams: [
      {
        stream_id: "stream-joints",
        name: "/robot/joint_states",
        kind: "JOINT_STATE",
        value_kind: "VECTOR",
        sample_count: 3,
        temporal: true,
        start_ns: "1000000000",
        end_ns: "1100000000",
      },
    ],
  },
};
const stream = view.snapshot.streams[0]!;
const base =
  "http://localhost/api/v1/projects/project-logical/regions/local/datasets/dataset-logical/versions/version-frozen/episode-revisions/revision-fixed/logical";
const signal = () => new AbortController().signal;
const response = (value: unknown) =>
  new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });
const batch = () => ({
  snapshot_id: view.snapshot_id,
  stream_id: stream.stream_id,
  start: 0,
  end: 3,
  samples: [0, 1, 2].map((i) => ({
    sample_id: `sample-${i}`,
    sample_index: i,
    timestamp_ns: String(1000000000 + i * 33333333),
    value: { name: ["elbow"], position: [i] },
    valid: i !== 1,
    repeated: false,
    source_timestamps_ns: [String(1000000000 + i * 33333333)],
    time_error_ns: "0",
  })),
});
beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "http://localhost/api/v1",
    sseBaseUrl: "http://localhost/api/v1/events",
    buildVersion: "logical-test",
    releaseEnv: "test",
  });
  useShellStore
    .getState()
    .setSession(
      { actorId: "reader", displayName: "Reader", roleIds: [] },
      "test-session",
    );
  useShellStore.getState().setScope(scope);
  useShellStore.getState().setSessionScopes(
    [
      {
        ...scope,
        regionCodes: [scope.regionCode],
        projectWide: false,
        capabilities: [],
      },
    ],
    1,
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  useShellStore.getState().clearSensitiveState();
  resetRuntimeConfigForTests();
});

describe("frozen logical data consumer", () => {
  it("refuses a retained stream after its scope grant is withdrawn", async () => {
    const source = adaptLogicalViewerStreams(selection, view)[0]!.windowSource!;
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    useShellStore.getState().setSessionScopes([], 2);
    await expect(
      source.loadWindow(
        { startNs: "1000000000", endNs: "1100000000", lod: 1 },
        signal(),
      ),
    ).rejects.toBeDefined();
    expect(fetcher).not.toHaveBeenCalled();
  });
  it.each(["revoke", "new-session", "capability-revision"])(
    "quarantines already cached joint samples after %s",
    async (kind) => {
      const fetcher = vi.fn().mockResolvedValue(response(batch()));
      vi.stubGlobal("fetch", fetcher);
      const source = adaptLogicalViewerStreams(selection, view)[0]!
        .windowSource!;
      const window = { startNs: "1000000000", endNs: "1100000000", lod: 1 };
      expect((await source.loadWindow(window, signal())).values).toEqual([
        [0],
        [2],
      ]);
      if (kind === "revoke") useShellStore.getState().setSessionScopes([], 2);
      else if (kind === "new-session")
        useShellStore
          .getState()
          .setSession(
            { actorId: "new-reader", displayName: "New reader", roleIds: [] },
            "new-token",
          );
      else
        useShellStore
          .getState()
          .setSessionScopes(
            [
              {
                ...scope,
                regionCodes: [scope.regionCode],
                projectWide: false,
                capabilities: [],
              },
            ],
            2,
          );
      await expect(source.loadWindow(window, signal())).rejects.toBeDefined();
      expect(fetcher).toHaveBeenCalledTimes(1);
    },
  );
  it("propagates a server permission refusal without trying a historical endpoint", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: "FORBIDDEN",
          detail: "Scope grant withdrawn",
        }),
        { status: 403, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetcher);
    await expect(
      fetchLogicalSnapshot(selection, signal()),
    ).rejects.toMatchObject({ httpStatus: 403 });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("uses the generated logical endpoint with explicit scope and accepts additive fields", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(response({ ...view, future_field: true }));
    vi.stubGlobal("fetch", fetcher);
    expect(await fetchLogicalSnapshot(selection, signal())).toEqual(view);
    const [url, init] = fetcher.mock.calls[0]!;
    expect(url).toBe(`${base}?snapshot_token=${selection.snapshotToken}`);
    expect(new Headers(init.headers).get("X-Organization-Id")).toBe(
      scope.organizationId,
    );
    expect(init.cache).toBe("no-store");
  });
  it.each(["episode", "revision", "scope", "hash", "duplicate"])(
    "rejects a mismatched %s snapshot",
    async (kind) => {
      const wire = structuredClone(view);
      if (kind === "episode") wire.snapshot.episode_id = "different";
      if (kind === "revision")
        wire.snapshot.selection.revision_id = "different";
      if (kind === "scope")
        wire.snapshot.selection.organization_id = "different";
      if (kind === "hash") wire.snapshot.content_sha256 = "c".repeat(64);
      if (kind === "duplicate") wire.snapshot.streams.push(stream);
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(wire)));
      await expect(
        fetchLogicalSnapshot(selection, signal()),
      ).rejects.toMatchObject({
        code: "PRECONDITION_FAILED",
        problemCode: "LOGICAL_IDENTITY_MISMATCH",
      });
    },
  );
  it("renders valid channel samples and preserves named joints without any storage dispatch", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(batch()));
    vi.stubGlobal("fetch", fetcher);
    const [adapted] = adaptLogicalViewerStreams(selection, view);
    const actual = await adapted!.windowSource!.loadWindow(
      { startNs: "1000000000", endNs: "1100000000", lod: 1 },
      signal(),
    );
    expect(actual.values).toEqual([[0], [2]]);
    expect(actual.series).toEqual([{ id: "series-1", displayName: "elbow" }]);
    expect(actual.timestampsNs).toEqual(["1000000000", "1066666666"]);
    expect(fetcher.mock.calls[0]![0]).toContain(
      "/logical/streams/stream-joints/samples?",
    );
    expect(fetcher.mock.calls[0]![0]).not.toContain("/rollouts/");
  });
  it.each(["gap", "reorder", "snapshot", "time"])(
    "rejects %s in a sample window",
    async (kind) => {
      const wire = batch();
      if (kind === "gap") wire.samples.pop();
      if (kind === "reorder") wire.samples.reverse();
      if (kind === "snapshot") wire.snapshot_id = `snapshot_${"c".repeat(64)}`;
      if (kind === "time") wire.samples[0]!.timestamp_ns = "999";
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(wire)));
      await expect(
        fetchLogicalSamples(selection, view, stream, 0, 3, signal()),
      ).rejects.toMatchObject({
        code: "PRECONDITION_FAILED",
        problemCode: "LOGICAL_IDENTITY_MISMATCH",
      });
    },
  );
  it("preserves original video offset and refreshes authorization against the fixed snapshot", async () => {
    const camera = {
      ...stream,
      stream_id: "camera",
      kind: "RGB_VIDEO",
      value_kind: null,
    };
    const grant = {
      snapshot_id: view.snapshot_id,
      stream_id: "camera",
      kind: "video",
      content_type: "video/mp4",
      media_url: "http://localhost/media.mp4",
      expires_at: new Date(Date.now() + 60_000).toISOString(),
      sample_count: 3,
      sample_rate_hz: 30,
      media_start_seconds: 12,
      width: 16,
      height: 16,
    };
    const fetcher = vi.fn().mockResolvedValue(response(grant));
    vi.stubGlobal("fetch", fetcher);
    const media = adaptLogicalViewerStreams(selection, {
      ...view,
      snapshot: { ...view.snapshot, streams: [camera] },
    })[0]!.mediaSource!;
    expect(await media.authorize(signal())).toMatchObject({
      mediaStartSeconds: 12,
      mediaEndSeconds: 12.1,
      kind: "rgb-video",
    });
    fetcher.mockResolvedValue(response(grant));
    await media.refresh(signal());
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[0]![0]).toContain(
      "/logical/streams/camera/media/authorize?",
    );
  });
  it("does not invent a timeline for independent samples or claim an unknown value kind is supported", () => {
    expect(
      adaptLogicalViewerStreams(selection, {
        ...view,
        snapshot: {
          ...view.snapshot,
          streams: [
            { ...stream, temporal: false, start_ns: null, end_ns: null },
          ],
        },
      }),
    ).toEqual([]);
    expect(
      adaptLogicalViewerStreams(selection, {
        ...view,
        snapshot: {
          ...view.snapshot,
          streams: [
            { ...stream, kind: "NEW_MODALITY", value_kind: "NEW_VALUES" },
          ],
        },
      })[0],
    ).toMatchObject({ availability: "unsupported", modality: "new_modality" });
  });
  it("rejects oversized requests before sending and does not accept stale media grants", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    await expect(
      fetchLogicalSamples(
        selection,
        view,
        { ...stream, sample_count: 5000 },
        0,
        5000,
        signal(),
      ),
    ).rejects.toThrow();
    expect(fetcher).not.toHaveBeenCalled();
    fetcher.mockResolvedValue(
      response({
        snapshot_id: view.snapshot_id,
        stream_id: stream.stream_id,
        kind: "video",
        content_type: "video/mp4",
        media_url: "http://localhost/media.mp4",
        expires_at: "2020-01-01T00:00:00Z",
        sample_count: 3,
        sample_rate_hz: 30,
        media_start_seconds: 0,
        width: 16,
        height: 16,
      }),
    );
    await expect(
      authorizeLogicalMedia(selection, view, stream, signal()),
    ).rejects.toMatchObject({
      code: "PRECONDITION_FAILED",
      problemCode: "LOGICAL_IDENTITY_MISMATCH",
    });
  });
});
