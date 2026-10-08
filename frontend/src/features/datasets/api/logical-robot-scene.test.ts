import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "../../../shared/api/http-client";
import { useShellStore } from "../../../shared/scope/shell-store";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../../shared/config/runtime";
import {
  authorizeLogicalRobotAsset,
  loadLogicalRobotScene,
  type LogicalRobotScene,
} from "./logical-robot-scene";
import type { LogicalSelection, LogicalSnapshot } from "./logical-data";
vi.mock("../../../shared/api/http-client", () => ({ request: vi.fn() }));
const mocked = vi.mocked(request);
const scope = {
  organizationId: "org",
  projectId: "project",
  regionCode: "region",
};
const selection: LogicalSelection = {
  scope,
  datasetId: "dataset",
  versionId: "version",
  revisionId: "revision",
  episodeId: "episode",
  contentSha256: "a".repeat(64),
  snapshotToken: "fixed-token",
};
const snapshot: LogicalSnapshot = {
  snapshot_id: "snapshot_" + "b".repeat(64),
  snapshot: {
    format: "hc-logical-snapshot/1",
    selection: {
      organization_id: "org",
      project_id: "project",
      region_code: "region",
      dataset_id: "dataset",
      version_id: "version",
      revision_id: "revision",
    },
    episode_id: "episode",
    content_sha256: selection.contentSha256,
    streams: [],
  },
};
const asset = {
  asset_id: "urdf",
  relative_path: "robot.urdf",
  role: "URDF" as const,
  media_type: "application/xml",
  size_bytes: 20,
  sha256: "c".repeat(64),
  created_at: "2026-10-08T00:00:00Z",
};
const view: LogicalRobotScene = {
  snapshot_id: snapshot.snapshot_id,
  provenance: "FROZEN",
  scene_sha256: "d".repeat(64),
  scene: {
    format: "hc-robot-scene/1",
    captured_at_stage: "PACKAGE_PLANNING",
    robot_id: "robot",
    status: "FROZEN",
    binding_id: "binding",
    model_id: "fixed-model",
    model_version_id: "fixed-model-v1",
    asset_manifest_sha256: "e".repeat(64),
    assets: [asset],
    joint_mapping_sha256: "f".repeat(64),
    joint_mappings: [
      {
        source_joint_name: "q1",
        target_joint_name: "hinge",
        direction: "INVERTED",
      },
    ],
    calibration: { reference: "fixed-calibration", value: [0.1, 0.2] },
    calibration_sha256: "0".repeat(64),
  },
};
function grant() {
  return {
    snapshot_id: view.snapshot_id,
    scene_sha256: view.scene_sha256,
    asset,
    authorization: {
      asset_id: asset.asset_id,
      download_url: "https://assets.test/robot.urdf",
      expires_at: new Date(Date.now() + 60_000).toISOString(),
      sha256: asset.sha256,
      media_type: asset.media_type,
    },
  };
}
beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "http://localhost/api/v1",
    sseBaseUrl: "http://localhost/api/v1/events",
    buildVersion: "scene-test",
    releaseEnv: "test",
  });
  const store = useShellStore.getState();
  store.setSession(
    { actorId: "reader", displayName: "Reader", roleIds: [] },
    "session",
  );
  store.setSessionScopes(
    [
      {
        ...scope,
        regionCodes: [scope.regionCode],
        projectWide: false,
        capabilities: [
          "dataset_version.read",
          "episode.read",
          "robot_model.read",
        ],
      },
    ],
    1,
  );
  store.setScope(scope);
});
afterEach(() => {
  vi.resetAllMocks();
  useShellStore.getState().setSession(null, null);
  resetRuntimeConfigForTests();
});
it("reads the fixed scene, mappings and calibration through the logical selection", async () => {
  mocked.mockResolvedValueOnce(view as never);
  await expect(loadLogicalRobotScene(selection, snapshot)).resolves.toEqual(
    view,
  );
  expect(mocked).toHaveBeenCalledWith(
    expect.objectContaining({
      method: "GET",
      scope,
      scopeMode: "explicit",
      cache: "no-store",
      query: {
        snapshot_token: "fixed-token",
        snapshot_id: snapshot.snapshot_id,
      },
      path: "/projects/project/regions/region/datasets/dataset/versions/version/episode-revisions/revision/logical/robot-scene",
    }),
  );
});
it("preserves explicit historical absence and a frozen no-binding result", async () => {
  const historical = {
    snapshot_id: snapshot.snapshot_id,
    provenance: "MISSING_HISTORICAL",
    scene: null,
    scene_sha256: null,
  };
  mocked.mockResolvedValueOnce(historical as never);
  await expect(loadLogicalRobotScene(selection, snapshot)).resolves.toEqual(
    historical,
  );
  const noBinding = {
    ...view,
    scene: {
      ...view.scene!,
      status: "NO_MODEL_BINDING",
      binding_id: null,
      model_id: null,
      model_version_id: null,
      asset_manifest_sha256: null,
      assets: [],
      joint_mapping_sha256: null,
      joint_mappings: [],
    },
  };
  mocked.mockResolvedValueOnce(noBinding as never);
  await expect(loadLogicalRobotScene(selection, snapshot)).resolves.toEqual(
    noBinding,
  );
});
it.each([
  { ...view, snapshot_id: "different" },
  { ...view, provenance: "MISSING_HISTORICAL" },
  { ...view, scene: { ...view.scene!, status: "NO_MODEL_BINDING" } },
  { ...view, scene: { ...view.scene!, assets: [asset, asset] } },
])(
  "rejects mismatched snapshot or contradictory frozen evidence",
  async (response) => {
    mocked.mockResolvedValueOnce(response as never);
    await expect(
      loadLogicalRobotScene(selection, snapshot),
    ).rejects.toMatchObject({ code: "CONTRACT_MISMATCH" });
  },
);
it("authorizes assets only against the fixed scene rather than the current robot", async () => {
  const response = grant();
  mocked.mockResolvedValueOnce(response as never);
  await expect(
    authorizeLogicalRobotAsset(selection, view, asset),
  ).resolves.toEqual(response.authorization);
  expect(mocked).toHaveBeenCalledWith(
    expect.objectContaining({
      method: "POST",
      path: "/projects/project/regions/region/datasets/dataset/versions/version/episode-revisions/revision/logical/robot-scene/assets/urdf/authorize",
      scopeMode: "explicit",
    }),
  );
});
it.each(["scene", "bytes", "path", "expiry"])(
  "rejects a grant with mismatched %s",
  async (field) => {
    const response = grant();
    if (field === "scene") response.scene_sha256 = "a".repeat(64);
    if (field === "bytes") response.authorization.sha256 = "a".repeat(64);
    if (field === "path")
      response.asset = { ...asset, relative_path: "different.urdf" };
    if (field === "expiry")
      response.authorization.expires_at = "2020-01-01T00:00:00Z";
    mocked.mockResolvedValueOnce(response as never);
    await expect(
      authorizeLogicalRobotAsset(selection, view, asset),
    ).rejects.toMatchObject({ code: "CONTRACT_MISMATCH" });
  },
);
it("does not return a signed asset after the session is revoked", async () => {
  let complete!: (value: unknown) => void;
  mocked.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  const pending = authorizeLogicalRobotAsset(selection, view, asset);
  const rejected = expect(pending).rejects.toBe("session-or-grant-changed");
  useShellStore.getState().setSessionScopes([], 2);
  complete(grant());
  await rejected;
});
