import { lazy, Suspense, useEffect, useMemo, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { useStorageCost, useStorageInventory, useStorageMultipart, useStorageObject, useStorageOverview } from '../../features/storage-overview/api/queries';
import { displayByteMetric, displayDecimalMetric, displayMinorUnitMetric, displayMoneyMetric, formatByteString } from '../../features/storage-overview/metrics-contract';
import { StorageRegionState, type StorageRegionStatus } from '../../features/storage-overview/region-state';
import { patchStorageOverviewSearch, type StorageOverviewSearch } from '../../features/storage-overview/routing';
import type { StorageScope } from '../../features/storage-overview/types';
import { useShellStore } from '../../shared/scope/shell-store';
import { storageOverviewQueryCodec } from './query-codec';

const StorageCharts = lazy(() => import('../../features/storage-overview/storage-charts'));

function statusFromError(error: unknown, fatal = false): StorageRegionStatus {
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
    default: return fatal ? 'fatal-error' : 'partial-error';
  }
}

function setQuery(setter: ReturnType<typeof useSearchParams>[1], current: StorageOverviewSearch, patch: Partial<StorageOverviewSearch>) {
  setter(storageOverviewQueryCodec.build(patchStorageOverviewSearch(current, patch)));
}

export function StorageOverviewPage() {
  const shellScope = useShellStore((state) => state.scope);
  const scopeKey = useShellStore((state) => state.scopeKey);
  const scope: StorageScope | null = shellScope?.projectId && shellScope.regionCode ? {
    organizationId: shellScope.organizationId,
    projectId: shellScope.projectId,
    regionCode: shellScope.regionCode,
  } : null;
  const capabilities = useCapabilities();
  const [params, setParams] = useSearchParams();
  const parsedSearch = useMemo(() => storageOverviewQueryCodec.parse(params), [params]);
  const previousScopeKey = useRef(scopeKey);
  const scopeChanged = previousScopeKey.current !== scopeKey;
  const search: StorageOverviewSearch = useMemo(() => scopeChanged
    ? { ...parsedSearch, after: undefined, before: undefined, objectId: undefined }
    : parsedSearch, [parsedSearch, scopeChanged]);
  const canRead = !capabilities.loading && !capabilities.failed && capabilities.has('storage.overview.read');
  const canReadObjects = capabilities.has('storage.object.read');
  const canReadMultipart = capabilities.has('storage.multipart.read');
  const canReadCost = capabilities.has('storage.cost.read');
  const overview = useStorageOverview(scope, search.months, canRead);
  const inventory = useStorageInventory(scope, search, canRead && canReadObjects && search.tab === 'objects');
  const multipart = useStorageMultipart(scope, search, canRead && canReadMultipart && search.tab === 'multipart');
  const objectDetail = useStorageObject(scope, search.objectId, inventory.data?.snapshotId ?? overview.data?.snapshotId, canRead && canReadObjects && search.tab === 'objects');
  const billingPeriod = overview.data?.totals.monthlyCost.state === 'KNOWN' ? overview.data.totals.monthlyCost.value.billingPeriod : undefined;
  const cost = useStorageCost(scope, billingPeriod, canRead && canReadCost && search.tab === 'cost');

  useEffect(() => {
    if (search.tab === 'cost' && !capabilities.loading && !canReadCost) setQuery(setParams, search, { tab: 'overview' });
    if (search.tab === 'multipart' && !capabilities.loading && !canReadMultipart) setQuery(setParams, search, { tab: 'overview' });
  }, [canReadCost, canReadMultipart, capabilities.loading, search, setParams]);

  useEffect(() => {
    if (previousScopeKey.current === scopeKey) return;
    previousScopeKey.current = scopeKey;
    if (parsedSearch.after || parsedSearch.before || parsedSearch.objectId) {
      setParams(storageOverviewQueryCodec.build({ ...parsedSearch, after: undefined, before: undefined, objectId: undefined }), { replace: true });
    }
  }, [parsedSearch, scopeKey, setParams]);

  if (capabilities.loading) return <StorageRegionState status="first-loading" label="存储权限加载" />;
  if (!canRead) return <StorageRegionState status="forbidden" label="存储页面权限" />;
  if (!scope) return <StorageRegionState status="feature-unavailable" label="存储作用域" />;
  if (overview.isPending) return <StorageRegionState status="first-loading" label="存储概览" />;
  if (overview.error) return <StorageRegionState status={statusFromError(overview.error, true)} label="存储概览" requestId={isDomainError(overview.error) ? overview.error.requestId : null} onRetry={() => void overview.refetch()} />;
  if (!overview.data) return <StorageRegionState status="fatal-error" label="存储概览" />;

  const data = overview.data;
  return (
    <main style={{ minHeight: '100%', background: '#f5f8f8', padding: 24, color: '#18302d' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
        <div><h1>存储容量</h1><p>只读容量、费用、复用与 Inventory 事实</p></div>
        <p>快照：{data.snapshotId}<br />截至：<time dateTime={data.asOf}>{data.asOf}</time></p>
      </header>

      {data.hasUnknownEnum ? <StorageRegionState status="unknown-enum" label="存储未知枚举" /> : null}
      {data.partialErrors.map((error) => <StorageRegionState key={`${error.section}-${error.requestId}`} status="partial-error" label={`存储区域 ${error.section}`} requestId={error.requestId} />)}

      <nav aria-label="存储容量页面标签" style={{ display: 'flex', gap: 8, marginBlock: 16 }}>
        <button type="button" aria-current={search.tab === 'overview' ? 'page' : undefined} onClick={() => setQuery(setParams, search, { tab: 'overview' })}>概览</button>
        <button type="button" aria-current={search.tab === 'objects' ? 'page' : undefined} disabled={!canReadObjects} onClick={() => setQuery(setParams, search, { tab: 'objects' })}>Inventory 对象</button>
        <button type="button" aria-current={search.tab === 'multipart' ? 'page' : undefined} disabled={!canReadMultipart} onClick={() => setQuery(setParams, search, { tab: 'multipart', objectId: undefined })}>Multipart 诊断</button>
        <button type="button" aria-current={search.tab === 'cost' ? 'page' : undefined} disabled={!canReadCost} onClick={() => setQuery(setParams, search, { tab: 'cost' })}>费用</button>
      </nav>

      <section aria-label="存储指标" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 12, marginBottom: 16 }}>
        {[
          ['实际 OSS 容量', displayByteMetric(data.totals.actualOssPhysicalBytes)],
          ['逻辑引用容量', displayByteMetric(data.totals.logicalReferencedBytes)],
          ['计费容量', displayByteMetric(data.totals.billedBytes)],
          ['复用率', displayDecimalMetric(data.totals.reuseRate)],
          ['月度费用', displayMoneyMetric(data.totals.monthlyCost)],
        ].map(([label, value]) => <article key={label} style={{ background: '#fff', border: '1px solid #d7e2e0', borderRadius: 8, padding: 16 }}><span>{label}</span><strong style={{ display: 'block', fontSize: 22, fontVariantNumeric: 'tabular-nums' }}>{value}</strong></article>)}
      </section>

      {search.tab === 'overview' ? (
        <>
          <Suspense fallback={<StorageRegionState status="first-loading" label="存储图表" />}><StorageCharts overview={data} /></Suspense>
          <section style={{ marginTop: 16, background: '#fff', border: '1px solid #d7e2e0', borderRadius: 8, padding: 16 }}>
            <h2>Inventory 对账</h2>
            <dl><dt>状态</dt><dd>{data.reconciliation.status}</dd><dt>已登记物理量</dt><dd>{displayByteMetric(data.reconciliation.registeredPhysicalBytes)}</dd><dt>实际 OSS 物理量</dt><dd>{displayByteMetric(data.reconciliation.actualOssPhysicalBytes)}</dd><dt>未分类</dt><dd>{displayByteMetric(data.reconciliation.unclassifiedBytes)}</dd><dt>Multipart</dt><dd>{displayByteMetric(data.reconciliation.multipartBytes)}</dd></dl>
            {data.reconciliation.note ? <p>{data.reconciliation.note}</p> : null}
          </section>
        </>
      ) : null}

      {search.tab === 'objects' ? (
        !canReadObjects ? <StorageRegionState status="feature-unavailable" label="Inventory 对象" /> : (
          <StorageRegionState
            status={inventory.error ? statusFromError(inventory.error) : inventory.isPending ? 'first-loading' : inventory.isFetching ? 'refreshing' : inventory.data?.items.length === 0 ? (search.objectRole || search.storageClass || search.anomaly.length || search.status.length ? 'filtered-empty' : 'empty') : 'ready'}
            label="Inventory 对象表"
            requestId={isDomainError(inventory.error) ? inventory.error.requestId : null}
            onRetry={() => void inventory.refetch()}
          >
            {inventory.data?.hasUnknownEnum ? <StorageRegionState status="unknown-enum" label="Inventory 未知枚举" /> : null}
            <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse' }}><caption>同一快照下的存储对象事实</caption><thead><tr><th>对象</th><th>角色</th><th>层级</th><th>物理量</th><th>引用</th><th>状态</th></tr></thead><tbody>{inventory.data?.items.map((item) => <tr key={item.objectId}><td><button type="button" onClick={() => setQuery(setParams, search, { objectId: item.objectId })}>{item.displayKey}</button></td><td>{item.objectRole}</td><td>{item.storageClass}</td><td>{formatByteString(item.physicalBytes)}</td><td>{item.referenceCount}</td><td>{item.status}</td></tr>)}</tbody></table></div>
            <div aria-label="Inventory 游标分页"><button type="button" disabled={!inventory.data?.pageInfo.hasPreviousPage} onClick={() => setQuery(setParams, search, { before: inventory.data?.pageInfo.startCursor ?? undefined, after: undefined })}>上一组</button><button type="button" disabled={!inventory.data?.pageInfo.hasNextPage} onClick={() => setQuery(setParams, search, { after: inventory.data?.pageInfo.endCursor ?? undefined, before: undefined })}>下一组</button></div>
          </StorageRegionState>
        )
      ) : null}

      {search.tab === 'objects' && search.objectId ? (
        <aside role="dialog" aria-modal="true" aria-labelledby="storage-object-title" onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); setQuery(setParams, search, { objectId: undefined }); } }} style={{ position: 'fixed', inset: '0 0 0 auto', width: 'min(420px,100vw)', overflow: 'auto', padding: 20, background: '#fff', borderLeft: '1px solid #d7e2e0', zIndex: 10 }}>
          <button type="button" aria-label="关闭对象详情" autoFocus onClick={() => setQuery(setParams, search, { objectId: undefined })}>关闭</button>
          <h2 id="storage-object-title">对象详情</h2>
          <StorageRegionState status={objectDetail.error ? statusFromError(objectDetail.error) : objectDetail.isPending ? 'first-loading' : 'ready'} label="对象详情状态" requestId={isDomainError(objectDetail.error) ? objectDetail.error.requestId : null} onRetry={() => void objectDetail.refetch()}>
            {objectDetail.data ? <dl><dt>对象</dt><dd>{objectDetail.data.displayKey}</dd><dt>快照</dt><dd>{objectDetail.data.snapshotId}</dd><dt>角色</dt><dd>{objectDetail.data.objectRole}</dd><dt>物理量</dt><dd>{formatByteString(objectDetail.data.physicalBytes)}</dd><dt>保护原因</dt><dd>{objectDetail.data.protectionReasons.join('、') || '无'}</dd></dl> : null}
          </StorageRegionState>
          <p>此诊断抽屉只读，不提供下载、删除、Restore、Abort 或生命周期执行。</p>
        </aside>
      ) : null}

      {search.tab === 'multipart' ? (
        !canReadMultipart ? <StorageRegionState status="feature-unavailable" label="Multipart 诊断" /> : (
          <StorageRegionState status={multipart.error ? statusFromError(multipart.error) : multipart.isPending ? 'first-loading' : multipart.isFetching ? 'refreshing' : multipart.data?.items.length === 0 ? (search.status.length ? 'filtered-empty' : 'empty') : 'ready'} label="Multipart 诊断表" requestId={isDomainError(multipart.error) ? multipart.error.requestId : null} onRetry={() => void multipart.refetch()}>
            {multipart.data?.hasUnknownEnum ? <StorageRegionState status="unknown-enum" label="Multipart 未知枚举" /> : null}
            <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse' }}><caption>只读 Multipart 上传诊断</caption><thead><tr><th>Multipart</th><th>上传</th><th>状态</th><th>已接收</th><th>预计</th><th>Part 数</th><th>最后活动</th></tr></thead><tbody>{multipart.data?.items.map((item) => <tr key={item.multipartId}><td>{item.multipartId}</td><td>{item.uploadId ?? '未关联'}</td><td>{item.status}</td><td>{formatByteString(item.receivedBytes)}</td><td>{item.expectedBytes === null ? '未知' : formatByteString(item.expectedBytes)}</td><td>{item.partCount}</td><td>{item.lastActivityAt ?? '未提供'}</td></tr>)}</tbody></table></div>
            <div aria-label="Multipart 游标分页"><button type="button" disabled={!multipart.data?.pageInfo.hasPreviousPage} onClick={() => setQuery(setParams, search, { before: multipart.data?.pageInfo.startCursor ?? undefined, after: undefined })}>上一组</button><button type="button" disabled={!multipart.data?.pageInfo.hasNextPage} onClick={() => setQuery(setParams, search, { after: multipart.data?.pageInfo.endCursor ?? undefined, before: undefined })}>下一组</button></div>
          </StorageRegionState>
        )
      ) : null}

      {search.tab === 'cost' ? (
        !canReadCost ? <StorageRegionState status="feature-unavailable" label="存储费用" /> : (
          <StorageRegionState status={cost.error ? statusFromError(cost.error) : cost.isPending ? 'first-loading' : cost.isFetching ? 'refreshing' : 'ready'} label="存储费用" requestId={isDomainError(cost.error) ? cost.error.requestId : null} onRetry={() => void cost.refetch()}>
            {cost.data ? <section><h2>{cost.data.billingPeriod} 费用构成</h2><dl><dt>存储</dt><dd>{displayMinorUnitMetric(cost.data.storage, cost.data.currency)}</dd><dt>请求</dt><dd>{displayMinorUnitMetric(cost.data.request, cost.data.currency)}</dd><dt>传输</dt><dd>{displayMinorUnitMetric(cost.data.transfer, cost.data.currency)}</dd><dt>税费</dt><dd>{displayMinorUnitMetric(cost.data.tax, cost.data.currency)}</dd><dt>合计</dt><dd>{displayMinorUnitMetric(cost.data.total, cost.data.currency)}</dd></dl><p>以上为服务端 minor-unit 事实，页面不从容量估算费用。</p></section> : null}
          </StorageRegionState>
        )
      ) : null}
    </main>
  );
}

export default StorageOverviewPage;
