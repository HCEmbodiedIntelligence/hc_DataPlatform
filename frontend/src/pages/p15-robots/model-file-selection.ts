import type { RobotModelAssetUploadInput } from "../../features/robot-models/api";

export type PendingRobotModelAsset = Omit<RobotModelAssetUploadInput, "sha256">;

export interface RobotModelFileCandidate {
  readonly file: File;
  readonly relativePath?: string;
}

export interface RobotModelFileSelection {
  readonly assets: readonly PendingRobotModelAsset[];
  readonly rejectedPaths: readonly string[];
}

interface FileSystemEntryLike {
  readonly isFile: boolean;
  readonly isDirectory: boolean;
  readonly name: string;
}

interface FileSystemFileEntryLike extends FileSystemEntryLike {
  file(
    successCallback: (file: File) => void,
    errorCallback?: (error: DOMException) => void,
  ): void;
}

interface FileSystemDirectoryReaderLike {
  readEntries(
    successCallback: (entries: readonly FileSystemEntryLike[]) => void,
    errorCallback?: (error: DOMException) => void,
  ): void;
}

interface FileSystemDirectoryEntryLike extends FileSystemEntryLike {
  createReader(): FileSystemDirectoryReaderLike;
}

const assetRoleForExtension: Record<
  string,
  Pick<RobotModelAssetUploadInput, "role" | "mediaType">
> = {
  urdf: { role: "URDF", mediaType: "application/xml" },
  xml: { role: "URDF", mediaType: "application/xml" },
  stl: { role: "MESH", mediaType: "model/stl" },
  obj: { role: "MESH", mediaType: "model/obj" },
  dae: { role: "MESH", mediaType: "model/vnd.collada+xml" },
  glb: { role: "MESH", mediaType: "model/gltf-binary" },
  gltf: { role: "MESH", mediaType: "model/gltf+json" },
  png: { role: "TEXTURE", mediaType: "image/png" },
  jpg: { role: "TEXTURE", mediaType: "image/jpeg" },
  jpeg: { role: "TEXTURE", mediaType: "image/jpeg" },
  webp: { role: "TEXTURE", mediaType: "image/webp" },
  ktx2: { role: "TEXTURE", mediaType: "image/ktx2" },
  json: { role: "CONFIG", mediaType: "application/json" },
  yaml: { role: "CONFIG", mediaType: "application/yaml" },
  yml: { role: "CONFIG", mediaType: "application/yaml" },
  toml: { role: "CONFIG", mediaType: "application/toml" },
  md: { role: "DOCUMENTATION", mediaType: "text/markdown" },
  txt: { role: "DOCUMENTATION", mediaType: "text/plain" },
  pdf: { role: "DOCUMENTATION", mediaType: "application/pdf" },
};

function normalizeRelativePath(value: string): string | null {
  const normalized = value.replaceAll("\\", "/").replace(/^\/+|\/+$/gu, "");
  if (
    !normalized ||
    normalized.includes("\0") ||
    normalized
      .split("/")
      .some((part) => part === "" || part === "." || part === "..")
  ) {
    return null;
  }
  return normalized;
}

function metadataForFile(
  file: File,
): Pick<RobotModelAssetUploadInput, "role" | "mediaType"> | null {
  const extension = file.name.split(".").at(-1)?.toLowerCase();
  if (!extension) return null;
  const metadata = assetRoleForExtension[extension];
  return metadata
    ? {
        ...metadata,
        mediaType: file.type || metadata.mediaType,
      }
    : null;
}

export function classifyRobotModelFiles(
  candidates: readonly RobotModelFileCandidate[],
): RobotModelFileSelection {
  const assets = new Map<string, PendingRobotModelAsset>();
  const rejectedPaths: string[] = [];
  for (const candidate of candidates) {
    const rawPath =
      candidate.relativePath ||
      candidate.file.webkitRelativePath ||
      candidate.file.name;
    const relativePath = normalizeRelativePath(rawPath);
    const metadata = metadataForFile(candidate.file);
    if (!relativePath || !metadata || candidate.file.size === 0) {
      rejectedPaths.push(rawPath || candidate.file.name);
      continue;
    }
    assets.set(relativePath, {
      file: candidate.file,
      relativePath,
      role: metadata.role,
      mediaType: metadata.mediaType,
    });
  }
  return { assets: [...assets.values()], rejectedPaths };
}

function fileFromEntry(entry: FileSystemFileEntryLike): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

function directoryBatch(
  reader: FileSystemDirectoryReaderLike,
): Promise<readonly FileSystemEntryLike[]> {
  return new Promise((resolve, reject) => reader.readEntries(resolve, reject));
}

async function allDirectoryEntries(
  directory: FileSystemDirectoryEntryLike,
): Promise<readonly FileSystemEntryLike[]> {
  const reader = directory.createReader();
  const entries: FileSystemEntryLike[] = [];
  let batch: readonly FileSystemEntryLike[];
  do {
    batch = await directoryBatch(reader);
    entries.push(...batch);
  } while (batch.length);
  return entries;
}

async function candidatesFromEntry(
  entry: FileSystemEntryLike,
  parentPath: string,
): Promise<readonly RobotModelFileCandidate[]> {
  if (entry.isFile) {
    const file = await fileFromEntry(entry as FileSystemFileEntryLike);
    return [{ file, relativePath: `${parentPath}${file.name}` }];
  }
  if (!entry.isDirectory) return [];
  const directory = entry as FileSystemDirectoryEntryLike;
  const children = await allDirectoryEntries(directory);
  const directoryPath = `${parentPath}${directory.name}/`;
  return (
    await Promise.all(
      children.map((child) => candidatesFromEntry(child, directoryPath)),
    )
  ).flat();
}

export async function robotModelFilesFromDrop(
  dataTransfer: DataTransfer,
): Promise<readonly RobotModelFileCandidate[]> {
  const items = Array.from(dataTransfer.items ?? []);
  const entries = items.flatMap((item) => {
    const getEntry = (
      item as DataTransferItem & {
        webkitGetAsEntry?: () => FileSystemEntryLike | null;
      }
    ).webkitGetAsEntry;
    const entry = item.kind === "file" ? getEntry?.call(item) : null;
    return entry ? [entry] : [];
  });
  if (entries.length) {
    return (
      await Promise.all(entries.map((entry) => candidatesFromEntry(entry, "")))
    ).flat();
  }
  return Array.from(dataTransfer.files ?? []).map((file) => ({ file }));
}

export function modelAssetSelectionError(
  assets: readonly PendingRobotModelAsset[],
): string | null {
  if (assets.length > 256)
    return "一次最多上传 256 个模型文件，请精简文件夹后重试。";
  const urdfCount = assets.filter((asset) => asset.role === "URDF").length;
  if (assets.length && urdfCount === 0) {
    return "没有检测到 URDF 文件，请添加一个 .urdf 或 .xml 文件。";
  }
  if (urdfCount > 1) {
    return "检测到多个 URDF 文件，请只保留当前机器人使用的一个 URDF。";
  }
  return null;
}
