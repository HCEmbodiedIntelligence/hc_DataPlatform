import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { isDatasetId } from '../../entities/dataset';
import { isDatasetVersionId } from '../../entities/dataset-version';
import { isEpisodeId } from '../../entities/episode';
import type { ManualIssue, ManualIssueListItem, ManualIssueSeverity } from '../../entities/manual-issue';
import {
  useCreateDraftFromManualIssue,
  useManualIssue,
  useManualIssues,
  useManualIssuesPage,
  useResolveManualIssue,
  useTriageManualIssue,
} from '../../features/cleaning/api';
import '../../features/cleaning/cleaning.css';
import { CleaningStatePanel, cleaningStateFromError } from '../../features/cleaning/page-state';
import { manualIssuesQueryCodec, routes as cleaningRoutes } from '../../features/cleaning/routing';
import { routes as datasetRoutes } from '../../features/datasets/routing';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { EmptyState, PageHeader, StatusBadge } from '../../shared/ui';

type DialogState =
  | { readonly kind: 'triage'; readonly issue: ManualIssueListItem }
  | { readonly kind: 'resolve'; readonly issue: ManualIssueListItem }
  | null;

function mutationKey(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `manual-issue-${Date.now().toString(36)}`;
}

function issueStatus(issue: ManualIssueListItem): string {
  return issue.status.kind === 'known' ? issue.status.value : 'UNKNOWN';
}

function statusTone(issue: ManualIssueListItem): 'neutral' | 'info' | 'success' | 'warning' {
  if (issue.status.kind === 'unknown') return 'warning';
  if (issue.status.value === 'RESOLVED') return 'success';
  if (issue.status.value === 'IN_PROGRESS') return 'info';
  return 'neutral';
}

function viewerHref(issue: ManualIssueListItem, returnTo: string): string | null {
  const source = issue.source;
  if (!isDatasetId(source.datasetId) || !isDatasetVersionId(source.versionId) || !isEpisodeId(source.episodeId)) {
    return null;
  }
  // The P06 owner currently exposes only stable Episode identity. The missing typed
  // time-range input is tracked in docs/dep-requests/T6.md; never splice it here.
  return datasetRoutes.episodeViewer.build({
    datasetId: source.datasetId,
    versionId: source.versionId,
    episodeId: source.episodeId,
    returnTo,
  });
}

function IssueDetail({ issue, onClose }: Readonly<{ issue: ManualIssue; onClose(): void }>) {
  return (
    <aside className="cleaning-state-panel" role="dialog" aria-modal="false" aria-labelledby="manual-issue-detail-title">
      <button type="button" onClick={onClose}>关闭详情</button>
      <h2 id="manual-issue-detail-title">问题详情</h2>
      <dl>
        <dt>稳定 Issue ID</dt><dd><code>{issue.id}</code></dd>
        <dt>说明</dt><dd>{issue.note || '未填写'}</dd>
        <dt>固定 Version / Revision / Stream</dt>
        <dd><code>{issue.source.versionId}</code> / <code>{issue.source.revisionId}</code> / <code>{issue.source.streamId}</code></dd>
        <dt>半开范围</dt><dd>[{issue.source.startNs}, {issue.source.endNs}) ns</dd>
        <dt>派生 Draft</dt>
        <dd>{issue.relatedDrafts.length
          ? issue.relatedDrafts.map((draft) => <a key={draft.draftId} href={cleaningRoutes.cleaningWorkbench.build({ draftId: draft.draftId })}>{draft.draftId}（{draft.status}）</a>)
          : '尚未派生 Draft'}</dd>
      </dl>
      {issue.blockedReasons.map((reason) => <p className="cleaning-warning" key={reason.code}>{reason.message}</p>)}
    </aside>
  );
}

export function ManualIssuesPage() {
  const capabilities = useCapabilities();
  const canRead = !capabilities.loading && !capabilities.failed && capabilities.has('manual_issue.read');
  const [params, setParams] = useSearchParams();
  const search = manualIssuesQueryCodec.parse(params);
  const list = useManualIssues(search, canRead);
  const summary = useManualIssuesPage(search, canRead);
  const detail = useManualIssue(search.issueId, canRead);
  const navigate = useNavigate();
  const triage = useTriageManualIssue();
  const resolve = useResolveManualIssue();
  const createDraft = useCreateDraftFromManualIssue();
  const [dialog, setDialog] = useState<DialogState>(null);
  const [reason, setReason] = useState('');
  const [assigneeId, setAssigneeId] = useState('');
  const [severity, setSeverity] = useState<ManualIssueSeverity>('MEDIUM');
  const [resolutionVersionId, setResolutionVersionId] = useState('');
  const [resolutionNote, setResolutionNote] = useState('');
  const [selection, setSelection] = useState<Awaited<ReturnType<typeof createDraft.mutateAsync>> | null>(null);

  const filtered = Boolean(search.q || search.datasetId || search.versionId || search.episodeId ||
    search.status?.length || search.issueType?.length || search.severity?.length || search.assigneeId);
  const currentReturn = cleaningRoutes.manualIssues.build(search);
  const changing = triage.isPending || resolve.isPending || createDraft.isPending;

  const update = (changes: Parameters<typeof manualIssuesQueryCodec.withChanges>[1]) => {
    setParams(manualIssuesQueryCodec.build(manualIssuesQueryCodec.withChanges(search, changes)));
  };

  const performCreateDraft = (issue: ManualIssueListItem) => {
    createDraft.mutate({
      manualIssueId: issue.id,
      expectedVersion: issue.etag,
      idempotencyKey: mutationKey(),
    }, {
      onSuccess(result) {
        if (result.disposition === 'SELECTION_REQUIRED') {
          setSelection(result);
          return;
        }
        setSelection(null);
        void navigate(cleaningRoutes.cleaningWorkbench.build({ draftId: result.draftId }));
      },
    });
  };

  const rows = useMemo(() => list.data?.items ?? [], [list.data]);

  if (capabilities.loading) {
    return <main className="cleaning-page"><PageHeader title="人工问题" /><CleaningStatePanel state="first-loading" label="权限" /></main>;
  }
  if (!canRead) {
    return <main className="cleaning-page"><PageHeader title="人工问题" /><CleaningStatePanel state="forbidden" label="人工问题权限" /></main>;
  }
  if (list.isPending) {
    return <main className="cleaning-page"><PageHeader title="人工问题" description="清洗前或独立发现问题的唯一分诊清单" /><CleaningStatePanel state="first-loading" label="人工问题列表" /></main>;
  }
  if (list.error) {
    return <main className="cleaning-page"><PageHeader title="人工问题" /><CleaningStatePanel state={cleaningStateFromError(list.error, true)} label="人工问题列表" error={list.error} onRetry={() => void list.refetch()} /></main>;
  }

  return (
    <main className="cleaning-page">
      <PageHeader
        title="人工问题"
        description="ManualIssue 是可分诊事实；ReviewFinding 不进入此列表，也不共享任何写操作。"
        breadcrumbs={[{ label: '手动清洗' }, { label: '人工问题' }]}
        actions={<a href={cleaningRoutes.cleaningDrafts.build({})}>查看清洗草稿</a>}
      />

      {summary.error ? <CleaningStatePanel state={cleaningStateFromError(summary.error)} label="问题统计" error={summary.error} onRetry={() => void summary.refetch()} /> : (
        <section className="cleaning-summary-grid" aria-label="问题统计" aria-busy={summary.isFetching}>
          {([['全部', summary.data?.counts.total], ['待处理', summary.data?.counts.open], ['处理中', summary.data?.counts.inProgress], ['已解决', summary.data?.counts.resolved]] as const)
            .map(([label, value]) => <article className="cleaning-summary-card" key={label}><span>{label}</span><strong>{value ?? '—'}</strong></article>)}
        </section>
      )}

      <form className="cleaning-toolbar" aria-label="人工问题筛选" onSubmit={(event) => event.preventDefault()}>
        <label>搜索<input value={search.q ?? ''} onChange={(event) => update({ q: event.target.value || undefined })} placeholder="Issue / Episode / 说明" /></label>
        <label>状态<select value={search.status?.[0] ?? ''} onChange={(event) => update({ status: event.target.value ? [event.target.value as 'OPEN' | 'IN_PROGRESS' | 'RESOLVED'] : undefined })}><option value="">全部</option><option value="OPEN">待处理</option><option value="IN_PROGRESS">处理中</option><option value="RESOLVED">已解决</option></select></label>
        <label>严重度<select value={search.severity?.[0] ?? ''} onChange={(event) => update({ severity: event.target.value ? [event.target.value as ManualIssueSeverity] : undefined })}><option value="">全部</option><option>CRITICAL</option><option>HIGH</option><option>MEDIUM</option><option>LOW</option></select></label>
        <label>排序<select value={search.sort} onChange={(event) => update({ sort: event.target.value as typeof search.sort })}><option value="updatedAtDesc">最近更新</option><option value="updatedAtAsc">最早更新</option><option value="severityDesc">严重度</option><option value="createdAtDesc">最近创建</option></select></label>
        <label>每页<select value={search.limit} onChange={(event) => update({ limit: Number.parseInt(event.target.value, 10) as 20 | 50 | 100 })}><option>20</option><option>50</option><option>100</option></select></label>
        {filtered ? <button type="button" onClick={() => setParams(manualIssuesQueryCodec.build({ returnTo: search.returnTo }))}>清除筛选</button> : null}
      </form>

      {list.isFetching ? <p className="cleaning-refreshing" role="status">正在刷新；刷新完成前写操作不可用。</p> : null}
      <section className="cleaning-table-shell" aria-label="人工问题表格">
        {rows.length === 0 ? <EmptyState kind={filtered ? 'filtered-empty' : 'no-data'} title={filtered ? '当前筛选没有问题' : '当前作用域没有人工问题'} /> : (
          <table className="standard-table">
            <caption className="sr-only">按服务端稳定排序的人工问题</caption>
            <thead><tr><th>问题</th><th>严重度</th><th>状态</th><th>负责人</th><th>固定来源</th><th>范围</th><th>派生 Draft</th><th>操作</th></tr></thead>
            <tbody>{rows.map((issue) => {
              const known = issue.status.kind === 'known';
              const href = viewerHref(issue, currentReturn);
              const canTriage = known && capabilities.has('manual_issue.triage') && issue.allowedActions.includes('TRIAGE') && !list.isFetching;
              const canResolve = known && capabilities.has('manual_issue.resolve') && issue.allowedActions.includes('RESOLVE') && !list.isFetching;
              const canDraft = known && capabilities.has('cleaning.create') && (issue.allowedActions.includes('CREATE_DRAFT') || issue.allowedActions.includes('CONTINUE_DRAFT')) && !list.isFetching;
              return <tr key={issue.id}>
                <th scope="row"><button className="cleaning-link-button" type="button" onClick={() => update({ issueId: issue.id })}><code>{issue.id}</code></button><br /><small>{issue.issueType}</small></th>
                <td><StatusBadge status={issue.severity} tone={issue.severity === 'CRITICAL' ? 'danger' : issue.severity === 'HIGH' ? 'warning' : 'neutral'} /></td>
                <td><StatusBadge status={issueStatus(issue)} tone={statusTone(issue)} label={known ? issueStatus(issue) : 'UNKNOWN（只读）'} /></td>
                <td>{issue.assignee?.displayName ?? '未分配'}</td>
                <td><code>{issue.source.episodeId}</code><br /><small>{issue.source.versionId}</small></td>
                <td>[{issue.source.startNs}, {issue.source.endNs}) ns</td>
                <td>{issue.relatedDraftCount === '0' ? '无' : `${issue.relatedDraftCount} 个`}</td>
                <td><div className="cleaning-action-row">
                  {href ? <a href={href}>回到 Viewer</a> : <span className="cleaning-warning">来源绑定损坏</span>}
                  <button type="button" disabled={!canTriage || changing} onClick={() => { setDialog({ kind: 'triage', issue }); setSeverity(issue.severity); setAssigneeId(issue.assignee?.id ?? ''); setReason(''); }}>分诊</button>
                  <button type="button" disabled={!canDraft || changing} onClick={() => performCreateDraft(issue)}>{issue.allowedActions.includes('CONTINUE_DRAFT') ? '继续清洗' : '创建草稿'}</button>
                  <button type="button" disabled={!canResolve || changing} onClick={() => { setDialog({ kind: 'resolve', issue }); setResolutionVersionId(''); setResolutionNote(''); }}>解决</button>
                </div></td>
              </tr>;
            })}</tbody>
          </table>
        )}
      </section>
      <nav className="cursor-pager" aria-label="人工问题游标分页">
        <button type="button" disabled={!list.data?.pageInfo.hasPrevious || !list.data.pageInfo.before} onClick={() => update({ before: list.data?.pageInfo.before ?? undefined, after: undefined })}>上一组</button>
        <span>快照 {list.data?.snapshotAt ?? '—'}</span>
        <button type="button" disabled={!list.data?.pageInfo.hasNext || !list.data.pageInfo.after} onClick={() => update({ after: list.data?.pageInfo.after ?? undefined, before: undefined })}>下一组</button>
      </nav>

      {search.issueId ? detail.isPending ? <CleaningStatePanel state="first-loading" label="问题详情" />
        : detail.error ? <CleaningStatePanel state={cleaningStateFromError(detail.error)} label="问题详情" error={detail.error} onRetry={() => void detail.refetch()} />
          : detail.data ? <IssueDetail issue={detail.data} onClose={() => update({ issueId: undefined })} /> : null : null}

      {dialog ? <div className="dialog-backdrop"><section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="issue-command-title">
        <h2 id="issue-command-title">{dialog.kind === 'triage' ? '分诊人工问题' : '解决人工问题'}</h2>
        <p>稳定 Issue ID：<code>{dialog.issue.id}</code></p>
        {dialog.kind === 'triage' ? <form className="cleaning-dialog-form" onSubmit={(event) => {
          event.preventDefault();
          triage.mutate({ manualIssueId: dialog.issue.id, expectedVersion: dialog.issue.etag, idempotencyKey: mutationKey(), targetStatus: dialog.issue.status.kind === 'known' && dialog.issue.status.value === 'IN_PROGRESS' ? 'OPEN' : 'IN_PROGRESS', severity, assigneeId: assigneeId || null, reason }, { onSuccess: () => setDialog(null) });
        }}>
          <label>严重度<select value={severity} onChange={(event) => setSeverity(event.target.value as ManualIssueSeverity)}><option>LOW</option><option>MEDIUM</option><option>HIGH</option><option>CRITICAL</option></select></label>
          <label>负责人 ID<input value={assigneeId} onChange={(event) => setAssigneeId(event.target.value)} /></label>
          <label>{dialog.issue.status.kind === 'known' && dialog.issue.status.value === 'IN_PROGRESS' ? '退回待处理队列原因' : '分诊原因'}<textarea required value={reason} onChange={(event) => setReason(event.target.value)} /></label>
          {triage.error ? <p className="cleaning-error" role="alert">{isDomainError(triage.error) ? triage.error.message : '分诊失败；输入已保留。'}</p> : null}
          <div className="dialog-actions"><button type="button" disabled={triage.isPending} onClick={() => setDialog(null)}>取消</button><button type="submit" disabled={!reason.trim() || triage.isPending}>{triage.isPending ? '提交中…' : '确认分诊'}</button></div>
        </form> : <form className="cleaning-dialog-form" onSubmit={(event) => {
          event.preventDefault();
          resolve.mutate({ manualIssueId: dialog.issue.id, expectedVersion: dialog.issue.etag, idempotencyKey: mutationKey(), resolutionVersionId, resolutionNote }, { onSuccess: () => setDialog(null) });
        }}>
          <label>解决 Version ID<input required value={resolutionVersionId} onChange={(event) => setResolutionVersionId(event.target.value)} /></label>
          <label>解决说明<textarea required value={resolutionNote} onChange={(event) => setResolutionNote(event.target.value)} /></label>
          {resolve.error ? <p className="cleaning-error" role="alert">{isDomainError(resolve.error) ? resolve.error.message : '解决命令失败；输入已保留。'}</p> : null}
          <div className="dialog-actions"><button type="button" disabled={resolve.isPending} onClick={() => setDialog(null)}>取消</button><button type="submit" disabled={!resolutionVersionId.trim() || !resolutionNote.trim() || resolve.isPending}>{resolve.isPending ? '提交中…' : '确认解决'}</button></div>
        </form>}
      </section></div> : null}

      {selection?.disposition === 'SELECTION_REQUIRED' ? <section className="cleaning-state-panel" role="dialog" aria-modal="true">
        <h2>选择服务端候选草稿</h2><p>服务端发现多个权威候选；前端不会自行合并上下文。</p>
        <p className="cleaning-warning">当前安全命令只提交 ManualIssue ID、幂等键和 expectedVersion，不会把候选 Draft 或固定上下文回传给服务端。</p>
        {selection.candidates.map((candidate) => <p key={candidate.draftId}><code>{candidate.draftId}</code> · {candidate.updatedAt}</p>)}
        <button type="button" onClick={() => setSelection(null)}>取消</button>
      </section> : null}
      {createDraft.error ? <p className="cleaning-error" role="alert">{isDomainError(createDraft.error) ? createDraft.error.message : 'Issue → Draft 命令失败。'}</p> : null}
    </main>
  );
}

export const Component = ManualIssuesPage;
export default ManualIssuesPage;
