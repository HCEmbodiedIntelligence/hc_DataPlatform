import type {
  components,
  operations,
} from "../../shared/api/generated/platform";
import { createDomainError } from "../../shared/api/domain-error";
import { domainErrorFromResponse } from "../../shared/api/http-client";
import { request } from "../../shared/api/http-client";
import { getRuntimeConfig } from "../../shared/config/runtime";
import { makeScopeKey, type Scope } from "../../entities/scope";
import { getShellState } from "../../shared/scope/shell-store";

export type CollectionTask = components["schemas"]["CollectionTask"];
export type CollectionTaskPage = components["schemas"]["CollectionTaskPage"];
export type CollectionTaskProgress =
  components["schemas"]["CollectionTaskProgress"];
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

export interface CollectionTaskGateway {
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

function joinUrl(base: string, path: string): string {
  const normalizedBase = base.endsWith("/") ? base.slice(0, -1) : base;
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${normalizedBase}${normalizedPath}`;
}

async function responseJson(response: Response): Promise<unknown> {
  const body = await response.text();
  if (!body) return undefined;
  try {
    return JSON.parse(body) as unknown;
  } catch {
    return undefined;
  }
}

async function getCollectionTaskSnapshot(
  scope: CollectionTaskScope,
  collectionTaskId: string,
  signal?: AbortSignal,
): Promise<CollectionTaskSnapshot> {
  const shell = getShellState();
  const requestScopeKey = makeScopeKey(scope);
  if (shell.scopeKey !== requestScopeKey) {
    throw createDomainError({
      code: "PRECONDITION_FAILED",
      message: "当前项目已变化，请重新打开任务。",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [
        { code: "SCOPE_CHANGED", message: "请求项目与当前项目不一致。" },
      ],
      requestId: null,
      retryable: false,
      httpStatus: null,
    });
  }

  const config = getRuntimeConfig();
  const headers = new Headers({
    Accept: "application/json",
    "Accept-Language": globalThis.navigator?.language || "zh-CN",
    "X-Client-Version": config.buildVersion,
    "X-Project-Id": scope.projectId,
    "X-Region-Code": scope.regionCode,
  });
  if (scope.organizationId) {
    headers.set("X-Organization-Id", scope.organizationId);
  }
  if (shell.sessionToken) {
    headers.set("Authorization", `Bearer ${shell.sessionToken}`);
  }

  let response: Response;
  try {
    response = await globalThis.fetch(
      joinUrl(
        config.apiBaseUrl,
        `${collectionTaskRoot(scope)}/${encodeURIComponent(collectionTaskId)}`,
      ),
      { method: "GET", headers, signal },
    );
  } catch {
    throw createDomainError({
      code: "NETWORK_ERROR",
      message: signal?.aborted ? "请求已取消" : "网络连接失败",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: null,
      retryable: !signal?.aborted,
      httpStatus: null,
    });
  }

  const raw = await responseJson(response);
  if (getShellState().scopeKey !== requestScopeKey) {
    throw createDomainError({
      code: "PRECONDITION_FAILED",
      message: "请求返回时当前项目已变化，请重新打开任务。",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [
        { code: "SCOPE_CHANGED", message: "响应所属项目已经失效。" },
      ],
      requestId: null,
      retryable: false,
      httpStatus: null,
    });
  }
  if (!response.ok) throw domainErrorFromResponse(response.status, raw);

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
  const task = raw as CollectionTask;
  assertTaskScope(task, scope);
  return { task, etag };
}

export const collectionTaskGateway: CollectionTaskGateway = {
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
