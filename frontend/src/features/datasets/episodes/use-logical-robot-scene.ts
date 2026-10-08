import { useMemo } from "react";
import { useScopedQuery } from "../../../shared/api/use-scoped-query";
import {
  authorizeLogicalRobotAsset,
  loadLogicalRobotScene,
} from "../api/logical-robot-scene";
import type { LogicalSelection, LogicalSnapshot } from "../api/logical-data";
import { useEpisodeRobotScene } from "../../viewer/use-episode-robot-scene";
import type { RobotSceneProvenance } from "../../viewer/robot-scene-provenance";
import { buildJointFrameSource } from "../../viewer/joint-frame-source";
import { createLazyThreeRobotSceneLoader } from "../../viewer/lazy-three-loader";
import type { StreamDescriptor } from "../../viewer/types";

export interface LogicalRobotTarget {
  readonly selection: LogicalSelection;
  readonly view: LogicalSnapshot;
}

/** A current binding is a compatibility source only after explicit historical absence. */
export function useLogicalRobotScene(
  target: LogicalRobotTarget | null,
  jointStream: StreamDescriptor | null,
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
      enabled: target !== null,
      queryFn: ({ signal }) =>
        loadLogicalRobotScene(target!.selection, target!.view, signal),
      staleTime: 0,
      retry: false,
    },
  );
  const historical =
    !query.isError && query.data?.provenance === "MISSING_HISTORICAL";
  const compatibility = useEpisodeRobotScene(
    historical ? historicalRobotId : null,
    historical ? jointStream : null,
    historical ? (target?.selection.scope ?? null) : null,
    historicalProvenance,
  );
  const fixed = query.data?.scene;
  const jointFrameSource = useMemo(() => {
    const source = buildJointFrameSource(jointStream);
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
  }, [jointStream, fixed]);
  const robotScene = useMemo(() => {
    const view = query.data;
    const urdf = fixed?.assets.find((asset) => asset.role === "URDF");
    if (
      !target ||
      !view ||
      query.isError ||
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
      title: "处理规划时固定的机器人模型",
      sourceDescription:
        "模型、映射与标定来自固定数据快照；姿态回放应用固定关节映射，未叠加额外标定变换。",
      modelRef: { modelId, modelVersion },
      jointMapping: Object.fromEntries(
        fixed.joint_mappings.map((m) => [
          m.source_joint_name,
          m.target_joint_name,
        ]),
      ),
      jointFrameSource,
      runtimeLoader: createLazyThreeRobotSceneLoader(async (_props, signal) => {
        const assets = fixed.assets.filter(
          (a) =>
            a.asset_id === urdf.asset_id ||
            a.role === "MESH" ||
            a.role === "TEXTURE",
        );
        const grants = await Promise.all(
          assets.map(async (asset) => ({
            asset,
            grant: await authorizeLogicalRobotAsset(
              target.selection,
              view,
              asset,
              signal,
            ),
          })),
        );
        signal.throwIfAborted();
        return {
          manifest: { modelId, modelVersion, requiredJoints },
          urdfUrl: grants.find((g) => g.asset.asset_id === urdf.asset_id)!.grant
            .download_url,
          urdfPath: urdf.relative_path,
          assetUrls: Object.fromEntries(
            grants
              .filter((g) => g.asset.asset_id !== urdf.asset_id)
              .map((g) => [g.asset.relative_path, g.grant.download_url]),
          ),
        };
      }),
    };
  }, [target, query.data, query.isError, fixed, jointFrameSource]);
  const robotSceneUnavailableReason =
    !target || query.isPending
      ? "正在读取数据快照中的机器人场景…"
      : query.isError
        ? "固定机器人场景读取失败或读取权限已失效，请重新加载。"
        : fixed?.status === "NO_MODEL_BINDING"
          ? "处理规划时未绑定机器人模型；此快照没有可回放的模型。"
          : !jointFrameSource
            ? "固定数据未提供可用的关节角样本，暂时无法同步机器人姿态。"
            : !robotScene
              ? "固定机器人场景缺少可用的 URDF 资产。"
              : undefined;
  return {
    ...(historical
      ? compatibility
      : { robotScene, robotSceneUnavailableReason }),
    // Retain the original calibration value and hashes; do not invent a transform schema.
    frozenEvidence: query.data?.provenance === "FROZEN" ? query.data : null,
  };
}
