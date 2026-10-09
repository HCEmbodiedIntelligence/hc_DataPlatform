import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../../shared/config/runtime";
import { useShellStore } from "../../../shared/scope/shell-store";
import {
  loadCollectionRelease,
  preflightCollectionRelease,
  publishAndReadCollectionRelease,
  publishCollectionRelease,
  workingCollectionId,
  type CollectionRelease,
  type CollectionReleaseCommand,
  type CollectionReleasePreflight,
} from "./collection-releases";

const scope = {
  organizationId: "org-release",
  projectId: "project-release",
  regionCode: "local",
};
const command: CollectionReleaseCommand = {
  dataset_version: "v1",
  collection_version_id: `cv_${"a".repeat(64)}`,
  episode_ids: ["episode-1"],
  annotation_tasks: { "episode-1": "task-1" },
};
const preview: CollectionReleasePreflight = {
  dataset_id: "dataset-1",
  dataset_version: "v1",
  collection_version_id: command.collection_version_id,
  collection_manifest_sha256: "b".repeat(64),
  content_hash: "c".repeat(64),
  annotation_task_options: { "episode-1": ["task-1"], "episode-2": [] },
  eligible_episode_ids: ["episode-1"],
  excluded_episodes: [{ episode_id: "episode-2", reason: "NOT_SELECTED" }],
};
const release: CollectionRelease = {
  dataset_id: preview.dataset_id,
  dataset_version: preview.dataset_version,
  collection_version_id: preview.collection_version_id,
  collection_manifest_sha256: preview.collection_manifest_sha256,
  content_hash: preview.content_hash,
  eligible_episode_ids: preview.eligible_episode_ids,
  excluded_episodes: preview.excluded_episodes,
  format: "hc-collection-release/1",
  version_id: `version_release_${"d".repeat(32)}`,
  document_sha256: "e".repeat(64),
  document_size: 1000,
  created_at: "2026-10-08T03:00:00Z",
};
const publishCommand = {
  ...command,
  expected_content_hash: preview.content_hash,
};
function response(value: unknown) {
  return new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });
}
function login() {
  useShellStore
    .getState()
    .setSession(
      { actorId: "release-user", displayName: "发布人", roleIds: [] },
      "release-session",
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
}
beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "http://localhost/api/v1",
    sseBaseUrl: "http://localhost/api/v1/events",
    buildVersion: "release-test",
    releaseEnv: "test",
  });
  login();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  useShellStore.getState().clearSensitiveState();
  resetRuntimeConfigForTests();
});

it("routes explicit selection through collection release preflight/publication/fixed read only", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(response(preview))
    .mockResolvedValueOnce(response(release))
    .mockResolvedValueOnce(response(release));
  vi.stubGlobal("fetch", fetcher);
  expect(await preflightCollectionRelease(scope, "dataset-1", command)).toEqual(
    preview,
  );
  expect(
    await publishAndReadCollectionRelease(scope, "dataset-1", publishCommand),
  ).toEqual(release);
  const base =
    "http://localhost/api/v1/projects/project-release/regions/local/datasets/dataset-1/collection-releases";
  expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
    `${base}/preflight`,
    base,
    `${base}/${release.version_id}`,
  ]);
  expect(JSON.parse(fetcher.mock.calls[1]![1].body)).toEqual(publishCommand);
  expect(
    new Headers(fetcher.mock.calls[1]![1].headers).get("X-Organization-Id"),
  ).toBe(scope.organizationId);
});
it("requires both reviewed hash and explicit Episode selection before sending a publication", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  await expect(
    publishCollectionRelease(scope, "dataset-1", command),
  ).rejects.toThrow("预检");
  await expect(
    publishCollectionRelease(scope, "dataset-1", {
      ...publishCommand,
      episode_ids: null,
    }),
  ).rejects.toThrow("明确选择");
  expect(fetcher).not.toHaveBeenCalled();
});
it.each([
  { dataset_id: "other-dataset" },
  {
    eligible_episode_ids: ["episode-2"],
    excluded_episodes: [{ episode_id: "episode-1", reason: "NOT_SELECTED" }],
  },
  { annotation_task_options: { "episode-1": ["other-task"], "episode-2": [] } },
  { eligible_episode_ids: ["episode-1", "episode-1"] },
])("rejects a mismatched preflight: %j", async (changes) => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(response({ ...preview, ...changes })),
  );
  await expect(
    preflightCollectionRelease(scope, "dataset-1", command),
  ).rejects.toThrow();
});
it("reads historical receipts without requiring newly introduced selection metadata", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(response({ ...release, future_metadata: true })),
  );
  expect(
    await loadCollectionRelease(scope, "dataset-1", release.version_id),
  ).toEqual(release);
});
it.each([
  { document_size: 64 * 1024 ** 2 + 1 },
  { document_size: 0 },
  { document_size: 1.5 },
  { eligible_episode_ids: [] },
  { format: undefined },
  { excluded_episodes: [{ episode_id: "episode-2", reason: "UNKNOWN" }] },
])(
  "rejects malformed fixed receipts through generated runtime constraints: %j",
  async (changes) => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(response({ ...release, ...changes }));
    vi.stubGlobal("fetch", fetcher);
    await expect(
      loadCollectionRelease(scope, "dataset-1", release.version_id),
    ).rejects.toMatchObject({ code: "CONTRACT_MISMATCH" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);
it("checks the immutable readback against the write receipt", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(response(release))
      .mockResolvedValueOnce(
        response({ ...release, document_sha256: "f".repeat(64) }),
      ),
  );
  await expect(
    publishAndReadCollectionRelease(scope, "dataset-1", publishCommand),
  ).rejects.toThrow("读取回执不一致");
});
it("binds a publication to its explicit scope even after another workspace is selected", async () => {
  useShellStore.getState().setScope({ ...scope, projectId: "other-project" });
  const fetcher = vi.fn().mockResolvedValue(response(preview));
  vi.stubGlobal("fetch", fetcher);
  await preflightCollectionRelease(scope, "dataset-1", command);
  expect(
    new Headers(fetcher.mock.calls[0]![1].headers).get("X-Project-Id"),
  ).toBe(scope.projectId);
});
it("does not continue readback after the publishing session is revoked", async () => {
  const fetcher = vi.fn().mockImplementation(async () => {
    useShellStore.getState().setSessionScopes([], 2);
    return response(release);
  });
  vi.stubGlobal("fetch", fetcher);
  await expect(
    publishAndReadCollectionRelease(scope, "dataset-1", publishCommand),
  ).rejects.toThrow();
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it("keeps legacy and published IDs out of the working collection adapter", () => {
  expect(workingCollectionId(`version_${command.collection_version_id}`)).toBe(
    command.collection_version_id,
  );
  expect(workingCollectionId("version_lance_123")).toBeNull();
  expect(workingCollectionId(release.version_id)).toBeNull();
});
