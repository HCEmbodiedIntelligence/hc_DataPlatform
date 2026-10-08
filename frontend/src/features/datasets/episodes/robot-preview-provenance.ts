import {
  currentBindingPreview,
  type RobotSceneProvenance,
} from "../../viewer/robot-scene-provenance";

const missingModel: RobotSceneProvenance = Object.freeze({
  kind: "current-binding-compatibility",
  title: "机器人模型预览",
  availability: "unavailable",
  reason: "此数据没有机器人模型引用；可以查看已有数据，无需补填机器人。",
});
const importedDroidPreview: RobotSceneProvenance = Object.freeze({
  kind: "current-binding-compatibility",
  availability: "available",
  title: "兼容性机器人模型（非源数据 URDF）",
});

/** Legacy episode APIs do not carry a frozen embodiment. Do not imply historical truth. */
export function describeEpisodeRobotPreview(
  robotId: string | null,
): RobotSceneProvenance {
  if (!robotId) return missingModel;
  // Imported DROID manifests never supplied the original capture URDF.
  return robotId === "droid-franka"
    ? importedDroidPreview
    : currentBindingPreview;
}
