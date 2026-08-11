import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { DashboardRegionState, type DashboardRegionStatus } from '../../features/dashboard/region-state';
import {
  useDashboardActivity,
  useDashboardCoverage,
  useDashboardPending,
  useDashboardPendingPage,
  useDashboardSnapshot,
} from '../../features/dashboard/api/queries';
import { DASHBOARD_PROJECT_TIMEZONE_ASSUMPTION, type DashboardScope } from '../../features/dashboard/types';
import { storageOverviewRoute } from '../../features/storage-overview/routing';
import { useShellStore } from '../../shared/scope/shell-store';
import { dashboardQueryCodec, type DashboardRange } from './query-codec';

const DashboardCharts = lazy(() => import('../../features/dashboard/dashboard-charts'));

function statusFromError(error: unknown): DashboardRegionStatus {
  if (!isDomainError(error)) return 'contract-mismatch';
  switch (error.code) {
    case 'FORBIDDEN': return 'forbidden';
    case 'NOT_FOUND':
    case 'GONE': return 'not-found-gone';
    case 'VERSION_CONFLICT':
    case 'PRECONDITION_FAILED': return 'conflict';
    case 'RATE_LIMITED': return 'rate-limited';
    case 'NETWORK_ERROR': return 'offline-reconnecting';
    case 'CONTRACT_MISMATCH': return 'contract-mismatch';
    default: return 'partial-error';
  }
}

function metric(value: bigint | null | undefined, suffix = ''): string {
  if (value === undefined) return '—';
  if (value === null) return '--';
  return `${value.toLocaleString('zh-CN')}${suffix}`;
}

function queryStatus(query: Readonly<{ data?: unknown; isPending: boolean; isFetching: boolean; error: unknown }>): DashboardRegionStatus {
  if (query.isPending && query.data === undefined) return 'first-loading';
  if (query.error) return statusFromError(query.error);
  if (query.isFetching && query.data !== undefined) return 'refreshing';
  return 'ready';
}

function rangeWindow(range: DashboardRange, from: string | undefined, to: string | undefined, anchor: Date) {
  if (range === 'custom' && from && to) return { from, to };
  const hours = range === '7d' ? 7 * 24 : range === '30d' ? 30 * 24 : 24;
  return { from: new Date(anchor.getTime() - hours * 60 * 60 * 1000).toISOString(), to: anchor.toISOString() };
}

export function DashboardPage() {
  const shellScope = useShellStore((state) => state.scope);
  const scopeKey = useShellStore((state) => state.scopeKey);
  const scope: DashboardScope | null = shellScope?.projectId && shellScope.regionCode ? {
    organizationId: shellScope.organizationId,
    projectId: shellScope.projectId,
    regionCode: shellScope.regionCode,
    timezone: DASHBOARD_PROJECT_TIMEZONE_ASSUMPTION,
  } : null;
  const capabilities = useCapabilities();
  const [searchParams, setSearchParams] = useSearchParams();
  const search = dashboardQueryCodec.parse(searchParams);
  const [anchor, setAnchor] = useState(() => new Date(Math.floor(Date.now() / 1000) * 1000));
  const [coverageOpen, setCoverageOpen] = useState(false);
  const [pendingOpen, setPendingOpen] = useState(false);
  const [pendingCursor, setPendingCursor] = useState<Readonly<{ after?: string; before?: string }>>({});
  const previousScopeKey = useRef(scopeKey);
  const scopeChanged = previousScopeKey.current !== scopeKey;
  const mockDashboardReadEnabled = import.meta.env.VITE_MOCK_MODE === 'browser';
  const routeAllowed = !capabilities.loading && !capabilities.failed && (capabilities.has('dashboard.read') || mockDashboardReadEnabled);
  const window = useMemo(() => rangeWindow(search.range, search.from, search.to, anchor), [search.range, search.from, search.to, anchor]);
  const activity = useDashboardActivity(scope, window, routeAllowed);
  const snapshot = useDashboardSnapshot(scope, routeAllowed);
  const pending = useDashboardPending(scope, routeAllowed);
  const coverage = useDashboardCoverage(scope, routeAllowed);
  const pendingPageInput = useMemo(() => ({ limit: 50 as const, ...pendingCursor }), [pendingCursor]);
  const pendingPage = useDashboardPendingPage(scope, pendingPageInput, routeAllowed && pendingOpen && !scopeChanged);

  useEffect(() => {
    if (previousScopeKey.current === scopeKey) return;
    previousScopeKey.current = scopeKey;
    setPendingOpen(false);
    setPendingCursor({});
  }, [scopeKey]);

  useEffect(() => {
    if (!pendingOpen) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setPendingOpen(false);
    };
    globalThis.addEventListener('keydown', closeOnEscape);
    return () => globalThis.removeEventListener('keydown', closeOnEscape);
  }, [pendingOpen]);

  if (capabilities.loading) return <DashboardRegionState status="first-loading" label="工作台权限加载" />;
  if (capabilities.failed || !routeAllowed) return <DashboardRegionState status="forbidden" label="工作台权限" />;
  if (!scope) return <DashboardRegionState status="feature-unavailable" label="工作台作用域" />;

  const fatal = activity.error && snapshot.error && pending.error;
  if (fatal) return <DashboardRegionState status="fatal-error" label="工作台" requestId={isDomainError(activity.error) ? activity.error.requestId : null} onRetry={() => void Promise.all([activity.refetch(), snapshot.refetch(), pending.refetch()])} />;

  const rawBytes = snapshot.data?.roles.find((role) => role.role === 'RAW')?.bytes;
  const metrics = [
    ['期间上传量', activity.data?.acceptedUniqueBytes, ' bytes'],
    ['上传成功率', activity.data?.successRatio === null ? null : activity.data ? BigInt(Math.round(activity.data.successRatio * 1000)) : undefined, activity.data?.successRatio === null ? '' : '‰'],
    ['Raw 物理容量', rawBytes, ' bytes'],
    ['可查看 Episode', snapshot.data?.episodes.viewableCount, ''],
    ['开放人工问题', snapshot.data?.work.openManualIssueCount, ''],
    ['可行动清洗草稿', snapshot.data?.work.activeCleaningDraftCount, ''],
  ] as const;

  return (
    <main style={{ padding: 24, background: '#f5f8f8', minHeight: '100%', color: '#18302d' }}>
      <header style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <div><h1>数据工作台</h1><p>项目数据、质量、存储与待办的只读聚合视图</p></div>
        <div>
          <label htmlFor="dashboard-range">时间范围</label>{' '}
          <select id="dashboard-range" value={search.range} onChange={(event) => {
            const range = event.target.value as DashboardRange;
            setSearchParams(dashboardQueryCodec.build({ range }));
            setAnchor(new Date(Math.floor(Date.now() / 1000) * 1000));
          }}>
            <option value="24h">最近 24 小时</option><option value="7d">最近 7 天</option><option value="30d">最近 30 天</option>
          </select>{' '}
          <button type="button" aria-label="刷新工作台" onClick={() => { setAnchor(new Date(Math.floor(Date.now() / 1000) * 1000)); void Promise.all([activity.refetch(), snapshot.refetch(), pending.refetch()]); }}>刷新</button>
        </div>
      </header>

      <DashboardRegionState status={queryStatus(snapshot)} label="工作台指标" requestId={isDomainError(snapshot.error) ? snapshot.error.requestId : null} onRetry={() => void snapshot.refetch()}>
        {snapshot.data?.hasUnknownEnum ? <DashboardRegionState status="unknown-enum" label="工作台未知状态" /> : null}
        <section aria-label="关键指标" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(160px,1fr))', gap: 12, marginBlock: 20 }}>
          {metrics.map(([label, value, suffix]) => (
            <article key={label} style={{ background: '#fff', border: '1px solid #d9e2e1', borderRadius: 8, padding: 16 }}>
              <span>{label}</span><strong style={{ display: 'block', fontSize: 24, fontVariantNumeric: 'tabular-nums' }}>{metric(value, suffix)}</strong>
              {label === 'Raw 物理容量' && value !== undefined ? <a href={storageOverviewRoute.build({ tab: 'objects', objectRole: 'SOURCE' })}>查看对象</a> : null}
            </article>
          ))}
        </section>
      </DashboardRegionState>

      <DashboardRegionState status={activity.error ? statusFromError(activity.error) : snapshot.error ? statusFromError(snapshot.error) : activity.isPending || snapshot.isPending ? 'first-loading' : 'ready'} label="工作台趋势" onRetry={() => void Promise.all([activity.refetch(), snapshot.refetch()])}>
        {activity.data && snapshot.data ? <Suspense fallback={<DashboardRegionState status="first-loading" label="图表模块" />}><DashboardCharts activity={activity.data} snapshot={snapshot.data} /></Suspense> : null}
      </DashboardRegionState>

      <section style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))', gap: 16, marginTop: 16 }}>
        <DashboardRegionState status={coverageOpen ? queryStatus(coverage) : 'feature-unavailable'} label="覆盖率矩阵" onRetry={() => void coverage.refetch()}>
          {coverage.data ? <table><caption>机器人组与任务覆盖率</caption><tbody>{coverage.data.cells.map((cell) => <tr key={`${cell.robotGroupId}-${cell.taskId}`}><th>{cell.robotGroupId} / {cell.taskId}</th><td>{cell.ratio === null ? '无样本' : `${Math.round(cell.ratio * 1000) / 10}%`}</td></tr>)}</tbody></table> : null}
        </DashboardRegionState>
        {!coverageOpen ? <button type="button" onClick={() => setCoverageOpen(true)}>按需加载覆盖率矩阵</button> : null}

        <DashboardRegionState status={queryStatus(pending)} label="待办与最近活动" requestId={isDomainError(pending.error) ? pending.error.requestId : null} onRetry={() => void pending.refetch()}>
          {pending.data?.hasUnknownEnum ? <DashboardRegionState status="unknown-enum" label="待办未知状态" /> : null}
          <h2>待办与最近活动</h2>
          {pending.data?.items.length === 0 ? <DashboardRegionState status="empty" label="待办空状态" /> : (
            <ul>{pending.data?.items.map((item) => <li key={item.itemId}><strong>{item.title}</strong> · {item.status} · <time dateTime={item.updatedAt}>{item.updatedAt}</time>{item.clickable ? <span title="目标页 builder 待 Owner 交付">（目标暂不可用）</span> : null}</li>)}</ul>
          )}
          {pending.data && pending.data.totalCount > 5n ? <button type="button" onClick={() => setPendingOpen(true)}>查看全部待办</button> : null}
        </DashboardRegionState>
      </section>

      {pendingOpen ? (
        <aside role="dialog" aria-modal="true" aria-labelledby="dashboard-pending-title" style={{ position: 'fixed', inset: '0 0 0 auto', width: 'min(480px,100vw)', overflow: 'auto', padding: 20, background: '#fff', borderLeft: '1px solid #d9e2e1', zIndex: 10 }}>
          <button type="button" aria-label="关闭全部待办" onClick={() => setPendingOpen(false)}>关闭</button>
          <h2 id="dashboard-pending-title">全部待办</h2>
          <DashboardRegionState status={queryStatus(pendingPage)} label="分页待办" requestId={isDomainError(pendingPage.error) ? pendingPage.error.requestId : null} onRetry={() => void pendingPage.refetch()}>
            {pendingPage.data?.hasUnknownEnum ? <DashboardRegionState status="unknown-enum" label="分页待办未知状态" /> : null}
            {pendingPage.data?.items.length === 0 ? <DashboardRegionState status="empty" label="分页待办空状态" /> : <ul>{pendingPage.data?.items.map((item) => <li key={item.itemId}><strong>{item.title}</strong> · {item.status}</li>)}</ul>}
            <div aria-label="待办游标分页"><button type="button" disabled={!pendingPage.data?.pageInfo.hasPreviousPage} onClick={() => setPendingCursor({ before: pendingPage.data?.pageInfo.startCursor ?? undefined })}>上一组</button><button type="button" disabled={!pendingPage.data?.pageInfo.hasNextPage} onClick={() => setPendingCursor({ after: pendingPage.data?.pageInfo.endCursor ?? undefined })}>下一组</button></div>
          </DashboardRegionState>
        </aside>
      ) : null}
    </main>
  );
}

export default DashboardPage;
