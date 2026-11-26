import { useMemo } from "react";
import { useOrganizationCapabilities } from "../../../shared/auth/use-capabilities";
import { hasSessionScopeAccess } from "../../../shared/scope/session-access";
import { useShellStore } from "../../../shared/scope/shell-store";
import { useScopedQuery } from "../../../shared/api/use-scoped-query";
import {
  authorizeLogicalRobotAssets,
  loadLogicalRobotScene,
} from "../api/logical-robot-scene";
import type { LogicalSelection, LogicalSnapshot } from "../api/logical-data";
import { useEpisodeRobotScene } from "../../viewer/use-episode-robot-scene";
import type { RobotSceneProvenance } from "../../viewer/robot-scene-provenance";
import { buildJointFrameSource } from "../../viewer/joint-frame-source";
import { createLazyThreeRobotSceneLoader } from "../../viewer/lazy-three-loader";
import { authorizedModelCacheKey, authorizedModelSceneAssets } from "../../viewer/authorized-model-cache";
import type { StreamDescriptor } from "../../viewer/types";
import type { RobotSceneCoreProps } from "../../viewer/RobotSceneCore";

export interface LogicalRobotTarget {
  readonly selection: LogicalSelection;
  readonly view: LogicalSnapshot;
  readonly robot?: import("../api/logical-robot-scene").LogicalRobotScene;
  readonly robotAssets?: Awaited<ReturnType<typeof authorizeLogicalRobotAssets>>;
}

const instancePreview = {
  kind: "current-binding-compatibility",
  availability: "available",
  title: "实例当前绑定的机器人模型",
} as const;
export function useLogicalRobotScene(
  target: LogicalRobotTarget | null,
  actionStream: StreamDescriptor | null,
  historicalRobotId: string | null,
  historicalProvenance: RobotSceneProvenance,
) {
  const query = useScopedQuery(
    target?.selection.scope ?? null,
    "datasets",
    "logical-robot-scene",
    target
      ? { selection: target.selection, snapshotId: target.view.snapshot_id }
      : null,
    {
      enabled: target !== null && !target.robot,
      queryFn: ({ signal }) =>
        loadLogicalRobotScene(target!.selection, target!.view, signal),
      staleTime: 0,
      retry: false,
    },
  );
  const scope = target?.selection.scope ?? null;
  const canAccess = useShellStore((state) => hasSessionScopeAccess(state, scope));
  const capabilities = useOrganizationCapabilities(scope?.organizationId ?? null);
  const canReadBinding = capabilities.has("robot.read") && capabilities.has("robot_model.read");
  const sceneView = canAccess ? target?.robot ?? query.data : undefined;
  const sceneFailed = !target?.robot && query.isError;
  const historical =
    !sceneFailed && sceneView?.provenance === "MISSING_HISTORICAL";
  const fixed = sceneView?.scene;
  const useInstance = canReadBinding && !sceneFailed && (historical || Boolean(fixed));
  const preview = useEpisodeRobotScene(
    useInstance ? (fixed?.robot_id || historicalRobotId) : null,
    useInstance ? actionStream : null,
    useInstance ? (target?.selection.scope ?? null) : null,
    fixed ? instancePreview : historicalProvenance,
    fixed?.model_version_id,
  );
  const useFrozen = fixed?.status === "FROZEN" &&
    (!canReadBinding || preview.useSnapshotAssets || preview.bindingReadFailed);
  const jointFrameSource = useMemo(() => {
    const source = buildJointFrameSource(actionStream);
    if (!source || fixed?.status !== "FROZEN") return;
    const directions = new Map(
      fixed.joint_mappings.map((m) => [m.source_joint_name, m.direction]),
    );
    return {
      async sampleAt(ns: string, signal: AbortSignal) {
        const frame = await source.sampleAt(ns, signal);
        return Object.fromEntries(
          Object.entries(frame).map(([joint, value]) => [
            joint,
            directions.get(joint) === "INVERTED" ? -value : value,
          ]),
        );
      },
    };
  }, [actionStream, fixed]);
  const robotScene = useMemo(() => {
    const view = sceneView;
    const urdf = fixed?.assets.find((asset) => asset.role === "URDF");
    if (
      !target ||
      !view ||
      !useFrozen ||
      sceneFailed ||
      fixed?.status !== "FROZEN" ||
      !urdf ||
      !fixed.model_id ||
      !fixed.model_version_id ||
      !jointFrameSource
    )
      return;
    const modelId = fixed.model_id;
    const modelVersion = fixed.model_version_id;
    const requiredJoints = fixed.joint_mappings.map((m) => m.source_joint_name);
    return {
      title: "数据快照绑定的机器人模型",
      ...(target.robotAssets?.length && target.robotAssets.every(asset => Date.parse(asset.grant.expires_at) > Date.now() + 10_000)
        ? { reuseKey: JSON.stringify([authorizedModelCacheKey(target.robotAssets, target.selection.scope), view.scene_sha256]) }
        : {}),
      sourceDescription:
        "回放使用该条数据保存的模型版本和关节映射。",
      modelRef: { modelId, modelVersion },
      jointMapping: Object.fromEntries(
        fixed.joint_mappings.map((m) => [
          m.source_joint_name,
          m.target_joint_name,
        ]),
      ),
      jointFrameSource,
      runtimeLoader: createLazyThreeRobotSceneLoader(async (_props, signal) => {
        const grants = target.robotAssets?.every(g => Date.parse(g.grant.expires_at) > Date.now() + 10_000)
          ? target.robotAssets
          : await authorizeLogicalRobotAssets(target.selection, view, signal);
        signal.throwIfAborted();
        return {
          manifest: { modelId, modelVersion, requiredJoints },
          ...authorizedModelSceneAssets(grants, target.selection.scope, _props.onLoadProgress),
        };
      }),
    };
  }, [target, sceneView, sceneFailed, fixed, jointFrameSource, useFrozen]);
  const robotSceneUnavailableReason =
    !target || (!sceneView && query.isPending)
      ? "正在读取数据快照中的机器人场景…"
      : sceneFailed
        ? "固定机器人场景读取失败或读取权限已失效，请重新加载。"
        : !useInstance && sceneView && fixed?.status !== "FROZEN"
          ? "此数据没有保存绑定的机器人模型。查看当前实例模型需要机器人和模型查看权限。"
          : !jointFrameSource
            ? "当前快照缺少可用于姿态回放的关节动作指令（action）。"
            : !robotScene
              ? "固定机器人场景缺少可用的 URDF 资产。"
              : undefined;
  const usePreview = useInstance && !useFrozen;
  const resolvedRobotScene: (RobotSceneCoreProps & {
    title: string;
    sourceDescription?: string;
  }) | undefined = usePreview ? preview.robotScene : robotScene;
  return {
    robotScene: resolvedRobotScene,
    robotSceneUnavailableReason: usePreview
      ? preview.robotSceneUnavailableReason
      : robotSceneUnavailableReason,
    // Retain the original calibration value and hashes; do not invent a transform schema.
    frozenEvidence: sceneView?.provenance === "FROZEN" ? sceneView : null,
  };
}
