import { isDomainError } from "../../shared/api/domain-error";
import type { UploadManifest } from "./formal-client";

export const MAX_MANIFEST_BYTES = 1024 * 1024;
export const MAX_PACKAGE_BYTES = 5 * 1024 ** 4;
export const MIN_MULTIPART_BYTES = 5 * 1024 ** 2;
export const MAX_MULTIPART_PARTS = 10_000;
export const AUTHORIZATION_BATCH_SIZE = 256;

const CRC64_DECIMAL = /^(?:0|[1-9][0-9]{0,19})$/u;
const CRC64_MAX = 18_446_744_073_709_551_615n;

export interface UploadPartPlan {
  readonly partSize: number;
  readonly partCount: number;
  readonly partNumbers: readonly number[];
}

export interface FolderUploadBundle {
  /** Stable, non-secret selection identity used only in the browser queue. */
  readonly id: string;
  readonly manifestFile: File;
  readonly rawFile: File;
  readonly manifest: UploadManifest;
  readonly relativeDirectory: string;
}

export interface FolderUploadDiscoveryFailure {
  readonly relativePath: string;
  readonly code:
    | "MANIFEST_INVALID"
    | "MANIFEST_AMBIGUOUS"
    | "RAW_FILE_MISSING"
    | "RAW_FILE_AMBIGUOUS"
    | "RAW_FILE_PATH_INVALID"
    | "DATA_PACKAGE_DUPLICATE"
    | "SOURCE_RECORDING_DUPLICATE";
  readonly detail: string;
}

export interface FolderUploadDiscovery {
  readonly bundles: readonly FolderUploadBundle[];
  readonly failures: readonly FolderUploadDiscoveryFailure[];
}

export interface LeRobotSourceFile {
  readonly file: File;
  /** Original path below the selected LeRobot root; this becomes the Raw object suffix. */
  readonly path: string;
}

export interface LeRobotFolderSelection {
  readonly format: "lerobot";
  readonly version: "v3.0";
  readonly robotType: "unitree_g1";
  readonly rootDirectory: string;
  readonly info: Readonly<Record<string, unknown>>;
  readonly episodeCount: number;
  readonly sourceFiles: readonly LeRobotSourceFile[];
  readonly sourceBytes: number;
}

export class LocalLeRobotError extends Error {
  constructor(
    readonly code:
      | "LEROBOT_ROOT_AMBIGUOUS"
      | "LEROBOT_INFO_INVALID"
      | "LEROBOT_PROFILE_UNSUPPORTED"
      | "LEROBOT_LAYOUT_INCOMPLETE"
      | "LEROBOT_SOURCE_INVALID",
    message: string,
  ) {
    super(message);
    this.name = "LocalLeRobotError";
  }
}

export interface UploadProblemCopy {
  readonly title: string;
  readonly detail: string;
  readonly requestId: string | null;
  readonly retryable: boolean;
  readonly status: number | null;
  readonly problemCode: string | null;
}

export class LocalManifestError extends Error {
  constructor(
    readonly code: "MANIFEST_EMPTY" | "MANIFEST_TOO_LARGE" | "MANIFEST_INVALID",
    message: string,
  ) {
    super(message);
    this.name = "LocalManifestError";
  }
}

function assertDecimalCrc64(
  value: Record<string, unknown>,
  location: string,
): void {
  if (!("crc64" in value)) return;
  const crc64 = value.crc64;
  if (
    typeof crc64 !== "string" ||
    !CRC64_DECIMAL.test(crc64) ||
    BigInt(crc64) > CRC64_MAX
  ) {
    throw new LocalManifestError(
      "MANIFEST_INVALID",
      `${location} 必须是 uint64 十进制字符串；不能使用会丢失精度的 JSON 数字。`,
    );
  }
}

function validateManifestCrc64Values(raw: Record<string, unknown>): void {
  assertDecimalCrc64(raw, "数据清单 crc64");
  if (!Array.isArray(raw.files)) return;
  raw.files.forEach((item, index) => {
    if (typeof item === "object" && item !== null && !Array.isArray(item)) {
      assertDecimalCrc64(
        item as Record<string, unknown>,
        `数据清单 files[${index}].crc64`,
      );
    }
  });
}

export function planUploadParts(size: number): UploadPartPlan {
  if (!Number.isSafeInteger(size) || size <= 0 || size > MAX_PACKAGE_BYTES) {
    throw new RangeError("数据包不能为空，且大小不得超过 5 TiB。");
  }
  const partSize = Math.max(
    MIN_MULTIPART_BYTES,
    Math.ceil(size / MAX_MULTIPART_PARTS),
  );
  const partCount = Math.ceil(size / partSize);
  return {
    partSize,
    partCount,
    partNumbers: Array.from({ length: partCount }, (_, index) => index + 1),
  };
}

export function partBounds(
  partNumber: number,
  plan: UploadPartPlan,
  totalBytes: number,
) {
  const start = (partNumber - 1) * plan.partSize;
  return { start, end: Math.min(totalBytes, start + plan.partSize) };
}

export async function parseManifestFile(file: File): Promise<UploadManifest> {
  if (file.size === 0) {
    throw new LocalManifestError(
      "MANIFEST_EMPTY",
      "数据清单为空；请选择包含 JSON 内容的数据清单文件。",
    );
  }
  if (file.size > MAX_MANIFEST_BYTES) {
    throw new LocalManifestError(
      "MANIFEST_TOO_LARGE",
      "数据清单超过正式约定的 1 MiB 上限。",
    );
  }
  let raw: unknown;
  try {
    const text =
      typeof file.text === "function"
        ? await file.text()
        : await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onerror = () =>
              reject(reader.error ?? new Error("MANIFEST_READ_FAILED"));
            reader.onload = () =>
              resolve(typeof reader.result === "string" ? reader.result : "");
            reader.readAsText(file, "utf-8");
          });
    raw = JSON.parse(text) as unknown;
  } catch {
    throw new LocalManifestError(
      "MANIFEST_INVALID",
      "数据清单必须是有效的 UTF-8 JSON。",
    );
  }
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new LocalManifestError(
      "MANIFEST_INVALID",
      "数据清单根节点必须是 JSON 对象。",
    );
  }
  validateManifestCrc64Values(raw as Record<string, unknown>);
  // The production preflight endpoint performs the complete generated-schema validation.
  // This cast only bridges untrusted JSON into that typed request; it does not assert validity.
  return raw as UploadManifest;
}

export function findManifestFile(files: readonly File[]): File | null {
  const manifestFiles = findManifestFiles(files);
  return manifestFiles.length === 1 ? (manifestFiles[0] ?? null) : null;
}

export function findManifestFiles(files: readonly File[]): readonly File[] {
  return files.filter(
    (file) =>
      file.name.toLowerCase().endsWith(".json") &&
      /(?:^|[_-])(?:rollout[_-]?)?manifest(?:[_-]|\.|$)/iu.test(file.name),
  );
}

export function findRawPackageFile(
  files: readonly File[],
  manifest: UploadManifest,
): File | null {
  const raw = manifest.files?.find((item) => item.role === "RAW_MCAP");
  if (!raw) return null;
  const expectedName = raw.path.split("/").at(-1);
  return (
    files.find((file) => {
      const relative = file.webkitRelativePath || file.name;
      return relative === raw.path || file.name === expectedName;
    }) ?? null
  );
}

/**
 * Return the browser-provided path relative to the selected directory.
 *
 * `webkitRelativePath` is a path *label*, never an OS path we can open.  It is
 * still normalized and rejected if malformed so two nested packages with the
 * same `recording.mcap` name cannot be accidentally paired across folders.
 */
export function selectedRelativePath(file: File): string | null {
  const candidate = (file as File & { readonly webkitRelativePath?: string })
    .webkitRelativePath;
  return normalizeRelativePath(
    candidate && candidate.length > 0 ? candidate : file.name,
  );
}

const LEROBOT_CAMERAS = [
  "observation.images.head_stereo_left",
  "observation.images.head_stereo_right",
  "observation.images.wrist_left",
  "observation.images.wrist_right",
] as const;
const LEROBOT_REQUIRED_FEATURES = [
  "observation.state.ee_state",
  "observation.state.hand_state",
  "observation.state.robot_q_current",
  "action.ee_action",
  "action.hand_cmd",
  "action.robot_q_desired",
  ...LEROBOT_CAMERAS,
] as const;
const LEROBOT_DATA_PATH = /^data\/chunk-\d{3}\/file-\d{3}\.parquet$/u;
const LEROBOT_EPISODE_PATH =
  /^meta\/episodes\/chunk-\d{3}\/file-\d{3}\.parquet$/u;
const LEROBOT_VIDEO_PATH = /^videos\/([^/]+)\/chunk-\d{3}\/file-\d{3}\.mp4$/u;

async function readBrowserText(file: File): Promise<string> {
  if (typeof file.text === "function") return file.text();
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () =>
      reject(reader.error ?? new Error("LEROBOT_FILE_READ_FAILED"));
    reader.onload = () =>
      resolve(typeof reader.result === "string" ? reader.result : "");
    reader.readAsText(file, "utf-8");
  });
}

/** Detect one native Unitree G1 LeRobot v3 tree without rewriting any source file. */
export async function detectLeRobotFolder(
  files: readonly File[],
): Promise<LeRobotFolderSelection | null> {
  const entries = files
    .map((file) => ({ file, selectedPath: selectedRelativePath(file) }))
    .filter(
      (entry): entry is { file: File; selectedPath: string } =>
        entry.selectedPath !== null,
    );
  const infoEntries = entries.filter(({ selectedPath }) =>
    /(?:^|\/)meta\/info\.json$/u.test(selectedPath),
  );
  if (infoEntries.length === 0) return null;
  if (infoEntries.length !== 1) {
    throw new LocalLeRobotError(
      "LEROBOT_ROOT_AMBIGUOUS",
      `所选目录包含 ${infoEntries.length} 个 LeRobot meta/info.json；一次请选择一个原始数据集。`,
    );
  }
  const infoEntry = infoEntries[0];
  if (!infoEntry) return null;
  const marker = "meta/info.json";
  const rootDirectory = infoEntry.selectedPath
    .slice(0, -marker.length)
    .replace(/\/$/u, "");
  const sourcePrefix = rootDirectory ? `${rootDirectory}/` : "";
  const sourceFiles = entries
    .filter(({ selectedPath }) =>
      sourcePrefix ? selectedPath.startsWith(sourcePrefix) : true,
    )
    .map(({ file, selectedPath }) => ({
      file,
      path: sourcePrefix
        ? selectedPath.slice(sourcePrefix.length)
        : selectedPath,
    }))
    .sort((left, right) => left.path.localeCompare(right.path));
  if (
    sourceFiles.length === 0 ||
    sourceFiles.length > 10_000 ||
    sourceFiles.some(
      ({ file, path }) =>
        !path ||
        file.size <= 0 ||
        !Number.isSafeInteger(file.size) ||
        file.size > MAX_PACKAGE_BYTES,
    )
  ) {
    throw new LocalLeRobotError(
      "LEROBOT_SOURCE_INVALID",
      "LeRobot Raw 源文件必须非空、单文件不超过 5 TiB，且总文件数不超过 10000。",
    );
  }
  if (infoEntry.file.size > MAX_MANIFEST_BYTES) {
    throw new LocalLeRobotError(
      "LEROBOT_INFO_INVALID",
      "LeRobot meta/info.json 超过 1 MiB 上限。",
    );
  }
  let info: unknown;
  try {
    info = JSON.parse(await readBrowserText(infoEntry.file)) as unknown;
  } catch {
    throw new LocalLeRobotError(
      "LEROBOT_INFO_INVALID",
      "LeRobot meta/info.json 必须是有效的 UTF-8 JSON 对象。",
    );
  }
  if (typeof info !== "object" || info === null || Array.isArray(info)) {
    throw new LocalLeRobotError(
      "LEROBOT_INFO_INVALID",
      "LeRobot meta/info.json 根节点必须是 JSON 对象。",
    );
  }
  const metadata = info as Record<string, unknown>;
  const features = metadata.features;
  const episodeCount = metadata.total_episodes;
  if (
    metadata.codebase_version !== "v3.0" ||
    metadata.robot_type !== "unitree_g1" ||
    typeof metadata.fps !== "number" ||
    !Number.isFinite(metadata.fps) ||
    metadata.fps <= 0 ||
    metadata.fps > 240 ||
    typeof metadata.data_path !== "string" ||
    typeof metadata.video_path !== "string" ||
    typeof features !== "object" ||
    features === null ||
    Array.isArray(features) ||
    !Number.isInteger(episodeCount) ||
    typeof episodeCount !== "number" ||
    episodeCount < 1 ||
    episodeCount > 100 ||
    LEROBOT_REQUIRED_FEATURES.some(
      (feature) => !(feature in (features as Record<string, unknown>)),
    )
  ) {
    throw new LocalLeRobotError(
      "LEROBOT_PROFILE_UNSUPPORTED",
      "当前页面支持 Unitree G1 的 LeRobot v3.0 原始目录（1–100 个 episode，并包含完整状态、动作和四路相机特征）。",
    );
  }
  const paths = new Set(sourceFiles.map(({ path }) => path));
  const cameraFeatures = new Set(
    sourceFiles.flatMap(({ path }) => {
      const match = LEROBOT_VIDEO_PATH.exec(path);
      return match?.[1] &&
        LEROBOT_CAMERAS.includes(match[1] as (typeof LEROBOT_CAMERAS)[number])
        ? [match[1]]
        : [];
    }),
  );
  if (
    !paths.has("meta/info.json") ||
    !sourceFiles.some(({ path }) => LEROBOT_EPISODE_PATH.test(path)) ||
    !sourceFiles.some(({ path }) => LEROBOT_DATA_PATH.test(path)) ||
    LEROBOT_CAMERAS.some((camera) => !cameraFeatures.has(camera))
  ) {
    throw new LocalLeRobotError(
      "LEROBOT_LAYOUT_INCOMPLETE",
      "LeRobot 原始目录缺少 episode 元数据、数据 Parquet 或 Unitree G1 四路相机 MP4。",
    );
  }
  return {
    format: "lerobot",
    version: "v3.0",
    robotType: "unitree_g1",
    rootDirectory,
    info: metadata,
    episodeCount,
    sourceFiles,
    sourceBytes: sourceFiles.reduce((total, item) => total + item.file.size, 0),
  };
}

/** Discover independent upload packages inside an arbitrarily nested folder. */
export async function discoverFolderUploadBundles(
  files: readonly File[],
): Promise<FolderUploadDiscovery> {
  const filesByPath = new Map<string, File>();
  const manifestFiles: Array<{ file: File; path: string }> = [];
  for (const file of files) {
    const path = selectedRelativePath(file);
    if (!path || filesByPath.has(path)) continue;
    filesByPath.set(path, file);
    if (findManifestFiles([file]).length === 1)
      manifestFiles.push({ file, path });
  }

  const bundles: FolderUploadBundle[] = [];
  const failures: FolderUploadDiscoveryFailure[] = [];
  const packageIds = new Set<string>();
  const sourceOwners = new Map<string, string>();
  const ambiguousDirectories = new Set<string>();
  const manifestCountByDirectory = new Map<string, number>();
  for (const { path } of manifestFiles) {
    const directory = directoryOf(path);
    manifestCountByDirectory.set(
      directory,
      (manifestCountByDirectory.get(directory) ?? 0) + 1,
    );
  }
  for (const [directory, count] of manifestCountByDirectory) {
    if (count <= 1) continue;
    ambiguousDirectories.add(directory);
    failures.push({
      relativePath: directory || ".",
      code: "MANIFEST_AMBIGUOUS",
      detail: `同一数据包目录中发现 ${count} 个数据清单文件，无法确定上传声明。`,
    });
  }
  const claimedRawPaths = new Set<string>();
  for (const { file: manifestFile, path: manifestPath } of manifestFiles) {
    if (ambiguousDirectories.has(directoryOf(manifestPath))) continue;
    let manifest: UploadManifest;
    try {
      manifest = await parseManifestFile(manifestFile);
    } catch (error) {
      failures.push({
        relativePath: manifestPath,
        code: "MANIFEST_INVALID",
        detail:
          error instanceof Error
            ? error.message
            : "数据清单无法在浏览器中安全解析。",
      });
      continue;
    }
    if (packageIds.has(manifest.data_package_id)) {
      failures.push({
        relativePath: manifestPath,
        code: "DATA_PACKAGE_DUPLICATE",
        detail: `数据包 ${manifest.data_package_id} 在所选目录中出现了多个数据清单文件。`,
      });
      continue;
    }
    const sourceKey = manifestSourceKey(manifest);
    const sourceOwner = sourceKey ? sourceOwners.get(sourceKey) : undefined;
    if (sourceOwner) {
      failures.push({
        relativePath: manifestPath,
        code: "SOURCE_RECORDING_DUPLICATE",
        detail: `源 episode 已由数据包 ${sourceOwner} 声明，不能再作为 ${manifest.data_package_id} 重复上传。`,
      });
      continue;
    }
    const rawDeclaration = manifest.files?.find(
      (item) => item.role === "RAW_MCAP",
    );
    if (!rawDeclaration) {
      failures.push({
        relativePath: manifestPath,
        code: "RAW_FILE_MISSING",
        detail: "数据清单没有声明 RAW_MCAP 文件。",
      });
      continue;
    }
    const directory = directoryOf(manifestPath);
    const rawPath = resolveBundlePath(directory, rawDeclaration.path);
    if (!rawPath) {
      failures.push({
        relativePath: manifestPath,
        code: "RAW_FILE_PATH_INVALID",
        detail: "数据清单中的 RAW_MCAP 路径不能离开数据清单所在目录。",
      });
      continue;
    }
    const rawFile = filesByPath.get(rawPath);
    if (!rawFile) {
      failures.push({
        relativePath: manifestPath,
        code: "RAW_FILE_MISSING",
        detail: `未在同一数据包目录中找到 ${rawDeclaration.path}。`,
      });
      continue;
    }
    if (claimedRawPaths.has(rawPath)) {
      failures.push({
        relativePath: manifestPath,
        code: "RAW_FILE_AMBIGUOUS",
        detail: `RAW_MCAP ${rawDeclaration.path} 已被另一个数据清单引用。`,
      });
      continue;
    }
    if (
      rawFile === manifestFile ||
      rawFile.name.toLowerCase().endsWith(".json")
    ) {
      failures.push({
        relativePath: manifestPath,
        code: "RAW_FILE_AMBIGUOUS",
        detail: "数据清单声明的 RAW_MCAP 与数据清单文件冲突。",
      });
      continue;
    }
    packageIds.add(manifest.data_package_id);
    if (sourceKey) sourceOwners.set(sourceKey, manifest.data_package_id);
    claimedRawPaths.add(rawPath);
    bundles.push({
      id: `${manifestPath}:${manifest.data_package_id}`,
      manifestFile,
      rawFile,
      manifest,
      relativeDirectory: directory,
    });
  }
  return { bundles, failures };
}

function manifestSourceKey(manifest: UploadManifest): string | null {
  const source = manifest.source_recording;
  if (!source) return null;
  if (source.kind === "CONTINUOUS_CAPTURE") {
    return [
      manifest.task_id,
      source.kind,
      source.device_id,
      source.recording_id,
    ].join("\n");
  }
  return [
    manifest.task_id,
    source.kind,
    source.repository.toLowerCase(),
    String(source.episode_index),
  ].join("\n");
}

function normalizeRelativePath(value: string): string | null {
  const normalized = value.replaceAll("\\", "/").replace(/^\/+|\/+$/gu, "");
  if (!normalized || normalized.includes("\0")) return null;
  const segments = normalized.split("/");
  if (
    segments.some((segment) => !segment || segment === "." || segment === "..")
  )
    return null;
  return segments.join("/");
}

function directoryOf(path: string): string {
  const separator = path.lastIndexOf("/");
  return separator === -1 ? "" : path.slice(0, separator);
}

function resolveBundlePath(
  directory: string,
  declaredPath: string,
): string | null {
  const normalizedDeclared = normalizeRelativePath(declaredPath);
  if (!normalizedDeclared) return null;
  return directory ? `${directory}/${normalizedDeclared}` : normalizedDeclared;
}

export function validateObjectStorageUri(value: string): string | null {
  const normalized = value.trim();
  if (!normalized) return "请输入平台已授权的对象地址。";
  if (normalized.length > 2048) return "对象地址超过 2048 字符上限。";
  let parsed: URL;
  try {
    parsed = new URL(normalized);
  } catch {
    return "对象地址格式无效。";
  }
  if (!["s3:", "oss:"].includes(parsed.protocol))
    return "仅支持 s3:// 或 oss:// 授权对象地址。";
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    return "对象地址不能包含凭据、签名参数或片段。";
  }
  if (!parsed.hostname || !parsed.pathname || parsed.pathname === "/")
    return "对象地址必须包含存储桶和对象路径。";
  return null;
}

export function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"] as const;
  let amount = value;
  let unit: (typeof units)[number] = units[0];
  for (const candidate of units.slice(1)) {
    if (amount < 1024) break;
    amount /= 1024;
    unit = candidate;
  }
  return `${amount >= 100 || unit === "B" ? amount.toFixed(0) : amount.toFixed(1)} ${unit}`;
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0)
    return "计算中";
  const rounded = Math.ceil(seconds);
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const remaining = rounded % 60;
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  if (minutes > 0) return `${minutes} 分 ${remaining} 秒`;
  return `${remaining} 秒`;
}

export function uploadProblemCopy(error: unknown): UploadProblemCopy {
  if (error instanceof LocalManifestError) {
    return {
      title:
        error.code === "MANIFEST_EMPTY"
          ? "未发现可用数据清单"
          : "数据清单预检失败",
      detail: error.message,
      requestId: null,
      retryable: false,
      status: error.code === "MANIFEST_TOO_LARGE" ? 413 : 422,
      problemCode: error.code,
    };
  }
  if (!isDomainError(error)) {
    return {
      title: "上传操作未完成",
      detail: "未收到可安全解释的服务端结果，请刷新事实后重试。",
      requestId: null,
      retryable: false,
      status: null,
      problemCode: null,
    };
  }
  if (error.problemCode === "SOURCE_RECORDING_DUPLICATE") {
    return {
      title: "发现重复源 episode",
      detail: error.message,
      requestId: error.requestId,
      retryable: false,
      status: error.httpStatus,
      problemCode: error.problemCode,
    };
  }
  const statusCopy: Readonly<
    Record<number, { title: string; detail: string }>
  > = {
    403: {
      title: "当前授权不能上传",
      detail: "请确认项目、区域和上传权限后重试。",
    },
    409: {
      title: "数据包事实发生冲突",
      detail: "该数据包标识、内容或来源已存在冲突，请核对数据清单和上传记录。",
    },
    422: {
      title: "数据清单预检失败",
      detail: "数据清单内容不满足正式上传约定，请根据错误码修正后重试。",
    },
    429: {
      title: "请求频率受限",
      detail: "请按服务端重试提示等待，不要连续重复提交。",
    },
  };
  const fallback = statusCopy[error.httpStatus ?? 0];
  return {
    title:
      fallback?.title ??
      (error.httpStatus === 413 ? "数据包超过限制" : "上传操作未完成"),
    detail: error.message || fallback?.detail || "服务端拒绝了本次操作。",
    requestId: error.requestId,
    retryable: error.retryable,
    status: error.httpStatus,
    problemCode: error.problemCode,
  };
}
