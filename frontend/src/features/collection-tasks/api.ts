import type {
  components,
  operations,
} from "../../shared/api/generated/platform";
import { createDomainError } from "../../shared/api/domain-error";
import { request, requestWithResponse } from "../../shared/api/http-client";
import type { Scope } from "../../shared/scope/model";
import { fetchDatasets } from "../datasets/api/queries";

export type CollectionTask = components["schemas"]["CollectionTask"];
export type CollectionTaskPage = components["schemas"]["CollectionTaskPage"];
export type CollectionTaskProgress =
  components["schemas"]["CollectionTaskProgress"];
export type CollectionTaskPackage =
  components["schemas"]["CollectionTaskPackage"];
export type CollectionTaskPackageList =
  components["schemas"]["CollectionTaskPackageList"];
export type CollectionTaskStatus =
  components["schemas"]["CollectionTaskStatus"];
export type CreateCollectionTask =
  components["schemas"]["CreateCollectionTask"];
export type UpdateCollectionTask =
  components["schemas"]["UpdateCollectionTask"];

type ListCollectionTaskQuery = NonNullable<
  operations["listCollectionTasks"]["parameters"]["query"]
>;

export interface CollectionTaskScope extends Scope {
  readonly projectId: string;
  readonly regionCode: string;
}

export interface CollectionTaskSnapshot {
  readonly task: CollectionTask;
  readonly etag: string;
}

export interface AssignableDataset {
  readonly datasetId: string;
  readonly name: string;
  readonly folderPath: readonly string[];
}

export interface CollectionTaskGateway {
  delete: (
    scope: CollectionTaskScope,
    collectionTaskId: string,
    etag: string,
    idempotencyKey: string,
  ) => Promise<{
    status: string;
    deleted_objects: number;
    deleted_bytes: number;
  }>;
  listAssignableDatasets?: (
    scope: CollectionTaskScope,
    signal?: AbortSignal,
  ) => Promise<readonly AssignableDataset[]>;
  list: (
    scope: CollectionTaskScope,
    query: Readonly<ListCollectionTaskQuery>,
    signal?: AbortSignal,
  ) => Promise<CollectionTaskPage>;
  progress: (
    scope: CollectionTaskScope,
    collectionTaskId: string,
    signal?: AbortSignal,
  ) => Promise<CollectionTaskProgress>;
  packages: (
    scope: CollectionTaskScope,
    collectionTaskId: string,
    signal?: AbortSignal,
  ) => Promise<CollectionTaskPackageList>;
  detail: (
    scope: CollectionTaskScope,
    collectionTaskId: string,
    signal?: AbortSignal,
  ) => Promise<CollectionTaskSnapshot>;
  create: (
    scope: CollectionTaskScope,
    command: CreateCollectionTask,
    idempotencyKey: string,
  ) => Promise<CollectionTask>;
  update: (
    scope: CollectionTaskScope,
    collectionTaskId: string,
    command: UpdateCollectionTask,
    etag: string,
  ) => Promise<CollectionTask>;
  close: (
    scope: CollectionTaskScope,
    collectionTaskId: string,
    etag: string,
    idempotencyKey: string,
  ) => Promise<CollectionTask>;
  cancel: (
    scope: CollectionTaskScope,
    collectionTaskId: string,
    etag: string,
    idempotencyKey: string,
  ) => Promise<CollectionTask>;
  reopen: (
    scope: CollectionTaskScope,
    collectionTaskId: string,
    etag: string,
    idempotencyKey: string,
  ) => Promise<CollectionTask>;
}

function collectionTaskRoot(scope: CollectionTaskScope): string {
  return `/projects/${encodeURIComponent(scope.projectId)}/collection-tasks`;
}

function assertTaskScope(
  task: Readonly<Pick<CollectionTask, "organization_id" | "project_id">>,
  scope: CollectionTaskScope,
): void {
  if (
    task.organization_id === scope.organizationId &&
    task.project_id === scope.projectId
  ) {
    return;
  }
  throw createDomainError({
    code: "CONTRACT_MISMATCH",
    message: "采集任务响应与当前组织或项目不匹配，页面已停止写入。",
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId: null,
    retryable: false,
    httpStatus: null,
  });
}

async function getCollectionTaskSnapshot(
  scope: CollectionTaskScope,
  collectionTaskId: string,
  signal?: AbortSignal,
): Promise<CollectionTaskSnapshot> {
  const response = await requestWithResponse<CollectionTask>({
    method: "GET",
    path: `${collectionTaskRoot(scope)}/${encodeURIComponent(collectionTaskId)}`,
    scope,
    ...(signal ? { signal } : {}),
  });
  const etag = response.headers.get("ETag");
  if (!etag) {
    throw createDomainError({
      code: "CONTRACT_MISMATCH",
      message: "任务详情缺少版本标识，编辑和关闭已安全禁用。",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [
        { code: "ETAG_MISSING", message: "服务端未返回 ETag。" },
      ],
      requestId: null,
      retryable: false,
      httpStatus: null,
    });
  }
  const task = response.data;
  assertTaskScope(task, scope);
  return { task, etag };
}

export const collectionTaskGateway: CollectionTaskGateway = {
  async delete(scope, collectionTaskId, etag, idempotencyKey) {
    return request({
      method: "DELETE",
      path: `${collectionTaskRoot(scope)}/${encodeURIComponent(collectionTaskId)}`,
      scope,
      ifMatch: etag,
      idempotencyKey,
    });
  },
  async listAssignableDatasets(scope, signal) {
    const datasets: AssignableDataset[] = [];
    let after: string | undefined;
    do {
      const page = await fetchDatasets(
        { sort: "nameAsc", limit: 100, ...(after ? { after } : {}) },
        signal,
      );
      if (
        page.scope.organizationId !== scope.organizationId ||
        page.scope.projectId !== scope.projectId ||
        page.scope.regionCode !== scope.regionCode
      ) {
        throw createDomainError({
          code: "CONTRACT_MISMATCH",
          message: "数据集候选项与当前组织、项目或区域不匹配。",
          fieldErrors: [],
          operationErrors: [],
          blockedReasons: [],
          requestId: page.requestId,
          retryable: false,
          httpStatus: null,
        });
      }
      datasets.push(
        ...page.items
          .filter((item) => item.availability === "ACTIVE")
          .map((item) => ({
            datasetId: item.datasetId,
            name: item.name,
            folderPath: item.folderPath,
          })),
      );
      after = page.pageInfo.hasNextPage
        ? (page.pageInfo.after ?? undefined)
        : undefined;
    } while (after);
    return datasets;
  },

  async list(scope, query, signal) {
    const page = await request<CollectionTaskPage>({
      method: "GET",
      path: collectionTaskRoot(scope),
      scope,
      query,
      ...(signal ? { signal } : {}),
    });
    for (const task of page.items) assertTaskScope(task, scope);
    return page;
  },

  async progress(scope, collectionTaskId, signal) {
    const progress = await request<CollectionTaskProgress>({
      method: "GET",
      path: `${collectionTaskRoot(scope)}/${encodeURIComponent(collectionTaskId)}/progress`,
      scope,
      ...(signal ? { signal } : {}),
    });
    if (
      progress.organization_id !== scope.organizationId ||
      progress.project_id !== scope.projectId ||
      progress.collection_task_id !== collectionTaskId
    ) {
      throw createDomainError({
        code: "CONTRACT_MISMATCH",
        message: "任务进度响应与当前任务不匹配。",
        fieldErrors: [],
        operationErrors: [],
        blockedReasons: [],
        requestId: null,
        retryable: false,
        httpStatus: null,
      });
    }
    return progress;
  },

  async packages(scope, collectionTaskId, signal) {
    const packages = await request<CollectionTaskPackageList>({
      method: "GET",
      path: `${collectionTaskRoot(scope)}/${encodeURIComponent(collectionTaskId)}/packages`,
      scope,
      ...(signal ? { signal } : {}),
    });
    if (
      packages.organization_id !== scope.organizationId ||
      packages.project_id !== scope.projectId ||
      packages.collection_task_id !== collectionTaskId
    ) {
      throw createDomainError({
        code: "CONTRACT_MISMATCH",
        message: "任务数据包响应与当前任务不匹配。",
        fieldErrors: [],
        operationErrors: [],
        blockedReasons: [],
        requestId: null,
        retryable: false,
        httpStatus: null,
      });
    }
    return packages;
  },

  detail: getCollectionTaskSnapshot,

  async create(scope, command, idempotencyKey) {
    const task = await request<CollectionTask>({
      method: "POST",
      path: collectionTaskRoot(scope),
      scope,
      body: command,
      idempotencyKey,
    });
    assertTaskScope(task, scope);
    return task;
  },

  async update(scope, collectionTaskId, command, etag) {
    const task = await request<CollectionTask>({
      method: "PATCH",
      path: `${collectionTaskRoot(scope)}/${encodeURIComponent(collectionTaskId)}`,
      scope,
      body: command,
      ifMatch: etag,
    });
    assertTaskScope(task, scope);
    return task;
  },

  async close(scope, collectionTaskId, etag, idempotencyKey) {
    const task = await request<CollectionTask>({
      method: "POST",
      path: `${collectionTaskRoot(scope)}/${encodeURIComponent(collectionTaskId)}:close`,
      scope,
      ifMatch: etag,
      idempotencyKey,
    });
    assertTaskScope(task, scope);
    return task;
  },

  async cancel(scope, collectionTaskId, etag, idempotencyKey) {
    const task = await request<CollectionTask>({
      method: "POST",
      path: `${collectionTaskRoot(scope)}/${encodeURIComponent(collectionTaskId)}:cancel`,
      scope,
      ifMatch: etag,
      idempotencyKey,
    });
    assertTaskScope(task, scope);
    return task;
  },

  async reopen(scope, collectionTaskId, etag, idempotencyKey) {
    const task = await request<CollectionTask>({
      method: "POST",
      path: `${collectionTaskRoot(scope)}/${encodeURIComponent(collectionTaskId)}:reopen`,
      scope,
      ifMatch: etag,
      idempotencyKey,
    });
    assertTaskScope(task, scope);
    return task;
  },
};
