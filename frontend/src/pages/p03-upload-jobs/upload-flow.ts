import type { IngestScope } from "../../entities/data-source";
import type { ManifestPreflight, UploadManifest } from "./formal-client";
import type { BrowserSelectionMode } from "./components/UploadMethodPanel";
import {
  discoverFolderUploadBundles,
  detectLeRobotFolder,
  findManifestFiles,
  findRawPackageFile,
  LocalManifestError,
  LocalLeRobotError,
  parseManifestFile,
  selectedRelativePath,
  validateObjectStorageUri,
  type LeRobotFolderSelection,
} from "./upload-contract";

export type UploadFlowPhase =
  | "idle"
  | "folder_selected"
  | "confirming"
  | "prechecking"
  | "precheck_failed"
  | "queue_ready"
  | "uploading"
  | "completed";

export type UploadSourceChoice =
  | "BROWSER_MULTIPART"
  | "OBJECT_STORAGE_REFERENCE";

export type ServerPrecheckStage =
  | "submitting_manifest"
  | "validating_manifest"
  | "creating_queue";

export interface LocalSelectionProblem {
  readonly code: string;
  readonly detail: string;
  readonly fileName: string | null;
}

export interface LocalUploadUnit {
  readonly id: string;
  readonly manifestFile: File;
  readonly manifest: UploadManifest;
  readonly rawFile: File | null;
  readonly relativeDirectory: string;
}

export interface LocalUploadSelection {
  readonly sourceType: UploadSourceChoice;
  readonly browserSelectionMode: BrowserSelectionMode;
  readonly folderName: string;
  readonly files: readonly File[];
  readonly manifestFiles: readonly File[];
  readonly rawFiles: readonly File[];
  readonly units: readonly LocalUploadUnit[];
  readonly lerobot: LeRobotFolderSelection | null;
  readonly totalLocalBytes: number;
  readonly declaredRawBytes: number;
  readonly objectStorageUri: string;
  readonly problems: readonly LocalSelectionProblem[];
}

export type UploadFlowState =
  | { readonly phase: "idle" }
  | { readonly phase: "folder_selected"; readonly folderName: string }
  | {
      readonly phase: "confirming";
      readonly selection: LocalUploadSelection;
      readonly preparedItemIds?: readonly string[];
    }
  | {
      readonly phase: "prechecking";
      readonly selection: LocalUploadSelection;
      readonly stage: ServerPrecheckStage;
      readonly completedUnits: number;
    }
  | {
      readonly phase: "precheck_failed";
      readonly selection: LocalUploadSelection;
      readonly problem: {
        readonly title: string;
        readonly detail: string;
        readonly problemCode: string | null;
        readonly requestId: string | null;
        readonly retryable: boolean;
      };
      readonly preparedItemIds: readonly string[];
    }
  | { readonly phase: "queue_ready" }
  | { readonly phase: "uploading" }
  | { readonly phase: "completed" };

export interface InspectLocalSelectionInput {
  readonly sourceType: UploadSourceChoice;
  readonly browserSelectionMode: BrowserSelectionMode;
  readonly files: readonly File[];
  readonly objectStorageUri: string;
}

function rootFolderName(files: readonly File[]): string {
  const path = files[0] ? selectedRelativePath(files[0]) : null;
  if (!path) return "未命名选择";
  const segments = path.split("/");
  return segments.length > 1 ? (segments[0] ?? "未命名选择") : "所选文件";
}

function problem(
  code: string,
  detail: string,
  fileName: string | null = null,
): LocalSelectionProblem {
  return { code, detail, fileName };
}

function inspectParsedPackage(
  files: readonly File[],
  manifestFile: File,
  manifest: UploadManifest,
): {
  readonly unit: LocalUploadUnit;
  readonly problems: LocalSelectionProblem[];
} {
  const problems: LocalSelectionProblem[] = [];
  const rawDeclarations = manifest.files?.filter(
    (entry) => entry.role === "RAW_MCAP",
  );
  if (rawDeclarations.length !== 1) {
    problems.push(
      problem(
        "RAW_DECLARATION_INVALID",
        `数据清单必须且只能声明 1 个 RAW_MCAP，当前为 ${rawDeclarations.length} 个。`,
        manifestFile.name,
      ),
    );
  }
  const rawFile = findRawPackageFile(files, manifest);
  if (!rawFile) {
    problems.push(
      problem(
        "RAW_FILE_MISSING",
        "未找到数据清单声明的 RAW/MCAP 文件。",
        manifestFile.name,
      ),
    );
  } else {
    const declared = rawDeclarations[0];
    if (
      rawFile.size !== manifest.file_size ||
      rawFile.size !== declared?.size
    ) {
      problems.push(
        problem(
          "RAW_FILE_SIZE_MISMATCH",
          `本地文件 ${rawFile.name} 的大小与数据清单声明不一致。`,
          rawFile.name,
        ),
      );
    }
  }
  return {
    unit: {
      id: `${manifestFile.name}:${manifest.data_package_id ?? "unknown"}`,
      manifestFile,
      manifest,
      rawFile,
      relativeDirectory: "",
    },
    problems,
  };
}

export async function inspectLocalUploadSelection(
  input: InspectLocalSelectionInput,
): Promise<LocalUploadSelection> {
  const manifestFiles = findManifestFiles(input.files);
  const rawFiles = input.files.filter((file) =>
    /\.(?:mcap|raw)$/iu.test(file.name),
  );
  const problems: LocalSelectionProblem[] = [];
  const units: LocalUploadUnit[] = [];
  let lerobot: LeRobotFolderSelection | null = null;
  let lerobotInspectionFailed = false;

  if (
    input.sourceType === "BROWSER_MULTIPART" &&
    input.browserSelectionMode === "folder"
  ) {
    try {
      lerobot = await detectLeRobotFolder(input.files);
    } catch (error) {
      lerobotInspectionFailed = true;
      problems.push(
        problem(
          error instanceof LocalLeRobotError
            ? error.code
            : "LEROBOT_INFO_INVALID",
          error instanceof Error
            ? error.message
            : "LeRobot 原始目录无法在浏览器本地解析。",
        ),
      );
    }
  }

  if (manifestFiles.length === 0 && !lerobot && !lerobotInspectionFailed) {
    problems.push(
      problem(
        "MANIFEST_FILE_MISSING",
        "所选内容中没有找到可识别的数据清单文件。",
      ),
    );
  }

  if (
    !lerobot &&
    input.sourceType === "BROWSER_MULTIPART" &&
    input.browserSelectionMode === "folder"
  ) {
    const discovery = await discoverFolderUploadBundles(input.files);
    units.push(
      ...discovery.bundles.map((bundle) => ({
        id: bundle.id,
        manifestFile: bundle.manifestFile,
        manifest: bundle.manifest,
        rawFile: bundle.rawFile,
        relativeDirectory: bundle.relativeDirectory,
      })),
    );
    problems.push(
      ...discovery.failures.map((failure) =>
        problem(failure.code, failure.detail, failure.relativePath),
      ),
    );
    for (const unit of units) {
      const declaration = unit.manifest.files.find(
        (entry) => entry.role === "RAW_MCAP",
      );
      if (
        unit.rawFile &&
        (unit.rawFile.size !== unit.manifest.file_size ||
          unit.rawFile.size !== declaration?.size)
      ) {
        problems.push(
          problem(
            "RAW_FILE_SIZE_MISMATCH",
            `本地文件 ${unit.rawFile.name} 的大小与数据清单声明不一致。`,
            selectedRelativePath(unit.rawFile),
          ),
        );
      }
    }
  } else if (!lerobot && manifestFiles.length > 1) {
    problems.push(
      problem(
        "MANIFEST_FILE_AMBIGUOUS",
        `当前上传单元找到 ${manifestFiles.length} 个数据清单文件，请只保留一个。`,
      ),
    );
  } else if (!lerobot && manifestFiles[0]) {
    try {
      const manifest = await parseManifestFile(manifestFiles[0]);
      if (input.sourceType === "OBJECT_STORAGE_REFERENCE") {
        const uriProblem = validateObjectStorageUri(input.objectStorageUri);
        if (uriProblem)
          problems.push(
            problem("OBJECT_STORAGE_URI_INVALID", uriProblem, null),
          );
        units.push({
          id: `${manifestFiles[0].name}:${manifest.data_package_id ?? "unknown"}`,
          manifestFile: manifestFiles[0],
          manifest,
          rawFile: null,
          relativeDirectory: "",
        });
      } else {
        const parsed = inspectParsedPackage(
          input.files,
          manifestFiles[0],
          manifest,
        );
        units.push(parsed.unit);
        problems.push(...parsed.problems);
      }
    } catch (error) {
      problems.push(
        problem(
          error instanceof LocalManifestError ? error.code : "MANIFEST_INVALID",
          error instanceof Error
            ? error.message
            : "数据清单无法在浏览器本地解析。",
          manifestFiles[0].name,
        ),
      );
    }
  }

  if (units.length === 0 && !lerobot && problems.length === 0) {
    problems.push(
      problem("UPLOAD_UNIT_MISSING", "没有识别到可上传的数据单元。"),
    );
  }

  return {
    sourceType: input.sourceType,
    browserSelectionMode: input.browserSelectionMode,
    folderName: rootFolderName(input.files),
    files: input.files,
    manifestFiles,
    rawFiles,
    units,
    lerobot,
    totalLocalBytes: input.files.reduce((total, file) => total + file.size, 0),
    declaredRawBytes:
      lerobot?.sourceBytes ??
      units.reduce((total, unit) => total + unit.manifest.file_size, 0),
    objectStorageUri: input.objectStorageUri,
    problems,
  };
}

export function selectionTargetFacts(
  selection: LocalUploadSelection,
  scope: IngestScope,
) {
  return {
    projectId: scope.projectId,
    regionCode: scope.regionCode,
    taskIds: [...new Set(selection.units.map((unit) => unit.manifest.task_id))],
    robotIds: [
      ...new Set(selection.units.map((unit) => unit.manifest.robot_id)),
    ],
    packageIds: [
      ...new Set(selection.units.map((unit) => unit.manifest.data_package_id)),
    ],
  };
}

export interface ServerCheckedUploadUnit {
  readonly local: LocalUploadUnit;
  readonly preflight: ManifestPreflight;
}
