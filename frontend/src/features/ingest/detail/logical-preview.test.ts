import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  datasetIds,
  datasetFixtureScope,
  versionBootstrapFixture,
} from "../../../mocks/fixtures/datasets/core";
import { request } from "../../../shared/api/http-client";
import { useShellStore } from "../../../shared/scope/shell-store";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../../shared/config/runtime";
import { loadUploadLogicalPreview } from "./logical-preview";
import type { FormalUploadProcessingStatus } from "./client";
vi.mock("../../../shared/api/http-client", () => ({ request: vi.fn() }));
const mocked = vi.mocked(request);
const scope = {
  organizationId: datasetFixtureScope.organization_id,
  projectId: datasetFixtureScope.project_id,
  regionCode: datasetFixtureScope.region_code,
};
const workflow: FormalUploadProcessingStatus = {
  schema_version: "upload-processing-status/v1",
  session_id: "52faee8f-f489-4c19-8b54-38a51cf46899",
  rollout_id: "rollout-test",
  workflow_id: "workflow-test",
  status: "SUCCEEDED",
  stage: "ready",
  attempt: 1,
  aligned_media: null,
  error_code: null,
  updated_at: "2026-10-09T00:00:00Z",
  viewer: {
    schema_version: "dataset-ingest-viewer-target/v1",
    dataset_id: datasetIds.dataset,
    version_id: datasetIds.reviewing,
    episode_id: datasetIds.episode,
    revision_id: datasetIds.revision,
  },
  episode: {
    schema_version: "upload-episode-target/v1",
    project_id: scope.projectId,
    region_code: scope.regionCode,
    dataset_id: datasetIds.dataset,
    episode_id: datasetIds.episode,
    rollout_id: "rollout-test",
    data_version_id: "dv_test",
    content_hash: "a".repeat(64),
    annotation_task_id: "task-test",
    fps: 25,
    frame_count: 6,
  },
};
const view = {
  snapshot_id: `snapshot_${"b".repeat(64)}`,
  snapshot: {
    format: "hc-logical-snapshot/1",
    selection: {
      ...datasetFixtureScope,
      organization_id: String(datasetFixtureScope.organization_id),
      dataset_id: datasetIds.dataset,
      version_id: datasetIds.reviewing,
      revision_id: datasetIds.revision,
    },
    episode_id: String(datasetIds.episode),
    content_sha256: "a".repeat(64),
    streams: [
      {
        stream_id: "rgb",
        name: "/camera/rgb",
        kind: "RGB",
        value_kind: null,
        sample_count: 6,
        temporal: true,
        start_ns: "1700000000000000000",
        end_ns: "1700000000240000000",
      },
    ],
  },
};
beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "http://localhost/api/v1",
    sseBaseUrl: "http://localhost/api/v1/events",
    buildVersion: "test",
    releaseEnv: "test",
  });
  const store = useShellStore.getState();
  store.setSession(
    { actorId: "test", displayName: "test", roleIds: [] },
    "test-token",
  );
  store.setScope(scope);
  store.setSessionScopes(
    [
      {
        ...scope,
        regionCodes: [scope.regionCode],
        projectWide: false,
        capabilities: ["upload.read", "dataset_version.read", "episode.read"],
      },
    ],
    1,
  );
});
afterEach(() => {
  resetRuntimeConfigForTests();
  vi.resetAllMocks();
  useShellStore.getState().clearSensitiveState();
});
it("resolves the persisted viewer through explicit bootstrap/logical and lazy logical media, retaining source time and 25Hz", async () => {
  mocked
    .mockResolvedValueOnce(versionBootstrapFixture)
    .mockResolvedValueOnce(view);
  const result = await loadUploadLogicalPreview(scope, workflow);
  expect(result.streams[0]?.startNs).toBe("1700000000000000000");
  expect(mocked.mock.calls.map(([r]) => r.path)).toEqual([
    `/projects/${scope.projectId}/datasets/${datasetIds.dataset}/versions/${datasetIds.reviewing}/bootstrap`,
    `/projects/${scope.projectId}/regions/${scope.regionCode}/datasets/${datasetIds.dataset}/versions/${datasetIds.reviewing}/episode-revisions/${datasetIds.revision}/logical`,
  ]);
  mocked.mockResolvedValueOnce({
    snapshot_id: view.snapshot_id,
    stream_id: "rgb",
    kind: "video",
    media_url: "https://objects.example.test/video.mp4",
    content_type: "video/mp4",
    expires_at: "2099-10-09T00:00:00Z",
    sample_count: 6,
    sample_rate_hz: 25,
    media_start_seconds: 0,
    width: 16,
    height: 16,
    media_sha256: "c".repeat(64),
    depth_min_m: null,
    depth_max_m: null,
    colorizer: null,
  });
  const media = await result.streams[0]!.mediaSource!.authorize(
    new AbortController().signal,
  );
  expect(media.mediaEndSeconds).toBe(0.24);
  expect(mocked.mock.calls[2]?.[0].path).toContain(
    "/logical/streams/rgb/media/authorize",
  );
  for (const [r] of mocked.mock.calls)
    expect(r).toMatchObject({ scope, scopeMode: "explicit" });
});
it.each(["scope", "frames", "hash", "episode"])(
  "rejects %s mismatches instead of falling back to aligned media",
  async (change) => {
    const changed = structuredClone(view);
    if (change === "scope")
      changed.snapshot.selection.organization_id = "other";
    if (change === "frames") changed.snapshot.streams[0]!.sample_count = 5;
    if (change === "hash") changed.snapshot.content_sha256 = "d".repeat(64);
    if (change === "episode") changed.snapshot.episode_id = "episode_other";
    mocked
      .mockResolvedValueOnce(versionBootstrapFixture)
      .mockResolvedValueOnce(changed);
    await expect(
      loadUploadLogicalPreview(scope, workflow),
    ).rejects.toBeDefined();
    expect(mocked).toHaveBeenCalledTimes(2);
  },
);
it("does not fetch logical data after a late bootstrap returns across revocation", async () => {
  let resolve!: (value: unknown) => void;
  mocked.mockImplementationOnce(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  const pending = loadUploadLogicalPreview(scope, workflow);
  const rejected = expect(pending).rejects.toBe("session-or-grant-changed");
  useShellStore.getState().setSessionScopes([], 2);
  resolve(versionBootstrapFixture);
  await rejected;
  expect(mocked).toHaveBeenCalledTimes(1);
});
