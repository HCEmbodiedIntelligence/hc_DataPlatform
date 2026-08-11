import { useMemo } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import type { CleaningDraft } from '../../entities/cleaning-draft';
import {
  useCleaningDraftDetail,
  useCleaningDraftEvents,
  useCleaningDraftSummary,
  useCleaningDrafts,
} from '../../features/cleaning/api';
import '../../features/cleaning/cleaning.css';
import { CleaningStatePanel, cleaningStateFromError } from '../../features/cleaning/page-state';
import { routes as cleaningRoutes, type CleaningDraftScope } from '../../features/cleaning/routing';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { EmptyState, PageHeader, StatusBadge } from '../../shared/ui';
import { cleaningDraftsQueryCodec, type CleaningDraftsSearch } from './query-codec';

const scopeTabs: readonly { value: CleaningDraftScope; label: string }[] = [
  { value: 'mine', label: '我的草稿' },
  { value: 'actionable', label: '待处理' },
  { value: 'review', label: '待复核' },
  { value: 'returned', label: '已退回' },
  { value: 'submitted', label: '已提交' },
  { value: 'all', label: '全部可见' },
];

function primaryState(draft: Omit<CleaningDraft, 'createdAt'>): string {
  if (draft.hasUnknownState) return 'UNKNOWN（只读）';
  if (draft.outputVersionStatus === 'RETURNED') return '复核退回';
  if (draft.outputVersionStatus === 'READY') return '版本可用';
  if (draft.outputVersionStatus === 'REVIEWING') return '等待复核';
  if (draft.commitStatus === 'QUEUED') return '正在提交';
  if (draft.commitStatus === 'FAILED') return '提交失败';
  if (draft.previewStatus === 'RUNNING' || draft.previewStatus === 'QUEUED') return '预览生成中';
  if (draft.previewStatus === 'FAILED') return '预览失败';
  if (draft.previewStatus === 'EXPIRED' || draft.previewStatus === 'STALE') return '预览需重建';
  return draft.status === 'EDITING' ? '编辑中' : '已提交';
}

function tone(draft: Omit<CleaningDraft, 'createdAt'>): 'neutral' | 'info' | 'success' | 'warning' | 'danger' {
  if (draft.hasUnknownState || draft.outputVersionStatus === 'RETURNED') return 'warning';
  if (draft.commitStatus === 'FAILED' || draft.previewStatus === 'FAILED') return 'danger';
  if (draft.outputVersionStatus === 'READY') return 'success';
  if (draft.status === 'EDITING') return 'info';
  return 'neutral';
}

function DraftInspector({
  search,
  close,
}: Readonly<{ search: CleaningDraftsSearch; close(): void }>) {
  const detail = useCleaningDraftDetail(search.draftId, true);
  const events = useCleaningDraftEvents(search.draftId, Boolean(detail.data));
  if (detail.isPending) return <aside className="cleaning-inspector"><CleaningStatePanel state="first-loading" label="草稿详情" /></aside>;
  if (detail.error) return <aside className="cleaning-inspector"><button type="button" onClick={close}>关闭详情</button><CleaningStatePanel state={cleaningStateFromError(detail.error)} label="草稿详情" error={detail.error} onRetry={() => void detail.refetch()} /></aside>;
  if (!detail.data) return null;
  const { draft, relationships } = detail.data;
  const workbenchId = draft.outputVersionStatus === 'RETURNED' && relationships.successorDraftId
    ? relationships.successorDraftId
    : draft.id;
  const canOpen = !draft.hasUnknownState && (
    draft.allowedActions.includes('EDIT') || draft.allowedActions.includes('VIEW') ||
    draft.allowedActions.includes('OPEN_SUCCESSOR')
  );
  return (
    <aside className="cleaning-inspector" role="dialog" aria-modal="false" aria-labelledby="draft-inspector-title">
      <button type="button" onClick={close}>关闭详情</button>
      <h2 id="draft-inspector-title">草稿详情</h2>
      <dl>
        <dt>稳定 Draft ID</dt><dd><code>{draft.id}</code></dd>
        <dt>固定 Base</dt><dd><code>{draft.baseVersionId}</code> / <code>{draft.baseRevisionId}</code></dd>
        <dt>Episode</dt><dd><code>{draft.episodeId}</code></dd>
        <dt>五轴状态</dt><dd>Draft {draft.status} · Preview {draft.previewStatus} · Commit {draft.commitStatus} · Review {draft.outputVersionStatus ?? 'NONE'}</dd>
      </dl>
      {draft.origin.kind === 'ISSUE_DERIVED' ? (
        <section>
          <h3>ManualIssue 来源</h3>
          {draft.origin.manualIssueIds.map((id) => <Link key={id} to={cleaningRoutes.manualIssues.build({ issueId: id })}><code>{id}</code></Link>)}
          <p>[{draft.origin.startNs}, {draft.origin.endNs}) ns · Stream <code>{draft.origin.selectedStreamId}</code></p>
        </section>
      ) : (
        <section>
          <h3>Review Return 血缘</h3>
          <p>原草稿 <code>{draft.origin.supersedesDraftId}</code></p>
          <p>退回 Version <code>{draft.origin.returnedFromVersionId}</code></p>
          <p>不可变 Decision <code>{draft.origin.returnedFromReviewDecisionId}</code></p>
        </section>
      )}
      <section>
        <h3>输出与返工</h3>
        <p>Commit：<code>{relationships.commitId ?? '—'}</code></p>
        <p>Output Version：<code>{relationships.outputVersionId ?? '—'}</code></p>
        {relationships.outputRevisionIds.map((id) => <p key={id}>Output Revision：<code>{id}</code></p>)}
        {relationships.reviewFindingIds.length ? <p>只读 Finding：{relationships.reviewFindingIds.join('、')}</p> : null}
        {canOpen ? <Link className="primary-link" to={cleaningRoutes.cleaningWorkbench.build({ draftId: workbenchId, returnTo: cleaningDraftsQueryCodec.build(search) })}>{draft.outputVersionStatus === 'RETURNED' ? '打开后继草稿' : '打开清洗工作台'}</Link> : null}
      </section>
      <section aria-label="草稿近期事件">
        <h3>近期事件</h3>
        {events.isPending ? <p role="status">事件加载中…</p> : events.error ? <CleaningStatePanel state={cleaningStateFromError(events.error)} label="近期事件" error={events.error} onRetry={() => void events.refetch()} /> : (
          <ol>{events.data?.items.map((event) => <li key={event.eventId}><time>{event.occurredAt}</time> · {event.eventType}<br />{event.safeSummary ?? '无安全摘要'}</li>)}</ol>
        )}
      </section>
    </aside>
  );
}

export function CleaningDraftsPage() {
  const capabilities = useCapabilities();
  const canRead = !capabilities.loading && !capabilities.failed && capabilities.has('cleaning.read');
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const search = cleaningDraftsQueryCodec.parse(params);
  const list = useCleaningDrafts(search, canRead);
  const summary = useCleaningDraftSummary(search, canRead);
  const rows = useMemo(() => list.data?.items ?? [], [list.data]);
  const filtered = Boolean(search.q || search.datasetId || search.baseVersionId || search.episodeId || search.previewStatus?.length || search.commitStatus?.length || search.versionReviewStatus?.length);
  const change = (changes: Parameters<typeof cleaningDraftsQueryCodec.withChanges>[1]) => {
    void navigate(cleaningDraftsQueryCodec.build(cleaningDraftsQueryCodec.withChanges(search, changes)));
  };

  if (capabilities.loading) return <main className="cleaning-page"><PageHeader title="清洗草稿" /><CleaningStatePanel state="first-loading" label="权限" /></main>;
  if (!canRead) return <main className="cleaning-page"><PageHeader title="清洗草稿" /><CleaningStatePanel state="forbidden" label="清洗读取权限" /></main>;
  if (list.isPending) return <main className="cleaning-page"><PageHeader title="清洗草稿" description="只读聚合、检索与交接" /><CleaningStatePanel state="first-loading" label="清洗草稿列表" /></main>;
  if (list.error) return <main className="cleaning-page"><PageHeader title="清洗草稿" /><CleaningStatePanel state={cleaningStateFromError(list.error, true)} label="清洗草稿列表" error={list.error} onRetry={() => void list.refetch()} /></main>;

  return (
    <main className="cleaning-page">
      <PageHeader title="清洗草稿" description="P10 只读聚合五条正交状态轴；所有编辑、Preview 与 Commit 都在 P11 完成。" breadcrumbs={[{ label: '手动清洗' }, { label: '清洗草稿' }]} actions={<Link to={cleaningRoutes.manualIssues.build({})}>从人工问题进入清洗</Link>} />
      <nav className="cleaning-projection-tabs" aria-label="草稿范围">
        {scopeTabs.map((tab) => <Link key={tab.value} aria-current={search.scope === tab.value ? 'page' : undefined} to={cleaningDraftsQueryCodec.build({ ...search, scope: tab.value, after: undefined, before: undefined })}>{tab.label}</Link>)}
      </nav>
      {summary.error ? <CleaningStatePanel state={cleaningStateFromError(summary.error)} label="草稿摘要" error={summary.error} onRetry={() => void summary.refetch()} /> : (
        <section className="cleaning-summary-grid" aria-label="草稿摘要" aria-busy={summary.isFetching}>
          <article className="cleaning-summary-card"><span>编辑中</span><strong>{summary.data?.scopeCounts.editing ?? '—'}</strong></article>
          <article className="cleaning-summary-card"><span>已提交</span><strong>{summary.data?.scopeCounts.committed ?? '—'}</strong></article>
          <article className="cleaning-summary-card"><span>已退回</span><strong>{summary.data?.scopeCounts.returned ?? '—'}</strong></article>
          <article className="cleaning-summary-card"><span>活跃任务</span><strong>{summary.data ? `${summary.data.jobs.previewQueued}/${summary.data.jobs.previewRunning}/${summary.data.jobs.commitQueued}` : '—'}</strong></article>
        </section>
      )}
      <form className="cleaning-toolbar" aria-label="草稿筛选" onSubmit={(event) => event.preventDefault()}>
        <label>搜索<input value={search.q ?? ''} onChange={(event) => change({ q: event.target.value || undefined })} placeholder="Draft / Version / Episode" /></label>
        <label>状态<select value={search.status} onChange={(event) => change({ status: event.target.value as typeof search.status })}><option value="active">活跃</option><option value="submitted">已提交</option><option value="failed">失败</option><option value="archived">已归档</option></select></label>
        <label>Preview<select value={search.previewStatus?.[0] ?? ''} onChange={(event) => change({ previewStatus: event.target.value ? [event.target.value] : undefined })}><option value="">全部</option><option>NONE</option><option>QUEUED</option><option>RUNNING</option><option>READY</option><option>FAILED</option><option>EXPIRED</option><option>STALE</option></select></label>
        <label>排序<select value={search.sort} onChange={(event) => change({ sort: event.target.value as typeof search.sort })}><option value="updatedAtDesc">最近更新</option><option value="updatedAtAsc">最早更新</option><option value="createdAtDesc">最近创建</option><option value="reuseRatioDesc">复用率</option><option value="effectiveDurationDesc">有效时长</option></select></label>
        <label>每页<select value={search.limit} onChange={(event) => change({ limit: Number.parseInt(event.target.value, 10) as 20 | 50 | 100 })}><option>20</option><option>50</option><option>100</option></select></label>
        {filtered ? <button type="button" onClick={() => void navigate(cleaningDraftsQueryCodec.build({ scope: search.scope }))}>清除筛选</button> : null}
      </form>
      {list.isFetching ? <p className="cleaning-refreshing" role="status">正在刷新只读投影…</p> : null}
      <div className={search.draftId ? 'cleaning-list-inspector-layout' : undefined}>
        <section className="cleaning-table-shell" aria-label="清洗草稿表格">
          {rows.length === 0 ? <EmptyState kind={filtered ? 'filtered-empty' : 'no-data'} title={filtered ? '当前筛选没有草稿' : '当前作用域没有清洗草稿'} /> : (
            <table className="standard-table">
              <caption className="sr-only">清洗草稿只读列表</caption>
              <thead><tr><th>草稿</th><th>来源</th><th>主状态</th><th>Preview</th><th>Commit</th><th>Output Review</th><th>更新时间</th><th>操作</th></tr></thead>
              <tbody>{rows.map((draft) => <tr key={draft.id}>
                <th scope="row"><button className="cleaning-link-button" type="button" onClick={() => change({ draftId: draft.id })}><code>{draft.id}</code></button><br /><small>{draft.episodeId}</small></th>
                <td>{draft.origin.kind === 'ISSUE_DERIVED' ? <>ManualIssue<br /><code>{draft.origin.manualIssueIds[0]}</code></> : <>Review Return<br /><code>{draft.origin.returnedFromReviewDecisionId}</code></>}</td>
                <td><StatusBadge status={primaryState(draft)} tone={tone(draft)} /></td>
                <td>{draft.previewStatus}</td><td>{draft.commitStatus}</td><td>{draft.outputVersionStatus ?? '—'}</td><td>{draft.updatedAt}</td>
                <td><Link to={cleaningRoutes.cleaningWorkbench.build({ draftId: draft.outputVersionStatus === 'RETURNED' && draft.review ? draft.review.successorDraftId : draft.id, returnTo: cleaningDraftsQueryCodec.build(search) })}>{draft.outputVersionStatus === 'RETURNED' ? '返工' : draft.status === 'EDITING' ? '继续' : '查看'}</Link></td>
              </tr>)}</tbody>
            </table>
          )}
        </section>
        {search.draftId ? <DraftInspector search={search} close={() => change({ draftId: undefined })} /> : null}
      </div>
      <nav className="cursor-pager" aria-label="清洗草稿游标分页">
        <button type="button" disabled={!list.data?.pageInfo.hasPrevious || !list.data.pageInfo.before} onClick={() => change({ before: list.data?.pageInfo.before ?? undefined, after: undefined })}>上一组</button>
        <span>快照 {list.data?.snapshotAt ?? '—'}</span>
        <button type="button" disabled={!list.data?.pageInfo.hasNext || !list.data.pageInfo.after} onClick={() => change({ after: list.data?.pageInfo.after ?? undefined, before: undefined })}>下一组</button>
      </nav>
    </main>
  );
}

export const Component = CleaningDraftsPage;
export default CleaningDraftsPage;
