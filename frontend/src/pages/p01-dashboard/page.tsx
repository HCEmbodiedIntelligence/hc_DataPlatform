import { Button, Select } from 'antd';
import { RefreshCw } from 'lucide-react';
import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  useDashboardActivity,
  useDashboardCoverage,
  useDashboardPending,
  useDashboardPendingPage,
  useDashboardSnapshot,
} from '../../features/dashboard/api/queries';
import {
  DASHBOARD_PROJECT_TIMEZONE_ASSUMPTION,
  type DashboardScope,
} from '../../features/dashboard/types';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { useShellStore } from '../../shared/scope/shell-store';
import {
  PageState,
  StandardPageScaffold,
  type MetricState,
  type PageStateKind,
} from '../../shared/ui';
import { DashboardCoverageTable } from './components/DashboardCoverageTable';
import { DataLifecycleRail } from './components/DataLifecycleRail';
import { DashboardPendingDrawer } from './components/DashboardPendingDrawer';
import { DashboardPendingList } from './components/DashboardPendingList';
import { DashboardSummaryStrip } from './components/DashboardSummaryStrip';
import { dashboardQueryCodec, type DashboardRange } from './query-codec';
import styles from './styles.module.css';

const DashboardCharts = lazy(() => import('../../features/dashboard/dashboard-charts'));

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

function stateTitle(state: PageStateKind): string | undefined {
  return state === 'contract-mismatch' ? '服务端数据与页面合同不一致' : undefined;
}

function queryState(query: Readonly<{
  data?: unknown;
  isPending: boolean;
  isFetching: boolean;
  error: unknown;
}>, empty = false): PageStateKind | 'ready' {
  if (query.isPending && query.data === undefined) return 'loading';
  if (query.error) return stateFromError(query.error);
  if (empty) return 'empty';
  if (query.isFetching && query.data !== undefined) return 'refreshing';
  return 'ready';
}

function metricState(query: Readonly<{
  data?: unknown;
  isPending: boolean;
  error: unknown;
}>): MetricState {
  if (query.isPending && query.data === undefined) return 'loading';
  if (query.error) return 'error';
  return query.data === undefined ? 'unknown' : 'ready';
}

function rangeWindow(
  range: DashboardRange,
  from: string | undefined,
  to: string | undefined,
  anchor: Date,
) {
  if (range === 'custom' && from && to) return { from, to };
  const hours = range === '7d' ? 7 * 24 : range === '30d' ? 30 * 24 : 24;
  return {
    from: new Date(anchor.getTime() - hours * 60 * 60 * 1_000).toISOString(),
    to: anchor.toISOString(),
  };
}

function renderRegion(
  state: PageStateKind | 'ready',
  label: string,
  error: unknown,
  content: ReactNode,
  onRetry: () => void,
): ReactNode {
  if (state === 'ready') return content;
  if (state === 'refreshing') {
    return <PageState state="refreshing" label={label}>{content}</PageState>;
  }
  return (
    <PageState
      state={state}
      label={label}
      title={stateTitle(state)}
      requestId={requestId(error)}
      onRetry={state === 'empty' ? undefined : onRetry}
    />
  );
}

export function DashboardPage() {
  const shellScope = useShellStore((state) => state.scope);
  const scopeKey = useShellStore((state) => state.scopeKey);
  const scope: DashboardScope | null = shellScope?.projectId && shellScope.regionCode
    ? {
        organizationId: shellScope.organizationId,
        projectId: shellScope.projectId,
        regionCode: shellScope.regionCode,
        timezone: DASHBOARD_PROJECT_TIMEZONE_ASSUMPTION,
      }
    : null;
  const capabilities = useCapabilities();
  const [searchParams, setSearchParams] = useSearchParams();
  const search = dashboardQueryCodec.parse(searchParams);
  const [anchor, setAnchor] = useState(
    () => new Date(Math.floor(Date.now() / 1_000) * 1_000),
  );
  const [coverageOpen, setCoverageOpen] = useState(false);
  const [pendingOpen, setPendingOpen] = useState(false);
  const [pendingCursor, setPendingCursor] = useState<Readonly<{
    after?: string;
    before?: string;
  }>>({});
  const previousScopeKey = useRef(scopeKey);
  const scopeChanged = previousScopeKey.current !== scopeKey;
  useEffect(() => {
    if (previousScopeKey.current === scopeKey) return;
    previousScopeKey.current = scopeKey;
    setPendingOpen(false);
    setPendingCursor({});
  }, [scopeKey]);

  const mockDashboardReadEnabled = import.meta.env.VITE_MOCK_MODE === 'browser';
  const routeAllowed =
    !capabilities.loading &&
    !capabilities.failed &&
    (capabilities.has('dashboard.read') || mockDashboardReadEnabled);
  const dashboardReadEnabled = mockDashboardReadEnabled && routeAllowed;
  const window = useMemo(
    () => rangeWindow(search.range, search.from, search.to, anchor),
    [anchor, search.from, search.range, search.to],
  );
  const activity = useDashboardActivity(scope, window, dashboardReadEnabled);
  const snapshot = useDashboardSnapshot(scope, dashboardReadEnabled);
  const pending = useDashboardPending(scope, dashboardReadEnabled);
  const coverage = useDashboardCoverage(scope, dashboardReadEnabled && coverageOpen);
  const pendingPageInput = useMemo(
    () => ({ limit: 50 as const, ...pendingCursor }),
    [pendingCursor],
  );
  const pendingPage = useDashboardPendingPage(
    scope,
    pendingPageInput,
    dashboardReadEnabled && pendingOpen && !scopeChanged,
  );

  const header = {
    title: '数据工作台',
    description: '从原始数据、质量门禁、Lance 基线到发布版本的项目运营视图。',
    breadcrumbs: [{ key: 'dashboard', label: '工作台' }],
  } as const;

  if (!mockDashboardReadEnabled) {
    return (
      <main className={styles.page} data-page-id="P01">
        <StandardPageScaffold
          header={header}
          state={(
            <section
              className={styles.unavailable}
              role="status"
              aria-label="工作台聚合能力尚未开放"
              data-dashboard-read-state="product-contract-undefined"
            >
              <PageState
                state="feature-unavailable"
                label="工作台聚合能力"
                title="工作台聚合能力尚未开放"
                description="活动、指标快照、覆盖率与待办聚合的产品合同尚未定义。真实 API 模式下不会请求这些接口，也不会展示模拟数据或占位指标。"
              />
            </section>
          )}
        />
      </main>
    );
  }

  const fatal = activity.error && snapshot.error && pending.error;
  const pageState: PageStateKind | 'ready' = capabilities.loading
    ? 'loading'
    : capabilities.failed || !routeAllowed
      ? 'forbidden'
      : !scope
        ? 'feature-unavailable'
        : fatal
          ? stateFromError(activity.error)
          : 'ready';
  const trendsState: PageStateKind | 'ready' = activity.error
    ? stateFromError(activity.error)
    : snapshot.error
      ? stateFromError(snapshot.error)
      : activity.isPending || snapshot.isPending
        ? 'loading'
        : activity.isFetching || snapshot.isFetching
          ? 'refreshing'
          : 'ready';
  const coverageState = queryState(coverage);
  const pendingState = queryState(pending, pending.data?.items.length === 0);
  const pendingPageState = queryState(pendingPage, pendingPage.data?.items.length === 0);

  const coverageContent = !coverageOpen ? (
    <Button onClick={() => setCoverageOpen(true)}>按需加载覆盖率矩阵</Button>
  ) : renderRegion(
    coverageState,
    '覆盖率矩阵',
    coverage.error,
    coverage.data ? <DashboardCoverageTable coverage={coverage.data} /> : null,
    () => void coverage.refetch(),
  );
  const charts = activity.data && snapshot.data ? (
    <Suspense fallback={<PageState state="loading" label="图表模块" />}>
      <DashboardCharts
        activity={activity.data}
        snapshot={snapshot.data}
        coverage={coverageContent}
      />
    </Suspense>
  ) : null;
  const pendingContent = pending.data ? (
    <section className={`${styles.panel} ${styles.pendingPanel}`} aria-labelledby="dashboard-pending-title">
      <div className={styles.panelHeading}>
        <h2 id="dashboard-pending-title">待办与最近活动</h2>
        {pending.data.totalCount > 5n ? (
          <Button type="link" onClick={() => setPendingOpen(true)}>查看全部待办</Button>
        ) : null}
      </div>
      {pending.data.hasUnknownEnum ? <PageState state="unknown" label="待办未知状态" /> : null}
      <DashboardPendingList items={pending.data.items} />
    </section>
  ) : null;

  const readyContent = (
    <div className={styles.contentStack}>
      {snapshot.data?.hasUnknownEnum ? <PageState state="unknown" label="工作台未知状态" /> : null}
      <DataLifecycleRail />
      {renderRegion(
        trendsState,
        '工作台趋势',
        activity.error ?? snapshot.error,
        charts,
        () => void Promise.all([activity.refetch(), snapshot.refetch()]),
      )}
      {renderRegion(
        pendingState,
        '待办与最近活动',
        pending.error,
        pendingContent,
        () => void pending.refetch(),
      )}
    </div>
  );

  const blockingState: PageStateKind = pageState === 'ready' ? 'error' : pageState;

  return (
    <main className={styles.page} data-page-id="P01">
      <StandardPageScaffold
        header={{
          ...header,
          actions: (
            <>
              <label>
                <span>时间范围</span>
                <Select
                  aria-label="时间范围"
                  value={search.range}
                  options={[
                    { value: '24h', label: '最近 24 小时' },
                    { value: '7d', label: '最近 7 天' },
                    { value: '30d', label: '最近 30 天' },
                  ]}
                  onChange={(range: DashboardRange) => {
                    setSearchParams(dashboardQueryCodec.build({ range }));
                    setAnchor(new Date(Math.floor(Date.now() / 1_000) * 1_000));
                  }}
                />
              </label>
              <Button
                icon={<RefreshCw aria-hidden="true" size={16} />}
                aria-label="刷新工作台"
                onClick={() => {
                  setAnchor(new Date(Math.floor(Date.now() / 1_000) * 1_000));
                  void Promise.all([activity.refetch(), snapshot.refetch(), pending.refetch()]);
                }}
              >
                刷新
              </Button>
            </>
          ),
        }}
        summary={pageState === 'ready' ? (
          <DashboardSummaryStrip
            activity={activity.data}
            snapshot={snapshot.data}
            activityState={metricState(activity)}
            snapshotState={metricState(snapshot)}
          />
        ) : undefined}
        state={pageState === 'ready' ? readyContent : (
          <PageState
            state={blockingState}
            label="工作台"
            title={stateTitle(blockingState)}
            requestId={requestId(activity.error)}
            onRetry={fatal ? () => void Promise.all([
              activity.refetch(),
              snapshot.refetch(),
              pending.refetch(),
            ]) : undefined}
          />
        )}
      />

      <DashboardPendingDrawer
        open={pendingOpen}
        page={pendingPage.data}
        state={pendingPageState}
        requestId={requestId(pendingPage.error)}
        onRetry={pendingPage.isError ? () => void pendingPage.refetch() : undefined}
        onCursorChange={setPendingCursor}
        onClose={() => setPendingOpen(false)}
      />
    </main>
  );
}

export default DashboardPage;
