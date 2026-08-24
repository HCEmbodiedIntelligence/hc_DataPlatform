import { Alert, Button, Descriptions, Grid, Input, Select, Space, Tabs, Typography } from 'antd';
import type { ColumnDef } from '@tanstack/react-table';
import { useEffect, useMemo } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import type { CleaningDraft } from '../../entities/cleaning-draft';
import {
  useCleaningDraftDetail,
  useCleaningDraftEvents,
  useCleaningDraftSummary,
  useCleaningDrafts,
} from '../../features/cleaning/api';
import { routes as cleaningRoutes, type CleaningDraftScope } from '../../features/cleaning/routing';
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
  UiMetricCard,
  type PageStateKind,
} from '../../shared/ui';
import { cleaningDraftsQueryCodec, type CleaningDraftsSearch } from './query-codec';
import styles from './styles.module.css';

const scopeTabs: readonly { value: CleaningDraftScope; label: string }[] = [
  { value: 'mine', label: '我的草稿' },
  { value: 'actionable', label: '待处理' },
  { value: 'review', label: '待复核' },
  { value: 'returned', label: '已退回' },
  { value: 'submitted', label: '已提交' },
  { value: 'all', label: '全部可见' },
];

type DraftListItem = Omit<CleaningDraft, 'createdAt'>;

function primaryState(draft: DraftListItem): string {
  if (draft.hasUnknownState) return 'UNKNOWN';
  if (draft.outputVersionStatus === 'RETURNED') return '复核退回';
  if (draft.outputVersionStatus === 'READY') return '版本可用';
  if (draft.outputVersionStatus === 'REVIEWING') return '等待复核';
  if (draft.commitStatus === 'QUEUED' || draft.commitStatus === 'RUNNING') return '正在提交';
  if (draft.commitStatus === 'FAILED') return '提交失败';
  if (draft.previewStatus === 'RUNNING' || draft.previewStatus === 'QUEUED') return '预览生成中';
  if (draft.previewStatus === 'FAILED') return '预览失败';
  if (draft.previewStatus === 'EXPIRED' || draft.previewStatus === 'STALE') return '预览需重建';
  return draft.status === 'EDITING' ? '编辑中' : '已提交';
}

function tone(draft: DraftListItem): 'neutral' | 'info' | 'success' | 'warning' | 'danger' {
  if (draft.hasUnknownState || draft.outputVersionStatus === 'RETURNED') return 'warning';
  if (draft.commitStatus === 'FAILED' || draft.previewStatus === 'FAILED') return 'danger';
  if (draft.outputVersionStatus === 'READY') return 'success';
  if (draft.status === 'EDITING') return 'info';
  return 'neutral';
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

function DraftInspector({
  search,
  close,
  desktop,
}: Readonly<{ search: CleaningDraftsSearch; close(): void; desktop: boolean }>) {
  const detail = useCleaningDraftDetail(search.draftId, true);
  const events = useCleaningDraftEvents(search.draftId, Boolean(detail.data));
  const draft = detail.data?.draft;
  const relationships = detail.data?.relationships;
  const workbenchId =
    draft?.outputVersionStatus === 'RETURNED' && relationships?.successorDraftId
      ? relationships.successorDraftId
      : draft?.id;
  const canOpen = Boolean(
    draft &&
      !draft.hasUnknownState &&
      (draft.allowedActions.includes('EDIT') ||
        draft.allowedActions.includes('VIEW') ||
        draft.allowedActions.includes('OPEN_SUCCESSOR')),
  );

  const content = detail.error ? (
    <PageState
      state={stateFromError(detail.error)}
      label="草稿详情"
      requestId={requestId(detail.error)}
      onRetry={() => void detail.refetch()}
    />
  ) : draft && relationships ? (
    <Space orientation="vertical" size="middle" className={styles.drawerContent}>
      <Descriptions bordered column={1} size="small">
        <Descriptions.Item label="稳定 Draft ID">
          <Typography.Text code>{draft.id}</Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="固定 Base">
          <Typography.Text code>{draft.baseVersionId}</Typography.Text> /{' '}
          <Typography.Text code>{draft.baseRevisionId}</Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="Episode">
          <Typography.Text code>{draft.episodeId}</Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="五轴状态">
          Draft {draft.status} · Preview {draft.previewStatus} · Commit {draft.commitStatus} ·
          Review {draft.outputVersionStatus ?? 'NONE'}
        </Descriptions.Item>
      </Descriptions>
      {draft.origin.kind === 'ISSUE_DERIVED' ? (
        <section className={styles.drawerSection}>
          <Typography.Title level={3}>ManualIssue 来源</Typography.Title>
          {draft.origin.manualIssueIds.map((id) => (
            <Link key={id} to={cleaningRoutes.manualIssues.build({ issueId: id })}>
              <Typography.Text code>{id}</Typography.Text>
            </Link>
          ))}
          <Typography.Paragraph>
            [{draft.origin.startNs}, {draft.origin.endNs}) ns · Stream{' '}
            <Typography.Text code>{draft.origin.selectedStreamId}</Typography.Text>
          </Typography.Paragraph>
        </section>
      ) : (
        <section className={styles.drawerSection}>
          <Typography.Title level={3}>Review Return 血缘</Typography.Title>
          <Typography.Paragraph>
            原草稿 <Typography.Text code>{draft.origin.supersedesDraftId}</Typography.Text>
          </Typography.Paragraph>
          <Typography.Paragraph>
            退回 Version{' '}
            <Typography.Text code>{draft.origin.returnedFromVersionId}</Typography.Text>
          </Typography.Paragraph>
          <Typography.Paragraph>
            不可变 Decision{' '}
            <Typography.Text code>{draft.origin.returnedFromReviewDecisionId}</Typography.Text>
          </Typography.Paragraph>
        </section>
      )}
      <section className={styles.drawerSection}>
        <Typography.Title level={3}>输出与返工</Typography.Title>
        <Typography.Paragraph>
          Commit：<Typography.Text code>{relationships.commitId ?? '—'}</Typography.Text>
        </Typography.Paragraph>
        <Typography.Paragraph>
          Output Version：
          <Typography.Text code>{relationships.outputVersionId ?? '—'}</Typography.Text>
        </Typography.Paragraph>
        {relationships.outputRevisionIds.map((id) => (
          <Typography.Paragraph key={id}>
            Output Revision：<Typography.Text code>{id}</Typography.Text>
          </Typography.Paragraph>
        ))}
        {relationships.reviewFindingIds.length ? (
          <Typography.Paragraph>
            只读 Finding：{relationships.reviewFindingIds.join('、')}
          </Typography.Paragraph>
        ) : null}
        {canOpen && workbenchId ? (
          <Link
            className={styles.primaryLink}
            to={cleaningRoutes.cleaningWorkbench.build({
              draftId: workbenchId,
              returnTo: cleaningDraftsQueryCodec.build(search),
            })}
          >
            {draft.outputVersionStatus === 'RETURNED' ? '打开后继草稿' : '打开清洗工作台'}
          </Link>
        ) : null}
      </section>
      <section className={styles.drawerSection} aria-label="草稿近期事件">
        <Typography.Title level={3}>近期事件</Typography.Title>
        {events.isPending ? (
          <Typography.Text role="status">事件加载中…</Typography.Text>
        ) : events.error ? (
          <PageState
            state={stateFromError(events.error)}
            label="近期事件"
            requestId={requestId(events.error)}
            onRetry={() => void events.refetch()}
          />
        ) : (
          <ol>
            {events.data?.items.map((event) => (
              <li key={event.eventId}>
                <time>{event.occurredAt}</time> · {event.eventType}
                <br />
                {event.safeSummary ?? '无安全摘要'}
              </li>
            ))}
          </ol>
        )}
      </section>
    </Space>
  ) : null;

  if (desktop) {
    return (
      <aside
        className={styles.desktopInspector}
        role="dialog"
        aria-modal="false"
        aria-label="草稿详情"
      >
        <header>
          <Typography.Title level={2}>草稿详情</Typography.Title>
          <Button type="text" onClick={close}>
            关闭
          </Button>
        </header>
        {detail.isPending ? <PageState state="loading" label="草稿详情" /> : content}
      </aside>
    );
  }

  return (
    <EntityDrawer
      open={Boolean(search.draftId)}
      title="草稿详情"
      loading={detail.isPending}
      onClose={close}
      width={520}
    >
      {content}
    </EntityDrawer>
  );
}

export function CleaningDraftsPage() {
  const screens = Grid.useBreakpoint();
  const desktopInspector = Boolean(screens.xl);
  const capabilities = useCapabilities();
  const canRead =
    !capabilities.loading && !capabilities.failed && capabilities.has('cleaning.read');
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const search = cleaningDraftsQueryCodec.parse(params);
  const list = useCleaningDrafts(search, canRead);
  const summary = useCleaningDraftSummary(search, canRead);
  const rows = useMemo(() => list.data?.items ?? [], [list.data]);
  const filtered = Boolean(
    search.q ||
      search.datasetId ||
      search.baseVersionId ||
      search.episodeId ||
      search.previewStatus?.length ||
      search.commitStatus?.length ||
      search.versionReviewStatus?.length,
  );

  const change = (changes: Parameters<typeof cleaningDraftsQueryCodec.withChanges>[1]) => {
    void navigate(
      cleaningDraftsQueryCodec.build(cleaningDraftsQueryCodec.withChanges(search, changes)),
    );
  };

  const resetFilters = () => {
    void navigate(cleaningDraftsQueryCodec.build({ scope: search.scope }));
  };

  useEffect(() => {
    if (!desktopInspector || !search.draftId) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      void navigate(
        cleaningDraftsQueryCodec.build(
          cleaningDraftsQueryCodec.withChanges(search, { draftId: undefined }),
        ),
      );
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [desktopInspector, navigate, search]);

  const columns: ColumnDef<DraftListItem, unknown>[] = [
    {
      id: 'draft',
      header: '草稿',
      cell: ({ row }) => (
        <Button type="link" size="small" onClick={() => change({ draftId: row.original.id })}>
          <Typography.Text code>{row.original.id}</Typography.Text>
          <small>{row.original.episodeId}</small>
        </Button>
      ),
    },
    {
      id: 'origin',
      header: '来源',
      cell: ({ row }) =>
        row.original.origin.kind === 'ISSUE_DERIVED' ? (
          <>
            ManualIssue
            <br />
            <Typography.Text code>{row.original.origin.manualIssueIds[0]}</Typography.Text>
          </>
        ) : (
          <>
            Review Return
            <br />
            <Typography.Text code>
              {row.original.origin.returnedFromReviewDecisionId}
            </Typography.Text>
          </>
        ),
    },
    {
      id: 'primaryState',
      header: '主状态',
      cell: ({ row }) => (
        <StatusTag
          status={primaryState(row.original)}
          tone={tone(row.original)}
          known={!row.original.hasUnknownState}
        />
      ),
    },
    {
      id: 'previewStatus',
      header: 'Preview',
      cell: ({ row }) => (
        <StatusTag
          status={row.original.previewStatus}
          known={row.original.previewStatus !== 'UNKNOWN'}
        />
      ),
    },
    {
      id: 'commitStatus',
      header: 'Commit',
      cell: ({ row }) => (
        <StatusTag
          status={row.original.commitStatus}
          known={row.original.commitStatus !== 'UNKNOWN'}
        />
      ),
    },
    {
      id: 'outputVersionStatus',
      header: 'Output Review',
      cell: ({ row }) =>
        row.original.outputVersionStatus ? (
          <StatusTag
            status={row.original.outputVersionStatus}
            known={row.original.outputVersionStatus !== 'UNKNOWN'}
          />
        ) : (
          '—'
        ),
    },
    {
      id: 'updatedAt',
      header: '更新时间',
      cell: ({ row }) => <time>{row.original.updatedAt}</time>,
    },
    {
      id: 'actions',
      header: '操作',
      cell: ({ row }) => {
        const draft = row.original;
        const canOpen = !draft.hasUnknownState && (
          draft.allowedActions.includes('EDIT') ||
          draft.allowedActions.includes('VIEW') ||
          draft.allowedActions.includes('OPEN_SUCCESSOR')
        );
        const draftId =
          draft.outputVersionStatus === 'RETURNED' && draft.review
            ? draft.review.successorDraftId
            : draft.id;
        return canOpen ? (
          <Link
            to={cleaningRoutes.cleaningWorkbench.build({
              draftId,
              returnTo: cleaningDraftsQueryCodec.build(search),
            })}
          >
            {draft.outputVersionStatus === 'RETURNED'
              ? '返工'
              : draft.status === 'EDITING'
                ? '继续'
                : '查看'}
          </Link>
        ) : <Typography.Text type="secondary">—</Typography.Text>;
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
            ? filtered
              ? 'filtered-empty'
              : 'empty'
            : list.isFetching
              ? 'refreshing'
              : 'ready';
  const table = (
    <DataTable
      data={rows}
      columns={columns}
      getRowId={(draft) => draft.id}
      caption="清洗草稿只读列表"
    />
  );
  const content =
    listState === 'ready' ? (
      table
    ) : listState === 'refreshing' ? (
      <PageState state="refreshing" label="清洗草稿列表">
        {table}
      </PageState>
    ) : (
      <PageState
        state={listState}
        label="清洗草稿列表"
        title={
          listState === 'empty'
            ? '当前作用域没有清洗草稿'
            : listState === 'filtered-empty'
              ? '当前筛选没有草稿'
              : undefined
        }
        requestId={requestId(list.error)}
        onRetry={list.error ? () => void list.refetch() : undefined}
        action={
          listState === 'filtered-empty' ? (
            <Button onClick={resetFilters}>清除筛选</Button>
          ) : undefined
        }
      />
    );

  return (
    <main className={styles.page} data-page-id="P10">
      <StandardPageScaffold
        header={{
          title: '清洗草稿',
          description: 'P10 只读聚合五条正交状态轴；所有编辑、Preview 与 Commit 都在 P11 完成。',
          breadcrumbs: [
            { key: 'cleaning', label: '手动清洗', to: cleaningRoutes.manualIssues.build({}) },
            { key: 'drafts', label: '清洗草稿' },
          ],
          actions: <Link to={cleaningRoutes.manualIssues.build({})}>从人工问题进入清洗</Link>,
        }}
        summary={
          summary.error ? (
            <PageState
              state={stateFromError(summary.error)}
              label="草稿摘要"
              requestId={requestId(summary.error)}
              onRetry={() => void summary.refetch()}
            />
          ) : (
            <div className={styles.overviewStack}>
              <Tabs
                activeKey={search.scope}
                onChange={(value) =>
                  change({
                    scope: value as CleaningDraftScope,
                    after: undefined,
                    before: undefined,
                  })
                }
                items={scopeTabs.map((tab) => ({ key: tab.value, label: tab.label }))}
                more={{
                  icon: (
                    <>
                      <span aria-hidden>•••</span>
                      <span className="sr-only">更多草稿范围</span>
                    </>
                  ),
                }}
                aria-label="草稿范围"
              />
              <div className={styles.metricGrid}>
                <UiMetricCard
                  label="编辑中"
                  value={summary.data?.scopeCounts.editing}
                  state={summary.isPending ? 'loading' : undefined}
                  asOf={summary.data?.asOf}
                />
                <UiMetricCard
                  label="已提交"
                  value={summary.data?.scopeCounts.committed}
                  state={summary.isPending ? 'loading' : undefined}
                />
                <UiMetricCard
                  label="已退回"
                  value={summary.data?.scopeCounts.returned}
                  state={summary.isPending ? 'loading' : undefined}
                />
                <UiMetricCard
                  label="活跃任务"
                  value={
                    summary.data
                      ? `${summary.data.jobs.previewQueued}/${summary.data.jobs.previewRunning}/${summary.data.jobs.commitQueued}`
                      : undefined
                  }
                  state={summary.isPending ? 'loading' : undefined}
                  description="Preview 排队 / 运行 / Commit 排队"
                />
              </div>
            </div>
          )
        }
        filters={
          canRead ? (
            <FilterToolbar
              label="草稿筛选"
              onReset={filtered ? resetFilters : undefined}
              disabled={list.isFetching}
            >
              <label className={styles.filterField}>
                搜索
                <Input
                  value={search.q ?? ''}
                  onChange={(event) => change({ q: event.target.value || undefined })}
                  placeholder="Draft / Version / Episode"
                  allowClear
                />
              </label>
              <label className={styles.filterField}>
                状态
                <Select
                  value={search.status}
                  onChange={(value) => change({ status: value })}
                  options={[
                    { value: 'active', label: '活跃' },
                    { value: 'submitted', label: '已提交' },
                    { value: 'failed', label: '失败' },
                    { value: 'archived', label: '已归档' },
                  ]}
                />
              </label>
              <label className={styles.filterField}>
                Preview
                <Select
                  value={search.previewStatus?.[0] ?? ''}
                  onChange={(value) => change({ previewStatus: value ? [value] : undefined })}
                  options={[
                    { value: '', label: '全部' },
                    ...(
                      ['NONE', 'QUEUED', 'RUNNING', 'READY', 'FAILED', 'EXPIRED', 'STALE'] as const
                    ).map((value) => ({ value, label: value })),
                  ]}
                />
              </label>
              <label className={styles.filterField}>
                排序
                <Select
                  value={search.sort}
                  onChange={(value) => change({ sort: value })}
                  options={[
                    { value: 'updatedAtDesc', label: '最近更新' },
                    { value: 'updatedAtAsc', label: '最早更新' },
                    { value: 'createdAtDesc', label: '最近创建' },
                    { value: 'reuseRatioDesc', label: '复用率' },
                    { value: 'effectiveDurationDesc', label: '有效时长' },
                  ]}
                />
              </label>
              <label className={styles.filterField}>
                每页
                <Select
                  value={search.limit}
                  onChange={(value) => change({ limit: value })}
                  options={([20, 50, 100] as const).map((value) => ({
                    value,
                    label: String(value),
                  }))}
                />
              </label>
            </FilterToolbar>
          ) : undefined
        }
        pagination={
          list.data ? (
            <DataCursorPager
              pageInfo={{
                startCursor: list.data.pageInfo.before,
                endCursor: list.data.pageInfo.after,
                hasPreviousPage: list.data.pageInfo.hasPrevious,
                hasNextPage: list.data.pageInfo.hasNext,
              }}
              busy={list.isFetching}
              windowLabel={`快照 ${list.data.snapshotAt}`}
              onChange={(request) =>
                change(
                  'before' in request
                    ? { before: request.before, after: undefined }
                    : { after: request.after, before: undefined },
                )
              }
            />
          ) : undefined
        }
      >
        <div
          className={search.draftId && desktopInspector ? styles.tableInspectorLayout : undefined}
        >
          <div className={styles.tableRegion}>{content}</div>
          {search.draftId && desktopInspector ? (
            <DraftInspector search={search} close={() => change({ draftId: undefined })} desktop />
          ) : null}
        </div>
      </StandardPageScaffold>
      {search.draftId && !desktopInspector ? (
        <DraftInspector
          search={search}
          close={() => change({ draftId: undefined })}
          desktop={false}
        />
      ) : null}
      {list.isFetching ? (
        <Alert
          className={styles.refreshNotice}
          type="info"
          showIcon
          title="正在刷新只读投影；写操作仍只在 P11 提供。"
        />
      ) : null}
    </main>
  );
}

export const Component = CleaningDraftsPage;
export default CleaningDraftsPage;
