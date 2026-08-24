import { Alert, Button, Descriptions, Form, Grid, Input, Modal, Select, Space, Tabs, Typography } from 'antd';
import type { ColumnDef } from '@tanstack/react-table';
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
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
import { manualIssuesQueryCodec, routes as cleaningRoutes } from '../../features/cleaning/routing';
import { routes as datasetRoutes } from '../../features/datasets/routing';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import {
  DataCursorPager,
  DataTable,
  EntityDrawer,
  FilterToolbar,
  PageState,
  StandardPageScaffold,
  StatusTag,
  type PageStateKind,
} from '../../shared/ui';
import styles from './styles.module.css';

type DialogState =
  | { readonly kind: 'triage'; readonly issue: ManualIssueListItem }
  | { readonly kind: 'resolve'; readonly issue: ManualIssueListItem }
  | null;

function mutationKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `manual-issue-${Date.now().toString(36)}`;
}

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
  return datasetRoutes.episodeViewer.build({
    datasetId: source.datasetId,
    versionId: source.versionId,
    episodeId: source.episodeId,
    returnTo,
  });
}

function IssueDetail({
  issue,
}: Readonly<{ issue: ManualIssue }>) {
  return (
    <Space orientation="vertical" size="middle" className={styles.drawerContent}>
      <section className={styles.mediaUnavailable} aria-label="来源媒体预览">
        <strong>来源媒体</strong>
        <span>当前列表合同不包含安全媒体预览；请通过固定来源打开 Viewer。</span>
        <Typography.Text className={styles.rangeAccent}>{issue.source.startNs} → {issue.source.endNs} ns</Typography.Text>
      </section>
      <Descriptions bordered column={1} size="small">
        <Descriptions.Item label="稳定 Issue ID"><Typography.Text code>{issue.id}</Typography.Text></Descriptions.Item>
        <Descriptions.Item label="说明">{issue.note || '未填写'}</Descriptions.Item>
        <Descriptions.Item label="固定 Version / Revision / Stream">
          <Typography.Text code>{issue.source.versionId}</Typography.Text> /{' '}
          <Typography.Text code>{issue.source.revisionId}</Typography.Text> /{' '}
          <Typography.Text code>{issue.source.streamId}</Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="半开范围">[{issue.source.startNs}, {issue.source.endNs}) ns</Descriptions.Item>
        <Descriptions.Item label="派生 Draft">
          {issue.relatedDrafts.length
            ? issue.relatedDrafts.map((draft) => (
                <Link key={draft.draftId} to={cleaningRoutes.cleaningWorkbench.build({ draftId: draft.draftId })}>
                  {draft.draftId}（{draft.status}）
                </Link>
              ))
            : '尚未派生 Draft'}
        </Descriptions.Item>
      </Descriptions>
      {issue.blockedReasons.map((reason) => (
        <Alert key={reason.code} type="warning" showIcon title={reason.code} description={reason.message} />
      ))}
    </Space>
  );
}

export function ManualIssuesPage() {
  const screens = Grid.useBreakpoint();
  const desktopInspector = Boolean(screens.xl);
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
  const rows = useMemo(() => list.data?.items ?? [], [list.data]);

  const update = (changes: Parameters<typeof manualIssuesQueryCodec.withChanges>[1]) => {
    setParams(manualIssuesQueryCodec.build(manualIssuesQueryCodec.withChanges(search, changes)));
  };

  const resetFilters = () => {
    setParams(manualIssuesQueryCodec.build({ returnTo: search.returnTo }));
  };

  useEffect(() => {
    if (!desktopInspector || !search.issueId) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setParams((currentParams) => {
        const current = manualIssuesQueryCodec.parse(currentParams);
        return manualIssuesQueryCodec.build(manualIssuesQueryCodec.withChanges(current, { issueId: undefined }));
      });
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [desktopInspector, search.issueId, setParams]);

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

  const columns: ColumnDef<ManualIssueListItem, unknown>[] = [
    {
      id: 'issue',
      header: '问题',
      cell: ({ row }) => (
        <Button type="link" size="small" onClick={() => update({ issueId: row.original.id })}>
          <Typography.Text code>{row.original.id}</Typography.Text>
          <small>{row.original.issueType}</small>
        </Button>
      ),
    },
    {
      id: 'severity',
      header: '严重度',
      cell: ({ row }) => <StatusTag status={row.original.severity} tone={row.original.severity === 'CRITICAL' ? 'danger' : row.original.severity === 'HIGH' ? 'warning' : 'neutral'} />,
    },
    {
      id: 'status',
      header: '状态',
      cell: ({ row }) => <StatusTag status={issueStatus(row.original)} tone={statusTone(row.original)} known={row.original.status.kind === 'known'} />,
    },
    { id: 'assignee', header: '负责人', cell: ({ row }) => row.original.assignee?.displayName ?? '未分配' },
    {
      id: 'source',
      header: '固定来源',
      cell: ({ row }) => <><Typography.Text code>{row.original.source.episodeId}</Typography.Text><br /><small>{row.original.source.versionId}</small></>,
    },
    { id: 'range', header: '范围', cell: ({ row }) => `[${row.original.source.startNs}, ${row.original.source.endNs}) ns` },
    { id: 'drafts', header: '派生 Draft', cell: ({ row }) => row.original.relatedDraftCount === '0' ? '无' : `${row.original.relatedDraftCount} 个` },
    {
      id: 'actions',
      header: '操作',
      cell: ({ row }) => {
        const issue = row.original;
        const known = issue.status.kind === 'known';
        const href = viewerHref(issue, currentReturn);
        const canTriage = known && capabilities.has('manual_issue.triage') && issue.allowedActions.includes('TRIAGE') && !list.isFetching;
        const canResolve = known && capabilities.has('manual_issue.resolve') && issue.allowedActions.includes('RESOLVE') && !list.isFetching;
        const canDraft = known && capabilities.has('cleaning.create') && (issue.allowedActions.includes('CREATE_DRAFT') || issue.allowedActions.includes('CONTINUE_DRAFT')) && !list.isFetching;
        return (
          <Space wrap size="small" className={styles.rowActions}>
            {href ? <a href={href}>回到 Viewer</a> : <Typography.Text type="warning">来源绑定损坏</Typography.Text>}
            <Button size="small" disabled={!canTriage || changing} onClick={() => {
              setDialog({ kind: 'triage', issue });
              setSeverity(issue.severity);
              setAssigneeId(issue.assignee?.id ?? '');
              setReason('');
            }}>分诊</Button>
            <Button size="small" type="primary" disabled={!canDraft || changing} onClick={() => performCreateDraft(issue)}>
              {issue.allowedActions.includes('CONTINUE_DRAFT') ? '继续清洗' : '创建草稿'}
            </Button>
            <Button size="small" danger disabled={!canResolve || changing} onClick={() => {
              setDialog({ kind: 'resolve', issue });
              setResolutionVersionId('');
              setResolutionNote('');
            }}>解决</Button>
          </Space>
        );
      },
    },
  ];

  const listState: PageStateKind | 'ready' = capabilities.loading
    ? 'loading'
    : !canRead
      ? 'forbidden'
      : list.isPending
        ? 'loading'
        : list.error
          ? stateFromError(list.error)
          : rows.length === 0
            ? filtered ? 'filtered-empty' : 'empty'
            : list.isFetching ? 'refreshing' : 'ready';

  const table = (
    <DataTable
      data={rows}
      columns={columns}
      getRowId={(issue) => issue.id}
      caption="按服务端稳定排序的人工问题"
    />
  );
  const content = listState === 'ready'
    ? table
    : listState === 'refreshing'
      ? <PageState state="refreshing" label="人工问题列表">{table}</PageState>
      : <PageState
          state={listState}
          label="人工问题列表"
          title={listState === 'empty' ? '当前作用域没有人工问题' : listState === 'filtered-empty' ? '当前筛选没有问题' : undefined}
          requestId={requestId(list.error)}
          onRetry={list.error ? () => void list.refetch() : undefined}
          action={listState === 'filtered-empty' ? <Button onClick={resetFilters}>清除筛选</Button> : undefined}
        />;

  return (
    <main className={styles.page} data-page-id="P09">
      <StandardPageScaffold
        header={{
          title: '人工问题清单',
          description: 'ManualIssue 是可分诊事实；ReviewFinding 不进入此列表，也不共享任何写操作。',
          breadcrumbs: [{ key: 'cleaning', label: '手动清洗', to: cleaningRoutes.manualIssues.build({}) }, { key: 'issues', label: '人工问题' }],
          actions: <Link to={cleaningRoutes.cleaningDrafts.build({})}>查看清洗草稿</Link>,
        }}
        summary={summary.error
          ? <PageState state={stateFromError(summary.error)} label="问题统计" requestId={requestId(summary.error)} onRetry={() => void summary.refetch()} />
          : <Tabs
              className={styles.projectionTabs}
              activeKey={search.status?.[0] ?? 'all'}
              onChange={(value) => update({ status: value === 'all' ? undefined : [value as 'OPEN' | 'IN_PROGRESS' | 'RESOLVED'] })}
              items={[
                { key: 'OPEN', label: `待处理 ${summary.data?.counts.open ?? '—'}` },
                { key: 'IN_PROGRESS', label: `处理中 ${summary.data?.counts.inProgress ?? '—'}` },
                { key: 'RESOLVED', label: `已解决 ${summary.data?.counts.resolved ?? '—'}` },
                { key: 'all', label: `全部 ${summary.data?.counts.total ?? '—'}` },
              ]}
              aria-label="人工问题状态投影"
            />}
        filters={canRead ? (
          <FilterToolbar label="人工问题筛选" onReset={filtered ? resetFilters : undefined} disabled={list.isFetching}>
            <label className={styles.filterField}>搜索<Input value={search.q ?? ''} onChange={(event) => update({ q: event.target.value || undefined })} placeholder="Issue / Episode / 说明" allowClear /></label>
            <label className={styles.filterField}>状态<Select value={search.status?.[0] ?? ''} onChange={(value) => update({ status: value ? [value as 'OPEN' | 'IN_PROGRESS' | 'RESOLVED'] : undefined })} options={[{ value: '', label: '全部' }, { value: 'OPEN', label: '待处理' }, { value: 'IN_PROGRESS', label: '处理中' }, { value: 'RESOLVED', label: '已解决' }]} /></label>
            <label className={styles.filterField}>严重度<Select value={search.severity?.[0] ?? ''} onChange={(value) => update({ severity: value ? [value as ManualIssueSeverity] : undefined })} options={[{ value: '', label: '全部' }, ...(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as const).map((value) => ({ value, label: value }))]} /></label>
            <label className={styles.filterField}>排序<Select value={search.sort} onChange={(value) => update({ sort: value })} options={[{ value: 'updatedAtDesc', label: '最近更新' }, { value: 'updatedAtAsc', label: '最早更新' }, { value: 'severityDesc', label: '严重度' }, { value: 'createdAtDesc', label: '最近创建' }]} /></label>
            <label className={styles.filterField}>每页<Select value={search.limit} onChange={(value) => update({ limit: value })} options={([20, 50, 100] as const).map((value) => ({ value, label: String(value) }))} /></label>
          </FilterToolbar>
        ) : undefined}
        pagination={list.data ? (
          <DataCursorPager
            pageInfo={{
              startCursor: list.data.pageInfo.before,
              endCursor: list.data.pageInfo.after,
              hasPreviousPage: list.data.pageInfo.hasPrevious,
              hasNextPage: list.data.pageInfo.hasNext,
            }}
            busy={list.isFetching}
            windowLabel={`快照 ${list.data.snapshotAt}`}
            onChange={(request) => update('before' in request
              ? { before: request.before, after: undefined }
              : { after: request.after, before: undefined })}
          />
        ) : undefined}
      >
        <div className={search.issueId && desktopInspector ? styles.tableInspectorLayout : undefined}>
          <div className={styles.tableRegion}>{content}</div>
          {search.issueId && desktopInspector ? (
            <aside className={styles.desktopInspector} role="dialog" aria-modal="false" aria-label="问题详情">
              <header><Typography.Title level={2}>问题详情</Typography.Title><Button type="text" onClick={() => update({ issueId: undefined })}>关闭</Button></header>
              {detail.isPending ? <PageState state="loading" label="问题详情" />
                : detail.error ? <PageState state={stateFromError(detail.error)} label="问题详情" requestId={requestId(detail.error)} onRetry={() => void detail.refetch()} />
                  : detail.data ? <IssueDetail issue={detail.data} /> : null}
            </aside>
          ) : null}
        </div>
      </StandardPageScaffold>

      <EntityDrawer
        open={Boolean(search.issueId) && !desktopInspector}
        title="问题详情"
        loading={detail.isPending}
        onClose={() => update({ issueId: undefined })}
      >
        {detail.error
          ? <PageState state={stateFromError(detail.error)} label="问题详情" requestId={requestId(detail.error)} onRetry={() => void detail.refetch()} />
          : detail.data ? <IssueDetail issue={detail.data} /> : null}
      </EntityDrawer>

      <Modal open={dialog?.kind === 'triage'} title="分诊人工问题" footer={null} destroyOnHidden onCancel={() => { if (!triage.isPending) setDialog(null); }}>
        {dialog?.kind === 'triage' ? (
          <Form layout="vertical" onFinish={() => {
            triage.mutate({
              manualIssueId: dialog.issue.id,
              expectedVersion: dialog.issue.etag,
              idempotencyKey: mutationKey(),
              targetStatus: dialog.issue.status.kind === 'known' && dialog.issue.status.value === 'IN_PROGRESS' ? 'OPEN' : 'IN_PROGRESS',
              severity,
              assigneeId: assigneeId || null,
              reason,
            }, { onSuccess: () => setDialog(null) });
          }}>
            <Typography.Paragraph>稳定 Issue ID：<Typography.Text code>{dialog.issue.id}</Typography.Text></Typography.Paragraph>
            <Form.Item label="严重度"><Select aria-label="严重度" value={severity} onChange={setSeverity} options={(['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'] as const).map((value) => ({ value, label: value }))} /></Form.Item>
            <Form.Item label="负责人 ID"><Input aria-label="负责人 ID" value={assigneeId} onChange={(event) => setAssigneeId(event.target.value)} /></Form.Item>
            <Form.Item label={dialog.issue.status.kind === 'known' && dialog.issue.status.value === 'IN_PROGRESS' ? '退回待处理队列原因' : '分诊原因'} required>
              <Input.TextArea aria-label={dialog.issue.status.kind === 'known' && dialog.issue.status.value === 'IN_PROGRESS' ? '退回待处理队列原因' : '分诊原因'} rows={4} value={reason} onChange={(event) => setReason(event.target.value)} />
            </Form.Item>
            {triage.error ? <Alert type="error" showIcon title={isDomainError(triage.error) ? triage.error.message : '分诊失败；输入已保留。'} /> : null}
            <Space className={styles.modalActions}><Button disabled={triage.isPending} onClick={() => setDialog(null)}>取消</Button><Button type="primary" htmlType="submit" loading={triage.isPending} disabled={!reason.trim()}>确认分诊</Button></Space>
          </Form>
        ) : null}
      </Modal>

      <Modal open={dialog?.kind === 'resolve'} title="解决人工问题" footer={null} destroyOnHidden onCancel={() => { if (!resolve.isPending) setDialog(null); }}>
        {dialog?.kind === 'resolve' ? (
          <Form layout="vertical" onFinish={() => {
            resolve.mutate({ manualIssueId: dialog.issue.id, expectedVersion: dialog.issue.etag, idempotencyKey: mutationKey(), resolutionVersionId, resolutionNote }, { onSuccess: () => setDialog(null) });
          }}>
            <Typography.Paragraph>稳定 Issue ID：<Typography.Text code>{dialog.issue.id}</Typography.Text></Typography.Paragraph>
            <Form.Item label="解决 Version ID" required><Input aria-label="解决 Version ID" value={resolutionVersionId} onChange={(event) => setResolutionVersionId(event.target.value)} /></Form.Item>
            <Form.Item label="解决说明" required><Input.TextArea aria-label="解决说明" rows={4} value={resolutionNote} onChange={(event) => setResolutionNote(event.target.value)} /></Form.Item>
            {resolve.error ? <Alert type="error" showIcon title={isDomainError(resolve.error) ? resolve.error.message : '解决命令失败；输入已保留。'} /> : null}
            <Space className={styles.modalActions}><Button disabled={resolve.isPending} onClick={() => setDialog(null)}>取消</Button><Button danger type="primary" htmlType="submit" loading={resolve.isPending} disabled={!resolutionVersionId.trim() || !resolutionNote.trim()}>确认解决</Button></Space>
          </Form>
        ) : null}
      </Modal>

      <Modal open={selection?.disposition === 'SELECTION_REQUIRED'} title="选择服务端候选草稿" footer={<Button onClick={() => setSelection(null)}>取消</Button>} onCancel={() => setSelection(null)}>
        <Alert type="warning" showIcon title="服务端发现多个权威候选；前端不会自行合并上下文。" description="当前安全命令只提交 ManualIssue ID、幂等键和 expectedVersion。" />
        {selection?.disposition === 'SELECTION_REQUIRED' ? selection.candidates.map((candidate) => <Typography.Paragraph key={candidate.draftId}><Typography.Text code>{candidate.draftId}</Typography.Text> · {candidate.updatedAt}</Typography.Paragraph>) : null}
      </Modal>
      {createDraft.error ? <Alert className={styles.operationAlert} type="error" showIcon title={isDomainError(createDraft.error) ? createDraft.error.message : 'Issue → Draft 命令失败。'} /> : null}
    </main>
  );
}

export const Component = ManualIssuesPage;
export default ManualIssuesPage;
