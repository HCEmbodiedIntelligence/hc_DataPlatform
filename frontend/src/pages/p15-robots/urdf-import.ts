import type { RobotModelJointMapping } from "../../features/robot-models/api";
import type { PendingRobotModelAsset } from "./model-file-selection";

export interface ParsedUrdfJoint {
  readonly name: string;
  readonly type: string;
  readonly parent: string | null;
  readonly child: string | null;
}

export interface ParsedRobotModel {
  readonly urdfPath: string;
  readonly robotName: string;
  readonly linkNames: readonly string[];
  readonly joints: readonly ParsedUrdfJoint[];
  readonly actuatedJointNames: readonly string[];
  readonly meshReferences: readonly string[];
  readonly missingMeshReferences: readonly string[];
  readonly configurationPath: string | null;
  readonly configuration: Readonly<Record<string, unknown>>;
  readonly mappings: readonly RobotModelJointMapping[];
  readonly urdfXml: string;
}

export interface RobotConfigurationIdentity {
  readonly robotId: string;
  readonly displayName: string;
  readonly serialNo: string;
}

export interface LocalRobotPreview {
  readonly urdfUrl: string;
  dispose(): void;
}

function normalizePath(value: string): string {
  const segments: string[] = [];
  for (const segment of value.replaceAll("\\", "/").split("/")) {
    if (!segment || segment === ".") continue;
    if (segment === "..") segments.pop();
    else segments.push(segment);
  }
  return segments.join("/");
}

function directoryName(path: string): string {
  const normalized = normalizePath(path);
  const separator = normalized.lastIndexOf("/");
  return separator < 0 ? "" : normalized.slice(0, separator);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function parseXml(xml: string): XMLDocument {
  const document = new DOMParser().parseFromString(xml, "application/xml");
  if (
    document.documentElement.localName !== "robot" ||
    document.querySelector("parsererror")
  ) {
    throw new Error("URDF_XML_INVALID");
  }
  return document;
}

function mappingRowsFromConfiguration(
  configuration: Readonly<Record<string, unknown>>,
): readonly RobotModelJointMapping[] {
  const rows = configuration.joint_mapping;
  if (!Array.isArray(rows)) return [];
  return rows.flatMap((row) => {
    if (!isRecord(row)) return [];
    if (
      typeof row.source_joint_name !== "string" ||
      typeof row.target_joint_name !== "string"
    ) {
      return [];
    }
    return [
      {
        source_joint_name: row.source_joint_name.trim(),
        target_joint_name: row.target_joint_name.trim(),
        direction: row.direction === "INVERTED" ? "INVERTED" : "SAME",
      } satisfies RobotModelJointMapping,
    ];
  });
}

function completeMappings(
  imported: readonly RobotModelJointMapping[],
  actuatedJointNames: readonly string[],
): readonly RobotModelJointMapping[] {
  const required = new Set(actuatedJointNames);
  const byTarget = new Map<string, RobotModelJointMapping>();
  for (const mapping of imported) {
    if (
      mapping.source_joint_name &&
      required.has(mapping.target_joint_name) &&
      !byTarget.has(mapping.target_joint_name)
    ) {
      byTarget.set(mapping.target_joint_name, mapping);
    }
  }
  return actuatedJointNames.map(
    (jointName) =>
      byTarget.get(jointName) ?? {
        source_joint_name: jointName,
        target_joint_name: jointName,
        direction: "SAME",
      },
  );
}

function resolveReferencedAsset(
  reference: string,
  urdfPath: string,
  assetPaths: ReadonlySet<string>,
): string | null {
  const normalizedReference = reference.trim().replaceAll("\\", "/");
  if (
    !normalizedReference ||
    /^(?:https?:|data:|blob:)/iu.test(normalizedReference)
  ) {
    return null;
  }
  const urdfDirectory = directoryName(urdfPath);
  const candidates: string[] = [];
  if (normalizedReference.startsWith("package://")) {
    const packagePath = normalizePath(
      normalizedReference.slice("package://".length),
    );
    candidates.push(packagePath);
    const firstSlash = packagePath.indexOf("/");
    if (firstSlash >= 0) candidates.push(packagePath.slice(firstSlash + 1));
  } else {
    candidates.push(
      normalizePath(
        urdfDirectory
          ? `${urdfDirectory}/${normalizedReference}`
          : normalizedReference,
      ),
    );
    candidates.push(normalizePath(normalizedReference));
  }
  for (const candidate of candidates) {
    if (assetPaths.has(candidate)) return candidate;
    const suffixMatch = [...assetPaths].find(
      (path) => path === candidate || path.endsWith(`/${candidate}`),
    );
    if (suffixMatch) return suffixMatch;
  }
  return null;
}

export async function parseRobotModelAssets(
  assets: readonly PendingRobotModelAsset[],
): Promise<ParsedRobotModel> {
  const urdfs = assets.filter((asset) => asset.role === "URDF");
  if (urdfs.length !== 1) {
    throw new Error(urdfs.length ? "URDF_MULTIPLE" : "URDF_MISSING");
  }
  const urdf = urdfs[0]!;
  const urdfXml = await urdf.file.text();
  const document = parseXml(urdfXml);
  const robotName = document.documentElement.getAttribute("name")?.trim();
  if (!robotName) throw new Error("URDF_ROBOT_NAME_MISSING");

  const linkNames = Array.from(document.getElementsByTagName("link"))
    .map((link) => link.getAttribute("name")?.trim() ?? "")
    .filter(Boolean);
  if (!linkNames.length) throw new Error("URDF_LINKS_MISSING");
  if (new Set(linkNames).size !== linkNames.length) {
    throw new Error("URDF_LINKS_DUPLICATED");
  }

  const joints = Array.from(document.getElementsByTagName("joint")).map(
    (joint) => ({
      name: joint.getAttribute("name")?.trim() ?? "",
      type: joint.getAttribute("type")?.trim() || "unknown",
      parent:
        joint.getElementsByTagName("parent").item(0)?.getAttribute("link") ??
        null,
      child:
        joint.getElementsByTagName("child").item(0)?.getAttribute("link") ??
        null,
    }),
  );
  if (joints.some((joint) => !joint.name)) {
    throw new Error("URDF_JOINT_NAME_MISSING");
  }
  if (new Set(joints.map((joint) => joint.name)).size !== joints.length) {
    throw new Error("URDF_JOINTS_DUPLICATED");
  }
  const actuatedJointNames = joints
    .filter((joint) => joint.type.toLowerCase() !== "fixed")
    .map((joint) => joint.name);

  const configurationAsset = assets.find(
    (asset) =>
      asset.role === "CONFIG" &&
      asset.relativePath.toLowerCase().endsWith(".json"),
  );
  let configuration: Readonly<Record<string, unknown>> = {};
  if (configurationAsset) {
    const parsed: unknown = JSON.parse(await configurationAsset.file.text());
    if (!isRecord(parsed)) throw new Error("CONFIG_JSON_OBJECT_REQUIRED");
    configuration = parsed;
  }

  const assetPaths = new Set(
    assets.map((asset) => normalizePath(asset.relativePath)),
  );
  const meshReferences = Array.from(
    document.querySelectorAll("mesh[filename], texture[filename]"),
  )
    .map((element) => element.getAttribute("filename")?.trim() ?? "")
    .filter(Boolean);
  const missingMeshReferences = meshReferences.filter(
    (reference) =>
      !resolveReferencedAsset(reference, urdf.relativePath, assetPaths),
  );

  return {
    urdfPath: urdf.relativePath,
    robotName,
    linkNames,
    joints,
    actuatedJointNames,
    meshReferences,
    missingMeshReferences,
    configurationPath: configurationAsset?.relativePath ?? null,
    configuration,
    mappings: completeMappings(
      mappingRowsFromConfiguration(configuration),
      actuatedJointNames,
    ),
    urdfXml,
  };
}

export function mappingsCoverUrdf(
  mappings: readonly RobotModelJointMapping[],
  actuatedJointNames: readonly string[],
): boolean {
  const sources = mappings.map((mapping) => mapping.source_joint_name.trim());
  const targets = mappings.map((mapping) => mapping.target_joint_name.trim());
  return (
    sources.every(Boolean) &&
    new Set(sources).size === sources.length &&
    new Set(targets).size === targets.length &&
    targets.length === actuatedJointNames.length &&
    targets.every((target) => actuatedJointNames.includes(target))
  );
}

export function buildRobotConfigurationAsset(
  analysis: ParsedRobotModel,
  mappings: readonly RobotModelJointMapping[],
  robot: RobotConfigurationIdentity,
): PendingRobotModelAsset {
  const relativePath =
    analysis.configurationPath ??
    normalizePath(
      directoryName(analysis.urdfPath)
        ? `${directoryName(analysis.urdfPath)}/robot.config.json`
        : "robot.config.json",
    );
  const content = {
    ...analysis.configuration,
    format: "hc-robot-description/v1",
    robot: {
      id: robot.robotId,
      display_name: robot.displayName,
      serial_no: robot.serialNo,
    },
    urdf: {
      entry: analysis.urdfPath,
      name: analysis.robotName,
    },
    joint_mapping: mappings.map((mapping) => ({
      source_joint_name: mapping.source_joint_name.trim(),
      target_joint_name: mapping.target_joint_name.trim(),
      direction: mapping.direction,
    })),
  };
  const serialized = `${JSON.stringify(content, null, 2)}\n`;
  const file = new File([serialized], relativePath.split("/").at(-1)!, {
    type: "application/json",
  });
  // Older embedded WebViews and jsdom expose File without Blob.text/arrayBuffer.
  // Keep the generated configuration readable and hashable in those runtimes.
  if (typeof file.text !== "function") {
    Object.defineProperty(file, "text", {
      configurable: true,
      value: async () => serialized,
    });
  }
  if (typeof file.arrayBuffer !== "function") {
    Object.defineProperty(file, "arrayBuffer", {
      configurable: true,
      value: async () => new TextEncoder().encode(serialized).buffer,
    });
  }
  return {
    file,
    relativePath,
    role: "CONFIG",
    mediaType: "application/json",
  };
}

export function replaceRobotConfigurationAsset(
  assets: readonly PendingRobotModelAsset[],
  configurationAsset: PendingRobotModelAsset,
): readonly PendingRobotModelAsset[] {
  return [
    ...assets.filter(
      (asset) =>
        asset.relativePath !== configurationAsset.relativePath &&
        !(
          asset.role === "CONFIG" &&
          asset.relativePath.toLowerCase().endsWith(".json")
        ),
    ),
    configurationAsset,
  ];
}

export function createLocalRobotPreview(
  analysis: ParsedRobotModel,
  assets: readonly PendingRobotModelAsset[],
): LocalRobotPreview {
  const urls = new Map<string, string>();
  const assetPaths = new Set(
    assets.map((asset) => normalizePath(asset.relativePath)),
  );
  for (const asset of assets) {
    if (asset.role === "URDF") continue;
    urls.set(
      normalizePath(asset.relativePath),
      URL.createObjectURL(asset.file),
    );
  }
  const document = parseXml(analysis.urdfXml);
  for (const element of Array.from(
    document.querySelectorAll("mesh[filename], texture[filename]"),
  )) {
    const reference = element.getAttribute("filename");
    if (!reference) continue;
    const resolved = resolveReferencedAsset(
      reference,
      analysis.urdfPath,
      assetPaths,
    );
    const url = resolved ? urls.get(resolved) : null;
    if (url) element.setAttribute("filename", url);
  }
  const urdfUrl = URL.createObjectURL(
    new Blob([new XMLSerializer().serializeToString(document)], {
      type: "application/xml",
    }),
  );
  return {
    urdfUrl,
    dispose() {
      URL.revokeObjectURL(urdfUrl);
      for (const url of urls.values()) URL.revokeObjectURL(url);
    },
  };
}
