import { create } from "zustand";
import type { IngestScope } from "../../entities/data-source";
import {
  cancelFormalUpload,
  commitFormalManifest,
  completeFormalUpload,
  createFormalUploadSession,
  getFormalUploadManifest,
  getFormalUploadSession,
  listFormalUploadParts,
  listFormalUploadSessions,
  pauseFormalUpload,
  preflightUploadManifest,
  renewFormalUploadParts,
  resumeFormalUpload,
  retryFormalUploadParts,
  type FormalUploadSession,
  type ManifestPreflight,
  type PartAuthorization,
  type UploadManifest,
  type UploadPart,
} from "./formal-client";
import {
  AUTHORIZATION_BATCH_SIZE,
  type FolderUploadBundle,
  findRawPackageFile,
  formatBytes,
  partBounds,
  planUploadParts,
  uploadProblemCopy,
  type UploadPartPlan,
  type UploadProblemCopy,
} from "./upload-contract";
import {
  LeRobotUploadFailure,
  uploadNativeLeRobot,
  type LeRobotTargetBinding,
  type LeRobotUploadProgress,
  type LeRobotUploadResume,
} from "./lerobot-client";
import type { LeRobotFolderSelection } from "./upload-contract";

export type QueueTransferStatus =
  | "waiting"
  | "preparing"
  | "uploading"
  | "pausing"
  | "paused"
  | "offline"
  | "failed"
  | "finalizing"
  | "committed"
  | "cancelled"
  | "needs-file";

export const PART_TRANSFER_TIMEOUT_MS = 120_000;
export const MAX_CONCURRENT_PART_UPLOADS = 4;
export const MAX_IN_FLIGHT_PART_BYTES = 128 * 1024 ** 2;
export const UPLOAD_PROGRESS_PAINT_INTERVAL_MS = 250;
/**
 * A 5 TiB object necessarily has roughly 524 MiB parts at the S3/OSS 10,000-part
 * ceiling.  A fixed two-minute timeout rejects such a part on otherwise usable
 * links.  This is deliberately a conservative *floor* for timeout calculation,
 * not a throughput promise or a global admission limit.
 */
export const SLOW_LINK_BYTES_PER_SECOND = 1024 ** 2;
export const PRESIGNED_URL_SETTLE_MARGIN_MS = 30_000;
const MIN_PART_TRANSFER_TIMEOUT_MS = 15_000;

export interface FailedPartTransfer {
  readonly partNumber: number;
  readonly failureCode: string;
}

export interface UploadQueueItem {
  readonly id: string;
  readonly scopeKey: string;
  readonly sessionId: string | null;
  readonly sourceType:
    | "BROWSER_MULTIPART"
    | "OBJECT_STORAGE_REFERENCE"
    | "LEROBOT_NATIVE";
  readonly fileName: string;
  readonly dataPackageId: string;
  readonly totalBytes: number;
  readonly uploadedBytes: number;
  readonly completedParts: number;
  readonly totalParts: number;
  readonly speedBytesPerSecond: number | null;
  readonly remainingSeconds: number | null;
  readonly transferStatus: QueueTransferStatus;
  readonly serverStatus: FormalUploadSession["status"] | null;
  readonly failedParts: readonly number[];
  readonly failedPartTransfers: readonly FailedPartTransfer[];
  readonly failureCode: string | null;
  readonly failureMessage: string | null;
  readonly requestId: string | null;
  readonly createdAt: string;
  readonly robotId?: string;
  readonly collectionTaskId?: string;
}

interface UploadRuntime {
  scope: IngestScope;
  manifest: UploadManifest;
  preflight: ManifestPreflight;
  file: File | null;
  plan: UploadPartPlan | null;
  controller: AbortController | null;
  intent: "run" | "pause" | "cancel" | "offline";
  initialAuthorizations: readonly PartAuthorization[];
}

interface LeRobotUploadRuntime {
  readonly scope: IngestScope;
  readonly selection: LeRobotFolderSelection;
  readonly binding: LeRobotTargetBinding;
  controller: AbortController | null;
  intent: "run" | "pause" | "offline";
  resume: LeRobotUploadResume | null;
}

export interface StartUploadInput {
  readonly scope: IngestScope;
  readonly preflight: ManifestPreflight;
  readonly sourceType: "BROWSER_MULTIPART" | "OBJECT_STORAGE_REFERENCE";
  readonly files: readonly File[];
  readonly objectStorageUri?: string;
  /** Internal queue identity so a folder batch can resume the same item. */
  readonly queueItemId?: string;
  /** Stable per-package key used only by the folder-import scheduler. */
  readonly idempotencyKey?: string;
  /** Queue-first flows renew authorization only when this item actually starts. */
  readonly deferPartAuthorization?: boolean;
}

export interface FolderBatchProgress {
  readonly batchId: string;
  readonly total: number;
  readonly completed: number;
  readonly failed: number;
  readonly activeDataPackageId: string | null;
  readonly status: "running" | "offline" | "completed";
}

interface FolderBatchRuntime {
  readonly batchId: string;
  readonly scope: IngestScope;
  readonly bundles: readonly FolderUploadBundle[];
  nextIndex: number;
  pausedItemId: string | null;
  running: boolean;
}

interface UploadQueueState {
  readonly scopeKey: string | null;
  readonly recovering: boolean;
  readonly recoveryProblem: UploadProblemCopy | null;
  readonly items: readonly UploadQueueItem[];
  readonly folderBatch: FolderBatchProgress | null;
  bindScope: (scope: IngestScope) => void;
  recover: (scope: IngestScope) => Promise<void>;
  prepare: (input: StartUploadInput) => Promise<string>;
  beginPrepared: (itemId: string) => Promise<void>;
  start: (input: StartUploadInput) => Promise<void>;
  startLeRobot: (input: {
    readonly scope: IngestScope;
    readonly selection: LeRobotFolderSelection;
    readonly binding: LeRobotTargetBinding;
  }) => Promise<void>;
  startFolderBatch: (input: {
    readonly scope: IngestScope;
    readonly bundles: readonly FolderUploadBundle[];
  }) => Promise<void>;
  pause: (itemId: string) => Promise<void>;
  resume: (itemId: string) => Promise<void>;
  retryFailedParts: (itemId: string) => Promise<void>;
  reattachAndResume: (itemId: string, file: File) => Promise<void>;
  cancel: (itemId: string) => Promise<void>;
  clearSettled: () => void;
  handleOffline: () => void;
  handleOnline: () => void;
}

class PartTransferError extends Error {
  constructor(
    readonly partNumber: number,
    readonly failureCode: string,
    message: string,
  ) {
    super(message);
    this.name = "PartTransferError";
  }
}

const runtimeByItem = new Map<string, UploadRuntime>();
const leRobotRuntimeByItem = new Map<string, LeRobotUploadRuntime>();
const folderBatchRuntimeById = new Map<string, FolderBatchRuntime>();
const activeServerStates = new Set<FormalUploadSession["status"]>([
  "REGISTERED",
  "UPLOADING",
  "PAUSED",
  "MULTIPART_COMPLETED",
  "FAILED",
]);

/**
 * Keep browser multipart throughput bounded by both a connection cap and an
 * in-flight byte budget. Small 5–16 MiB parts can use the measured eight-way
 * parallelism; very large computed parts step down before they can retain
 * multi-gigabyte request bodies in the browser.
 */
export function partUploadConcurrency(
  plan: UploadPartPlan,
  authorizationCount: number,
): number {
  const memoryBound = Math.max(
    1,
    Math.floor(MAX_IN_FLIGHT_PART_BYTES / plan.partSize),
  );
  return Math.max(
    1,
    Math.min(MAX_CONCURRENT_PART_UPLOADS, memoryBound, authorizationCount),
  );
}

/**
 * Bound an XHR timeout by the actual short-lived authorization, while allowing
 * large legal multipart parts enough time on a conservative 1 MiB/s link.  The
 * server supplies the deadline, so the browser never knowingly keeps a PUT alive
 * through a presigned URL expiry.
 */
export function partTransferTimeoutMs(
  partBytes: number,
  authorization: Pick<PartAuthorization, "expires_at">,
  nowMs = Date.now(),
): number {
  const transferMs = Math.ceil(
    (partBytes / SLOW_LINK_BYTES_PER_SECOND) * 1_000,
  );
  const desiredMs = Math.max(
    PART_TRANSFER_TIMEOUT_MS,
    transferMs + PRESIGNED_URL_SETTLE_MARGIN_MS,
  );
  const expiresAtMs = Date.parse(authorization.expires_at);
  const authorizedWindowMs =
    expiresAtMs - nowMs - PRESIGNED_URL_SETTLE_MARGIN_MS;
  if (!Number.isFinite(authorizedWindowMs) || authorizedWindowMs <= 0) {
    throw new RangeError("part authorization is invalid or expired");
  }
  return Math.max(
    MIN_PART_TRANSFER_TIMEOUT_MS,
    Math.min(desiredMs, authorizedWindowMs),
  );
}

function scopeKey(scope: IngestScope): string {
  return `${scope.organizationId}/${scope.projectId}/${scope.regionCode}`;
}

function newId(): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `upload-${Date.now()}-${Math.random().toString(16).slice(2)}`
  );
}

function updateItem(
  set: (
    updater: (state: UploadQueueState) => Partial<UploadQueueState>,
  ) => void,
  itemId: string,
  updater: (item: UploadQueueItem) => UploadQueueItem,
) {
  set((state) => ({
    items: state.items.map((item) =>
      item.id === itemId ? updater(item) : item,
    ),
  }));
}

function updateFolderBatch(
  set: (
    updater: (state: UploadQueueState) => Partial<UploadQueueState>,
  ) => void,
  batchId: string,
  updater: (batch: FolderBatchProgress) => FolderBatchProgress,
) {
  set((state) => ({
    folderBatch:
      state.folderBatch?.batchId === batchId
        ? updater(state.folderBatch)
        : state.folderBatch,
  }));
}

function folderIdempotencyKey(bundle: FolderUploadBundle): string {
  return `folder-import:${bundle.manifest.data_package_id}:${bundle.manifest.sha256}`;
}

function folderFailureQueueItem(
  bundle: FolderUploadBundle,
  scope: IngestScope,
  itemId: string,
  error: unknown,
): UploadQueueItem {
  const copy =
    error instanceof Error && error.message === "FOLDER_PACKAGE_ALREADY_ACTIVE"
      ? {
          problemCode: "FOLDER_PACKAGE_ALREADY_ACTIVE",
          detail:
            "此数据包已在当前页面的另一条传输中运行；为避免重复写入，本批次未接管它。",
          requestId: null,
        }
      : uploadProblemCopy(error);
  return {
    id: itemId,
    scopeKey: scopeKey(scope),
    sessionId: null,
    sourceType: "BROWSER_MULTIPART",
    fileName: bundle.rawFile.name,
    dataPackageId: bundle.manifest.data_package_id,
    totalBytes: bundle.rawFile.size,
    uploadedBytes: 0,
    completedParts: 0,
    totalParts: planUploadParts(bundle.rawFile.size).partCount,
    speedBytesPerSecond: null,
    remainingSeconds: null,
    transferStatus: "failed",
    serverStatus: null,
    failedParts: [],
    failedPartTransfers: [],
    failureCode: copy.problemCode ?? "MANIFEST_PREFLIGHT_FAILED",
    failureMessage: copy.detail,
    requestId: copy.requestId,
    createdAt: new Date().toISOString(),
  };
}

function recordFolderBatchItem(
  set: (
    updater: (state: UploadQueueState) => Partial<UploadQueueState>,
  ) => void,
  batchId: string,
  item: UploadQueueItem | undefined,
) {
  updateFolderBatch(set, batchId, (batch) => ({
    ...batch,
    completed: batch.completed + 1,
    failed: batch.failed + Number(item?.transferStatus === "failed"),
    activeDataPackageId: null,
  }));
}

async function continueFolderBatch(
  batchId: string,
  set: (
    updater: (state: UploadQueueState) => Partial<UploadQueueState>,
  ) => void,
  get: () => UploadQueueState,
): Promise<void> {
  const runtime = folderBatchRuntimeById.get(batchId);
  if (!runtime || runtime.running) return;
  runtime.running = true;
  try {
    while (runtime.nextIndex < runtime.bundles.length) {
      if (typeof navigator !== "undefined" && navigator.onLine === false) {
        updateFolderBatch(set, batchId, (batch) => ({
          ...batch,
          status: "offline",
          activeDataPackageId: null,
        }));
        return;
      }
      const bundle = runtime.bundles[runtime.nextIndex++];
      if (!bundle) continue;
      const itemId = newId();
      updateFolderBatch(set, batchId, (batch) => ({
        ...batch,
        status: "running",
        activeDataPackageId: bundle.manifest.data_package_id,
      }));
      const recoveredItem = get().items.find(
        (item) =>
          item.scopeKey === scopeKey(runtime.scope) &&
          item.sourceType === "BROWSER_MULTIPART" &&
          item.sessionId !== null &&
          item.dataPackageId === bundle.manifest.data_package_id,
      );
      if (recoveredItem) {
        if (recoveredItem.transferStatus === "committed") {
          recordFolderBatchItem(set, batchId, recoveredItem);
          continue;
        }
        if (
          ["preparing", "uploading", "pausing", "finalizing"].includes(
            recoveredItem.transferStatus,
          )
        ) {
          set((state) => ({
            items: [
              folderFailureQueueItem(
                bundle,
                runtime.scope,
                itemId,
                new Error("FOLDER_PACKAGE_ALREADY_ACTIVE"),
              ),
              ...state.items,
            ],
          }));
          recordFolderBatchItem(
            set,
            batchId,
            get().items.find((item) => item.id === itemId),
          );
          continue;
        }
        await get().reattachAndResume(recoveredItem.id, bundle.rawFile);
        const resumed = get().items.find(
          (item) => item.id === recoveredItem.id,
        );
        if (resumed?.transferStatus === "offline") {
          runtime.pausedItemId = recoveredItem.id;
          updateFolderBatch(set, batchId, (batch) => ({
            ...batch,
            status: "offline",
            activeDataPackageId: bundle.manifest.data_package_id,
          }));
          return;
        }
        recordFolderBatchItem(set, batchId, resumed);
        continue;
      }
      let preflight: ManifestPreflight;
      try {
        preflight = await preflightUploadManifest(
          runtime.scope,
          bundle.manifest,
        );
      } catch (error) {
        set((state) => ({
          items: [
            folderFailureQueueItem(bundle, runtime.scope, itemId, error),
            ...state.items,
          ],
        }));
        recordFolderBatchItem(
          set,
          batchId,
          get().items.find((item) => item.id === itemId),
        );
        continue;
      }
      await get().start({
        scope: runtime.scope,
        preflight,
        sourceType: "BROWSER_MULTIPART",
        files: [bundle.manifestFile, bundle.rawFile],
        queueItemId: itemId,
        idempotencyKey: folderIdempotencyKey(bundle),
      });
      const item = get().items.find((candidate) => candidate.id === itemId);
      if (item?.transferStatus === "offline") {
        runtime.pausedItemId = itemId;
        updateFolderBatch(set, batchId, (batch) => ({
          ...batch,
          status: "offline",
          activeDataPackageId: bundle.manifest.data_package_id,
        }));
        return;
      }
      recordFolderBatchItem(set, batchId, item);
    }
    updateFolderBatch(set, batchId, (batch) => ({
      ...batch,
      status: "completed",
      activeDataPackageId: null,
    }));
    folderBatchRuntimeById.delete(batchId);
  } finally {
    runtime.running = false;
  }
}

async function resumeFolderBatch(
  runtime: FolderBatchRuntime,
  set: (
    updater: (state: UploadQueueState) => Partial<UploadQueueState>,
  ) => void,
  get: () => UploadQueueState,
): Promise<void> {
  if (runtime.pausedItemId) {
    const itemId = runtime.pausedItemId;
    runtime.pausedItemId = null;
    await get().resume(itemId);
    const item = get().items.find((candidate) => candidate.id === itemId);
    if (item?.transferStatus === "offline") {
      runtime.pausedItemId = itemId;
      return;
    }
    recordFolderBatchItem(set, runtime.batchId, item);
  }
  await continueFolderBatch(runtime.batchId, set, get);
}

function uploadedBytes(
  parts: readonly UploadPart[],
  plan: UploadPartPlan,
  totalBytes: number,
): number {
  return parts
    .filter((part) => part.status === "UPLOADED")
    .reduce((total, part) => {
      if (part.size !== null && part.size !== undefined)
        return total + part.size;
      const bounds = partBounds(part.part_number, plan, totalBytes);
      return total + bounds.end - bounds.start;
    }, 0);
}

function failedPartTransfers(
  parts: readonly UploadPart[],
): readonly FailedPartTransfer[] {
  return parts
    .filter((part) => part.status === "FAILED")
    .map((part) => ({
      partNumber: part.part_number,
      failureCode: part.failure_code ?? "PART_FAILED",
    }));
}

function mergeFailedPartTransfers(
  current: readonly FailedPartTransfer[],
  additions: readonly FailedPartTransfer[],
): readonly FailedPartTransfer[] {
  const merged = new Map(
    current.map((failure) => [failure.partNumber, failure.failureCode]),
  );
  for (const failure of additions)
    merged.set(failure.partNumber, failure.failureCode);
  return [...merged]
    .sort(([left], [right]) => left - right)
    .map(([partNumber, failureCode]) => ({ partNumber, failureCode }));
}

function itemFromRecovered(
  session: FormalUploadSession,
  preflight: ManifestPreflight,
  parts: readonly UploadPart[],
  itemScopeKey: string,
): UploadQueueItem {
  const plan =
    session.source_type === "BROWSER_MULTIPART"
      ? planUploadParts(session.expected_size)
      : null;
  const complete = parts.filter((part) => part.status === "UPLOADED").length;
  const failures = failedPartTransfers(parts);
  const raw = preflight.files.find((file) => file.role === "RAW_MCAP");
  let transferStatus: QueueTransferStatus = "needs-file";
  if (session.source_type === "OBJECT_STORAGE_REFERENCE")
    transferStatus = "failed";
  if (session.status === "PAUSED") transferStatus = "paused";
  if (session.status === "MULTIPART_COMPLETED") transferStatus = "failed";
  if (session.status === "FAILED") transferStatus = "failed";
  return {
    id: session.session_id ?? newId(),
    scopeKey: itemScopeKey,
    sessionId: session.session_id ?? null,
    sourceType: session.source_type,
    fileName: raw?.path ?? session.object_key.split("/").at(-1) ?? "Raw MCAP",
    dataPackageId: session.data_package_id,
    totalBytes: session.expected_size,
    uploadedBytes: plan
      ? uploadedBytes(parts, plan, session.expected_size)
      : session.expected_size,
    completedParts: complete,
    totalParts: plan?.partCount ?? 1,
    speedBytesPerSecond: null,
    remainingSeconds: null,
    transferStatus,
    serverStatus: session.status,
    failedParts: failures.map((failure) => failure.partNumber),
    failedPartTransfers: failures,
    failureCode: session.failure_code ?? null,
    failureMessage:
      session.status === "MULTIPART_COMPLETED"
        ? "对象分片已传完；重新提交将继续执行数据清单提交。"
        : session.status === "FAILED"
          ? "服务端已将该上传标记为失败，请根据错误码核对后重试。"
          : session.source_type === "OBJECT_STORAGE_REFERENCE"
            ? "对象引用任务已恢复；请重试提交，平台会继续使用服务端已登记对象。"
            : "浏览器不能在刷新后保留本地文件句柄；重新选择同一原文件即可从服务端分片断点继续。",
    requestId: null,
    createdAt: session.created_at ?? new Date().toISOString(),
  };
}

function putAuthorizedPart(
  authorization: PartAuthorization,
  blob: Blob,
  signal: AbortSignal,
  onProgress: (loaded: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let settled = false;
    const abort = () => {
      if (!settled) xhr.abort();
    };
    const cleanup = () => {
      signal.removeEventListener("abort", abort);
      xhr.upload.onprogress = null;
      xhr.onload = null;
      xhr.onerror = null;
      xhr.onabort = null;
      xhr.ontimeout = null;
    };
    const settle = (completion: () => void) => {
      if (settled) return;
      settled = true;
      cleanup();
      completion();
    };
    const timeoutMs = partTransferTimeoutMs(blob.size, authorization);
    xhr.open("PUT", authorization.url, true);
    xhr.timeout = timeoutMs;
    if (signal.aborted) {
      settle(() =>
        reject(
          new PartTransferError(
            authorization.part_number,
            "TRANSFER_ABORTED",
            `分片 ${authorization.part_number} 传输已中止。`,
          ),
        ),
      );
      return;
    }
    signal.addEventListener("abort", abort, { once: true });
    xhr.upload.onprogress = (event) =>
      onProgress(event.lengthComputable ? event.loaded : 0);
    xhr.onload = () => {
      settle(() => {
        if (xhr.status >= 200 && xhr.status < 300) resolve();
        else
          reject(
            new PartTransferError(
              authorization.part_number,
              `HTTP_${xhr.status}`,
              `分片 ${authorization.part_number} 上传返回 HTTP ${xhr.status}。`,
            ),
          );
      });
    };
    xhr.onerror = () => {
      settle(() =>
        reject(
          new PartTransferError(
            authorization.part_number,
            "NETWORK_ERROR",
            `分片 ${authorization.part_number} 网络传输失败。`,
          ),
        ),
      );
    };
    xhr.onabort = () => {
      settle(() =>
        reject(
          new PartTransferError(
            authorization.part_number,
            "TRANSFER_ABORTED",
            `分片 ${authorization.part_number} 传输已中止。`,
          ),
        ),
      );
    };
    xhr.ontimeout = () => {
      settle(() =>
        reject(
          new PartTransferError(
            authorization.part_number,
            "PART_TIMEOUT",
            `分片 #${authorization.part_number} 在 ${Math.ceil(timeoutMs / 1_000)} 秒内未完成传输，本批其余请求已停止。可“重试失败分片”；服务端已确认分片不会重复上传。`,
          ),
        ),
      );
    };
    try {
      xhr.send(blob);
    } catch {
      settle(() =>
        reject(
          new PartTransferError(
            authorization.part_number,
            "NETWORK_ERROR",
            `分片 ${authorization.part_number} 网络传输失败。`,
          ),
        ),
      );
    }
  });
}

async function uploadAuthorizationBatch(
  itemId: string,
  authorizations: readonly PartAuthorization[],
  runtime: UploadRuntime,
  set: (
    updater: (state: UploadQueueState) => Partial<UploadQueueState>,
  ) => void,
  get: () => UploadQueueState,
) {
  if (!runtime.file || !runtime.plan || !runtime.controller)
    throw new Error("LOCAL_FILE_REQUIRED");
  const activeProgress = new Map<number, number>();
  const startedAt = performance.now();
  const startingBytes =
    get().items.find((item) => item.id === itemId)?.uploadedBytes ?? 0;
  const startingCompletedParts =
    get().items.find((item) => item.id === itemId)?.completedParts ?? 0;
  let confirmedThisRun = 0;
  let confirmedPartsThisRun = 0;
  let lastPaint = 0;

  const paint = () => {
    const now = performance.now();
    if (now - lastPaint < UPLOAD_PROGRESS_PAINT_INTERVAL_MS) return;
    lastPaint = now;
    const inFlight = [...activeProgress.values()].reduce(
      (sum, value) => sum + value,
      0,
    );
    const visible = Math.min(
      runtime.file!.size,
      startingBytes + confirmedThisRun + inFlight,
    );
    const elapsed = Math.max(0.25, (now - startedAt) / 1000);
    const speed = (confirmedThisRun + inFlight) / elapsed;
    updateItem(set, itemId, (item) => ({
      ...item,
      uploadedBytes: visible,
      speedBytesPerSecond: speed > 0 ? speed : null,
      remainingSeconds:
        speed > 0 ? Math.max(0, (item.totalBytes - visible) / speed) : null,
    }));
  };

  let cursor = 0;
  const workers = Array.from(
    { length: partUploadConcurrency(runtime.plan, authorizations.length) },
    async () => {
      while (cursor < authorizations.length) {
        const authorization = authorizations[cursor++];
        if (!authorization) return;
        const bounds = partBounds(
          authorization.part_number,
          runtime.plan!,
          runtime.file!.size,
        );
        activeProgress.set(authorization.part_number, 0);
        await putAuthorizedPart(
          authorization,
          runtime.file!.slice(bounds.start, bounds.end),
          runtime.controller!.signal,
          (loaded) => {
            activeProgress.set(authorization.part_number, loaded);
            paint();
          },
        );
        activeProgress.delete(authorization.part_number);
        confirmedThisRun += bounds.end - bounds.start;
        confirmedPartsThisRun += 1;
        updateItem(set, itemId, (item) => ({
          ...item,
          completedParts: Math.min(item.totalParts, item.completedParts + 1),
        }));
        paint();
      }
    },
  );
  try {
    await Promise.all(workers);
  } catch (error) {
    activeProgress.clear();
    updateItem(set, itemId, (item) => ({
      ...item,
      uploadedBytes: Math.min(
        item.totalBytes,
        startingBytes + confirmedThisRun,
      ),
      completedParts: Math.min(
        item.totalParts,
        startingCompletedParts + confirmedPartsThisRun,
      ),
      speedBytesPerSecond: null,
      remainingSeconds: null,
    }));
    runtime.controller.abort();
    throw error;
  }
}

async function runBrowserTransfer(
  itemId: string,
  initialAuthorizations: readonly PartAuthorization[],
  retryFailures: readonly FailedPartTransfer[],
  set: (
    updater: (state: UploadQueueState) => Partial<UploadQueueState>,
  ) => void,
  get: () => UploadQueueState,
) {
  const runtime = runtimeByItem.get(itemId);
  const initialItem = get().items.find((item) => item.id === itemId);
  if (!runtime || !initialItem?.sessionId || !runtime.file || !runtime.plan)
    return;
  runtime.intent = "run";
  const controller = new AbortController();
  runtime.controller = controller;
  updateItem(set, itemId, (item) => ({
    ...item,
    transferStatus: "uploading",
    failureCode: null,
    failureMessage: null,
    requestId: null,
  }));

  try {
    let serverParts = await listFormalUploadParts(
      runtime.scope,
      initialItem.sessionId,
      controller.signal,
    );
    let completed = new Set(
      serverParts
        .filter((part) => part.status === "UPLOADED")
        .map((part) => part.part_number),
    );
    const initialServerFailures = failedPartTransfers(serverParts);
    updateItem(set, itemId, (item) => ({
      ...item,
      uploadedBytes: uploadedBytes(
        serverParts,
        runtime.plan!,
        runtime.file!.size,
      ),
      completedParts: completed.size,
      failedParts: initialServerFailures.map((failure) => failure.partNumber),
      failedPartTransfers: initialServerFailures,
    }));
    let firstAuthorizations = [...initialAuthorizations];
    let pendingRetries = [...retryFailures].filter(
      (failure) => !completed.has(failure.partNumber),
    );

    while (completed.size < runtime.plan.partCount) {
      if (typeof navigator !== "undefined" && navigator.onLine === false) {
        runtime.intent = "offline";
        throw new PartTransferError(0, "NETWORK_OFFLINE", "网络连接已中断。");
      }
      const missing = runtime.plan.partNumbers.filter(
        (partNumber) => !completed.has(partNumber),
      );
      const batchNumbers = missing.slice(0, AUTHORIZATION_BATCH_SIZE);
      let authorizations: readonly PartAuthorization[];
      if (firstAuthorizations.length > 0) {
        const allowed = new Set(batchNumbers);
        authorizations = firstAuthorizations.filter((entry) =>
          allowed.has(entry.part_number),
        );
        firstAuthorizations = [];
        if (authorizations.length === 0) {
          authorizations = await renewFormalUploadParts(
            runtime.scope,
            initialItem.sessionId,
            batchNumbers,
            controller.signal,
          );
        }
      } else if (pendingRetries.length > 0) {
        const retryBatch = pendingRetries.slice(0, AUTHORIZATION_BATCH_SIZE);
        pendingRetries = pendingRetries.slice(AUTHORIZATION_BATCH_SIZE);
        authorizations = await retryFormalUploadParts(
          runtime.scope,
          initialItem.sessionId,
          {
            failures: retryBatch.map((failure) => ({
              part_number: failure.partNumber,
              failure_code: failure.failureCode,
            })),
          },
        );
      } else {
        authorizations = await renewFormalUploadParts(
          runtime.scope,
          initialItem.sessionId,
          batchNumbers,
          controller.signal,
        );
      }
      if (authorizations.length === 0)
        throw new Error("PART_AUTHORIZATION_EMPTY");
      await uploadAuthorizationBatch(itemId, authorizations, runtime, set, get);
      serverParts = await listFormalUploadParts(
        runtime.scope,
        initialItem.sessionId,
        controller.signal,
      );
      completed = new Set(
        serverParts
          .filter((part) => part.status === "UPLOADED")
          .map((part) => part.part_number),
      );
      const confirmedBytes = uploadedBytes(
        serverParts,
        runtime.plan,
        runtime.file.size,
      );
      const serverFailures = failedPartTransfers(serverParts);
      updateItem(set, itemId, (item) => ({
        ...item,
        uploadedBytes: confirmedBytes,
        completedParts: completed.size,
        failedParts: serverFailures.map((failure) => failure.partNumber),
        failedPartTransfers: serverFailures,
      }));
    }

    const completedParts = serverParts
      .filter(
        (part): part is UploadPart & { etag: string } =>
          part.status === "UPLOADED" && typeof part.etag === "string",
      )
      .sort((left, right) => left.part_number - right.part_number)
      .map((part) => ({ part_number: part.part_number, etag: part.etag }));
    if (completedParts.length !== runtime.plan.partCount)
      throw new Error("PART_RECONCILIATION_INCOMPLETE");
    updateItem(set, itemId, (item) => ({
      ...item,
      transferStatus: "finalizing",
      speedBytesPerSecond: null,
      remainingSeconds: 0,
    }));
    const completedSession = await completeFormalUpload(
      runtime.scope,
      initialItem.sessionId,
      { parts: completedParts },
    );
    await commitFormalManifest(
      runtime.scope,
      initialItem.sessionId,
      runtime.manifest,
    );
    updateItem(set, itemId, (item) => ({
      ...item,
      transferStatus: "committed",
      serverStatus: "RAW_COMMITTED",
      uploadedBytes: item.totalBytes,
      completedParts: item.totalParts,
      failedParts: [],
      failedPartTransfers: [],
      failureCode: null,
      failureMessage: null,
      requestId: null,
    }));
    void completedSession;
  } catch (error) {
    if ((["pause", "cancel"] as readonly string[]).includes(runtime.intent))
      return;
    if (
      runtime.intent === "offline" ||
      (typeof navigator !== "undefined" && navigator.onLine === false)
    ) {
      updateItem(set, itemId, (item) => ({
        ...item,
        transferStatus: "offline",
        speedBytesPerSecond: null,
        remainingSeconds: null,
        failureCode: "NETWORK_OFFLINE",
        failureMessage:
          "网络已断开；传输在浏览器侧停止，联网后将从服务端已确认分片继续。",
      }));
      return;
    }
    const copy = uploadProblemCopy(error);
    const failedTransfer =
      error instanceof PartTransferError && error.partNumber > 0
        ? [
            {
              partNumber: error.partNumber,
              failureCode: error.failureCode,
            },
          ]
        : [];
    updateItem(set, itemId, (item) => ({
      ...item,
      transferStatus: "failed",
      speedBytesPerSecond: null,
      remainingSeconds: null,
      failedParts: mergeFailedPartTransfers(
        item.failedPartTransfers,
        failedTransfer,
      ).map((failure) => failure.partNumber),
      failedPartTransfers: mergeFailedPartTransfers(
        item.failedPartTransfers,
        failedTransfer,
      ),
      failureCode:
        error instanceof PartTransferError
          ? error.failureCode
          : copy.problemCode,
      failureMessage: error instanceof Error ? error.message : copy.detail,
      requestId: copy.requestId,
    }));
  } finally {
    if (runtime.controller === controller) runtime.controller = null;
  }
}

async function commitObjectReference(
  itemId: string,
  set: (
    updater: (state: UploadQueueState) => Partial<UploadQueueState>,
  ) => void,
  get: () => UploadQueueState,
) {
  const runtime = runtimeByItem.get(itemId);
  const item = get().items.find((candidate) => candidate.id === itemId);
  if (!runtime || !item?.sessionId) return;
  updateItem(set, itemId, (current) => ({
    ...current,
    transferStatus: "finalizing",
    failureMessage: null,
  }));
  try {
    await commitFormalManifest(runtime.scope, item.sessionId, runtime.manifest);
    updateItem(set, itemId, (current) => ({
      ...current,
      transferStatus: "committed",
      serverStatus: "RAW_COMMITTED",
      uploadedBytes: current.totalBytes,
      completedParts: 1,
      failureCode: null,
      failureMessage: null,
      requestId: null,
    }));
  } catch (error) {
    const copy = uploadProblemCopy(error);
    updateItem(set, itemId, (current) => ({
      ...current,
      transferStatus: "failed",
      failureCode: copy.problemCode,
      failureMessage: copy.detail,
      requestId: copy.requestId,
    }));
  }
}

async function prepareUploadSession(
  input: StartUploadInput,
  set: (
    updater: (state: UploadQueueState) => Partial<UploadQueueState>,
  ) => void,
  get: () => UploadQueueState,
): Promise<string> {
  const { scope, preflight, sourceType, files, objectStorageUri } = input;
  get().bindScope(scope);
  const rawFile =
    sourceType === "BROWSER_MULTIPART"
      ? findRawPackageFile(files, preflight.manifest)
      : null;
  const rawManifestFile = preflight.files.find(
    (file) => file.role === "RAW_MCAP",
  );
  const plan = rawFile ? planUploadParts(rawFile.size) : null;

  if (sourceType === "BROWSER_MULTIPART" && (!rawFile || !plan)) {
    throw new Error("浏览器数据包中未找到数据清单声明的 RAW_MCAP 原文件。");
  }
  if (rawFile && rawFile.size !== preflight.manifest.file_size) {
    throw new Error(
      `本地原文件为 ${formatBytes(rawFile.size)}，与数据清单声明 ${formatBytes(preflight.manifest.file_size)} 不一致。`,
    );
  }

  const existing = get().items.find(
    (item) =>
      item.scopeKey === scopeKey(scope) &&
      item.sourceType === sourceType &&
      item.dataPackageId === preflight.identifiers.data_package_id &&
      item.sessionId !== null &&
      item.transferStatus !== "cancelled",
  );
  if (existing) {
    runtimeByItem.set(existing.id, {
      scope,
      manifest: preflight.manifest,
      preflight,
      file: rawFile,
      plan,
      controller: null,
      intent: "pause",
      initialAuthorizations: [],
    });
    return existing.id;
  }

  const initialPartNumbers =
    input.deferPartAuthorization === true
      ? []
      : (plan?.partNumbers.slice(0, AUTHORIZATION_BATCH_SIZE) ?? []);
  const grant = await createFormalUploadSession(
    scope,
    {
      manifest: {
        ...preflight.manifest,
        processing_mode: preflight.manifest.processing_mode,
      },
      ...(sourceType === "BROWSER_MULTIPART"
        ? { part_numbers: initialPartNumbers }
        : { object_storage_uri: objectStorageUri?.trim() }),
    },
    input.idempotencyKey ?? newId(),
  );
  if (!grant.session.session_id) throw new Error("UPLOAD_SESSION_ID_MISSING");

  const id = input.queueItemId ?? grant.session.session_id;
  const committed = grant.session.status === "RAW_COMMITTED";
  const item: UploadQueueItem = {
    id,
    scopeKey: scopeKey(scope),
    sessionId: grant.session.session_id,
    sourceType,
    fileName: rawFile?.name ?? rawManifestFile?.path ?? "授权对象",
    dataPackageId: preflight.identifiers.data_package_id,
    totalBytes: preflight.manifest.file_size,
    uploadedBytes: committed ? preflight.manifest.file_size : 0,
    completedParts: committed ? (plan?.partCount ?? 1) : 0,
    totalParts: plan?.partCount ?? 1,
    speedBytesPerSecond: null,
    remainingSeconds: null,
    transferStatus: committed ? "committed" : "waiting",
    serverStatus: grant.session.status,
    failedParts: [],
    failedPartTransfers: [],
    failureCode: null,
    failureMessage: null,
    requestId: null,
    createdAt: grant.session.created_at ?? new Date().toISOString(),
  };
  runtimeByItem.set(id, {
    scope,
    manifest: preflight.manifest,
    preflight,
    file: rawFile,
    plan,
    controller: null,
    intent: "pause",
    initialAuthorizations: grant.parts,
  });
  set((state) => ({
    items: state.items.some((candidate) => candidate.id === id)
      ? state.items.map((candidate) => (candidate.id === id ? item : candidate))
      : [item, ...state.items],
  }));
  return id;
}

async function beginPreparedUpload(
  itemId: string,
  set: (
    updater: (state: UploadQueueState) => Partial<UploadQueueState>,
  ) => void,
  get: () => UploadQueueState,
): Promise<void> {
  const runtime = runtimeByItem.get(itemId);
  const item = get().items.find((candidate) => candidate.id === itemId);
  if (
    !runtime ||
    !item?.sessionId ||
    ["committed", "cancelled"].includes(item.transferStatus)
  )
    return;
  if (item.sourceType === "OBJECT_STORAGE_REFERENCE") {
    await commitObjectReference(itemId, set, get);
    return;
  }
  if (!runtime.file || !runtime.plan) {
    updateItem(set, itemId, (current) => ({
      ...current,
      transferStatus: "needs-file",
      failureMessage: "请重新选择数据清单对应的同一原文件以恢复传输。",
    }));
    return;
  }
  let authorizations = runtime.initialAuthorizations;
  runtime.initialAuthorizations = [];
  if (item.serverStatus === "PAUSED") {
    authorizations = (
      await resumeFormalUpload(runtime.scope, item.sessionId, {
        part_numbers: [],
      })
    ).parts;
  }
  await runBrowserTransfer(itemId, authorizations, [], set, get);
}

function preparationFailureItem(
  input: StartUploadInput,
  error: unknown,
): UploadQueueItem {
  const copy = uploadProblemCopy(error);
  const raw = input.preflight.files.find((file) => file.role === "RAW_MCAP");
  const file = findRawPackageFile(input.files, input.preflight.manifest);
  const plan = file ? planUploadParts(file.size) : null;
  return {
    id: input.queueItemId ?? newId(),
    scopeKey: scopeKey(input.scope),
    sessionId: null,
    sourceType: input.sourceType,
    fileName: file?.name ?? raw?.path ?? "授权对象",
    dataPackageId: input.preflight.identifiers.data_package_id,
    totalBytes: input.preflight.manifest.file_size,
    uploadedBytes: 0,
    completedParts: 0,
    totalParts: plan?.partCount ?? 1,
    speedBytesPerSecond: null,
    remainingSeconds: null,
    transferStatus: "failed",
    serverStatus: null,
    failedParts: [],
    failedPartTransfers: [],
    failureCode: copy.problemCode ?? "UPLOAD_SESSION_CREATE_FAILED",
    failureMessage:
      error instanceof Error && error.message ? error.message : copy.detail,
    requestId: copy.requestId,
    createdAt: new Date().toISOString(),
  };
}

function leRobotQueueItem(input: {
  readonly id: string;
  readonly scope: IngestScope;
  readonly selection: LeRobotFolderSelection;
  readonly binding: LeRobotTargetBinding;
}): UploadQueueItem {
  return {
    id: input.id,
    scopeKey: scopeKey(input.scope),
    sessionId: null,
    sourceType: "LEROBOT_NATIVE",
    fileName: input.selection.rootDirectory,
    dataPackageId: input.binding.datasetId,
    totalBytes: input.selection.sourceBytes,
    uploadedBytes: 0,
    completedParts: 0,
    totalParts: input.selection.sourceFiles.length,
    speedBytesPerSecond: null,
    remainingSeconds: null,
    transferStatus: "waiting",
    serverStatus: null,
    failedParts: [],
    failedPartTransfers: [],
    failureCode: null,
    failureMessage: null,
    requestId: null,
    createdAt: new Date().toISOString(),
    robotId: input.binding.robotId,
    collectionTaskId: input.binding.collectionTaskId,
  };
}

async function runLeRobotUpload(
  itemId: string,
  set: (
    updater: (state: UploadQueueState) => Partial<UploadQueueState>,
  ) => void,
  get: () => UploadQueueState,
): Promise<void> {
  const runtime = leRobotRuntimeByItem.get(itemId);
  const item = get().items.find((candidate) => candidate.id === itemId);
  if (!runtime || !item || item.sourceType !== "LEROBOT_NATIVE") return;

  const controller = new AbortController();
  runtime.controller = controller;
  runtime.intent = "run";
  let acceptingProgress = true;
  let lastProgressPaint = Number.NEGATIVE_INFINITY;
  updateItem(set, itemId, (current) => ({
    ...current,
    transferStatus: "uploading",
    failureCode: null,
    failureMessage: null,
    requestId: null,
  }));

  try {
    await uploadNativeLeRobot(
      runtime.scope,
      runtime.selection,
      runtime.binding,
      (progress: LeRobotUploadProgress) => {
        if (
          !acceptingProgress ||
          runtime.controller !== controller ||
          runtime.intent !== "run" ||
          controller.signal.aborted
        )
          return;
        const now = performance.now();
        if (
          progress.stage === "uploading" &&
          now - lastProgressPaint < UPLOAD_PROGRESS_PAINT_INTERVAL_MS
        )
          return;
        lastProgressPaint = now;
        updateItem(set, itemId, (current) => ({
          ...current,
          transferStatus:
            progress.stage === "committing" ? "finalizing" : "uploading",
          uploadedBytes: progress.uploadedBytes,
          completedParts: progress.completedFiles,
          totalParts: progress.totalFiles,
        }));
      },
      runtime.resume,
      {
        signal: controller.signal,
        onSession: (resume) => {
          runtime.resume = resume;
          updateItem(set, itemId, (current) => ({
            ...current,
            sessionId: resume.importId,
          }));
        },
      },
    );
    acceptingProgress = false;
    updateItem(set, itemId, (current) => ({
      ...current,
      transferStatus: "committed",
      uploadedBytes: current.totalBytes,
      completedParts: current.totalParts,
      speedBytesPerSecond: null,
      remainingSeconds: 0,
    }));
  } catch (error) {
    acceptingProgress = false;
    if (runtime.controller !== controller) return;
    const failure = error instanceof LeRobotUploadFailure ? error : null;
    if (failure) runtime.resume = failure.resume;
    const interruptedIntent = leRobotRuntimeByItem.get(itemId)?.intent ?? "run";
    controller.abort();
    const interruptedStatus =
      interruptedIntent === "pause"
        ? "paused"
        : interruptedIntent === "offline"
          ? "offline"
          : "failed";
    const copy = uploadProblemCopy(failure?.originalError ?? error);
    updateItem(set, itemId, (current) => ({
      ...current,
      sessionId: runtime.resume?.importId ?? current.sessionId,
      transferStatus: interruptedStatus,
      failureCode:
        interruptedStatus === "failed"
          ? (copy.problemCode ?? "LEROBOT_UPLOAD_INTERRUPTED")
          : interruptedStatus === "offline"
            ? "NETWORK_OFFLINE"
            : null,
      failureMessage:
        interruptedStatus === "paused"
          ? null
          : interruptedStatus === "offline"
            ? "网络已断开；已停止发送新分片。联网后会继续上传。"
            : error instanceof Error
              ? error.message
              : copy.detail,
      requestId: interruptedStatus === "failed" ? copy.requestId : null,
      speedBytesPerSecond: null,
      remainingSeconds: null,
    }));
  } finally {
    if (runtime.controller === controller) runtime.controller = null;
  }
}

function pauseLeRobotUpload(
  itemId: string,
  set: (
    updater: (state: UploadQueueState) => Partial<UploadQueueState>,
  ) => void,
): void {
  const runtime = leRobotRuntimeByItem.get(itemId);
  if (!runtime?.controller || runtime.resume === null) return;
  runtime.intent = "pause";
  updateItem(set, itemId, (current) => ({
    ...current,
    transferStatus: "pausing",
    speedBytesPerSecond: null,
    remainingSeconds: null,
  }));
  runtime.controller.abort();
}

export const useUploadQueueStore = create<UploadQueueState>((set, get) => ({
  scopeKey: null,
  recovering: false,
  recoveryProblem: null,
  items: [],
  folderBatch: null,
  bindScope: (scope) => {
    const nextKey = scopeKey(scope);
    if (get().scopeKey === nextKey) return;
    for (const runtime of runtimeByItem.values()) {
      runtime.intent = "pause";
      runtime.controller?.abort();
    }
    runtimeByItem.clear();
    for (const runtime of leRobotRuntimeByItem.values()) {
      runtime.intent = "pause";
      runtime.controller?.abort();
    }
    leRobotRuntimeByItem.clear();
    folderBatchRuntimeById.clear();
    set({
      scopeKey: nextKey,
      items: [],
      recovering: false,
      recoveryProblem: null,
      folderBatch: null,
    });
  },
  recover: async (scope) => {
    get().bindScope(scope);
    if (get().recovering) return;
    set({ recovering: true, recoveryProblem: null });
    const itemScopeKey = scopeKey(scope);
    try {
      const recovered: UploadQueueItem[] = [];
      const seenCursors = new Set<string>();
      let cursor: string | undefined;
      do {
        const response = await listFormalUploadSessions(scope, {
          limit: 100,
          cursor,
        });
        const sessions = response.items.filter(
          (session) =>
            activeServerStates.has(session.status) && session.session_id,
        );
        for (const session of sessions) {
          if (get().scopeKey !== itemScopeKey) return;
          const [preflight, parts] = await Promise.all([
            getFormalUploadManifest(scope, session.session_id!),
            listFormalUploadParts(scope, session.session_id!),
          ]);
          const item = itemFromRecovered(
            session,
            preflight,
            parts,
            itemScopeKey,
          );
          runtimeByItem.set(item.id, {
            scope,
            manifest: preflight.manifest,
            preflight,
            file: null,
            plan:
              session.source_type === "BROWSER_MULTIPART"
                ? planUploadParts(session.expected_size)
                : null,
            controller: null,
            intent: "pause",
            initialAuthorizations: [],
          });
          recovered.push(item);
        }
        const nextCursor = response.next_cursor ?? undefined;
        if (nextCursor && seenCursors.has(nextCursor))
          throw new Error("UPLOAD_SESSION_CURSOR_LOOP");
        if (nextCursor) seenCursors.add(nextCursor);
        cursor = nextCursor;
      } while (cursor);
      set((state) => {
        if (state.scopeKey !== itemScopeKey) return { recovering: false };
        const currentIds = new Set(
          state.items.map((item) => item.sessionId).filter(Boolean),
        );
        return {
          items: [
            ...state.items,
            ...recovered.filter((item) => !currentIds.has(item.sessionId)),
          ],
          recovering: false,
          recoveryProblem: null,
        };
      });
    } catch (error) {
      set({ recovering: false, recoveryProblem: uploadProblemCopy(error) });
    }
  },
  prepare: (input) => prepareUploadSession(input, set, get),
  beginPrepared: (itemId) => beginPreparedUpload(itemId, set, get),
  start: async (input) => {
    try {
      const itemId = await prepareUploadSession(input, set, get);
      await beginPreparedUpload(itemId, set, get);
    } catch (error) {
      const item = preparationFailureItem(input, error);
      set((state) => ({
        items: state.items.some((candidate) => candidate.id === item.id)
          ? state.items.map((candidate) =>
              candidate.id === item.id ? item : candidate,
            )
          : [item, ...state.items],
      }));
    }
  },
  startLeRobot: async ({ scope, selection, binding }) => {
    get().bindScope(scope);
    const duplicate = get().items.find(
      (item) =>
        item.sourceType === "LEROBOT_NATIVE" &&
        item.robotId === binding.robotId &&
        item.collectionTaskId === binding.collectionTaskId &&
        item.dataPackageId === binding.datasetId &&
        !["committed", "cancelled"].includes(item.transferStatus),
    );
    if (duplicate) {
      if (["paused", "offline", "failed"].includes(duplicate.transferStatus))
        await get().resume(duplicate.id);
      return;
    }
    const id = newId();
    const item = leRobotQueueItem({ id, scope, selection, binding });
    leRobotRuntimeByItem.set(id, {
      scope,
      selection,
      binding,
      controller: null,
      intent: "run",
      resume: null,
    });
    set((state) => ({ items: [item, ...state.items] }));
    await runLeRobotUpload(id, set, get);
  },
  startFolderBatch: async ({ scope, bundles }) => {
    get().bindScope(scope);
    if (bundles.length === 0) return;
    const current = get().folderBatch;
    if (current?.status === "running" || current?.status === "offline") return;
    const batchId = newId();
    folderBatchRuntimeById.set(batchId, {
      batchId,
      scope,
      bundles,
      nextIndex: 0,
      pausedItemId: null,
      running: false,
    });
    set({
      folderBatch: {
        batchId,
        total: bundles.length,
        completed: 0,
        failed: 0,
        activeDataPackageId: null,
        status: "running",
      },
    });
    await continueFolderBatch(batchId, set, get);
  },
  pause: async (itemId) => {
    const item = get().items.find((candidate) => candidate.id === itemId);
    if (item?.sourceType === "LEROBOT_NATIVE") {
      pauseLeRobotUpload(itemId, set);
      return;
    }
    const runtime = runtimeByItem.get(itemId);
    if (!runtime || !item?.sessionId) return;
    runtime.intent = "pause";
    runtime.controller?.abort();
    updateItem(set, itemId, (current) => ({
      ...current,
      transferStatus: "pausing",
      speedBytesPerSecond: null,
      remainingSeconds: null,
    }));
    try {
      const session = await pauseFormalUpload(runtime.scope, item.sessionId);
      updateItem(set, itemId, (current) => ({
        ...current,
        transferStatus: "paused",
        serverStatus: session.status,
      }));
    } catch (error) {
      const copy = uploadProblemCopy(error);
      updateItem(set, itemId, (current) => ({
        ...current,
        transferStatus: "failed",
        failureCode: copy.problemCode,
        failureMessage: copy.detail,
        requestId: copy.requestId,
      }));
    }
  },
  resume: async (itemId) => {
    const item = get().items.find((candidate) => candidate.id === itemId);
    if (item?.sourceType === "LEROBOT_NATIVE") {
      await runLeRobotUpload(itemId, set, get);
      return;
    }
    const runtime = runtimeByItem.get(itemId);
    if (!runtime || !item?.sessionId) return;
    if (item.sourceType === "OBJECT_STORAGE_REFERENCE") {
      await commitObjectReference(itemId, set, get);
      return;
    }
    if (!runtime.file) {
      updateItem(set, itemId, (current) => ({
        ...current,
        transferStatus: "needs-file",
        failureMessage: "请重新选择数据清单对应的同一原文件以恢复传输。",
      }));
      return;
    }
    try {
      const session = await getFormalUploadSession(
        runtime.scope,
        item.sessionId,
      );
      let initial: readonly PartAuthorization[] = [];
      if (session.status === "PAUSED")
        initial = (
          await resumeFormalUpload(runtime.scope, item.sessionId, {
            part_numbers: [],
          })
        ).parts;
      else if (session.status === "MULTIPART_COMPLETED") {
        await commitObjectReference(itemId, set, get);
        return;
      }
      await runBrowserTransfer(itemId, initial, [], set, get);
    } catch (error) {
      const copy = uploadProblemCopy(error);
      updateItem(set, itemId, (current) => ({
        ...current,
        transferStatus: "failed",
        failureCode: copy.problemCode,
        failureMessage: copy.detail,
        requestId: copy.requestId,
      }));
    }
  },
  retryFailedParts: async (itemId) => {
    const item = get().items.find((candidate) => candidate.id === itemId);
    if (item?.sourceType === "LEROBOT_NATIVE") {
      await runLeRobotUpload(itemId, set, get);
      return;
    }
    const runtime = runtimeByItem.get(itemId);
    if (!item || !runtime) return;
    if (!runtime.file) {
      updateItem(set, itemId, (current) => ({
        ...current,
        transferStatus: "needs-file",
        failureMessage: "请先重新选择原文件，再重试失败分片。",
      }));
      return;
    }
    try {
      const session = item.sessionId
        ? await getFormalUploadSession(runtime.scope, item.sessionId)
        : null;
      if (session?.status === "PAUSED" && item.sessionId)
        await resumeFormalUpload(runtime.scope, item.sessionId, {
          part_numbers: [],
        });
      const retryFailures =
        item.failedPartTransfers.length > 0
          ? item.failedPartTransfers
          : item.failedParts.map((partNumber) => ({
              partNumber,
              failureCode: item.failureCode ?? "NETWORK_ERROR",
            }));
      await runBrowserTransfer(itemId, [], retryFailures, set, get);
    } catch (error) {
      const copy = uploadProblemCopy(error);
      updateItem(set, itemId, (current) => ({
        ...current,
        transferStatus: "failed",
        failureCode: copy.problemCode,
        failureMessage: copy.detail,
        requestId: copy.requestId,
      }));
    }
  },
  reattachAndResume: async (itemId, file) => {
    const runtime = runtimeByItem.get(itemId);
    const item = get().items.find((candidate) => candidate.id === itemId);
    if (!runtime || !item) return;
    if (file.size !== item.totalBytes) {
      updateItem(set, itemId, (current) => ({
        ...current,
        transferStatus: "needs-file",
        failureCode: "LOCAL_FILE_SIZE_MISMATCH",
        failureMessage: `所选文件大小 ${formatBytes(file.size)} 与原上传 ${formatBytes(item.totalBytes)} 不一致。`,
      }));
      return;
    }
    runtime.file = file;
    runtime.plan = planUploadParts(file.size);
    if (item.failedParts.length > 0) await get().retryFailedParts(itemId);
    else await get().resume(itemId);
  },
  cancel: async (itemId) => {
    const item = get().items.find((candidate) => candidate.id === itemId);
    if (item?.sourceType === "LEROBOT_NATIVE") return;
    const runtime = runtimeByItem.get(itemId);
    if (!runtime || !item?.sessionId) return;
    runtime.intent = "cancel";
    runtime.controller?.abort();
    try {
      const session = await cancelFormalUpload(runtime.scope, item.sessionId);
      updateItem(set, itemId, (current) => ({
        ...current,
        transferStatus: "cancelled",
        serverStatus: session.status,
        speedBytesPerSecond: null,
        remainingSeconds: null,
      }));
    } catch (error) {
      const copy = uploadProblemCopy(error);
      updateItem(set, itemId, (current) => ({
        ...current,
        transferStatus: "failed",
        failureCode: copy.problemCode,
        failureMessage: copy.detail,
        requestId: copy.requestId,
      }));
    }
  },
  clearSettled: () => {
    const settledIds = get()
      .items.filter((item) =>
        ["committed", "cancelled"].includes(item.transferStatus),
      )
      .map((item) => item.id);
    settledIds.forEach((itemId) => {
      runtimeByItem.delete(itemId);
      leRobotRuntimeByItem.delete(itemId);
    });
    set((state) => ({
      items: state.items.filter((item) => !settledIds.includes(item.id)),
    }));
  },
  handleOffline: () => {
    for (const item of get().items) {
      if (item.transferStatus !== "uploading") continue;
      if (item.sourceType === "LEROBOT_NATIVE") {
        const runtime = leRobotRuntimeByItem.get(item.id);
        if (!runtime) continue;
        runtime.intent = "offline";
        runtime.controller?.abort();
        updateItem(set, item.id, (current) => ({
          ...current,
          transferStatus: "offline",
          speedBytesPerSecond: null,
          remainingSeconds: null,
          failureCode: "NETWORK_OFFLINE",
          failureMessage: "网络已断开；已停止发送新分片。联网后会继续上传。",
        }));
        continue;
      }
      const runtime = runtimeByItem.get(item.id);
      if (!runtime) continue;
      runtime.intent = "offline";
      runtime.controller?.abort();
      updateItem(set, item.id, (current) => ({
        ...current,
        transferStatus: "offline",
        speedBytesPerSecond: null,
        remainingSeconds: null,
        failureCode: "NETWORK_OFFLINE",
        failureMessage:
          "网络已断开；已停止发送新分片。联网后将按服务端已确认分片继续。",
      }));
    }
  },
  handleOnline: () => {
    const batchPausedItems = new Set(
      [...folderBatchRuntimeById.values()]
        .map((runtime) => runtime.pausedItemId)
        .filter((itemId): itemId is string => itemId !== null),
    );
    for (const item of get().items) {
      if (item.transferStatus === "offline" && !batchPausedItems.has(item.id))
        void get().resume(item.id);
    }
    for (const runtime of folderBatchRuntimeById.values()) {
      const batch = get().folderBatch;
      if (batch?.batchId !== runtime.batchId || batch.status !== "offline")
        continue;
      void resumeFolderBatch(runtime, set, get);
    }
  },
}));

if (typeof window !== "undefined") {
  window.addEventListener("offline", () =>
    useUploadQueueStore.getState().handleOffline(),
  );
  window.addEventListener("online", () =>
    useUploadQueueStore.getState().handleOnline(),
  );
}

export function resetUploadQueueStoreForTests() {
  for (const runtime of runtimeByItem.values()) runtime.controller?.abort();
  for (const runtime of leRobotRuntimeByItem.values())
    runtime.controller?.abort();
  runtimeByItem.clear();
  leRobotRuntimeByItem.clear();
  folderBatchRuntimeById.clear();
  useUploadQueueStore.setState({
    scopeKey: null,
    recovering: false,
    recoveryProblem: null,
    items: [],
    folderBatch: null,
  });
}

export function seedUploadRuntimeForTests(
  itemId: string,
  value: {
    readonly scope: IngestScope;
    readonly manifest: UploadManifest;
    readonly preflight: ManifestPreflight;
    readonly file: File;
  },
) {
  runtimeByItem.set(itemId, {
    scope: value.scope,
    manifest: value.manifest,
    preflight: value.preflight,
    file: value.file,
    plan: planUploadParts(value.file.size),
    controller: null,
    intent: "pause",
    initialAuthorizations: [],
  });
}
