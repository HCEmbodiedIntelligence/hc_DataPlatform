import {
  keepPreviousData,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Alert, Button, Input, Modal, Select, Space, Typography } from "antd";
import { Plus, RefreshCw, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import { FilterToolbar } from "../../shared/ui/layout/FilterToolbar";
import { StandardPageScaffold } from "../../shared/ui/layout/StandardPageScaffold";
import { PageState } from "../../shared/ui/state/PageState";
import { StatusTag } from "../../shared/ui/state/StatusTag";
import type { PageStateKind } from "../../shared/ui/state/contracts";
import {
  collectionTaskGateway,
  type CollectionTask,
  type CollectionTaskGateway,
  type CollectionTaskScope,
  type CreateCollectionTask,
} from "./api";
import { CollectionTaskDrawer } from "./components/CollectionTaskDrawer";
import {
  CollectionTaskTable,
  type TaskProgressState,
} from "./components/CollectionTaskTable";
import {
  collectionTaskQueryCodec,
  type CollectionTaskSearch,
} from "./query-codec";
import styles from "./styles.module.css";

const pageSize = 20;

interface SaveRequest {
  readonly mode: "create" | "edit";
  readonly command: CreateCollectionTask;
  readonly taskId?: string;
  readonly etag?: string;
  readonly idempotencyKey?: string;
}

type LifecycleAction = "close" | "cancel" | "reopen";

interface LifecycleTarget {
  readonly task: CollectionTask;
  readonly action: LifecycleAction;
}

interface LifecycleRequest extends LifecycleTarget {}

export interface CollectionTaskPageProps {
  readonly gateway?: CollectionTaskGateway;
  readonly capabilityOverride?: "manage" | "read-only";
}

function errorState(error: unknown): PageStateKind {
  if (!isDomainError(error)) return "contract-mismatch";
  switch (error.code) {
    case "FORBIDDEN":
    case "UNAUTHENTICATED":
      return "forbidden";
    case "NOT_FOUND":
      return "not-found";
    case "GONE":
      return "gone";
    case "VERSION_CONFLICT":
    case "PRECONDITION_FAILED":
      return "conflict";
    case "RATE_LIMITED":
      return "rate-limited";
    case "NETWORK_ERROR":
      return "offline";
    case "CONTRACT_MISMATCH":
      return "contract-mismatch";
    default:
      return "error";
  }
}

function requestId(error: unknown): string | null {
  return isDomainError(error) ? error.requestId : null;
}

function problemDescription(error: unknown, fallback: string) {
  if (!isDomainError(error)) return fallback;
  return (
    <Space orientation="vertical" size={4}>
      <span>{error.message}</span>
      {error.problemCode ? (
        <Typography.Text code translate="no">
          问题代码：{error.problemCode}
        </Typography.Text>
      ) : null}
      <span>
        {error.retryable ? "服务端允许重试。" : "请先核对作用域或资源状态。"}
      </span>
    </Space>
  );
}

function newIdempotencyKey(prefix: "create" | LifecycleAction): string {
  const identifier =
    globalThis.crypto?.randomUUID?.() ??
    `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${identifier}`;
}

function matchesCurrentWindow(
  task: CollectionTask,
  search: CollectionTaskSearch,
): boolean {
  const query = search.query.toLocaleLowerCase("zh-CN");
  const type = search.type.toLocaleLowerCase("zh-CN");
  const identityMatches =
    !query ||
    task.name.toLocaleLowerCase("zh-CN").includes(query) ||
    task.task_code.includes(query);
  const typeMatches =
    !type || task.type.toLocaleLowerCase("zh-CN").includes(type);
  return identityMatches && typeMatches;
}

export function CollectionTaskPage({
  capabilityOverride,
  gateway = collectionTaskGateway,
}: Readonly<CollectionTaskPageProps>) {
  const queryClient = useQueryClient();
  const shellScope = useShellStore((state) => state.scope);
  const scopeKey = useShellStore((state) => state.scopeKey);
  const capabilities = useCapabilities();
  const [params, setParams] = useSearchParams();
  const search = collectionTaskQueryCodec.parse(params);
  const [cursorTrail, setCursorTrail] = useState<readonly string[]>([]);
  const [lifecycleTarget, setLifecycleTarget] =
    useState<LifecycleTarget | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const createIdempotencyKey = useRef(newIdempotencyKey("create"));
  const lifecycleAttempt = useRef<{
    readonly taskId: string;
    readonly etag: string;
    readonly idempotencyKey: string;
    readonly action: LifecycleAction;
  } | null>(null);
  const lifecycleSubmitLock = useRef(false);

  const scope = useMemo<CollectionTaskScope | null>(
    () =>
      shellScope?.projectId && shellScope.regionCode
        ? {
            organizationId: shellScope.organizationId,
            projectId: shellScope.projectId,
            regionCode: shellScope.regionCode,
          }
        : null,
    [shellScope?.organizationId, shellScope?.projectId, shellScope?.regionCode],
  );
  const authorizationReady =
    capabilityOverride !== undefined ||
    (!capabilities.loading && !capabilities.failed);
  const canManage =
    capabilityOverride === "manage" ||
    (capabilityOverride === undefined && capabilities.has("upload.manage"));
  const listEnabled = scope !== null && authorizationReady;
  const serverStatus = search.status === "ALL" ? undefined : search.status;

  const list = useQuery({
    queryKey: [
      "collection-tasks",
      scopeKey,
      serverStatus ?? "ALL",
      search.cursor ?? "FIRST",
      pageSize,
    ],
    queryFn: ({ signal }) =>
      gateway.list(
        scope as CollectionTaskScope,
        {
          status: serverStatus,
          limit: pageSize,
          cursor: search.cursor,
        },
        signal,
      ),
    enabled: listEnabled,
    placeholderData: keepPreviousData,
    retry: false,
  });

  const visibleTasks = useMemo(
    () =>
      (list.data?.items ?? []).filter((task) =>
        matchesCurrentWindow(task, search),
      ),
    [list.data?.items, search],
  );
  const progressQueries = useQueries({
    queries: visibleTasks.map((task) => ({
      queryKey: ["collection-task-progress", scopeKey, task.collection_task_id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        gateway.progress(
          scope as CollectionTaskScope,
          task.collection_task_id,
          signal,
        ),
      enabled: listEnabled,
      staleTime: 30_000,
      retry: false,
    })),
  });
  const progressByTaskId = useMemo(
    () =>
      new Map<string, TaskProgressState>(
        visibleTasks.map((task, index) => {
          const progress = progressQueries[index];
          return [
            task.collection_task_id,
            {
              data: progress?.data,
              pending: progress?.isPending ?? true,
              error: progress?.error ?? undefined,
            },
          ];
        }),
      ),
    [progressQueries, visibleTasks],
  );

  const editingTaskId =
    search.drawer?.mode === "edit" ? search.drawer.taskId : null;
  const detail = useQuery({
    queryKey: ["collection-task-detail", scopeKey, editingTaskId],
    queryFn: ({ signal }) =>
      gateway.detail(
        scope as CollectionTaskScope,
        editingTaskId as string,
        signal,
      ),
    enabled:
      scope !== null &&
      canManage &&
      authorizationReady &&
      editingTaskId !== null,
    retry: false,
  });

  const closeDrawer = useCallback(() => {
    const next = { ...search };
    delete next.drawer;
    setParams(collectionTaskQueryCodec.build(next));
    window.setTimeout(() => returnFocusRef.current?.focus(), 0);
  }, [search, setParams]);

  const save = useMutation({
    mutationFn: async (request: SaveRequest) => {
      if (scope === null) throw new Error("Missing collection-task scope");
      if (request.mode === "create") {
        return gateway.create(
          scope,
          request.command,
          request.idempotencyKey as string,
        );
      }
      return gateway.update(
        scope,
        request.taskId as string,
        request.command,
        request.etag as string,
      );
    },
    onSuccess: async (task) => {
      await queryClient.invalidateQueries({
        queryKey: ["collection-task-detail", scopeKey, task.collection_task_id],
        exact: true,
        refetchType: "none",
      });
      await queryClient.invalidateQueries({
        queryKey: ["collection-tasks", scopeKey],
      });
      closeDrawer();
    },
  });

  const lifecycleTask = useMutation({
    mutationFn: async ({ action, task }: LifecycleRequest) => {
      if (scope === null) throw new Error("Missing collection-task scope");
      let attempt = lifecycleAttempt.current;
      if (
        attempt === null ||
        attempt.taskId !== task.collection_task_id ||
        attempt.action !== action
      ) {
        const snapshot = await gateway.detail(scope, task.collection_task_id);
        attempt = {
          taskId: task.collection_task_id,
          etag: snapshot.etag,
          idempotencyKey: newIdempotencyKey(action),
          action,
        };
        lifecycleAttempt.current = attempt;
      }
      try {
        if (action === "close") {
          return await gateway.close(
            scope,
            task.collection_task_id,
            attempt.etag,
            attempt.idempotencyKey,
          );
        }
        if (action === "cancel") {
          return await gateway.cancel(
            scope,
            task.collection_task_id,
            attempt.etag,
            attempt.idempotencyKey,
          );
        }
        return await gateway.reopen(
          scope,
          task.collection_task_id,
          attempt.etag,
          attempt.idempotencyKey,
        );
      } catch (error) {
        if (
          isDomainError(error) &&
          (error.code === "VERSION_CONFLICT" ||
            error.code === "PRECONDITION_FAILED")
        ) {
          lifecycleAttempt.current = null;
        }
        throw error;
      }
    },
    onSuccess: async () => {
      lifecycleAttempt.current = null;
      setLifecycleTarget(null);
      await queryClient.invalidateQueries({
        queryKey: ["collection-tasks", scopeKey],
      });
      window.setTimeout(() => returnFocusRef.current?.focus(), 0);
    },
    onSettled: () => {
      lifecycleSubmitLock.current = false;
    },
  });

  const changeSearch = useCallback(
    (patch: Partial<CollectionTaskSearch>, resetCursor = false) => {
      const next: CollectionTaskSearch = { ...search, ...patch };
      if (resetCursor) {
        setCursorTrail([]);
        const { cursor: _cursor, ...withoutCursor } = next;
        setParams(collectionTaskQueryCodec.build(withoutCursor));
        return;
      }
      setParams(collectionTaskQueryCodec.build(next));
    },
    [search, setParams],
  );

  useEffect(() => {
    setCursorTrail([]);
    setLifecycleTarget(null);
    lifecycleAttempt.current = null;
  }, [scopeKey]);

  const openCreate = useCallback(() => {
    if (!canManage) return;
    returnFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    createIdempotencyKey.current = newIdempotencyKey("create");
    save.reset();
    changeSearch({ drawer: { mode: "create" } });
  }, [canManage, changeSearch, save]);

  const openEdit = useCallback(
    (task: CollectionTask) => {
      if (!canManage || task.status !== "ACTIVE") return;
      returnFocusRef.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
      save.reset();
      changeSearch({
        drawer: { mode: "edit", taskId: task.collection_task_id },
      });
    },
    [canManage, changeSearch, save],
  );

  const requestCloseTask = useCallback(
    (task: CollectionTask) => {
      if (!canManage || task.status !== "ACTIVE") return;
      returnFocusRef.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
      lifecycleAttempt.current = null;
      lifecycleTask.reset();
      setLifecycleTarget({ task, action: "close" });
    },
    [canManage, lifecycleTask],
  );

  const requestCancelTask = useCallback(
    (task: CollectionTask) => {
      if (!canManage || task.status !== "ACTIVE") return;
      returnFocusRef.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
      lifecycleAttempt.current = null;
      lifecycleTask.reset();
      setLifecycleTarget({ task, action: "cancel" });
    },
    [canManage, lifecycleTask],
  );

  const requestReopenTask = useCallback(
    (task: CollectionTask) => {
      if (!canManage || task.status === "ACTIVE") return;
      returnFocusRef.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
      lifecycleAttempt.current = null;
      lifecycleTask.reset();
      setLifecycleTarget({ task, action: "reopen" });
    },
    [canManage, lifecycleTask],
  );

  const submitDrawer = async (command: CreateCollectionTask) => {
    if (search.drawer?.mode === "create") {
      await save.mutateAsync({
        mode: "create",
        command,
        idempotencyKey: createIdempotencyKey.current,
      });
      return;
    }
    if (
      search.drawer?.mode === "edit" &&
      detail.data?.etag &&
      detail.data.task.status === "ACTIVE"
    ) {
      await save.mutateAsync({
        mode: "edit",
        command,
        taskId: search.drawer.taskId,
        etag: detail.data.etag,
      });
    }
  };

  const filtered = Boolean(search.query || search.type);
  const authorizationState: PageStateKind | "ready" =
    capabilities.loading && capabilityOverride === undefined
      ? "loading"
      : !authorizationReady
        ? "forbidden"
        : scope === null
          ? "feature-unavailable"
          : list.isPending
            ? "loading"
            : list.error && list.data === undefined
              ? errorState(list.error)
              : "ready";

  const table = (
    <CollectionTaskTable
      canManage={canManage}
      filtered={filtered}
      loading={list.isPending}
      onCancel={requestCancelTask}
      onClose={requestCloseTask}
      onEdit={openEdit}
      onReopen={requestReopenTask}
      progressByTaskId={progressByTaskId}
      tasks={visibleTasks}
    />
  );

  const content =
    authorizationState === "ready" ? (
      <div className={styles.tableStack}>
        {list.error ? (
          <PageState
            label="采集任务列表"
            layout="list"
            onRetry={() => void list.refetch()}
            requestId={requestId(list.error)}
            state="partial"
            description={problemDescription(
              list.error,
              "刷新失败，仍保留上次成功加载的采集任务。",
            )}
          >
            {table}
          </PageState>
        ) : (
          table
        )}
      </div>
    ) : (
      <PageState
        action={
          authorizationState === "empty" && canManage ? (
            <Button type="primary" onClick={openCreate}>
              新建采集任务
            </Button>
          ) : undefined
        }
        label="采集任务"
        layout="list"
        onRetry={list.error ? () => void list.refetch() : undefined}
        requestId={requestId(list.error)}
        state={authorizationState}
        description={
          list.error
            ? problemDescription(list.error, "采集任务加载失败，请重试。")
            : undefined
        }
      />
    );

  const drawerOpen = search.drawer !== undefined && canManage;
  const drawerMode = search.drawer?.mode ?? "create";
  const drawerTask = drawerMode === "edit" ? detail.data?.task : undefined;
  const drawerLoadError = drawerMode === "edit" ? detail.error : undefined;
  const lifecycleAction = lifecycleTarget?.action ?? "close";
  const lifecycleTitle =
    lifecycleAction === "close"
      ? "关闭采集任务"
      : lifecycleAction === "cancel"
        ? "取消采集任务"
        : "重新开启采集任务";
  const lifecycleConfirm =
    lifecycleAction === "close"
      ? "确认关闭任务"
      : lifecycleAction === "cancel"
        ? "确认取消任务"
        : "确认重新开启";

  return (
    <div
      className={`${styles.pageFrame} ${drawerOpen ? styles.pageFrameWithDrawer : ""}`}
      data-drawer-open={drawerOpen || undefined}
      data-page-id="P20"
    >
      <section className={styles.page}>
        <StandardPageScaffold
          header={{
            title: "采集任务",
            description:
              "定义采集目标与质量要求，跟踪已接收数据包和质量进度，任务持续有效直至关闭。",
            breadcrumbs: [
              { key: "ingest", label: "采集与接收" },
              { key: "tasks", label: "采集任务" },
            ],
            actions: canManage ? (
              <Button
                icon={<Plus aria-hidden="true" size={17} />}
                type="primary"
                onClick={openCreate}
              >
                新建采集任务
              </Button>
            ) : (
              <StatusTag label="只读" status="READ_ONLY" tone="neutral" />
            ),
          }}
          filters={
            authorizationState === "ready" ? (
              <FilterToolbar
                actions={
                  <Button
                    aria-label="刷新采集任务"
                    disabled={list.isFetching}
                    icon={<RefreshCw aria-hidden="true" size={16} />}
                    onClick={() => void list.refetch()}
                  >
                    刷新
                  </Button>
                }
                disabled={list.isFetching}
                label="采集任务筛选"
                onReset={
                  search.status !== "ALL" || filtered
                    ? () =>
                        changeSearch(
                          { status: "ALL", query: "", type: "" },
                          true,
                        )
                    : undefined
                }
              >
                <label className={styles.filterField}>
                  状态
                  <Select
                    aria-label="任务状态"
                    options={[
                      { value: "ALL", label: "全部状态" },
                      { value: "ACTIVE", label: "进行中" },
                      { value: "CLOSED", label: "已关闭" },
                      { value: "CANCELLED", label: "已取消" },
                    ]}
                    value={search.status}
                    onChange={(status) => changeSearch({ status }, true)}
                  />
                </label>
                <label className={styles.filterField}>
                  项目
                  <Input
                    aria-label="当前项目"
                    autoComplete="off"
                    readOnly
                    value={scope?.projectId ?? ""}
                  />
                </label>
                <label className={styles.filterField}>
                  采集类型
                  <Input
                    aria-label="筛选采集类型"
                    autoComplete="off"
                    maxLength={100}
                    placeholder="当前窗口内筛选…"
                    value={search.type}
                    onChange={(event) =>
                      changeSearch({ type: event.currentTarget.value }, true)
                    }
                  />
                </label>
                <label
                  className={`${styles.filterField} ${styles.searchField}`}
                >
                  搜索
                  <Input
                    aria-label="搜索任务名称或编号"
                    autoComplete="off"
                    maxLength={200}
                    placeholder="搜索任务名称或编号…"
                    prefix={<Search aria-hidden="true" size={15} />}
                    value={search.query}
                    onChange={(event) =>
                      changeSearch({ query: event.currentTarget.value }, true)
                    }
                  />
                </label>
              </FilterToolbar>
            ) : undefined
          }
          pagination={
            authorizationState === "ready" && list.data ? (
              <Space className={styles.pagination} wrap>
                <Typography.Text type="secondary">
                  当前窗口 {visibleTasks.length} 条
                  {filtered ? ` / 加载 ${list.data.items.length} 条` : ""}
                </Typography.Text>
                <Button
                  disabled={cursorTrail.length === 0 || list.isFetching}
                  onClick={() => {
                    const previous = cursorTrail.at(-1);
                    setCursorTrail((trail) => trail.slice(0, -1));
                    changeSearch({ cursor: previous || undefined }, false);
                  }}
                >
                  上一窗口
                </Button>
                <Button
                  disabled={!list.data.next_cursor || list.isFetching}
                  onClick={() => {
                    setCursorTrail((trail) => [...trail, search.cursor ?? ""]);
                    changeSearch({
                      cursor: list.data?.next_cursor ?? undefined,
                    });
                  }}
                >
                  下一窗口
                </Button>
              </Space>
            ) : undefined
          }
          state={content}
        />
      </section>

      {drawerOpen ? (
        <CollectionTaskDrawer
          key={
            drawerMode === "edit"
              ? `edit:${editingTaskId ?? "unknown"}`
              : "create"
          }
          error={save.error ?? drawerLoadError}
          initialTask={drawerTask}
          loading={drawerMode === "edit" && detail.isPending}
          mode={drawerMode}
          pending={save.isPending}
          projectId={scope?.projectId ?? ""}
          onClose={closeDrawer}
          onFormChanged={() => {
            if (drawerMode === "create") {
              createIdempotencyKey.current = newIdempotencyKey("create");
            }
            if (save.error) save.reset();
          }}
          onReload={
            drawerMode === "edit"
              ? () => {
                  save.reset();
                  void detail.refetch();
                }
              : undefined
          }
          onSubmit={submitDrawer}
        />
      ) : null}

      <Modal
        cancelText="取消"
        closable={!lifecycleTask.isPending}
        keyboard={!lifecycleTask.isPending}
        mask={{ closable: false }}
        okButtonProps={{
          danger: lifecycleAction !== "reopen",
          disabled: lifecycleTask.isPending,
          loading: lifecycleTask.isPending,
        }}
        okText={
          lifecycleTask.isPending
            ? lifecycleAction === "close"
              ? "正在关闭…"
              : lifecycleAction === "cancel"
                ? "正在取消…"
                : "正在重新开启…"
            : lifecycleTask.error &&
                isDomainError(lifecycleTask.error) &&
                (lifecycleTask.error.code === "VERSION_CONFLICT" ||
                  lifecycleTask.error.code === "PRECONDITION_FAILED")
              ? `重新加载并${lifecycleAction === "reopen" ? "开启" : lifecycleAction === "cancel" ? "取消" : "关闭"}`
              : lifecycleConfirm
        }
        open={lifecycleTarget !== null}
        title={lifecycleTitle}
        onCancel={() => {
          if (lifecycleTask.isPending) return;
          setLifecycleTarget(null);
          lifecycleAttempt.current = null;
          window.setTimeout(() => returnFocusRef.current?.focus(), 0);
        }}
        onOk={() => {
          if (!lifecycleTarget || lifecycleSubmitLock.current) return;
          lifecycleSubmitLock.current = true;
          lifecycleTask.mutate(lifecycleTarget);
        }}
      >
        {lifecycleTarget ? (
          <Space orientation="vertical" size="middle">
            <Alert
              showIcon
              title={
                lifecycleAction === "close"
                  ? "关闭后不再接受新的数据包关联，历史数据仍可查询。"
                  : lifecycleAction === "cancel"
                    ? "取消后不再接受新的数据包关联；可由有权限成员明确重新开启。"
                    : "重新开启后，任务可再次接受新的数据包关联。"
              }
              type="warning"
            />
            <p className={styles.closeIdentity}>
              <strong>{lifecycleTarget.task.name}</strong>
              <code translate="no">{lifecycleTarget.task.task_code}</code>
            </p>
            <p>
              {lifecycleAction === "close"
                ? "关闭后可由有权限成员明确重新开启；系统不会因进度达标而自动关闭。"
                : lifecycleAction === "cancel"
                  ? "取消不是删除，历史数据与审计记录仍会保留。"
                  : "重新开启不会修改已关联的数据包、质量事实或目标。"}
            </p>
            {lifecycleTask.error ? (
              <Alert
                role="alert"
                showIcon
                title={
                  isDomainError(lifecycleTask.error) &&
                  lifecycleTask.error.code === "RATE_LIMITED"
                    ? "请求频率受限"
                    : "任务状态未更新"
                }
                description={
                  <Space orientation="vertical" size={4}>
                    <span>
                      {isDomainError(lifecycleTask.error)
                        ? lifecycleTask.error.message
                        : "请稍后重试。"}
                    </span>
                    {requestId(lifecycleTask.error) ? (
                      <Typography.Text code translate="no">
                        请求 ID：{requestId(lifecycleTask.error)}
                      </Typography.Text>
                    ) : null}
                    {isDomainError(lifecycleTask.error) &&
                    lifecycleTask.error.problemCode ? (
                      <Typography.Text code translate="no">
                        问题代码：{lifecycleTask.error.problemCode}
                      </Typography.Text>
                    ) : null}
                    {isDomainError(lifecycleTask.error) ? (
                      <span>
                        {lifecycleTask.error.retryable
                          ? "服务端允许重试。"
                          : "请先重新加载任务事实。"}
                      </span>
                    ) : null}
                  </Space>
                }
                type="error"
              />
            ) : null}
          </Space>
        ) : null}
      </Modal>
    </div>
  );
}

export default CollectionTaskPage;
