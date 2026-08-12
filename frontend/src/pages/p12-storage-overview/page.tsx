import { Alert, Button } from 'antd';
import { useCallback, useEffect, useMemo, useRef, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  useStorageCost,
  useStorageInventory,
  useStorageMultipart,
  useStorageObject,
  useStorageOverview,
} from '../../features/storage-overview/api/queries';
import {
  patchStorageOverviewSearch,
  type StorageOverviewSearch,
  type StorageOverviewTab,
} from '../../features/storage-overview/routing';
import type { StorageScope } from '../../features/storage-overview/types';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { useShellStore } from '../../shared/scope/shell-store';
import {
  DataCursorPager,
  DetailTabs,
  PageState,
  StandardPageScaffold,
  StatusTag,
  type MetricState,
  type PageStateKind,
} from '../../shared/ui';
import { StorageCostPanel } from './components/StorageCostPanel';
import { StorageInventoryTable } from './components/StorageInventoryTable';
import { StorageMultipartTable } from './components/StorageMultipartTable';
import { StorageObjectDrawer } from './components/StorageObjectDrawer';
import { StorageOverviewPanel } from './components/StorageOverviewPanel';
import { StorageSummaryStrip } from './components/StorageSummaryStrip';
import { storageOverviewQueryCodec } from './query-codec';
import styles from './styles.module.css';

function stateFromError(error: unknown): PageStateKind {
  if (!isDomainError(error)) return 'contract-mismatch';
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

function storageStateTitle(state: PageStateKind): string | undefined {
  return state === 'contract-mismatch' ? '存储响应不符合合同' : undefined;
}

function hasInventoryFilters(search: StorageOverviewSearch): boolean {
  return Boolean(
    search.objectRole ||
      search.storageClass ||
      search.anomaly.length ||
      search.status.length,
  );
}

function queryState(
  query: {
    readonly isPending: boolean;
    readonly isFetching: boolean;
    readonly isError: boolean;
    readonly error: unknown;
    readonly data?: { readonly items: readonly unknown[] };
  },
  filtered: boolean,
): PageStateKind | 'ready' {
  if (query.isPending) return 'loading';
  if (query.isError) return stateFromError(query.error);
  if (!query.data) return 'error';
  if (query.data.items.length === 0) return filtered ? 'filtered-empty' : 'empty';
  return query.isFetching ? 'refreshing' : 'ready';
}

function regionState(
  state: PageStateKind | 'ready',
  label: string,
  error: unknown,
  content: ReactNode,
  onRetry: () => void,
): ReactNode {
  if (state === 'ready') return content;
  if (state === 'refreshing') {
    return (
      <PageState state="refreshing" label={label}>
        {content}
      </PageState>
    );
  }
  return (
    <PageState
      state={state}
      label={label}
      title={storageStateTitle(state)}
      requestId={requestId(error)}
      onRetry={state === 'empty' || state === 'filtered-empty' ? undefined : onRetry}
    />
  );
}

export function StorageOverviewPage() {
  const shellScope = useShellStore((state) => state.scope);
  const scopeKey = useShellStore((state) => state.scopeKey);
  const scope: StorageScope | null = shellScope?.projectId && shellScope.regionCode
    ? {
        organizationId: shellScope.organizationId,
        projectId: shellScope.projectId,
        regionCode: shellScope.regionCode,
      }
    : null;
  const capabilities = useCapabilities();
  const [params, setParams] = useSearchParams();
  const parsedSearch = useMemo(() => storageOverviewQueryCodec.parse(params), [params]);
  const previousScopeKey = useRef(scopeKey);
  const scopeChanged = previousScopeKey.current !== scopeKey;
  const search: StorageOverviewSearch = useMemo(
    () =>
      scopeChanged
        ? { ...parsedSearch, after: undefined, before: undefined, objectId: undefined }
        : parsedSearch,
    [parsedSearch, scopeChanged],
  );
  const canRead =
    !capabilities.loading &&
    !capabilities.failed &&
    capabilities.has('storage.overview.read');
  const canReadObjects = capabilities.has('storage.object.read');
  const canReadMultipart = capabilities.has('storage.multipart.read');
  const canReadCost = capabilities.has('storage.cost.read');
  const overview = useStorageOverview(scope, search.months, canRead);
  const inventory = useStorageInventory(
    scope,
    search,
    canRead && canReadObjects && search.tab === 'objects',
  );
  const multipart = useStorageMultipart(
    scope,
    search,
    canRead && canReadMultipart && search.tab === 'multipart',
  );
  const objectDetail = useStorageObject(
    scope,
    search.objectId,
    inventory.data?.snapshotId ?? overview.data?.snapshotId,
    canRead && canReadObjects && search.tab === 'objects',
  );
  const billingPeriod =
    overview.data?.totals.monthlyCost.state === 'KNOWN'
      ? overview.data.totals.monthlyCost.value.billingPeriod
      : undefined;
  const cost = useStorageCost(
    scope,
    billingPeriod,
    canRead && canReadCost && search.tab === 'cost',
  );

  const change = useCallback(
    (patch: Partial<StorageOverviewSearch>) => {
      setParams(storageOverviewQueryCodec.build(patchStorageOverviewSearch(search, patch)));
    },
    [search, setParams],
  );

  useEffect(() => {
    if (search.tab === 'cost' && !capabilities.loading && !canReadCost) {
      change({ tab: 'overview' });
    }
    if (search.tab === 'multipart' && !capabilities.loading && !canReadMultipart) {
      change({ tab: 'overview' });
    }
  }, [canReadCost, canReadMultipart, capabilities.loading, change, search.tab]);

  useEffect(() => {
    if (previousScopeKey.current === scopeKey) return;
    previousScopeKey.current = scopeKey;
    if (parsedSearch.after || parsedSearch.before || parsedSearch.objectId) {
      setParams(
        storageOverviewQueryCodec.build({
          ...parsedSearch,
          after: undefined,
          before: undefined,
          objectId: undefined,
        }),
        { replace: true },
      );
    }
  }, [parsedSearch, scopeKey, setParams]);

  const pageState: PageStateKind | 'ready' = capabilities.loading
    ? 'loading'
    : capabilities.failed || !canRead
      ? 'forbidden'
      : !scope
        ? 'feature-unavailable'
        : overview.isPending
          ? 'loading'
          : overview.isError
            ? stateFromError(overview.error)
            : overview.data
              ? 'ready'
              : 'error';
  const summaryState: MetricState = capabilities.loading || overview.isPending
    ? 'loading'
    : capabilities.failed || !canRead
      ? 'forbidden'
      : overview.isError
        ? 'error'
        : overview.data
          ? 'ready'
          : 'unknown';

  const setTab = (tabId: string) => {
    if (tabId === 'overview' || tabId === 'objects' || tabId === 'multipart' || tabId === 'cost') {
      change({ tab: tabId as StorageOverviewTab, objectId: undefined });
    }
  };

  let activeContent: ReactNode = null;
  if (pageState === 'ready' && overview.data) {
    if (search.tab === 'overview') {
      activeContent = <StorageOverviewPanel overview={overview.data} />;
    }

    if (search.tab === 'objects') {
      if (!canReadObjects) {
        activeContent = <PageState state="feature-unavailable" label="Inventory 对象" />;
      } else {
        const state = queryState(inventory, hasInventoryFilters(search));
        const table = inventory.data ? (
          <div className={styles.contentStack}>
            {inventory.data.hasUnknownEnum ? (
              <PageState state="unknown" label="Inventory 未知枚举" />
            ) : null}
            <StorageInventoryTable
              items={inventory.data.items}
              onOpen={(objectId) => change({ objectId })}
            />
          </div>
        ) : null;
        activeContent = regionState(
          state,
          'Inventory 对象表',
          inventory.error,
          table,
          () => void inventory.refetch(),
        );
      }
    }

    if (search.tab === 'multipart') {
      if (!canReadMultipart) {
        activeContent = <PageState state="feature-unavailable" label="Multipart 诊断" />;
      } else {
        const state = queryState(multipart, search.status.length > 0);
        const table = multipart.data ? (
          <div className={styles.contentStack}>
            {multipart.data.hasUnknownEnum ? (
              <PageState state="unknown" label="Multipart 未知枚举" />
            ) : null}
            <StorageMultipartTable items={multipart.data.items} />
          </div>
        ) : null;
        activeContent = regionState(
          state,
          'Multipart 诊断表',
          multipart.error,
          table,
          () => void multipart.refetch(),
        );
      }
    }

    if (search.tab === 'cost') {
      if (!canReadCost) {
        activeContent = <PageState state="feature-unavailable" label="存储费用" />;
      } else if (cost.isPending) {
        activeContent = <PageState state="loading" label="存储费用" />;
      } else if (cost.isError) {
        const state = stateFromError(cost.error);
        activeContent = (
          <PageState
            state={state}
            label="存储费用"
            title={storageStateTitle(state)}
            requestId={requestId(cost.error)}
            onRetry={() => void cost.refetch()}
          />
        );
      } else if (cost.data) {
        activeContent = cost.isFetching ? (
          <PageState state="refreshing" label="存储费用">
            <StorageCostPanel cost={cost.data} />
          </PageState>
        ) : (
          <StorageCostPanel cost={cost.data} />
        );
      } else {
        activeContent = <PageState state="error" label="存储费用" />;
      }
    }
  }

  const blockingPageState: PageStateKind = pageState === 'ready' ? 'error' : pageState;
  const pageContent = pageState === 'ready' && overview.data ? (
    <div className={styles.contentStack}>
      {overview.data.hasUnknownEnum ? (
        <PageState state="unknown" label="存储未知枚举" />
      ) : null}
      {overview.data.partialErrors.map((error) => (
        <Alert
          key={`${error.section}-${error.requestId}`}
          type="warning"
          showIcon
          title="部分存储区域暂不可用"
          description={`区域：${error.section}；请求 ID：${error.requestId}`}
          action={<Button onClick={() => void overview.refetch()}>重试概览</Button>}
        />
      ))}
      <section
        className={styles.tabPanel}
        id={`tabpanel-${search.tab}`}
        role="tabpanel"
        aria-labelledby={`tab-${search.tab}`}
      >
        {activeContent}
      </section>
    </div>
  ) : (
    <PageState
      state={blockingPageState}
      label="存储概览"
      title={storageStateTitle(blockingPageState)}
      requestId={requestId(overview.error)}
      onRetry={overview.isError ? () => void overview.refetch() : undefined}
    />
  );

  const activePage = search.tab === 'objects' ? inventory.data : search.tab === 'multipart' ? multipart.data : undefined;

  const objectDrawerState: PageStateKind | 'ready' = objectDetail.isPending
    ? 'loading'
    : objectDetail.isError
      ? stateFromError(objectDetail.error)
      : objectDetail.data
        ? 'ready'
        : 'error';

  return (
    <main className={styles.page} data-page-id="P12">
      <StandardPageScaffold
        header={{
          title: '存储容量',
          description: '只读容量、费用、复用与 Inventory 事实；全页不提供存储写入口。',
          breadcrumbs: [
            { key: 'storage', label: '存储管理' },
            { key: 'overview', label: '存储容量' },
          ],
          metadata: overview.data ? (
            <span className={styles.snapshotMeta}>
              <span>快照：<code>{overview.data.snapshotId}</code></span>
              <span>截至：<time dateTime={overview.data.asOf}>{overview.data.asOf}</time></span>
              <StatusTag status={overview.data.freshness} label={overview.data.freshness} tone="success" />
            </span>
          ) : undefined,
        }}
        summary={<StorageSummaryStrip overview={overview.data} state={summaryState} />}
        filters={pageState === 'ready' ? (
          <DetailTabs
            label="存储容量页面标签"
            activeTab={search.tab}
            onChange={setTab}
            tabs={[
              { id: 'overview', label: '概览' },
              { id: 'objects', label: 'Inventory 对象', disabled: !canReadObjects },
              { id: 'multipart', label: 'Multipart 诊断', disabled: !canReadMultipart },
              { id: 'cost', label: '费用', disabled: !canReadCost },
            ]}
          />
        ) : undefined}
        state={pageContent}
        pagination={activePage && activePage.items.length > 0 ? (
          <DataCursorPager
            pageInfo={activePage.pageInfo}
            busy={search.tab === 'objects' ? inventory.isFetching : multipart.isFetching}
            windowLabel={`当前窗口 ${activePage.items.length} 条 · 快照 ${activePage.snapshotAt}`}
            onChange={(cursor) => change(cursor)}
          />
        ) : undefined}
      />

      <StorageObjectDrawer
        open={search.tab === 'objects' && Boolean(search.objectId)}
        state={objectDrawerState}
        object={objectDetail.data}
        requestId={requestId(objectDetail.error)}
        onRetry={objectDetail.isError ? () => void objectDetail.refetch() : undefined}
        onClose={() => change({ objectId: undefined })}
      />
    </main>
  );
}

export default StorageOverviewPage;
