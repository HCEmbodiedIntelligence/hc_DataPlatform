import type { ImportStage } from "./contracts";
import type { PendingRobotModelAsset } from "./model-file-selection";

export const EMPTY_MAPPING: Readonly<Record<string, string>> = {};

export const modelFileAccept = [
  ".urdf",
  ".xml",
  ".stl",
  ".obj",
  ".dae",
  ".glb",
  ".gltf",
  ".png",
  ".jpg",
  ".jpeg",
  ".webp",
  ".ktx2",
  ".json",
  ".yaml",
  ".yml",
  ".toml",
].join(",");

export const modelAssetRoleLabels: Record<
  PendingRobotModelAsset["role"],
  string
> = {
  URDF: "URDF",
  MESH: "网格",
  TEXTURE: "纹理",
  CONFIG: "配置",
  DOCUMENTATION: "文档",
};

export const parseErrorLabels: Readonly<Record<string, string>> = {
  URDF_MISSING: "没有找到 URDF 文件，请选择一个 .urdf 或 .xml 文件。",
  URDF_MULTIPLE: "检测到多个 URDF，请只保留一个入口 URDF。",
  URDF_XML_INVALID: "URDF 不是有效 XML，或根节点不是 <robot>。",
  URDF_ROBOT_NAME_MISSING: "URDF 的 <robot> 缺少 name。",
  URDF_LINKS_MISSING: "URDF 中没有 link，无法构成机器人模型。",
  URDF_LINKS_DUPLICATED: "URDF 中存在重复 link 名称。",
  URDF_JOINT_NAME_MISSING: "URDF 中存在没有 name 的 joint。",
  URDF_JOINTS_DUPLICATED: "URDF 中存在重复 joint 名称。",
  URDF_MIMIC_INVALID: "URDF 从动关节的引用缺失、重复或存在循环，请检查 mimic 配置。",
  CONFIG_JSON_OBJECT_REQUIRED: "JSON 配置文件的根节点必须是对象。",
};

export function uniqueVersionLabel(prefix: string): string {
  const stamp = new Date()
    .toISOString()
    .replace(/[-:TZ.]/gu, "")
    .slice(0, 14);
  return `${prefix}-${stamp}`;
}

export function importStep(stage: ImportStage): number {
  if (stage === "files") return 0;
  if (stage === "review") return 1;
  return 2;
}
