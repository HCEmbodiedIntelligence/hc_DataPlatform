import { useEffect, useMemo, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { useAuditBootstrap, useAuditEvent, useAuditEvents, useAuditFacets } from '../../features/audit/api/queries';
import { canonicalAuditEventNames } from '../../features/audit/event-catalog';
import { resolveAuditFieldVisibility, type AuditFieldVisibility } from '../../features/audit/field-visibility';
import { AuditRegionState, type AuditRegionStatus } from '../../features/audit/region-state';
import { patchAuditSearch, type AuditSearch } from '../../features/audit/routing';
import { resolvePendingAuditResourceLink } from '../../features/audit/pending-links';
import type { AuditEventView, AuditScope } from '../../features/audit/types';
import { useShellStore } from '../../shared/scope/shell-store';
import { auditQueryCodec } from './query-codec';

function statusFromError(error: unknown, fatal = false): AuditRegionStatus {
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

function setQuery(setter: ReturnType<typeof useSearchParams>[1], current: AuditSearch, patch: Partial<AuditSearch>) {
  setter(auditQueryCodec.build(patchAuditSearch(current, patch)));
}

function detailValue(value: string | number | boolean | null | readonly (string | number | boolean | null)[]): string {
  if (value === null) return '空值';
  if (Array.isArray(value)) return value.map((entry) => String(entry)).join('、');
  return String(value);
}

function AuditInspector({ event, visibility, onClose }: Readonly<{ event: AuditEventView; visibility: AuditFieldVisibility; onClose(): void }>) {
  const resourceLink = resolvePendingAuditResourceLink(event.resource.type);
  return (
    <aside role="dialog" aria-modal="true" aria-labelledby="audit-inspector-title" style={{ position: 'fixed', inset: '0 0 0 auto', width: 'min(420px,100vw)', overflow: 'auto', padding: 20, background: '#fff', borderLeft: '1px solid #cddbd9', zIndex: 10 }}>
      <button type="button" aria-label="关闭审计详情" onClick={onClose}>关闭</button>
      <h2 id="audit-inspector-title">事件详情</h2>
      {!event.eventNameKnown || event.hasUnknownEnum ? <AuditRegionState status="unknown-enum" label="事件未知枚举" /> : null}
      <dl>
        <dt>事件 ID</dt><dd>{event.eventId}</dd>
        <dt>事件名</dt><dd>{event.eventNameKnown ? event.eventName : '未登记事件'}</dd>
        <dt>发生时间</dt><dd><time dateTime={event.occurredAt}>{event.occurredAt}</time></dd>
        <dt>记录时间</dt><dd><time dateTime={event.recordedAt}>{event.recordedAt}</time></dd>
        <dt>Actor</dt><dd>{event.actor.displayName}（{event.actor.type}）</dd>
        {visibility.actorRoleIds ? <><dt>角色投影</dt><dd>{event.actor.roles.join('、') || '无角色投影'}</dd></> : null}
        <dt>资源</dt><dd>{event.resource.displayName} / {event.resource.id}{resourceLink ? <a href={resourceLink}>打开资源</a> : '（无可用深链）'}</dd>
        <dt>结果</dt><dd>{event.outcome.status}</dd>
        <dt>风险</dt><dd>{event.risk.level}</dd>
        <dt>请求 ID</dt><dd>{event.request.requestId}</dd>
        {visibility.requestMetadata ? <><dt>请求上下文</dt><dd>{event.request.clientType} / {event.request.ipAddress ?? '已脱敏'} / {event.request.deviceSummary ?? '未提供'}</dd></> : null}
        <dt>保留类别</dt><dd>{event.retention.className}；{event.retention.retainUntil ?? '未提供到期时间'}</dd>
        <dt>完整性</dt><dd>{event.integrity.status}</dd>
        {visibility.integrityEvidence ? <><dt>完整性证据</dt><dd>{event.integrity.recordDigest ?? '未提供'} / {event.integrity.checkpointId ?? '未提供'}</dd></> : null}
      </dl>
      <section aria-labelledby="safe-change-title"><h3 id="safe-change-title">安全变更摘要</h3>
        {event.change === null ? <p>未提供或不适用</p> : (
          <><p>{event.change.summaryCode}</p>{visibility.changeValues ? <table><thead><tr><th>字段</th><th>之前</th><th>之后</th></tr></thead><tbody>{event.change.changedFields.map((field) => <tr key={field}><th>{field}</th><td>{detailValue(event.change!.before[field] ?? null)}</td><td>{detailValue(event.change!.after[field] ?? null)}</td></tr>)}</tbody></table> : <p>当前能力只允许查看变更类型。</p>}</>
        )}
      </section>
    </aside>
  );
}

export function AuditPage() {
  const shellScope = useShellStore((state) => state.scope);
  const scopeKey = useShellStore((state) => state.scopeKey);
  const scope: AuditScope | null = shellScope?.projectId ? {
    organizationId: shellScope.organizationId,
    projectId: shellScope.projectId,
    regionCode: shellScope.regionCode ?? null,
  } : null;
  const capabilities = useCapabilities();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const parsedSearch = useMemo(() => auditQueryCodec.parse(params), [params]);
  const previousScopeKey = useRef(scopeKey);
  const scopeChanged = previousScopeKey.current !== scopeKey;
  const search: AuditSearch = useMemo(() => scopeChanged
    ? { ...parsedSearch, after: undefined, before: undefined, eventId: undefined }
    : parsedSearch, [parsedSearch, scopeChanged]);
  const canRead = !capabilities.loading && !capabilities.failed && capabilities.has('audit.read');
  const visibility = resolveAuditFieldVisibility(capabilities.has);
  const bootstrap = useAuditBootstrap(scope, search, canRead);
  const facets = useAuditFacets(scope, search, canRead);
  const events = useAuditEvents(scope, search, canRead);
  const detail = useAuditEvent(scope, search.eventId, canRead && search.eventId !== undefined);

  useEffect(() => {
    if (!capabilities.loading && !canRead) queryClient.removeQueries({ queryKey: ['audit'] });
  }, [canRead, capabilities.loading, queryClient]);

  useEffect(() => {
    if (previousScopeKey.current === scopeKey) return;
    previousScopeKey.current = scopeKey;
    if (parsedSearch.after || parsedSearch.before || parsedSearch.eventId) {
      setParams(auditQueryCodec.build({ ...parsedSearch, after: undefined, before: undefined, eventId: undefined }), { replace: true });
    }
  }, [parsedSearch, scopeKey, setParams]);

  useEffect(() => {
    if (!search.eventId) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setQuery(setParams, search, { eventId: undefined });
    };
    globalThis.addEventListener('keydown', closeOnEscape);
    return () => globalThis.removeEventListener('keydown', closeOnEscape);
  }, [search, setParams]);

  if (capabilities.loading) return <AuditRegionState status="first-loading" label="审计权限加载" />;
  if (!canRead) return <AuditRegionState status="forbidden" label="审计读取权限" />;
  if (!scope) return <AuditRegionState status="feature-unavailable" label="审计作用域" />;
  if (events.error && bootstrap.error) return <AuditRegionState status={statusFromError(events.error, true)} label="审计日志" requestId={isDomainError(events.error) ? events.error.requestId : null} onRetry={() => void Promise.all([events.refetch(), bootstrap.refetch(), facets.refetch()])} />;

  const filtered = search.actorId.length > 0 || search.eventName.length > 0 || search.resourceType.length > 0
    || search.resourceId !== undefined || search.result.length > 0 || search.riskLevel.length > 0 || search.requestId !== undefined;
  return (
    <main style={{ padding: 24, background: '#f5f8f8', minHeight: '100%', color: '#18302d' }}>
      <header><h1>审计日志</h1><p>系统管理 · 第 6 项；当前读取者的脱敏、追加式审计投影</p></header>

      <AuditRegionState status={bootstrap.error ? statusFromError(bootstrap.error) : bootstrap.isPending ? 'first-loading' : bootstrap.isFetching ? 'refreshing' : 'ready'} label="审计摘要" requestId={isDomainError(bootstrap.error) ? bootstrap.error.requestId : null} onRetry={() => void bootstrap.refetch()}>
        <section aria-label="审计指标" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 12 }}>
          {bootstrap.data ? ([['今日事件', bootstrap.data.metrics.today], ['高风险', bootstrap.data.metrics.highRisk], ['失败', bootstrap.data.metrics.failed], ['活跃 Actor', bootstrap.data.metrics.activeActors]] as const).map(([label, value]) => <article key={label} style={{ background: '#fff', border: '1px solid #d7e2e0', borderRadius: 8, padding: 16 }}><span>{label}</span><strong style={{ display: 'block', fontSize: 24 }}>{value.toLocaleString('zh-CN')}</strong></article>) : null}
        </section>
      </AuditRegionState>

      <AuditRegionState status={facets.error ? 'partial-error' : facets.isPending ? 'first-loading' : 'ready'} label="审计筛选" requestId={isDomainError(facets.error) ? facets.error.requestId : null} onRetry={() => void facets.refetch()}>
        {facets.data?.hasUnknownEventName ? <AuditRegionState status="unknown-enum" label="审计 Facet 未登记事件" /> : null}
        <form aria-label="审计日志筛选" style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBlock: 16 }} onSubmit={(event) => event.preventDefault()}>
          <label>开始（含）<input type="datetime-local" value={search.from.slice(0, 16)} onChange={(event) => { const value = new Date(event.target.value); if (Number.isFinite(value.getTime())) setQuery(setParams, search, { from: value.toISOString() }); }} /></label>
          <label>结束（不含）<input type="datetime-local" value={search.to.slice(0, 16)} onChange={(event) => { const value = new Date(event.target.value); if (Number.isFinite(value.getTime())) setQuery(setParams, search, { to: value.toISOString() }); }} /></label>
          <label>事件名<select value={search.eventName[0] ?? ''} onChange={(event) => setQuery(setParams, search, { eventName: event.target.value ? [event.target.value as (typeof canonicalAuditEventNames)[number]] : [] })}><option value="">全部事件</option>{canonicalAuditEventNames.map((name) => <option key={name} value={name}>{name}</option>)}</select></label>
          <label>Actor<input value={search.actorId[0] ?? ''} onChange={(event) => setQuery(setParams, search, { actorId: event.target.value ? [event.target.value] : [] })} /></label>
          <label>目标类型<input value={search.resourceType[0] ?? ''} onChange={(event) => setQuery(setParams, search, { resourceType: event.target.value ? [event.target.value.toUpperCase()] : [] })} /></label>
          <label>目标 ID<input value={search.resourceId ?? ''} onChange={(event) => setQuery(setParams, search, event.target.value ? { resourceId: event.target.value } : { resourceId: undefined })} /></label>
          <label>结果<select value={search.result[0] ?? ''} onChange={(event) => setQuery(setParams, search, { result: event.target.value ? [event.target.value as AuditSearch['result'][number]] : [] })}><option value="">全部结果</option><option>SUCCEEDED</option><option>DENIED</option><option>FAILED</option><option>PARTIAL</option></select></label>
          <label>风险<select value={search.riskLevel[0] ?? ''} onChange={(event) => setQuery(setParams, search, { riskLevel: event.target.value ? [event.target.value as AuditSearch['riskLevel'][number]] : [] })}><option value="">全部风险</option><option>LOW</option><option>MEDIUM</option><option>HIGH</option><option>CRITICAL</option></select></label>
          <label>请求 ID<input value={search.requestId ?? ''} onChange={(event) => setQuery(setParams, search, event.target.value ? { requestId: event.target.value } : { requestId: undefined })} /></label>
        </form>
      </AuditRegionState>

      <AuditRegionState
        status={events.error ? statusFromError(events.error) : events.isPending ? 'first-loading' : events.isFetching ? 'refreshing' : events.data?.items.length === 0 ? (filtered ? 'filtered-empty' : 'empty') : 'ready'}
        label="审计事件列表"
        requestId={isDomainError(events.error) ? events.error.requestId : null}
        onRetry={() => void events.refetch()}
      >
        {events.data?.hasUnknownEnum ? <AuditRegionState status="unknown-enum" label="审计事件未知枚举" /> : null}
        <div style={{ overflowX: 'auto', background: '#fff' }}><table style={{ width: '100%', borderCollapse: 'collapse' }}><caption>按 occurred_at DESC、event_id DESC 稳定排序</caption><thead><tr><th>时间</th><th>事件</th><th>Actor</th><th>目标</th><th>结果</th><th>风险</th><th>请求</th></tr></thead><tbody>{events.data?.items.map((item) => <tr key={item.eventId} tabIndex={0} onClick={() => setQuery(setParams, search, { eventId: item.eventId })} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') setQuery(setParams, search, { eventId: item.eventId }); }}><td><time dateTime={item.occurredAt}>{item.occurredAt}</time></td><td>{item.eventNameKnown ? item.eventName : '未登记事件'}</td><td>{item.actor.displayName}</td><td>{item.resource.displayName}</td><td>{item.outcome.status}</td><td>{item.risk.level}</td><td>{item.request.requestId}</td></tr>)}</tbody></table></div>
        <div aria-label="审计游标分页"><button type="button" disabled={!events.data?.pageInfo.hasPreviousPage} onClick={() => setQuery(setParams, search, { before: events.data?.pageInfo.startCursor ?? undefined, after: undefined, eventId: undefined })}>上一组</button><button type="button" disabled={!events.data?.pageInfo.hasNextPage} onClick={() => setQuery(setParams, search, { after: events.data?.pageInfo.endCursor ?? undefined, before: undefined, eventId: undefined })}>下一组</button></div>
      </AuditRegionState>

      <section style={{ marginTop: 16 }}><AuditRegionState status="feature-unavailable" label="审计导出与合规功能" /><p>{visibility.exportControls ? '已具备审计导出能力，但后端预检与下载闭环尚未冻结；当前不发送写请求。' : '当前能力仅允许读取审计投影；导出、保留策略和 Legal Hold 均不可操作。'}</p></section>

      {search.eventId ? (
        <AuditRegionState status={detail.error ? statusFromError(detail.error) : detail.isPending ? 'first-loading' : 'ready'} label="审计事件详情" requestId={isDomainError(detail.error) ? detail.error.requestId : null} onRetry={() => void detail.refetch()}>
          {detail.data ? <AuditInspector event={detail.data} visibility={visibility} onClose={() => setQuery(setParams, search, { eventId: undefined })} /> : null}
        </AuditRegionState>
      ) : null}
    </main>
  );
}

export default AuditPage;
