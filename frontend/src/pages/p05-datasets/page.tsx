import { Alert, Button } from 'antd';
import { Plus } from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import type { DatasetId } from '../../entities/dataset';
import {
  useCreateDatasetMutation,
  useDatasetFacetsQuery,
  useDatasetsPageCapabilitiesQuery,
  useDatasetSummaryQuery,
  useDatasetsQuery,
  type DatasetListItemVm,
} from '../../features/datasets/api';
import { routes } from '../../features/datasets/routing';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import {
  DataCursorPager,
  PageState,
  StandardPageScaffold,
  type MetricState,
  type PageStateKind,
} from '../../shared/ui';
import { CreateDatasetDialog, type CreateDatasetDraft } from './components/CreateDatasetDialog';
import { DatasetFilterPanel } from './components/DatasetFilterPanel';
import { DatasetSummaryStrip } from './components/DatasetSummaryStrip';
import { DatasetTable } from './components/DatasetTable';
import { SelectedDatasetSummary } from './components/SelectedDatasetSummary';
import datasetsQueryCodec, { type DatasetsSearch } from './query-codec';
import styles from './styles.module.css';

function nextIdempotencyKey(): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `dataset-create-${Date.now()}-${Math.random().toString(36).slice(2)}`
  );
}

function hasFilters(search: DatasetsSearch): boolean {
  return Boolean(
    search.q ||
      search.robotModelId ||
      search.robotId ||
      search.task ||
      search.scene ||
      search.assetState ||
      search.storageClass ||
      search.channels?.length ||
      search.datasetCreatedFrom ||
      search.datasetCreatedTo,
  );
}

function stateFromError(error: unknown): PageStateKind {
  if (!isDomainError(error)) return 'error';
  switch (error.code) {
    case 'FORBIDDEN':
    case 'UNAUTHENTICATED':
      return 'forbidden';
    case 'NOT_FOUND':
      return 'not-found';
    case 'GONE':
      return 'gone';
    case 'VERSION_CONFLICT':
    case 'PRECONDITION_FAILED':
      return 'conflict';
    case 'RATE_LIMITED':
      return 'rate-limited';
    case 'NETWORK_ERROR':
      return 'offline';
    case 'CONTRACT_MISMATCH':
      return 'contract-mismatch';
    default:
      return 'error';
  }
}

function requestId(error: unknown): string | null {
  return isDomainError(error) ? error.requestId : null;
}

function safeMutationError(error: unknown): string | null {
  if (!error) return null;
  if (!isDomainError(error)) return '创建未完成；没有乐观创建 Dataset 或 Version。';
  const message =
    error.code === 'VALIDATION_ERROR'
      ? '输入未通过服务端校验，请核对后重试。'
      : error.code === 'FORBIDDEN' || error.code === 'UNAUTHENTICATED'
        ? '当前授权不允许创建数据集。'
        : error.code === 'VERSION_CONFLICT' || error.code === 'PRECONDITION_FAILED'
          ? '创建意图发生冲突，请关闭后重新发起。'
          : '创建未完成；没有乐观创建 Dataset 或 Version。';
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
): PageStateKind | 'ready' {
  if (query.isPending) return 'loading';
  if (query.isError) return stateFromError(query.error);
  if (query.data?.items.length === 0) return filtered ? 'filtered-empty' : 'empty';
  return query.isFetching ? 'refreshing' : 'ready';
}

export function DatasetsPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const search = useMemo(() => datasetsQueryCodec.parse(params), [params]);
  const capabilities = useCapabilities();
  const canRead = capabilities.has('dataset.read');
  const query = useDatasetsQuery(search, canRead);
  const summary = useDatasetSummaryQuery(search, canRead);
  const facets = useDatasetFacetsQuery(search, canRead);
  const pageCapabilities = useDatasetsPageCapabilitiesQuery(canRead);
  const createMutation = useCreateDatasetMutation();
  const [createOpen, setCreateOpen] = useState(false);
  const [createIntentKey, setCreateIntentKey] = useState<string | null>(null);
  const [createdDatasetId, setCreatedDatasetId] = useState<DatasetId | null>(null);
  const [selectedDatasetId, setSelectedDatasetId] = useState<DatasetId | null>(null);

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
          description: draft.description,
          labels: [...draft.labels],
        },
      },
      {
        onSuccess: (created) => {
          setCreateOpen(false);
          setCreateIntentKey(null);
          setCreatedDatasetId(created.dataset_id as DatasetId);
          void navigate(routes.datasets.build({ ...search, after: undefined, before: undefined }), {
            replace: true,
          });
        },
      },
    );
  };

  const canCreate =
    !capabilities.loading &&
    !capabilities.failed &&
    capabilities.has('dataset.create') &&
    !pageCapabilities.isFetching &&
    pageCapabilities.data?.allowedActions.includes('CREATE_DATASET') === true;

  const openDataset = useCallback(
    (datasetId: DatasetId) => {
      void navigate(routes.datasetDetail.build({ datasetId }));
    },
    [navigate],
  );
  const openEpisodes = useCallback(
    (item: DatasetListItemVm) => {
      if (!item.currentVersion) return;
      void navigate(
        routes.datasetDetail.build({
          datasetId: item.datasetId,
          tab: 'episodes',
          versionId: item.currentVersion.versionId,
        }),
      );
    },
    [navigate],
  );

  const summaryState: MetricState = capabilities.loading
    ? 'loading'
    : capabilities.failed || !canRead
      ? 'forbidden'
      : summary.isPending
        ? 'loading'
        : summary.isError
          ? 'error'
          : summary.data
            ? 'ready'
            : 'unknown';
  const resolvedListState: PageStateKind | 'ready' = capabilities.loading
    ? 'loading'
    : capabilities.failed || !canRead
      ? 'forbidden'
      : listState(query, hasFilters(search));
  const selectedDataset =
    query.data?.items.find((item) => item.datasetId === selectedDatasetId) ??
    query.data?.items[0] ??
    null;
  const selectedDatasetPosition = selectedDataset
    ? (query.data?.items.findIndex((item) => item.datasetId === selectedDataset.datasetId) ?? 0) + 1
    : 0;
  const table =
    query.data && selectedDataset ? (
      <div className={styles.datasetWorkspace}>
        <DatasetTable
          page={query.data}
          selectedDatasetId={selectedDataset.datasetId}
          canReadEpisodes={capabilities.has('episode.read')}
          onSelect={setSelectedDatasetId}
          onOpen={openDataset}
          onOpenEpisodes={openEpisodes}
        />
        <SelectedDatasetSummary
          item={selectedDataset}
          position={selectedDatasetPosition}
          total={query.data.items.length}
          canReadEpisodes={capabilities.has('episode.read')}
          onOpen={openDataset}
          onOpenEpisodes={openEpisodes}
        />
      </div>
    ) : null;
  const listContent =
    resolvedListState === 'ready' ? (
      table
    ) : resolvedListState === 'refreshing' ? (
      <PageState state="refreshing" label="数据集列表">
        {table}
      </PageState>
    ) : (
      <PageState
        state={resolvedListState}
        label="数据集列表"
        requestId={requestId(query.error)}
        onRetry={query.isError ? () => void query.refetch() : undefined}
        action={
          resolvedListState === 'filtered-empty' ? (
            <Button onClick={() => void navigate(routes.datasets.build({}), { replace: true })}>
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
          title: '数据集',
          description: '服务端筛选、稳定排序与游标分页；筛选变化会回到首个游标窗口。',
          breadcrumbs: [
            { key: 'assets', label: '数据资产', to: routes.datasets.build() },
            { key: 'datasets', label: '数据集' },
          ],
          actions: (
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
          <div className={styles.toolbarStack}>
            <DatasetFilterPanel
              search={search}
              facets={facets.data}
              disabled={capabilities.loading || capabilities.failed || !canRead}
              onApply={change}
              onReset={() => void navigate(routes.datasets.build({}), { replace: true })}
            />
            <section className={styles.visualSummary} aria-label="页面摘要">
              <DatasetSummaryStrip summary={summary.data} state={summaryState} />
            </section>
          </div>
        }
        state={
          <div className={styles.contentStack}>
            {pageCapabilities.isError ? (
              <Alert
                type="warning"
                showIcon
                title="页面操作不可用"
                description="列表仍保持只读；创建和批量动作已关闭。"
                action={<Button onClick={() => void pageCapabilities.refetch()}>重试</Button>}
              />
            ) : null}
            {facets.isError ? (
              <Alert
                type="warning"
                showIcon
                title="筛选项加载失败"
                description="可以继续使用 URL 中已有筛选或重试此区域。"
                action={<Button onClick={() => void facets.refetch()}>重试</Button>}
              />
            ) : null}
            {summary.isError ? (
              <Alert
                type="warning"
                showIcon
                title="摘要区域加载失败"
                description="列表仍可独立使用；摘要未使用猜测值。"
                action={<Button onClick={() => void summary.refetch()}>重试</Button>}
              />
            ) : null}
            {createdDatasetId ? (
              <Alert
                type="success"
                showIcon
                title="数据集已创建"
                description={
                  <span>
                    <code>{createdDatasetId}</code> 是空 Dataset；没有隐式创建 Version。
                  </span>
                }
                action={<Button onClick={() => openDataset(createdDatasetId)}>查看数据集</Button>}
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
              windowLabel={`当前窗口 ${query.data.items.length} 条 · 快照 ${query.data.snapshotAt}`}
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
