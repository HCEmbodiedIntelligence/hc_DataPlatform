import { useEffect, useMemo, useState } from 'react';
import type { JSX } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import type { AnnotationTask } from '../../entities/annotation-task';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { isDomainError } from '../../shared/api/domain-error';
import { useShellStore } from '../../shared/scope/shell-store';
import { AnnotationPageState, createIdempotencyKey, useAnnotationTasks, useClaimAnnotationTask } from '../../features/annotation';
import { annotationCapabilities, decideAnnotationCapability } from '../../features/annotation/capabilities';
import type { AnnotationScope } from '../../features/annotation/api/client';
import { annotationRoutes } from './routes';
import { annotationQueueQueryCodec } from './query-codec';
import './p08.css';

function TaskRow({ task, scope, canClaim, onOpen }: { task: AnnotationTask; scope: AnnotationScope; canClaim: boolean; onOpen: (id: string) => void }): JSX.Element {
  const claim = useClaimAnnotationTask(scope, task.id);
  const allowed = canClaim && task.allowedActions.has('CLAIM') && task.displayState === 'UNASSIGNED';
  return (
    <tr>
      <th scope="row"><button type="button" className="p08-link-button" onClick={() => onOpen(task.id)}>{task.id}</button></th>
      <td><span className={`p08-badge p08-badge--${task.displayState.toLowerCase()}`}>{task.displayState}</span></td>
      <td>{task.source.datasetId}</td>
      <td>{task.source.episodeId}</td>
      <td>{task.ontology.version}</td>
      <td>{task.source.startNs}–{task.source.endNs}</td>
      <td>{task.priority}</td>
      <td>
        {allowed ? <button type="button" disabled={claim.isPending} onClick={() => claim.mutate({ ...scope, taskId: task.id, etag: task.etag, idempotencyKey: createIdempotencyKey(), clientSessionId: createIdempotencyKey() })}>{claim.isPending ? '领取中…' : '领取任务'}</button> : <button type="button" onClick={() => onOpen(task.id)}>打开任务</button>}
        {claim.error ? <span role="alert">领取失败，任务事实已刷新。</span> : null}
      </td>
    </tr>
  );
}

export function AnnotationQueuePage(): JSX.Element {
  const location = useLocation();
  const navigate = useNavigate();
  const capabilities = useCapabilities();
  const shellScope = useShellStore((state) => state.scope);
  const canAssign = capabilities.has('annotation_task.assign');
  const search = useMemo(() => annotationQueueQueryCodec.parse(location.search, canAssign), [canAssign, location.search]);
  const [q, setQ] = useState(search.q ?? '');
  const scope = shellScope?.projectId && shellScope.regionCode ? { projectId: shellScope.projectId, regionCode: shellScope.regionCode } : null;
  const canRead = capabilities.has('annotation_task.read');
  const query = useAnnotationTasks(scope, {
    queue: search.queue,
    states: search.states,
    datasetId: search.datasetId,
    schemaVersionId: search.schemaVersionId,
    assigneeId: search.assigneeId,
    q: search.q,
    sort: search.sort,
    after: search.after,
    before: search.before,
    limit: search.limit,
  }, canRead && !capabilities.loading && !capabilities.failed);

  useEffect(() => { setQ(search.q ?? ''); }, [search.q]);

  useEffect(() => {
    if (q === (search.q ?? '')) return;
    const timer = setTimeout(() => {
      const next = { ...search, q: q.trim() || undefined, after: undefined, before: undefined };
      void navigate(annotationRoutes.queue.build(next), { replace: true });
    }, 300);
    return () => clearTimeout(timer);
  }, [navigate, q, search]);

  const granted = useMemo(() => new Set(annotationCapabilities.filter((capability) => capabilities.has(capability))), [capabilities]);
  const claimDecision = decideAnnotationCapability(granted, 'annotation_task.claim');
  const setSearch = (next: typeof search): void => { void navigate(annotationRoutes.queue.build(next)); };

  if (capabilities.loading) return <AnnotationPageState kind="first-loading" />;
  if (capabilities.failed || !canRead) return <AnnotationPageState kind="forbidden" />;
  if (!scope) return <AnnotationPageState kind="feature-unavailable" detail="需要选择有效项目和 Region。" />;
  if (query.isLoading || (!query.data && !query.error)) return <AnnotationPageState kind="first-loading" />;
  if (query.error) {
    const error = query.error;
    const kind = isDomainError(error) && error.code === 'FORBIDDEN' ? 'forbidden'
      : isDomainError(error) && error.code === 'RATE_LIMITED' ? 'rate-limited'
      : isDomainError(error) && error.code === 'CONTRACT_MISMATCH' ? 'contract-mismatch'
      : isDomainError(error) && error.code === 'NETWORK_ERROR' ? 'offline-reconnecting'
      : 'fatal-error';
    return <AnnotationPageState kind={kind} requestId={isDomainError(error) ? error.requestId ?? undefined : undefined} onRetry={() => void query.refetch()} />;
  }

  const result = query.data;
  return (
    <main className="p08-page p08-queue-page">
      <header className="p08-page-header"><div><p className="p08-eyebrow">数据标注 / 任务中心</p><h1>数据标注</h1><p>领取、保存并提交固定 Revision 上的语义标注任务。</p></div><button type="button" onClick={() => void query.refetch()}>刷新</button></header>
      {query.isFetching ? <AnnotationPageState kind="refreshing" /> : null}
      <nav className="p08-tabs" role="tablist" aria-label="标注任务队列">
        <button type="button" role="tab" aria-selected={search.queue === 'assigned_to_me'} onClick={() => setSearch({ ...search, queue: 'assigned_to_me', after: undefined, before: undefined })}>分配给我</button>
        <button type="button" role="tab" aria-selected={search.queue === 'claimable'} onClick={() => setSearch({ ...search, queue: 'claimable', after: undefined, before: undefined })}>可领取</button>
      </nav>
      <section className="p08-filter-bar" aria-label="服务端筛选">
        <label>搜索任务或 Episode<input value={q} onChange={(event) => setQ(event.target.value)} /></label>
        <label>Dataset ID<input value={search.datasetId ?? ''} onChange={(event) => setSearch({ ...search, datasetId: event.target.value || undefined, after: undefined, before: undefined })} /></label>
        <label>状态<select value={search.states[0] ?? ''} onChange={(event) => setSearch({ ...search, states: event.target.value ? [event.target.value as typeof search.states[number]] : [], after: undefined, before: undefined })}><option value="">全部</option>{['UNASSIGNED','ASSIGNED','IN_PROGRESS','SUBMITTED','RETURNED','COMPLETED','BLOCKED','STALE'].map((state) => <option key={state}>{state}</option>)}</select></label>
      </section>
      {claimDecision.state === 'feature-unavailable' ? <AnnotationPageState kind="feature-unavailable" detail={claimDecision.reason} /> : null}
      {!result.items.length ? <AnnotationPageState kind="empty" detail={search.q || search.datasetId || search.states.length ? '当前筛选无结果。' : undefined} /> : (
        <div className="p08-table-wrap"><table><caption>标注任务，快照时间 {result.snapshotAt}</caption><thead><tr><th>任务</th><th>状态</th><th>Dataset</th><th>Episode</th><th>Schema</th><th>范围(ns)</th><th>优先级</th><th>操作</th></tr></thead><tbody>{result.items.map((task) => <TaskRow key={task.id} task={task} scope={scope} canClaim={claimDecision.state === 'allowed'} onOpen={(id) => { void navigate(annotationRoutes.task.build({ taskId: id }, { returnTo: `${location.pathname}${location.search}` })); }} />)}</tbody></table></div>
      )}
      <footer className="p08-pagination"><button type="button" disabled={!result.pageInfo.hasPreviousPage || !result.pageInfo.startCursor} onClick={() => setSearch({ ...search, before: result.pageInfo.startCursor ?? undefined, after: undefined })}>上一组</button><span>每组 {search.limit}</span><button type="button" disabled={!result.pageInfo.hasNextPage || !result.pageInfo.endCursor} onClick={() => setSearch({ ...search, after: result.pageInfo.endCursor ?? undefined, before: undefined })}>下一组</button></footer>
    </main>
  );
}
