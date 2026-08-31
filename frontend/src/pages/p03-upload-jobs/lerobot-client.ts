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
  readonly currentPath: string | null;
  readonly completedFiles: number;
  readonly totalFiles: number;
  readonly uploadedBytes: number;
  readonly totalBytes: number;
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
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", authorization.url, true);
    xhr.timeout = PART_TIMEOUT_MS;
    xhr.upload.onprogress = (event) =>
      onProgress(event.lengthComputable ? event.loaded : 0);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new Error(`LeRobot 分片上传返回 HTTP ${xhr.status}。`));
    };
    xhr.onerror = () => reject(new Error("LeRobot 分片网络传输失败。"));
    xhr.onabort = () => reject(new Error("LeRobot 分片传输已中止。"));
    xhr.ontimeout = () => reject(new Error("LeRobot 分片传输超时。"));
    xhr.send(blob);
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
): Promise<LeRobotImportAccepted> {
  const manifest = buildLeRobotImportManifest(selection, binding);
  const root = resourceRoot(scope);
  const grant = await request<ImportGrant>({
    method: "POST",
    path: root,
    scope,
    body: manifest,
    cache: "no-store",
  });
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
  for (const source of orderedFiles) {
    const asset = grants.get(source.path);
    if (!asset?.multipart_upload_id) {
      throw new Error(`服务端没有为原始文件 ${source.path} 创建上传授权。`);
    }
    const plan = partPlan(source.file.size);
    // Begin grants are only an optimization. Renew the current file's URLs just
    // in time so a large earlier file cannot make later-file grants expire.
    const authorizations = new Map<number, PartAuthorization>();
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
      }>({
        method: "POST",
        path: `${root}/${encodeURIComponent(grant.import_id)}/assets:authorize-parts`,
        scope,
        body: {
          dataset_id: binding.datasetId,
          path: source.path,
          multipart_upload_id: asset.multipart_upload_id,
          part_numbers: partNumbers,
        },
        cache: "no-store",
      });
      renewed.parts.forEach((part) =>
        authorizations.set(part.part_number, part),
      );
    }
    const loadedByPart = new Map<number, number>();
    onProgress({
      stage: "uploading",
      currentPath: source.path,
      completedFiles,
      totalFiles: orderedFiles.length,
      uploadedBytes: completedBytes,
      totalBytes: selection.sourceBytes,
    });
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
        await putPart(
          authorization,
          source.file.slice(
            start,
            Math.min(source.file.size, start + plan.partSize),
          ),
          (loaded) => {
            loadedByPart.set(partNumber, loaded);
            onProgress({
              stage: "uploading",
              currentPath: source.path,
              completedFiles,
              totalFiles: orderedFiles.length,
              uploadedBytes:
                completedBytes +
                [...loadedByPart.values()].reduce(
                  (total, value) => total + value,
                  0,
                ),
              totalBytes: selection.sourceBytes,
            });
          },
        );
      },
    );
    await request({
      method: "POST",
      path: `${root}/${encodeURIComponent(grant.import_id)}/assets:complete`,
      scope,
      body: {
        dataset_id: binding.datasetId,
        path: source.path,
        multipart_upload_id: asset.multipart_upload_id,
        size: source.file.size,
        part_count: plan.partCount,
      },
    });
    completedBytes += source.file.size;
    completedFiles += 1;
  }
  onProgress({
    stage: "committing",
    currentPath: null,
    completedFiles,
    totalFiles: orderedFiles.length,
    uploadedBytes: completedBytes,
    totalBytes: selection.sourceBytes,
  });
  return request<LeRobotImportAccepted>({
    method: "POST",
    path: `${root}/${encodeURIComponent(grant.import_id)}:commit`,
    scope,
    body: { manifest },
  });
}
