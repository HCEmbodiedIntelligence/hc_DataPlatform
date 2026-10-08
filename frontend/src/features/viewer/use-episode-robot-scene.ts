import { useMemo } from "react";
import { authorizeRobotModelViewerAssets } from "../robot-models/api/client";
import {
  useScopedRobotModelAssets,
  useScopedRobotModelJointMappings,
  useScopedRobotModelVersion,
} from "../robot-models/api/hooks";
import { useScopedRobotBootstrap } from "../robots/api/hooks";
import { createLazyThreeRobotSceneLoader } from "./lazy-three-loader";
import { buildJointFrameSource } from "./joint-frame-source";
import type { StreamDescriptor } from "./types";
import {
  currentBindingPreview,
  type RobotSceneProvenance,
} from "./robot-scene-provenance";

export function useEpisodeRobotScene(
  manifestRobotId: string | null,
  jointAngleStream: StreamDescriptor | null,
  scope: { readonly organizationId: string } | null,
  provenance: RobotSceneProvenance = currentBindingPreview,
) {
  const organizationId = scope?.organizationId ?? null;
  const robotBootstrap = useScopedRobotBootstrap(
    organizationId,
    provenance.availability === "available" ? manifestRobotId : null,
  );
  const boundVersionId =
    robotBootstrap.data?.effectiveModelBinding?.robotModelVersionId ?? null;
  const robotModelVersion = useScopedRobotModelVersion(
    organizationId,
    boundVersionId,
  );
  const robotModelAssets = useScopedRobotModelAssets(
    organizationId,
    boundVersionId,
  );
  const robotJointMappings = useScopedRobotModelJointMappings(
    organizationId,
    boundVersionId,
  );
  const jointFrameSource = useMemo(() => {
    const base = buildJointFrameSource(jointAngleStream);
    if (!base) return;
    const directions = new Map(
      (robotJointMappings.data ?? []).map((mapping) => [
        mapping.source_joint_name,
        mapping.direction,
      ]),
    );
    return {
      async sampleAt(ns: string, signal: AbortSignal) {
        const frame = await base.sampleAt(ns, signal);
        return Object.fromEntries(
          Object.entries(frame).map(([joint, value]) => [
            joint,
            directions.get(joint) === "INVERTED" ? -value : value,
          ]),
        );
      },
    };
  }, [jointAngleStream, robotJointMappings.data]);
  const urdfAsset = robotModelAssets.data?.find(
    (asset) => asset.role === "URDF",
  );
  const robotScene = useMemo(() => {
    if (
      !scope ||
      provenance.availability !== "available" ||
      !robotModelVersion.data ||
      robotModelVersion.data.lifecycle !== "PUBLISHED" ||
      !urdfAsset ||
      !jointFrameSource ||
      robotJointMappings.isPending ||
      robotJointMappings.isError
    )
      return;
    const modelId = robotModelVersion.data.robotModelId;
    const modelVersion = robotModelVersion.data.id;
    const jointMapping = Object.fromEntries(
      (robotJointMappings.data ?? []).map((mapping) => [
        mapping.source_joint_name,
        mapping.target_joint_name,
      ]),
    );
    const requiredJoints = (robotJointMappings.data ?? []).map(
      (mapping) => mapping.source_joint_name,
    );
    return {
      title: provenance.title,
      modelRef: { modelId, modelVersion },
      jointMapping,
      jointFrameSource,
      runtimeLoader: createLazyThreeRobotSceneLoader(async (_props, signal) => {
        const viewerAssets = await authorizeRobotModelViewerAssets(
          scope.organizationId,
          modelVersion,
          robotModelAssets.data ?? [],
          signal,
        );
        return {
          manifest: { modelId, modelVersion, requiredJoints },
          ...viewerAssets,
        };
      }),
    };
  }, [
    jointFrameSource,
    robotJointMappings.data,
    robotJointMappings.isPending,
    robotJointMappings.isError,
    robotModelAssets.data,
    robotModelVersion.data,
    provenance,
    scope,
    urdfAsset,
  ]);
  const robotSceneUnavailableReason = useMemo(() => {
    if (provenance.availability === "unavailable") return provenance.reason;
    if (!jointAngleStream)
      return "当前数据未发现关节角 Topic；写入关节角数据后才能同步机器人姿态。";
    if (!manifestRobotId)
      return "采集数据清单未返回 robot_id，无法解析本次数据对应的机器人。请重新生成采集清单。";
    if (robotBootstrap.isPending)
      return `正在解析机器人 ${manifestRobotId} 的模型绑定…`;
    if (robotBootstrap.isError)
      return `无法读取机器人 ${manifestRobotId} 的配置或当前账号无权访问。`;
    if (!boundVersionId)
      return `机器人 ${manifestRobotId} 尚未绑定已发布 URDF 模型。请在“机器人管理 → 模型配置”中完成绑定。`;
    if (robotModelVersion.isPending || robotModelAssets.isPending)
      return "正在加载已发布模型版本与 URDF 资产…";
    if (robotModelVersion.isError || robotModelAssets.isError)
      return "模型绑定已存在，但固定版本或 URDF 资产加载失败。";
    if (robotModelVersion.data?.lifecycle !== "PUBLISHED")
      return "当前机器人绑定的模型版本尚未发布，暂时无法预览。";
    if (!urdfAsset)
      return "当前已发布模型没有可用的 URDF 资产。请在机器人管理中创建更新草稿并导入 URDF。";
    if (robotJointMappings.isPending) return "正在加载关节映射…";
    if (robotJointMappings.isError) return "URDF 已找到，但关节映射加载失败。";
    return undefined;
  }, [
    boundVersionId,
    provenance,
    jointAngleStream,
    manifestRobotId,
    robotBootstrap.isError,
    robotBootstrap.isPending,
    robotJointMappings.isError,
    robotJointMappings.isPending,
    robotModelAssets.isError,
    robotModelAssets.isPending,
    robotModelVersion.data?.lifecycle,
    robotModelVersion.isError,
    robotModelVersion.isPending,
    urdfAsset,
  ]);
  return { robotScene, robotSceneUnavailableReason };
}
