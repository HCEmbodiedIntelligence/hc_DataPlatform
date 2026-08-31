import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Tag, Typography } from "antd";
import { Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import type { DatasetId } from "../../entities/dataset";
import type { DatasetVersionId } from "../../entities/dataset-version";
import type { EpisodeId } from "../../entities/episode";
import {
  useCreateDatasetMutation,
  useDatasetFacetsQuery,
  useDatasetsPageCapabilitiesQuery,
  useDatasetSummaryQuery,
  useDatasetsQuery,
  useVersionEpisodesQuery,
  type DatasetListItemVm,
  type EpisodeListItemVm,
} from "../../features/datasets/api";
import { routes } from "../../features/datasets/routing";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  DataCursorPager,
  PageState,
  StandardPageScaffold,
  type MetricState,
  type PageStateKind,
} from "../../shared/ui";
import {
  CreateDatasetDialog,
  type CreateDatasetDraft,
} from "./components/CreateDatasetDialog";
import { DatasetFilterPanel } from "./components/DatasetFilterPanel";
import { DatasetTable } from "./components/DatasetTable";
import { SelectedDatasetSummary } from "./components/SelectedDatasetSummary";
import { EpisodeTable } from "../p06-dataset-detail/components/DatasetDetailTables";
import {
  collectionTaskGateway,
  type CollectionTaskPackage,
  type CollectionTaskScope,
} from "../p20-collection-tasks/api";
import datasetsQueryCodec, { type DatasetsSearch } from "./query-codec";
import styles from "./styles.module.css";

function nextIdempotencyKey(): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `dataset-create-${Date.now()}-${Math.random().toString(36).slice(2)}`
  );
}

function hasFilters(search: DatasetsSearch): boolean {
  return Boolean(
    search.collectionTaskId ||
      search.q ||
      search.robotModelId ||
      search.robotId ||
      search.task ||
      search.scene ||
      search.assetState ||
      search.workflowState ||
      search.storageClass ||
      search.datasetCreatedFrom ||
      search.datasetCreatedTo,
  );
}

function stateFromError(error: unknown): PageStateKind {
  if (!isDomainError(error)) return "error";
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

function datasetCountLabel(
  value: string | undefined,
  state: MetricState,
): string {
  if (state === "loading") return "数据集总数加载中";
  if (state === "forbidden") return "无权查看数据集总数";
  if (state === "error") return "数据集总数暂不可用";
  if (state === "unknown") return "数据集总数未知";
  return value === undefined ? "数据集总数未知" : `共 ${value} 个数据集`;
}

const packageStatePresentation: Readonly<
  Record<
    CollectionTaskPackage["state"],
    { readonly color: string; readonly label: string; readonly description: string }
  >
> = {
  PENDING_QC: { color: "default", label: "等待质检", description: "尚未产生质检结果" },
  PROCESSING: { color: "processing", label: "处理中", description: "正在对齐、入库或发布" },
  PUBLISHED: { color: "success", label: "可视化", description: "已发布为当前任务数据集的 Episode" },
  QUALITY_RISK: { color: "warning", label: "质量风险", description: "需确认风险后才能继续发布" },
  QUALITY_REJECTED: { color: "error", label: "质检拒绝", description: "未通过质量门禁" },
  TECHNICAL_FAILED: { color: "error", label: "技术失败", description: "处理工作流失败，可修复后重试" },
  DATASET_MISMATCH: { color: "warning", label: "数据集不匹配", description: "已发布到非本任务指定的数据集" },
};

function TaskPackageList({
  items,
  onOpenViewer,
}: Readonly<{
  items: readonly CollectionTaskPackage[];
  onOpenViewer: (item: CollectionTaskPackage) => void;
}>) {
  return (
    <section className={styles.taskPackageSection} aria-labelledby="task-package-heading">
      <div className={styles.taskPackageHeader}>
        <div>
          <Typography.Title id="task-package-heading" level={2}>数据包处理明细</Typography.Title>
          <Typography.Paragraph>
            共 {items.length} 个接收包；只有完成质检、处理并发布到本任务数据集的包可以打开 Viewer。
          </Typography.Paragraph>
        </div>
      </div>
      <div className={styles.taskPackageTable} role="table" aria-label="任务数据包处理明细">
        <div className={styles.taskPackageRow} role="row">
          <strong role="columnheader">数据包 / Rollout</strong>
          <strong role="columnheader">机器人</strong>
          <strong role="columnheader">处理状态</strong>
          <strong role="columnheader">结果</strong>
          <strong role="columnheader">操作</strong>
        </div>
        {items.map((item) => {
          const presentation = packageStatePresentation[item.state];
          return (
            <div className={styles.taskPackageRow} role="row" key={item.data_package_id}>
              <span role="cell" className={styles.taskPackageIdentity}>
                <code>{item.data_package_id}</code>
                <small>{item.rollout_id}</small>
              </span>
              <code role="cell">{item.robot_id}</code>
              <span role="cell">
                <Tag color={presentation.color}>{presentation.label}</Tag>
                <small>{presentation.description}</small>
              </span>
              <span role="cell" className={styles.taskPackageIdentity}>
                <small>QC：{item.qc_outcome ?? "待定"}</small>
                <small>
                  工作流：{item.workflow_stage ?? item.workflow_status ?? "待启动"}
                  {item.error_code ? ` · ${item.error_code}` : ""}
                </small>
              </span>
              <span role="cell">
                <Button
                  type="link"
                  disabled={
                    !item.visualizable ||
                    !item.dataset_id ||
                    !item.version_id ||
                    !item.episode_id
                  }
                  onClick={() => onOpenViewer(item)}
                >
                  {item.visualizable ? "打开 Viewer" : "暂不可视化"}
                </Button>
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

type EpisodeCursor = Readonly<{ after?: string; before?: string }>;

function TaskDatasetEpisodes({
  item,
  collectionTaskId,
  onOpenDataset,
  onInspect,
  onOpenViewer,
}: Readonly<{
  item: DatasetListItemVm & {
    currentVersion: NonNullable<DatasetListItemVm["currentVersion"]>;
  };
  collectionTaskId: string;
  onOpenDataset: (item: DatasetListItemVm) => void;
  onInspect: (item: DatasetListItemVm, episode: EpisodeListItemVm) => void;
  onOpenViewer: (item: DatasetListItemVm, episode: EpisodeListItemVm) => void;
}>) {
  const [cursor, setCursor] = useState<EpisodeCursor>({});
  const episodes = useVersionEpisodesQuery(
    item.datasetId,
    item.currentVersion.versionId,
    {
      collectionTaskId,
      ...cursor,
      limit: 50,
    },
  );

  const content = episodes.isPending ? (
    <PageState state="loading" label={`${item.name} 数据明细`} />
  ) : episodes.isError ? (
    <PageState
      state={stateFromError(episodes.error)}
      label={`${item.name} 数据明细`}
      requestId={requestId(episodes.error)}
      onRetry={() => void episodes.refetch()}
    />
  ) : episodes.data.items.length === 0 ? (
    <PageState
      state="empty"
      title="该数据集暂无此任务数据"
      description="数据集已关联到任务，但当前可浏览版本中尚无匹配的数据明细。"
    />
  ) : (
    <>
      <EpisodeTable
        items={episodes.data.items}
        onInspect={(episode) => onInspect(item, episode)}
        onOpenViewer={(episode) => onOpenViewer(item, episode)}
      />
      <DataCursorPager
        pageInfo={{
          startCursor: episodes.data.pageInfo.before,
          endCursor: episodes.data.pageInfo.after,
          hasPreviousPage: episodes.data.pageInfo.hasPreviousPage,
          hasNextPage: episodes.data.pageInfo.hasNextPage,
        }}
        busy={episodes.isFetching}
        windowLabel={`当前 ${episodes.data.items.length} 条 · 仅限任务 ${collectionTaskId}`}
        onChange={setCursor}
      />
    </>
  );

  return (
    <section
      className={styles.taskDatasetSection}
      aria-labelledby={`dataset-${item.datasetId}`}
    >
      <div className={styles.taskDatasetHeader}>
        <div>
          <Typography.Title id={`dataset-${item.datasetId}`} level={2}>
            {item.name}
          </Typography.Title>
          <Typography.Paragraph>
            <code>{item.datasetId}</code> · {item.currentVersion.displayVersion}{" "}
            · <code>{item.currentVersion.versionId}</code>
          </Typography.Paragraph>
        </div>
        <Button onClick={() => onOpenDataset(item)}>打开数据集详情</Button>
      </div>
      {content}
    </section>
  );
}

function hasCurrentVersion(
  item: DatasetListItemVm,
): item is DatasetListItemVm & {
  currentVersion: NonNullable<DatasetListItemVm["currentVersion"]>;
} {
  return item.currentVersion !== null;
}

function safeMutationError(error: unknown): string | null {
  if (!error) return null;
  if (!isDomainError(error))
    return "创建未完成；没有乐观创建 Dataset 或 Version。";
  const message =
    error.code === "VALIDATION_ERROR"
      ? "输入未通过服务端校验，请核对后重试。"
      : error.code === "FORBIDDEN" || error.code === "UNAUTHENTICATED"
        ? "当前授权不允许创建数据集。"
        : error.code === "VERSION_CONFLICT" ||
            error.code === "PRECONDITION_FAILED"
          ? "创建意图发生冲突，请关闭后重新发起。"
          : "创建未完成；没有乐观创建 Dataset 或 Version。";
  return error.requestId ? `${message} 请求 ID：${error.requestId}` : message;
}

function listState(
  query: {
    readonly isPending: boolean;
    readonly isError: boolean;
    readonly isFetching: boolean;
    readonly error: unknown;
    readonly data?: { readonly items: readonly unknown[] };
  },
  filtered: boolean,
): PageStateKind | "ready" {
  if (query.isPending) return "loading";
  if (query.isError) return stateFromError(query.error);
  if (query.data?.items.length === 0)
    return filtered ? "filtered-empty" : "empty";
  return query.isFetching ? "refreshing" : "ready";
}

export function DatasetsPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const search = useMemo(() => datasetsQueryCodec.parse(params), [params]);
  const canonicalSearch = useMemo(
    () => datasetsQueryCodec.canonicalize(params),
    [params],
  );
  const shouldCanonicalize =
    params.has("channels") ||
    params.has("channelMatch") ||
    (params.has("workflowState") && search.workflowState === undefined) ||
    (params.has("task") && params.get("task") !== (search.task ?? "")) ||
    (params.has("after") && params.has("before"));
  const rawCollectionTaskId = params.get("collectionTaskId");
  const collectionTaskParamInvalid =
    rawCollectionTaskId !== null && search.collectionTaskId === undefined;
  const collectionTaskId = search.collectionTaskId;
  const capabilities = useCapabilities();
  const shellScope = useShellStore((state) => state.scope);
  const unscopedAccount = useShellStore(
    (state) =>
      state.bootstrapLoaded &&
      !state.authorizationFailed &&
      state.scope === null,
  );
  const canRead = capabilities.has("dataset.read");
  const canReadEpisodes = capabilities.has("episode.read");
  const listEnabled =
    canRead &&
    (!collectionTaskId || canReadEpisodes) &&
    !collectionTaskParamInvalid;
  const ancillaryQueriesEnabled = listEnabled && !collectionTaskId;
  const query = useDatasetsQuery(search, listEnabled);
  const collectionTaskScope = useMemo<CollectionTaskScope | null>(
    () =>
      collectionTaskId && shellScope?.projectId && shellScope.regionCode
        ? {
            organizationId: shellScope.organizationId,
            projectId: shellScope.projectId,
            regionCode: shellScope.regionCode,
          }
        : null,
    [collectionTaskId, shellScope],
  );
  const packageQuery = useQuery({
    queryKey: ["collection-task-packages", collectionTaskId, collectionTaskScope],
    enabled: listEnabled && collectionTaskScope !== null && Boolean(collectionTaskId),
    queryFn: ({ signal }) =>
      collectionTaskGateway.packages(
        collectionTaskScope as CollectionTaskScope,
        collectionTaskId as string,
        signal,
      ),
    staleTime: 5_000,
    refetchInterval: (current) =>
      current.state.data?.items.some(
        (item) => item.state === "PENDING_QC" || item.state === "PROCESSING",
      )
        ? 5_000
        : false,
  });
  const summary = useDatasetSummaryQuery(search, ancillaryQueriesEnabled);
  const facets = useDatasetFacetsQuery(search, ancillaryQueriesEnabled);
  const pageCapabilities = useDatasetsPageCapabilitiesQuery(
    ancillaryQueriesEnabled,
  );
  const createMutation = useCreateDatasetMutation();
  const [createOpen, setCreateOpen] = useState(false);
  const [createIntentKey, setCreateIntentKey] = useState<string | null>(null);
  const [createdDatasetId, setCreatedDatasetId] = useState<DatasetId | null>(
    null,
  );
  const [selectedDatasetId, setSelectedDatasetId] = useState<DatasetId | null>(
    null,
  );
  const summaryPaneRef = useRef<HTMLElement>(null);

  const selectDataset = useCallback((datasetId: DatasetId) => {
    setSelectedDatasetId(datasetId);
    if (!window.matchMedia("(max-width: 1220px)").matches) return;
    const behavior = window.matchMedia("(prefers-reduced-motion: reduce)")
      .matches
      ? "auto"
      : "smooth";
    window.requestAnimationFrame(() => {
      summaryPaneRef.current?.scrollIntoView({ behavior, block: "start" });
    });
  }, []);

  useEffect(() => {
    if (!shouldCanonicalize || params.toString() === canonicalSearch) return;
    void navigate(
      canonicalSearch ? `/datasets?${canonicalSearch}` : "/datasets",
      {
        replace: true,
      },
    );
  }, [canonicalSearch, navigate, params, shouldCanonicalize]);

  const change = useCallback(
    (changes: Partial<DatasetsSearch>) => {
      const next = datasetsQueryCodec.withChanges(search, changes);
      void navigate(routes.datasets.build(next), { replace: true });
    },
    [navigate, search],
  );

  const openCreate = () => {
    createMutation.reset();
    setCreateIntentKey(nextIdempotencyKey());
    setCreateOpen(true);
  };

  const closeCreate = () => {
    if (createMutation.isPending) return;
    setCreateOpen(false);
    setCreateIntentKey(null);
  };

  const submitCreate = (draft: CreateDatasetDraft) => {
    if (!createIntentKey) return;
    createMutation.mutate(
      {
        idempotencyKey: createIntentKey,
        command: {
          name: draft.name,
          folder_path: [...draft.folderPath],
          description: draft.description,
          labels: [...draft.labels],
        },
      },
      {
        onSuccess: (created) => {
          setCreateOpen(false);
          setCreateIntentKey(null);
          setCreatedDatasetId(created.dataset_id as DatasetId);
          void navigate(
            routes.datasets.build({
              ...search,
              after: undefined,
              before: undefined,
            }),
            {
              replace: true,
            },
          );
        },
      },
    );
  };

  const canCreate =
    !capabilities.loading &&
    !capabilities.failed &&
    capabilities.has("dataset.create") &&
    !pageCapabilities.isFetching &&
    pageCapabilities.data?.allowedActions.includes("CREATE_DATASET") === true;

  const openDataset = useCallback(
    (datasetId: DatasetId) => {
      void navigate(
        routes.datasetDetail.build({
          datasetId,
          ...(collectionTaskId
            ? {
                tab: "episodes" as const,
                collectionTaskId,
                returnTo: "/collection-tasks",
              }
            : {}),
        }),
      );
    },
    [collectionTaskId, navigate],
  );
  const openEpisodes = useCallback(
    (item: DatasetListItemVm) => {
      if (!item.currentVersion) return;
      void navigate(
        routes.datasetDetail.build({
          datasetId: item.datasetId,
          tab: "episodes",
          versionId: item.currentVersion.versionId,
          collectionTaskId,
          ...(collectionTaskId
            ? { returnTo: routes.datasets.build({ collectionTaskId }) }
            : {}),
        }),
      );
    },
    [collectionTaskId, navigate],
  );
  const inspectTaskEpisode = useCallback(
    (item: DatasetListItemVm, episode: EpisodeListItemVm) => {
      if (!collectionTaskId) return;
      void navigate(
        routes.datasetDetail.build({
          datasetId: item.datasetId,
          tab: "episodes",
          versionId: episode.versionId,
          collectionTaskId,
          episodeId: episode.episodeId,
          returnTo: routes.datasets.build({ collectionTaskId }),
        }),
      );
    },
    [collectionTaskId, navigate],
  );
  const openTaskEpisodeViewer = useCallback(
    (item: DatasetListItemVm, episode: EpisodeListItemVm) => {
      if (!collectionTaskId) return;
      const detailUrl = routes.datasetDetail.build({
        datasetId: item.datasetId,
        tab: "episodes",
        versionId: episode.versionId,
        collectionTaskId,
        episodeId: episode.episodeId,
        returnTo: routes.datasets.build({ collectionTaskId }),
      });
      void navigate(
        routes.episodeViewer.build({
          datasetId: item.datasetId,
          versionId: episode.versionId,
          episodeId: episode.episodeId,
          returnTo: detailUrl,
        }),
      );
    },
    [collectionTaskId, navigate],
  );
  const openTaskPackageViewer = useCallback(
    (item: CollectionTaskPackage) => {
      if (
        !collectionTaskId ||
        !item.visualizable ||
        !item.dataset_id ||
        !item.version_id ||
        !item.episode_id
      ) return;
      void navigate(
        routes.episodeViewer.build({
          datasetId: item.dataset_id as DatasetId,
          versionId: item.version_id as DatasetVersionId,
          episodeId: item.episode_id as EpisodeId,
          returnTo: routes.datasets.build({ collectionTaskId }),
        }),
      );
    },
    [collectionTaskId, navigate],
  );

  const summaryState: MetricState = unscopedAccount
    ? "ready"
    : capabilities.loading
      ? "loading"
      : capabilities.failed || !canRead
        ? "forbidden"
        : summary.isPending
          ? "loading"
          : summary.isError
            ? "error"
            : summary.data
              ? "ready"
              : "unknown";
  const resolvedListState: PageStateKind | "ready" = collectionTaskParamInvalid
    ? "not-found"
    : unscopedAccount
      ? "ready"
      : capabilities.loading
        ? "loading"
        : capabilities.failed ||
            !canRead ||
            Boolean(collectionTaskId && !canReadEpisodes)
          ? "forbidden"
          : listState(query, hasFilters(search));
  const selectedDataset =
    query.data?.items.find((item) => item.datasetId === selectedDatasetId) ??
    query.data?.items[0] ??
    null;
  const selectedDatasetPosition = selectedDataset
    ? (query.data?.items.findIndex(
        (item) => item.datasetId === selectedDataset.datasetId,
      ) ?? 0) + 1
    : 0;
  const table = (
    <div className={styles.datasetWorkspace}>
      <div className={styles.datasetListPanel}>
        <DatasetTable
          items={query.data?.items ?? []}
          selectedDatasetId={selectedDataset?.datasetId ?? null}
          canReadEpisodes={capabilities.has("episode.read")}
          onSelect={selectDataset}
          onOpen={openDataset}
          onOpenEpisodes={openEpisodes}
        />
      </div>
      {selectedDataset ? (
        <aside
          ref={summaryPaneRef}
          className={styles.datasetSummaryPane}
          aria-label="数据集摘要面板"
        >
          <SelectedDatasetSummary
            item={selectedDataset}
            position={selectedDatasetPosition}
            total={query.data?.items.length ?? 0}
            canReadEpisodes={capabilities.has("episode.read")}
            onOpen={openDataset}
            onOpenEpisodes={openEpisodes}
          />
        </aside>
      ) : null}
    </div>
  );
  const taskDatasets = query.data?.items.filter(hasCurrentVersion) ?? [];
  const taskPackages = packageQuery.data?.items ?? [];
  const visualizablePackageCount = taskPackages.filter(
    (item) => item.visualizable,
  ).length;
  const taskTable = collectionTaskId ? (
    <div className={styles.taskDatasetWorkspace}>
      <Alert
        type="info"
        showIcon
        title={
          packageQuery.isPending
            ? "正在读取任务数据包明细"
            : `任务接收 ${taskPackages.length} 个数据包，其中 ${visualizablePackageCount} 个已可视化`
        }
        description={`下方先列出全部接收包及不能可视化的原因，再按 ${taskDatasets.length} 个指定数据集展示已发布 Episode。`}
      />
      {packageQuery.isError ? (
        <Alert
          type="warning"
          showIcon
          title="数据包明细加载失败"
          description="已发布 Episode 仍可浏览；可重试读取 14 个原始接收包的处理状态。"
          action={<Button onClick={() => void packageQuery.refetch()}>重试</Button>}
        />
      ) : taskPackages.length > 0 ? (
        <TaskPackageList items={taskPackages} onOpenViewer={openTaskPackageViewer} />
      ) : null}
      {taskDatasets.map((item) => (
        <TaskDatasetEpisodes
          key={item.datasetId}
          item={item}
          collectionTaskId={collectionTaskId}
          onOpenDataset={openEpisodes}
          onInspect={inspectTaskEpisode}
          onOpenViewer={openTaskEpisodeViewer}
        />
      ))}
      {query.isSuccess && taskDatasets.length === 0 ? (
        <PageState
          state="empty"
          title="该任务尚无已发布 Episode"
          description="上方仍会保留全部接收包；待质检和处理完成后，可视化入口会自动出现。"
        />
      ) : null}
    </div>
  ) : null;
  const listContent = collectionTaskParamInvalid ? (
    <PageState
      state="not-found"
      title="采集任务参数无效"
      description="collectionTaskId 必须是稳定、不可变的采集任务 ID。"
      action={
        <Button onClick={() => void navigate("/collection-tasks")}>
          返回采集任务
        </Button>
      }
    />
  ) : collectionTaskId && query.isSuccess ? (
    taskTable
  ) : resolvedListState === "ready" ? (
    collectionTaskId ? (
      taskTable
    ) : (
      table
    )
  ) : resolvedListState === "refreshing" ? (
    <PageState state="refreshing" label="数据集列表">
      {collectionTaskId ? taskTable : table}
    </PageState>
  ) : (
    <PageState
      state={resolvedListState}
      label="数据集列表"
      requestId={requestId(query.error)}
      onRetry={query.isError ? () => void query.refetch() : undefined}
      action={
        resolvedListState === "filtered-empty" ? (
          <Button
            onClick={() =>
              void navigate(routes.datasets.build({}), { replace: true })
            }
          >
            清除筛选
          </Button>
        ) : undefined
      }
    />
  );

  return (
    <main className={styles.page} data-page-id="P05">
      <StandardPageScaffold
        header={{
          title: collectionTaskId ? "采集任务数据" : "数据集",
          breadcrumbs: [
            { key: "assets", label: "数据资产", to: routes.datasets.build() },
            { key: "datasets", label: "数据集" },
          ],
          actions: collectionTaskId ? undefined : (
            <Button
              type="primary"
              icon={<Plus aria-hidden="true" size={16} />}
              disabled={!canCreate}
              onClick={openCreate}
            >
              创建数据集
            </Button>
          ),
        }}
        summary={undefined}
        filters={
          collectionTaskId ? undefined : (
            <DatasetFilterPanel
              search={search}
              facets={facets.data}
              disabled={
                capabilities.loading ||
                capabilities.failed ||
                (!canRead && !unscopedAccount)
              }
              busy={query.isFetching || summary.isFetching || facets.isFetching}
              onApply={change}
              onReset={() =>
                void navigate(
                  routes.datasets.build({
                    sort: search.sort,
                    limit: search.limit,
                  }),
                  { replace: true },
                )
              }
            />
          )
        }
        state={
          <div className={styles.contentStack}>
            {!collectionTaskId ? (
              <div className={styles.resultsHeading}>
                <Typography.Title level={2}>数据集列表</Typography.Title>
                <span className={styles.datasetCount} aria-live="polite">
                  {unscopedAccount
                    ? "共 0 个数据集"
                    : datasetCountLabel(
                        summary.data?.datasetCount,
                        summaryState,
                      )}
                </span>
              </div>
            ) : null}
            {!collectionTaskId && pageCapabilities.isError ? (
              <Alert
                type="warning"
                showIcon
                title="页面操作不可用"
                description="列表仍保持只读；创建和批量动作已关闭。"
                action={
                  <Button onClick={() => void pageCapabilities.refetch()}>
                    重试
                  </Button>
                }
              />
            ) : null}
            {!collectionTaskId && facets.isError ? (
              <Alert
                type="warning"
                showIcon
                title="筛选项加载失败"
                description="可以继续使用 URL 中已有筛选或重试此区域。"
                action={
                  <Button onClick={() => void facets.refetch()}>重试</Button>
                }
              />
            ) : null}
            {!collectionTaskId && summary.isError ? (
              <Alert
                type="warning"
                showIcon
                title="数据集总数加载失败"
                description="列表仍可独立使用；总数未使用猜测值。"
                action={
                  <Button onClick={() => void summary.refetch()}>重试</Button>
                }
              />
            ) : null}
            {createdDatasetId ? (
              <Alert
                type="success"
                showIcon
                title="数据集已创建"
                description={
                  <span>
                    <code>{createdDatasetId}</code> 是空 Dataset；没有隐式创建
                    Version。
                  </span>
                }
                action={
                  <Button onClick={() => openDataset(createdDatasetId)}>
                    查看数据集
                  </Button>
                }
              />
            ) : null}
            {listContent}
          </div>
        }
        pagination={
          query.data && query.data.items.length > 0 ? (
            <DataCursorPager
              pageInfo={{
                startCursor: query.data.pageInfo.before,
                endCursor: query.data.pageInfo.after,
                hasPreviousPage: query.data.pageInfo.hasPreviousPage,
                hasNextPage: query.data.pageInfo.hasNextPage,
              }}
              busy={query.isFetching}
              windowLabel={`当前窗口 ${query.data.items.length} 条`}
              onChange={(cursor) => change(cursor)}
            />
          ) : undefined
        }
      />

      <CreateDatasetDialog
        open={createOpen}
        pending={createMutation.isPending}
        errorMessage={safeMutationError(createMutation.error)}
        onSubmit={submitCreate}
        onCancel={closeCreate}
      />
    </main>
  );
}

export default DatasetsPage;
