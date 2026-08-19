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
  findRawPackageFile,
  formatBytes,
  partBounds,
  planUploadParts,
  uploadProblemCopy,
  type UploadPartPlan,
} from "./upload-contract";

export type QueueTransferStatus =
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

export interface FailedPartTransfer {
  readonly partNumber: number;
  readonly failureCode: string;
}

export interface UploadQueueItem {
  readonly id: string;
  readonly scopeKey: string;
  readonly sessionId: string | null;
  readonly sourceType: "BROWSER_MULTIPART" | "OBJECT_STORAGE_REFERENCE";
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
}

interface UploadRuntime {
  scope: IngestScope;
  manifest: UploadManifest;
  preflight: ManifestPreflight;
  file: File | null;
  plan: UploadPartPlan | null;
  controller: AbortController | null;
  intent: "run" | "pause" | "cancel" | "offline";
}

interface StartUploadInput {
  readonly scope: IngestScope;
  readonly preflight: ManifestPreflight;
  readonly sourceType: "BROWSER_MULTIPART" | "OBJECT_STORAGE_REFERENCE";
  readonly files: readonly File[];
  readonly objectStorageUri?: string;
}

interface UploadQueueState {
  readonly scopeKey: string | null;
  readonly recovering: boolean;
  readonly items: readonly UploadQueueItem[];
  bindScope: (scope: IngestScope) => void;
  recover: (scope: IngestScope) => Promise<void>;
  start: (input: StartUploadInput) => Promise<void>;
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
const activeServerStates = new Set<FormalUploadSession["status"]>([
  "REGISTERED",
  "UPLOADING",
  "PAUSED",
  "MULTIPART_COMPLETED",
  "FAILED",
]);

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
        ? "对象分片已传完；重新提交将继续执行 Manifest 提交。"
        : session.status === "FAILED"
          ? "服务端已将该上传标记为失败，请根据错误码核对后重试。"
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
    xhr.open("PUT", authorization.url, true);
    xhr.timeout = PART_TRANSFER_TIMEOUT_MS;
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
            `分片 #${authorization.part_number} 在 120 秒内未完成传输，本批其余请求已停止。可“重试失败分片”；服务端已确认分片不会重复上传。`,
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
    if (now - lastPaint < 90) return;
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
    { length: Math.min(3, authorizations.length) },
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

export const useUploadQueueStore = create<UploadQueueState>((set, get) => ({
  scopeKey: null,
  recovering: false,
  items: [],
  bindScope: (scope) => {
    const nextKey = scopeKey(scope);
    if (get().scopeKey === nextKey) return;
    for (const runtime of runtimeByItem.values()) {
      runtime.intent = "pause";
      runtime.controller?.abort();
    }
    runtimeByItem.clear();
    set({ scopeKey: nextKey, items: [], recovering: false });
  },
  recover: async (scope) => {
    get().bindScope(scope);
    if (get().recovering) return;
    set({ recovering: true });
    const itemScopeKey = scopeKey(scope);
    try {
      const response = await listFormalUploadSessions(scope, { limit: 100 });
      const sessions = response.items.filter(
        (session) =>
          activeServerStates.has(session.status) && session.session_id,
      );
      const recovered = await Promise.all(
        sessions.map(async (session) => {
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
          });
          return item;
        }),
      );
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
        };
      });
    } catch {
      set({ recovering: false });
    }
  },
  start: async ({ scope, preflight, sourceType, files, objectStorageUri }) => {
    get().bindScope(scope);
    const id = newId();
    const rawFile =
      sourceType === "BROWSER_MULTIPART"
        ? findRawPackageFile(files, preflight.manifest)
        : null;
    const rawManifestFile = preflight.files.find(
      (file) => file.role === "RAW_MCAP",
    );
    const plan = rawFile ? planUploadParts(rawFile.size) : null;
    const item: UploadQueueItem = {
      id,
      scopeKey: scopeKey(scope),
      sessionId: null,
      sourceType,
      fileName: rawFile?.name ?? rawManifestFile?.path ?? "授权对象",
      dataPackageId: preflight.identifiers.data_package_id,
      totalBytes: preflight.manifest.file_size,
      uploadedBytes: 0,
      completedParts: 0,
      totalParts: plan?.partCount ?? 1,
      speedBytesPerSecond: null,
      remainingSeconds: null,
      transferStatus: "preparing",
      serverStatus: null,
      failedParts: [],
      failedPartTransfers: [],
      failureCode: null,
      failureMessage: null,
      requestId: null,
      createdAt: new Date().toISOString(),
    };
    set((state) => ({ items: [item, ...state.items] }));
    runtimeByItem.set(id, {
      scope,
      manifest: preflight.manifest,
      preflight,
      file: rawFile,
      plan,
      controller: null,
      intent: "run",
    });
    if (sourceType === "BROWSER_MULTIPART" && (!rawFile || !plan)) {
      updateItem(set, id, (current) => ({
        ...current,
        transferStatus: "failed",
        failureCode: "RAW_FILE_REQUIRED",
        failureMessage:
          "浏览器数据包中未找到 Manifest 声明的 RAW_MCAP 原文件。",
      }));
      return;
    }
    if (rawFile && rawFile.size !== preflight.manifest.file_size) {
      updateItem(set, id, (current) => ({
        ...current,
        transferStatus: "failed",
        failureCode: "LOCAL_FILE_SIZE_MISMATCH",
        failureMessage: `本地原文件为 ${formatBytes(rawFile.size)}，与 Manifest 声明 ${formatBytes(preflight.manifest.file_size)} 不一致。`,
      }));
      return;
    }
    try {
      const initialPartNumbers =
        plan?.partNumbers.slice(0, AUTHORIZATION_BATCH_SIZE) ?? [];
      const grant = await createFormalUploadSession(
        scope,
        {
          manifest: preflight.manifest,
          ...(sourceType === "BROWSER_MULTIPART"
            ? { part_numbers: initialPartNumbers }
            : { object_storage_uri: objectStorageUri?.trim() }),
        },
        newId(),
      );
      updateItem(set, id, (current) => ({
        ...current,
        sessionId: grant.session.session_id ?? null,
        serverStatus: grant.session.status,
      }));
      if (!grant.session.session_id)
        throw new Error("UPLOAD_SESSION_ID_MISSING");
      if (grant.session.status === "RAW_COMMITTED") {
        updateItem(set, id, (current) => ({
          ...current,
          transferStatus: "committed",
          uploadedBytes: current.totalBytes,
          completedParts: current.totalParts,
        }));
      } else if (sourceType === "OBJECT_STORAGE_REFERENCE")
        await commitObjectReference(id, set, get);
      else await runBrowserTransfer(id, grant.parts, [], set, get);
    } catch (error) {
      const copy = uploadProblemCopy(error);
      updateItem(set, id, (current) => ({
        ...current,
        transferStatus: "failed",
        failureCode: copy.problemCode,
        failureMessage: copy.detail,
        requestId: copy.requestId,
      }));
    }
  },
  pause: async (itemId) => {
    const runtime = runtimeByItem.get(itemId);
    const item = get().items.find((candidate) => candidate.id === itemId);
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
    const runtime = runtimeByItem.get(itemId);
    const item = get().items.find((candidate) => candidate.id === itemId);
    if (!runtime || !item?.sessionId) return;
    if (item.sourceType === "OBJECT_STORAGE_REFERENCE") {
      await commitObjectReference(itemId, set, get);
      return;
    }
    if (!runtime.file) {
      updateItem(set, itemId, (current) => ({
        ...current,
        transferStatus: "needs-file",
        failureMessage: "请重新选择 Manifest 对应的同一原文件以恢复传输。",
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
    const runtime = runtimeByItem.get(itemId);
    const item = get().items.find((candidate) => candidate.id === itemId);
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
  clearSettled: () =>
    set((state) => ({
      items: state.items.filter(
        (item) => !["committed", "cancelled"].includes(item.transferStatus),
      ),
    })),
  handleOffline: () => {
    for (const item of get().items) {
      if (item.transferStatus !== "uploading") continue;
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
    for (const item of get().items) {
      if (item.transferStatus === "offline") void get().resume(item.id);
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
  runtimeByItem.clear();
  useUploadQueueStore.setState({
    scopeKey: null,
    recovering: false,
    items: [],
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
  });
}
