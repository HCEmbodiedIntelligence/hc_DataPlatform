// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useShellStore } from "../../../shared/scope/shell-store";
import {
  useLogicalRobotScene,
  type LogicalRobotTarget,
} from "./use-logical-robot-scene";
import {
  loadLogicalRobotScene,
  authorizeLogicalRobotAsset,
  type LogicalRobotScene,
} from "../api/logical-robot-scene";
import { currentBindingPreview } from "../../viewer/robot-scene-provenance";
import type { StreamDescriptor } from "../../viewer/types";
import type { RobotSceneAssetResolver } from "../../viewer/lazy-three-loader";

const calls = vi.hoisted(() => ({
  compatibility: vi.fn(),
  resolver: null as RobotSceneAssetResolver | null,
}));
vi.mock("../api/logical-robot-scene", () => ({
  loadLogicalRobotScene: vi.fn(),
  authorizeLogicalRobotAsset: vi.fn(),
}));
vi.mock("../../viewer/use-episode-robot-scene", () => ({
  useEpisodeRobotScene: (...args: unknown[]) => {
    calls.compatibility(...args);
    return { robotSceneUnavailableReason: "历史数据使用当前绑定兼容预览" };
  },
}));
vi.mock("../../viewer/lazy-three-loader", () => ({
  createLazyThreeRobotSceneLoader: (resolver: RobotSceneAssetResolver) => {
    calls.resolver = resolver;
    return vi.fn();
  },
}));
const scope = {
  organizationId: "org",
  projectId: "project",
  regionCode: "region",
};
const target: LogicalRobotTarget = {
  selection: {
    scope,
    datasetId: "dataset",
    versionId: "version",
    revisionId: "revision",
    episodeId: "episode",
    contentSha256: "a".repeat(64),
    snapshotToken: "token",
  },
  view: {
    snapshot_id: "snapshot",
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
      content_sha256: "a".repeat(64),
      streams: [],
    },
  },
};
const asset = {
  asset_id: "urdf",
  relative_path: "fixed.urdf",
  role: "URDF" as const,
  media_type: "application/xml",
  size_bytes: 100,
  sha256: "c".repeat(64),
  created_at: "2026-10-08T00:00:00Z",
};
const view: LogicalRobotScene = {
  snapshot_id: "snapshot",
  provenance: "FROZEN",
  scene_sha256: "d".repeat(64),
  scene: {
    format: "hc-robot-scene/1",
    captured_at_stage: "PACKAGE_PLANNING",
    robot_id: "robot",
    status: "FROZEN",
    binding_id: "binding-old",
    model_id: "model-old",
    model_version_id: "version-old",
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
    calibration: { fixed: [0.1, 0.2] },
    calibration_sha256: "0".repeat(64),
  },
};
const stream: StreamDescriptor = {
  id: "q",
  canonicalPath: "/joints",
  displayName: "joints",
  modality: "joint_state",
  schema: { id: "joints", version: "1" },
  startNs: "0",
  endNs: "1000000000",
  availability: "ready",
  windowSource: {
    loadWindow: async () => ({
      generation: 0,
      timestampsNs: ["0"],
      values: [[0.5]],
      series: [{ id: "q1", displayName: "q1" }],
    }),
  },
};
let client: QueryClient;
function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
function mount(robot = "current-robot") {
  return renderHook(
    ({ robotId }) =>
      useLogicalRobotScene(target, stream, robotId, currentBindingPreview),
    { wrapper, initialProps: { robotId: robot } },
  );
}
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.mocked(loadLogicalRobotScene).mockResolvedValue(view);
  vi.mocked(authorizeLogicalRobotAsset).mockResolvedValue({
    asset_id: "urdf",
    download_url: "https://assets.test/fixed.urdf",
    sha256: asset.sha256,
    media_type: asset.media_type,
    expires_at: "2099-01-01T00:00:00Z",
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
        regionCodes: ["region"],
        projectWide: false,
        capabilities: [
          "episode.read",
          "dataset_version.read",
          "robot_model.read",
        ],
      },
    ],
    1,
  );
  store.setScope(scope);
});
afterEach(() => {
  cleanup();
  client.clear();
  vi.resetAllMocks();
  calls.resolver = null;
  useShellStore.getState().setSession(null, null);
});
it("uses fixed identity, mapping direction, calibration and asset grants even when the current robot changes", async () => {
  const { result, rerender } = mount();
  await waitFor(() =>
    expect(result.current.robotScene?.modelRef).toEqual({
      modelId: "model-old",
      modelVersion: "version-old",
    }),
  );
  expect(result.current.robotScene?.jointMapping).toEqual({ q1: "hinge" });
  expect(result.current.frozenEvidence?.scene?.calibration).toEqual({
    fixed: [0.1, 0.2],
  });
  await expect(
    result.current.robotScene!.jointFrameSource!.sampleAt(
      "0",
      new AbortController().signal,
    ),
  ).resolves.toEqual({ q1: -0.5 });
  const assets = await calls.resolver!(
    result.current.robotScene!,
    new AbortController().signal,
  );
  expect(assets.urdfUrl).toBe("https://assets.test/fixed.urdf");
  expect(assets.manifest).toEqual({
    modelId: "model-old",
    modelVersion: "version-old",
    requiredJoints: ["q1"],
  });
  expect(authorizeLogicalRobotAsset).toHaveBeenCalledWith(
    target.selection,
    view,
    asset,
    expect.any(AbortSignal),
  );
  rerender({ robotId: "robot-with-new-binding" });
  expect(result.current.robotScene?.modelRef.modelVersion).toBe("version-old");
  expect(
    calls.compatibility.mock.calls.every(
      ([id, , scope]) => id === null && scope === null,
    ),
  ).toBe(true);
});
it("enables explicitly labelled compatibility only for missing historical evidence", async () => {
  vi.mocked(loadLogicalRobotScene).mockResolvedValue({
    snapshot_id: "snapshot",
    provenance: "MISSING_HISTORICAL",
    scene: null,
    scene_sha256: null,
  });
  const { result } = mount();
  await waitFor(() =>
    expect(calls.compatibility).toHaveBeenCalledWith(
      "current-robot",
      stream,
      scope,
      currentBindingPreview,
    ),
  );
  expect(result.current.robotSceneUnavailableReason).toContain("历史数据");
  expect(result.current.frozenEvidence).toBeNull();
});
it("does not substitute a current binding when processing explicitly froze no binding", async () => {
  vi.mocked(loadLogicalRobotScene).mockResolvedValue({
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
  });
  const { result } = mount();
  await waitFor(() =>
    expect(result.current.robotSceneUnavailableReason).toContain(
      "处理规划时未绑定",
    ),
  );
  expect(result.current.robotScene).toBeUndefined();
  expect(calls.compatibility.mock.calls.every(([id]) => id === null)).toBe(
    true,
  );
});
it("does not fall back on a failed frozen-scene request", async () => {
  vi.mocked(loadLogicalRobotScene).mockRejectedValue(new Error("403"));
  const { result } = mount();
  await waitFor(() =>
    expect(result.current.robotSceneUnavailableReason).toContain("读取失败"),
  );
  expect(result.current.robotScene).toBeUndefined();
  expect(calls.compatibility.mock.calls.every(([id]) => id === null)).toBe(
    true,
  );
});
it("quarantines an already loaded fixed scene immediately after revocation", async () => {
  const { result } = mount();
  await waitFor(() => expect(result.current.robotScene).toBeDefined());
  act(() => useShellStore.getState().setSessionScopes([], 2));
  await waitFor(() => expect(result.current.robotScene).toBeUndefined());
  expect(result.current.frozenEvidence).toBeNull();
});
it("does not render a late fixed scene after its grant was removed", async () => {
  let complete!: (value: LogicalRobotScene) => void;
  vi.mocked(loadLogicalRobotScene).mockImplementation(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  const { result } = mount();
  await waitFor(() => expect(loadLogicalRobotScene).toHaveBeenCalled());
  act(() => useShellStore.getState().setSessionScopes([], 2));
  await act(async () => complete(view));
  expect(result.current.robotScene).toBeUndefined();
  expect(result.current.frozenEvidence).toBeNull();
});
