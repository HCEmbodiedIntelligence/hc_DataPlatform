import type { IngestScope } from "../../entities/data-source";
import { request } from "../../shared/api/http-client";
import {
  AUTHORIZATION_BATCH_SIZE,
  MAX_MULTIPART_PARTS,
  type LeRobotFolderSelection,
} from "./upload-contract";

export const LEROBOT_MULTIPART_BYTES = 32 * 1024 ** 2;
const MAX_PARALLEL_PARTS = 4;
const PART_TIMEOUT_MS = 12 * 60 * 1_000;

export interface LeRobotTargetBinding {
  readonly datasetId: string;
  readonly collectionTaskId: string;
  readonly robotId: string;
}

interface LeRobotSourceDeclaration {
  readonly path: string;
  readonly size: number;
  readonly part_count: number;
}

export interface LeRobotImportManifest {
  readonly schema_version: "lerobot-web-import/v1";
  readonly dataset_id: string;
  readonly collection_task_id: string;
  readonly robot_id: string;
  readonly info: Readonly<Record<string, unknown>>;
  readonly files: readonly LeRobotSourceDeclaration[];
}

interface PartAuthorization {
  readonly part_number: number;
  readonly url: string;
  readonly expires_at: string;
}

interface AssetGrant {
  readonly path: string;
  readonly multipart_upload_id: string | null;
  readonly parts: readonly PartAuthorization[];
  readonly completed: boolean;
}

interface ImportGrant {
  readonly schema_version: "lerobot-web-import-grant/v1";
  readonly import_id: string;
  readonly assets: readonly AssetGrant[];
}

export interface LeRobotImportAccepted {
  readonly schema_version: "lerobot-web-import-accepted/v1";
  readonly import_id: string;
  readonly status: "EPISODES_QUEUED";
  readonly episode_count: number;
  readonly source_file_count: number;
  readonly episode_task_count: number;
  readonly episode_plan_key: string;
}

export interface LeRobotUploadProgress {
  readonly stage: "uploading" | "committing";
  readonly transferMode: "direct" | "proxy";
  readonly currentPath: string | null;
  readonly completedFiles: number;
  readonly totalFiles: number;
  readonly uploadedBytes: number;
  readonly totalBytes: number;
}

export interface LeRobotUploadResume {
  readonly importId: string;
  readonly assets: readonly AssetGrant[];
  readonly transferMode: "direct" | "proxy";
}

export class LeRobotUploadFailure extends Error {
  readonly resume: LeRobotUploadResume;
  readonly originalError: unknown;

  constructor(
    message: string,
    resume: LeRobotUploadResume,
    originalError: unknown,
  ) {
    super(message, { cause: originalError });
    this.name = "LeRobotUploadFailure";
    this.resume = resume;
    this.originalError = originalError;
  }
}

function resourceRoot(scope: IngestScope): string {
  return `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/lerobot-imports`;
}

function partPlan(size: number): { partSize: number; partCount: number } {
  const partSize = Math.max(
    LEROBOT_MULTIPART_BYTES,
    Math.ceil(size / MAX_MULTIPART_PARTS),
  );
  return { partSize, partCount: Math.ceil(size / partSize) };
}

export function buildLeRobotImportManifest(
  selection: LeRobotFolderSelection,
  binding: LeRobotTargetBinding,
): LeRobotImportManifest {
  return {
    schema_version: "lerobot-web-import/v1",
    dataset_id: binding.datasetId,
    collection_task_id: binding.collectionTaskId,
    robot_id: binding.robotId,
    info: selection.info,
    files: selection.sourceFiles.map(({ file, path }) => ({
      path,
      size: file.size,
      part_count: partPlan(file.size).partCount,
    })),
  };
}

function putPart(
  authorization: PartAuthorization,
  blob: Blob,
  onProgress: (loaded: number) => void,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let settled = false;
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener("abort", abort);
      callback();
    };
    const abort = () => xhr.abort();
    if (signal?.aborted) {
      reject(new Error("LeRobot 分片传输已暂停。"));
      return;
    }
    signal?.addEventListener("abort", abort, { once: true });
    xhr.open("PUT", authorization.url, true);
    xhr.timeout = PART_TIMEOUT_MS;
    xhr.upload.onprogress = (event) =>
      onProgress(event.lengthComputable ? event.loaded : 0);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) finish(resolve);
      else
        finish(() =>
          reject(new Error(`LeRobot 分片上传返回 HTTP ${xhr.status}。`)),
        );
    };
    xhr.onerror = () =>
      finish(() => reject(new Error("LeRobot 分片网络传输失败。")));
    xhr.onabort = () =>
      finish(() => reject(new Error("LeRobot 分片传输已暂停。")));
    xhr.ontimeout = () =>
      finish(() => reject(new Error("LeRobot 分片传输超时。")));
    xhr.send(blob);
  });
}

async function putPartThroughPlatform(
  scope: IngestScope,
  root: string,
  importId: string,
  binding: LeRobotTargetBinding,
  sourcePath: string,
  multipartUploadId: string,
  partNumber: number,
  blob: Blob,
  signal?: AbortSignal,
): Promise<void> {
  await request({
    method: "PUT",
    path: `${root}/${encodeURIComponent(importId)}/assets:upload-part`,
    scope,
    query: {
      datasetId: binding.datasetId,
      path: sourcePath,
      multipartUploadId,
      partNumber,
    },
    binaryBody: blob,
    cache: "no-store",
    signal,
  });
}

async function inParallel<T>(
  values: readonly T[],
  limit: number,
  worker: (value: T) => Promise<void>,
): Promise<void> {
  let cursor = 0;
  await Promise.all(
    Array.from({ length: Math.min(limit, values.length) }, async () => {
      while (cursor < values.length) {
        const index = cursor++;
        const value = values[index];
        if (value !== undefined) await worker(value);
      }
    }),
  );
}

export async function uploadNativeLeRobot(
  scope: IngestScope,
  selection: LeRobotFolderSelection,
  binding: LeRobotTargetBinding,
  onProgress: (progress: LeRobotUploadProgress) => void,
  resume: LeRobotUploadResume | null = null,
  options: {
    readonly signal?: AbortSignal;
    readonly onSession?: (resume: LeRobotUploadResume) => void;
  } = {},
): Promise<LeRobotImportAccepted> {
  const manifest = buildLeRobotImportManifest(selection, binding);
  const root = resourceRoot(scope);
  const grant = resume
    ? {
        schema_version: "lerobot-web-import-grant/v1" as const,
        import_id: resume.importId,
        assets: resume.assets,
      }
    : await request<ImportGrant>({
        method: "POST",
        path: root,
        scope,
        body: manifest,
        cache: "no-store",
        signal: options.signal,
      });
  let transferMode = resume?.transferMode ?? "direct";
  const uploadResume = (): LeRobotUploadResume => ({
    importId: grant.import_id,
    assets: grant.assets,
    transferMode,
  });
  options.onSession?.(uploadResume());
  const grants = new Map(grant.assets.map((asset) => [asset.path, asset]));
  let completedBytes = 0;
  let completedFiles = 0;

  // Keep source paths stable and put meta/info.json last. The Raw source graph is
  // committed only after every source object completes, so it is never observed half-built.
  const orderedFiles = [...selection.sourceFiles].sort((left, right) => {
    if (left.path === "meta/info.json") return 1;
    if (right.path === "meta/info.json") return -1;
    return left.path.localeCompare(right.path);
  });
  let currentPath: string | null = null;
  const progress = (
    stage: LeRobotUploadProgress["stage"],
    uploadedBytes: number,
  ) =>
    onProgress({
      stage,
      transferMode,
      currentPath,
      completedFiles,
      totalFiles: orderedFiles.length,
      uploadedBytes,
      totalBytes: selection.sourceBytes,
    });

  try {
    for (const source of orderedFiles) {
      currentPath = source.path;
      const asset = grants.get(source.path);
      if (!asset?.multipart_upload_id) {
        throw new Error(`服务端没有为原始文件 ${source.path} 创建上传授权。`);
      }
      const multipartUploadId = asset.multipart_upload_id;
      const plan = partPlan(source.file.size);
      // Renew URLs just in time. On retry this call also tells us whether the
      // immutable object already completed before the previous response failed.
      const authorizations = new Map<number, PartAuthorization>();
      let alreadyCompleted = false;
      for (
        let start = 1;
        start <= plan.partCount;
        start += AUTHORIZATION_BATCH_SIZE
      ) {
        const partNumbers = Array.from(
          {
            length: Math.min(
              AUTHORIZATION_BATCH_SIZE,
              plan.partCount - start + 1,
            ),
          },
          (_, index) => start + index,
        );
        const renewed = await request<{
          readonly path: string;
          readonly parts: readonly PartAuthorization[];
          readonly completed: boolean;
        }>({
          method: "POST",
          path: `${root}/${encodeURIComponent(grant.import_id)}/assets:authorize-parts`,
          scope,
          body: {
            dataset_id: binding.datasetId,
            path: source.path,
            multipart_upload_id: multipartUploadId,
            part_numbers: partNumbers,
          },
          cache: "no-store",
          signal: options.signal,
        });
        if (renewed.completed) {
          alreadyCompleted = true;
          break;
        }
        renewed.parts.forEach((part) =>
          authorizations.set(part.part_number, part),
        );
      }
      if (alreadyCompleted) {
        completedBytes += source.file.size;
        completedFiles += 1;
        progress("uploading", completedBytes);
        continue;
      }

      const loadedByPart = new Map<number, number>();
      progress("uploading", completedBytes);
      await inParallel(
        Array.from({ length: plan.partCount }, (_, index) => index + 1),
        MAX_PARALLEL_PARTS,
        async (partNumber) => {
          const authorization = authorizations.get(partNumber);
          if (!authorization)
            throw new Error(
              `原始文件 ${source.path} 缺少分片 ${partNumber} 授权。`,
            );
          const start = (partNumber - 1) * plan.partSize;
          const blob = source.file.slice(
            start,
            Math.min(source.file.size, start + plan.partSize),
          );
          const reportPartProgress = (loaded: number) => {
            loadedByPart.set(partNumber, loaded);
            progress(
              "uploading",
              completedBytes +
                [...loadedByPart.values()].reduce(
                  (total, value) => total + value,
                  0,
                ),
            );
          };
          if (transferMode === "direct") {
            try {
              await putPart(
                authorization,
                blob,
                reportPartProgress,
                options.signal,
              );
              reportPartProgress(blob.size);
              return;
            } catch {
              if (options.signal?.aborted) {
                throw new Error("LeRobot 分片传输已暂停。");
              }
              transferMode = "proxy";
              options.onSession?.(uploadResume());
              loadedByPart.set(partNumber, 0);
              progress(
                "uploading",
                completedBytes +
                  [...loadedByPart.values()].reduce(
                    (total, value) => total + value,
                    0,
                  ),
              );
            }
          }
          await putPartThroughPlatform(
            scope,
            root,
            grant.import_id,
            binding,
            source.path,
            multipartUploadId,
            partNumber,
            blob,
            options.signal,
          );
          reportPartProgress(blob.size);
        },
      );
      await request({
        method: "POST",
        path: `${root}/${encodeURIComponent(grant.import_id)}/assets:complete`,
        scope,
        body: {
          dataset_id: binding.datasetId,
          path: source.path,
          multipart_upload_id: multipartUploadId,
          size: source.file.size,
          part_count: plan.partCount,
        },
        signal: options.signal,
      });
      completedBytes += source.file.size;
      completedFiles += 1;
    }
    currentPath = null;
    progress("committing", completedBytes);
    return await request<LeRobotImportAccepted>({
      method: "POST",
      path: `${root}/${encodeURIComponent(grant.import_id)}:commit`,
      scope,
      body: { manifest },
      signal: options.signal,
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "未知上传错误";
    throw new LeRobotUploadFailure(
      `${currentPath ? `文件 ${currentPath}` : "LeRobot 数据"}上传中断：${detail}`,
      uploadResume(),
      error,
    );
  }
}
