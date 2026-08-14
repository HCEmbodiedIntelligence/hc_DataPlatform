import { Button } from 'antd';
import { Download, ShieldCheck, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import {
  useAuditBootstrap,
  useAuditEvent,
  useAuditEvents,
  useAuditFacets,
} from '../../features/audit/api/queries';
import { resolveAuditFieldVisibility } from '../../features/audit/field-visibility';
import { patchAuditSearch, type AuditSearch } from '../../features/audit/routing';
import type { AuditScope } from '../../features/audit/types';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { useShellStore } from '../../shared/scope/shell-store';
import {
  DataCursorPager,
  PageState,
  StandardPageScaffold,
  type MetricState,
  type PageStateKind,
} from '../../shared/ui';
import { AuditEventTable } from './components/AuditEventTable';
import { AuditFilterPanel } from './components/AuditFilterPanel';
import {
  AuditInspectorContent,
  AuditInspectorDrawer,
} from './components/AuditInspectorDrawer';
import { AuditSummaryStrip } from './components/AuditSummaryStrip';
import { auditQueryCodec } from './query-codec';
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

function auditStateTitle(state: PageStateKind): string | undefined {
  return state === 'contract-mismatch' ? '审计投影不符合合同' : undefined;
}

function hasFilters(search: AuditSearch): boolean {
  return Boolean(
    search.actorId.length ||
      search.eventName.length ||
      search.resourceType.length ||
      search.resourceId ||
      search.result.length ||
      search.riskLevel.length ||
      search.requestId,
  );
}

function eventsState(
  query: Readonly<{
    data?: { readonly items: readonly unknown[] };
    isPending: boolean;
    isFetching: boolean;
    error: unknown;
  }>,
  filtered: boolean,
): PageStateKind | 'ready' {
  if (query.isPending && query.data === undefined) return 'loading';
  if (query.error) return stateFromError(query.error);
  if (query.data?.items.length === 0) return filtered ? 'filtered-empty' : 'empty';
  if (query.isFetching && query.data !== undefined) return 'refreshing';
  return 'ready';
}

function renderRegion(
  state: PageStateKind | 'ready',
  label: string,
  error: unknown,
  content: ReactNode,
  onRetry: () => void,
  action?: ReactNode,
): ReactNode {
  if (state === 'ready') return content;
  if (state === 'refreshing') {
    return <PageState state="refreshing" label={label}>{content}</PageState>;
  }
  return (
    <PageState
      state={state}
      label={label}
      title={auditStateTitle(state)}
      requestId={requestId(error)}
      onRetry={state === 'empty' || state === 'filtered-empty' ? undefined : onRetry}
      action={action}
    />
  );
}

const desktopInspectorQuery = '(min-width: 1200px)';

function useDesktopInspector(): boolean {
  const [desktop, setDesktop] = useState(
    () => typeof window !== 'undefined' && window.matchMedia?.(desktopInspectorQuery).matches === true,
  );

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return undefined;
    const media = window.matchMedia(desktopInspectorQuery);
    const update = () => setDesktop(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);

  return desktop;
}

export function AuditPage() {
  const shellScope = useShellStore((state) => state.scope);
  const scopeKey = useShellStore((state) => state.scopeKey);
  const scope: AuditScope | null = shellScope?.projectId
    ? {
        organizationId: shellScope.organizationId,
        projectId: shellScope.projectId,
        regionCode: shellScope.regionCode ?? null,
      }
    : null;
  const capabilities = useCapabilities();
  const desktopInspector = useDesktopInspector();
  const queryClient = useQueryClient();
  const inspectorTriggerId = useRef<string | null>(null);
  const [params, setParams] = useSearchParams();
  const parsedSearch = useMemo(() => auditQueryCodec.parse(params), [params]);
  const previousScopeKey = useRef(scopeKey);
  const scopeChanged = previousScopeKey.current !== scopeKey;
  const search: AuditSearch = useMemo(
    () => scopeChanged
      ? { ...parsedSearch, after: undefined, before: undefined, eventId: undefined }
      : parsedSearch,
    [parsedSearch, scopeChanged],
  );
  const canRead =
    !capabilities.loading &&
    !capabilities.failed &&
    capabilities.has('audit.read');
  const visibility = resolveAuditFieldVisibility(capabilities.has);
  const bootstrap = useAuditBootstrap(scope, search, canRead);
  const facets = useAuditFacets(scope, search, canRead);
  const events = useAuditEvents(scope, search, canRead);
  const detail = useAuditEvent(scope, search.eventId, canRead && search.eventId !== undefined);

  const change = useCallback(
    (patch: Partial<AuditSearch>) => {
      setParams(auditQueryCodec.build(patchAuditSearch(search, patch)));
    },
    [search, setParams],
  );

  const openInspector = useCallback((eventId: string) => {
    inspectorTriggerId.current = `audit-event-trigger-${eventId}`;
    change({ eventId });
  }, [change]);

  const closeInspector = useCallback(() => {
    const triggerId = inspectorTriggerId.current;
    change({ eventId: undefined });
    globalThis.setTimeout(() => {
      if (triggerId) document.getElementById(triggerId)?.focus();
    }, 350);
  }, [change]);

  useEffect(() => {
    if (!capabilities.loading && !canRead) queryClient.removeQueries({ queryKey: ['audit'] });
  }, [canRead, capabilities.loading, queryClient]);

  useEffect(() => {
    if (previousScopeKey.current === scopeKey) return;
    previousScopeKey.current = scopeKey;
    if (parsedSearch.after || parsedSearch.before || parsedSearch.eventId) {
      setParams(
        auditQueryCodec.build({
          ...parsedSearch,
          after: undefined,
          before: undefined,
          eventId: undefined,
        }),
        { replace: true },
      );
    }
  }, [parsedSearch, scopeKey, setParams]);

  const fatal = events.error && bootstrap.error;
  const pageState: PageStateKind | 'ready' = capabilities.loading
    ? 'loading'
    : !canRead
      ? 'forbidden'
      : !scope
        ? 'feature-unavailable'
        : fatal
          ? stateFromError(events.error)
          : 'ready';
  const summaryState: MetricState = bootstrap.isPending
    ? 'loading'
    : bootstrap.error
      ? 'error'
      : bootstrap.data
        ? 'ready'
        : 'unknown';
  const resolvedEventsState = eventsState(events, hasFilters(search));
  const eventTable = events.data ? (
    <div className={styles.contentStack}>
      {events.data.hasUnknownEnum ? <PageState state="unknown" label="审计事件未知枚举" /> : null}
      <AuditEventTable events={events.data.items} selectedEventId={search.eventId} onOpen={openInspector} />
    </div>
  ) : null;
  const listContent = renderRegion(
    resolvedEventsState,
    '审计事件列表',
    events.error,
    eventTable,
    () => void events.refetch(),
    resolvedEventsState === 'filtered-empty' ? (
      <Button onClick={() => change({
        actorId: [],
        eventName: [],
        resourceType: [],
        resourceId: undefined,
        result: [],
        riskLevel: [],
        requestId: undefined,
      })}>
        清除筛选
      </Button>
    ) : undefined,
  );

  const filters = facets.isPending ? (
    <PageState state="loading" label="审计筛选" />
  ) : facets.error ? (
    <PageState
      state={stateFromError(facets.error)}
      label="审计筛选"
      title={auditStateTitle(stateFromError(facets.error))}
      requestId={requestId(facets.error)}
      onRetry={() => void facets.refetch()}
    />
  ) : (
    <div className={styles.contentStack}>
      {facets.data?.hasUnknownEventName ? (
        <PageState state="unknown" label="审计 Facet 未登记事件" />
      ) : null}
      <AuditFilterPanel search={search} onChange={change} />
    </div>
  );

  const readyContent = (
    <div className={styles.contentStack}>
      {listContent}
      {events.data ? (
        <section className={styles.auditWindowFacts} aria-label="当前审计窗口事实">
          <div><span>返回事件</span><strong>{events.data.items.length}</strong></div>
          <div><span>快照时间</span><strong><time dateTime={events.data.snapshotAt}>{events.data.snapshotAt}</time></strong></div>
          <div><span>脱敏策略</span><strong>{events.data.redactionPolicyVersion}</strong></div>
          <div><span>省略字段类别</span><strong>{events.data.omittedFieldClasses.join('、') || '无'}</strong></div>
        </section>
      ) : null}
      <section className={styles.unavailableNotice} role="status" aria-label="审计导出与合规功能尚未开放">
        <ShieldCheck aria-hidden="true" size={16} />
        <div>
          <strong>日志不可篡改，系统按保留策略归档。</strong>
          <span>{visibility.exportControls
            ? ' 导出预检与下载闭环尚未冻结，当前不发送写请求。'
            : ' 当前能力仅允许读取审计投影；导出与保留策略不可操作。'}</span>
        </div>
      </section>
    </div>
  );
  const blockingState: PageStateKind = pageState === 'ready' ? 'error' : pageState;
  const detailState: PageStateKind | 'ready' = detail.isPending
    ? 'loading'
    : detail.error
      ? stateFromError(detail.error)
      : detail.data
        ? 'ready'
        : 'error';
  const inspectorOpen = Boolean(search.eventId);
  const inspectorContent = (
    <AuditInspectorContent
      state={detailState}
      event={detail.data}
      visibility={visibility}
      requestId={requestId(detail.error)}
      onRetry={detail.isError ? () => void detail.refetch() : undefined}
    />
  );

  return (
    <main className={styles.page} data-page-id="P19">
      <div className={desktopInspector && inspectorOpen ? styles.desktopSplit : undefined}>
        <StandardPageScaffold
        header={{
          title: '审计日志',
          description: '当前读取者可见的脱敏、追加式审计投影。',
          breadcrumbs: [
            { key: 'settings', label: '系统管理' },
            { key: 'audit', label: '审计日志' },
          ],
          metadata: bootstrap.data ? (
            <span>目录版本：<code>{bootstrap.data.catalogVersion}</code> · 策略版本：<code>{bootstrap.data.policyVersion}</code></span>
          ) : undefined,
          actions: (
            <>
              <Button disabled icon={<Download aria-hidden="true" size={16} />} title="导出合同尚未冻结">
                导出日志
              </Button>
              <Button disabled icon={<ShieldCheck aria-hidden="true" size={16} />} title="策略写合同尚未冻结">
                审计策略
              </Button>
            </>
          ),
        }}
        summary={pageState === 'ready' ? (
          <AuditSummaryStrip bootstrap={bootstrap.data} state={summaryState} />
        ) : undefined}
        filters={pageState === 'ready' ? filters : undefined}
        state={pageState === 'ready' ? readyContent : (
          <PageState
            state={blockingState}
            label="审计日志"
            title={auditStateTitle(blockingState)}
            requestId={requestId(events.error)}
            onRetry={fatal ? () => void Promise.all([
              events.refetch(),
              bootstrap.refetch(),
              facets.refetch(),
            ]) : undefined}
          />
        )}
        pagination={events.data && events.data.items.length > 0 ? (
          <DataCursorPager
            pageInfo={events.data.pageInfo}
            busy={events.isFetching}
            windowLabel={`当前窗口 ${events.data.items.length} 条 · 快照 ${events.data.snapshotAt}`}
            onChange={(cursor) => change({ ...cursor, eventId: undefined })}
          />
        ) : undefined}
        />

        {desktopInspector && inspectorOpen ? (
          <section
            className={styles.desktopInspector}
            role="complementary"
            aria-labelledby="audit-desktop-inspector-title"
          >
            <header className={styles.inspectorHeader}>
              <div>
                <span className={styles.inspectorEyebrow}>选中事件</span>
                <h2 id="audit-desktop-inspector-title">事件详情</h2>
              </div>
              <Button
                type="text"
                aria-label="关闭事件详情"
                icon={<X aria-hidden="true" size={17} />}
                onClick={closeInspector}
              />
            </header>
            <div className={styles.inspectorBody}>{inspectorContent}</div>
          </section>
        ) : null}
      </div>

      {!desktopInspector ? <AuditInspectorDrawer
        open={inspectorOpen}
        state={detailState}
        event={detail.data}
        visibility={visibility}
        requestId={requestId(detail.error)}
        onRetry={detail.isError ? () => void detail.refetch() : undefined}
        onClose={closeInspector}
      /> : null}
    </main>
  );
}

export default AuditPage;
