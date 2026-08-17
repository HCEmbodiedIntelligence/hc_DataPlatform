import { Alert, Button, Input, Segmented, Select, Space } from 'antd';
import { CircleCheckBig, Database, HardDriveUpload, Plus, ShieldCheck } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useUploadBatchMutation, useUploadCreationOptions, useUploadSessions, useUploadSessionMutation } from '../../features/ingest/api';
import { createMutationIntentKey } from '../../features/ingest/mutation-machine';
import { routes } from '../../features/ingest/routing';
import { ingestUploadAuthorizationVault } from '../../features/ingest/upload/authorization-vault';
import { useUploadProgressStream } from '../../features/ingest/upload/use-upload-progress-stream';
import { useIngestScope } from '../../features/ingest/use-ingest-scope';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { DangerConfirmModal, DataCursorPager, FilterToolbar, PageState, StandardPageScaffold, type DangerPreflightEvidence, type PageStateKind } from '../../shared/ui';
import { BatchOperationBar } from './components/BatchOperationBar';
import { CreateUploadDialog, type CreateUploadDraft } from './components/CreateUploadDialog';
import { UploadSessionFacts, UploadSessionTable } from './components/UploadSessionTable';
import { updateUploadJobsSearch, uploadJobsQueryCodec, type UploadJobsSearch } from './query-codec';
import styles from './styles.module.css';

interface FilterDraft {
  readonly q: string;
  readonly dataSourceId: string;
  readonly lifecycleStatus: string;
  readonly sort: UploadJobsSearch['sort'];
  readonly limit: UploadJobsSearch['limit'];
}

function filterDraft(search: UploadJobsSearch): FilterDraft {
  return {
    q: search.q ?? '',
    dataSourceId: search.dataSourceId ?? '',
    lifecycleStatus: search.lifecycleStatus[0] ?? '',
    sort: search.sort,
    limit: search.limit,
  };
}

function MetricTile(props: Readonly<{
  eyebrow: string;
  label: string;
  value: ReactNode;
  icon: ReactNode;
  loading: boolean;
  failed: boolean;
}>) {
  return (
    <section
      className={styles.metricTile}
      aria-label={props.label}
      data-metric-state={props.loading ? 'loading' : props.failed ? 'error' : 'ready'}
    >
      <span className={styles.metricIcon} aria-hidden="true">
        {props.icon}
      </span>
      <span className={styles.metricCopy}>
        <span className={styles.metricEyebrow}>{props.eyebrow}</span>
        <span className={styles.metricLabel}>{props.label}</span>
        <strong>{props.loading ? '…' : props.failed ? '暂不可用' : props.value}</strong>
      </span>
    </section>
  );
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

function listState(
  query: {
    readonly isPending: boolean;
    readonly isError: boolean;
    readonly isFetching: boolean;
    readonly isPlaceholderData: boolean;
    readonly error: unknown;
    readonly data?: { readonly items: readonly unknown[] };
  },
  filtered: boolean,
): PageStateKind | 'ready' {
  if (query.isPending) return 'loading';
  if (query.isError && query.data === undefined) return stateFromError(query.error);
  if (query.data?.items.length === 0) return filtered ? 'filtered-empty' : 'empty';
  return query.isFetching && query.isPlaceholderData ? 'refreshing' : 'ready';
}

function requestId(error: unknown): string | null {
  return isDomainError(error) ? error.requestId : null;
}

function safeOperationError(error: unknown): string | null {
  if (!error) return null;
  if (!isDomainError(error)) return '操作未完成；服务端事实没有被乐观推进。';
  const message = error.code === 'FORBIDDEN' || error.code === 'UNAUTHENTICATED' ? '当前授权不允许执行此操作。' : error.code === 'VALIDATION_ERROR' ? '输入未通过服务端校验，请核对后重试。' : '操作未完成；服务端事实没有被乐观推进。';
  return error.requestId ? `${message} 请求 ID：${error.requestId}` : message;
}

export default function UploadJobsPage() {
  const scope = useIngestScope();
  const capabilities = useCapabilities();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const search = useMemo(() => uploadJobsQueryCodec.parse(params), [params]);
  const [draft, setDraft] = useState<FilterDraft>(() => filterDraft(search));
  useEffect(() => setDraft(filterDraft(search)), [search]);

  const listFilters = useMemo(() => {
    const tabStates = search.tab === 'uploading' ? ['CREATED', 'AUTHORIZING', 'UPLOADING', 'PAUSED', 'FINALIZING'] : search.tab === 'verifying' ? ['PENDING_VERIFY', 'VERIFYING'] : search.tab === 'available' ? ['AVAILABLE'] : search.tab === 'failed' ? ['FAILED', 'QUARANTINED', 'EXPIRED'] : [];
    return {
      q: search.q,
      dataSourceId: search.dataSourceId,
      datasetId: search.datasetId,
      lifecycleStatus: search.lifecycleStatus.length ? search.lifecycleStatus : tabStates,
      verificationStatus: search.verificationStatus,
      sort: search.sort,
      after: search.after,
      before: search.before,
      limit: search.limit,
    };
  }, [search]);

  const list = useUploadSessions(scope, listFilters, capabilities.has('upload.read'));
  const creationOptions = useUploadCreationOptions(
    scope,
    {
      targetDataSourceId: search.targetDataSourceId,
      targetDatasetId: search.targetDatasetId,
    },
    capabilities.has('upload.manage') && search.intent === 'create',
  );
  useUploadProgressStream(list.data?.items ?? [], capabilities.has('upload.read') && scope !== null);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [focusedUploadId, setFocusedUploadId] = useState<string>();
  const handleSelectedChange = useCallback((uploadIds: readonly string[]) => setSelected(new Set(uploadIds)), []);
  const handleOpenUpload = useCallback((uploadId: string) => void navigate(routes.uploads.build({ uploadId })), [navigate]);
  const vault = ingestUploadAuthorizationVault;
  const create = useUploadSessionMutation('create', vault);
  const pauseBatch = useUploadBatchMutation('pause', vault);
  const cancelBatch = useUploadBatchMutation('cancel', vault);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const scopeKey = scope ? `${scope.organizationId}/${scope.projectId}/${scope.regionCode}` : null;
  const previousScopeKey = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    vault.bindScope(scopeKey);
    if (previousScopeKey.current !== undefined && previousScopeKey.current !== scopeKey) {
      setParams(
        uploadJobsQueryCodec.build(
          updateUploadJobsSearch(
            search,
            {
              intent: undefined,
              targetDataSourceId: undefined,
              targetDatasetId: undefined,
            },
            true,
          ),
        ),
        { replace: true },
      );
      setSelected(new Set());
      setConfirmCancel(false);
    }
    previousScopeKey.current = scopeKey;
  }, [scopeKey, search, setParams, vault]);

  const selectedSessions = useMemo(() => list.data?.items.filter((session) => selected.has(session.uploadId)) ?? [], [list.data?.items, selected]);
  const focusedSession = list.data?.items.find((session) => session.uploadId === focusedUploadId) ?? list.data?.items[0];
  const cancelPreflight = useMemo<DangerPreflightEvidence | null>(() => {
    if (!scopeKey || list.dataUpdatedAt <= 0 || selectedSessions.length === 0) return null;
    const preparedAt = new Date(list.dataUpdatedAt);
    return {
      preparedAt: preparedAt.toISOString(),
      expiresAt: new Date(preparedAt.getTime() + 60_000).toISOString(),
      resourceVersion: selectedSessions.map((session) => `${session.uploadId}:${session.etag}`).join('|'),
      scopeKey,
    };
  }, [list.dataUpdatedAt, scopeKey, selectedSessions]);

  if (!scope)
    return (
      <main className={styles.page}>
        <PageState state="feature-unavailable" label="上传任务" />
      </main>
    );
  if (!capabilities.has('upload.read') && !capabilities.loading) {
    return (
      <main className={styles.page}>
        <PageState state="forbidden" label="上传任务" />
      </main>
    );
  }

  const apply = (next: Partial<UploadJobsSearch>) => {
    setSelected(new Set());
    setParams(uploadJobsQueryCodec.build({ ...search, ...next }, search));
  };
  const submitCreate = (uploadDraft: CreateUploadDraft) => {
    const source = creationOptions.data?.dataSources.find((candidate) => candidate.id === uploadDraft.dataSourceId && candidate.allowed);
    if (!source) return;
    create.mutate(
      {
        scope,
        idempotencyKey: createMutationIntentKey(),
        localFiles: uploadDraft.files,
        body: {
          data_source_id: uploadDraft.dataSourceId,
          target_dataset_id: uploadDraft.targetDatasetId,
          source_format: source.sourceFormat,
          source_format_version: creationOptions.data?.formats.find((format) => format.code === source.sourceFormat)?.version ?? null,
          expected_source_versions: {
            configuration_version: source.configurationVersion,
            credential_version: source.credentialVersion,
            upload_policy_version: source.uploadPolicyVersion,
          },
          objects: uploadDraft.files.map((file, index) => ({
            client_object_id: `local-${index + 1}`,
            relative_path: file.webkitRelativePath || file.name,
            size_bytes: String(file.size),
            media_type: file.type || 'application/octet-stream',
            last_modified_at: new Date(file.lastModified).toISOString(),
            declared_sha256: null,
          })),
          client_capabilities: {
            supports_web_worker_hash: true,
            supports_crc64: true,
            supports_background_continuation: false,
          },
        },
      },
      {
        onSuccess: (session) => {
          apply({
            intent: undefined,
            targetDataSourceId: undefined,
            targetDatasetId: undefined,
          });
          void navigate(routes.uploads.build({ uploadId: session.uploadId }));
        },
      },
    );
  };
  const clearAfterFullSuccess = (result: { readonly failed: number }) => {
    if (result.failed === 0) setSelected(new Set());
  };
  const creationBlockedReasons =
    creationOptions.data && !creationOptions.data.allowedActions.includes('CREATE')
      ? [
          ...creationOptions.data.blockedReasons,
          {
            code: 'ACTION_NOT_ALLOWED',
            message: '当前作用域未授予创建上传动作。',
          },
        ]
      : (creationOptions.data?.blockedReasons ?? []);
  const hasActiveFilters = search.tab !== 'all' || Boolean(search.q || search.dataSourceId || search.datasetId || search.lifecycleStatus.length || search.verificationStatus.length);
  const resolvedListState = listState(list, hasActiveFilters);
  const staleListSnapshot = list.isError && list.data !== undefined;
  const table = <UploadSessionTable items={list.data?.items ?? []} selected={selected} focusedUploadId={focusedSession?.uploadId} onSelectedChange={handleSelectedChange} onFocus={setFocusedUploadId} onOpen={handleOpenUpload} />;
  const pager = list.data ? <DataCursorPager pageInfo={list.data.pageInfo} busy={list.isFetching && list.isPlaceholderData} windowLabel={`当前窗口 ${list.data.items.length} 条 · 快照 ${list.data.snapshotAt}`} onChange={(cursor) => apply(cursor)} /> : null;
  const listContent =
    resolvedListState === 'ready' ? (
      table
    ) : resolvedListState === 'refreshing' ? (
      <PageState state="refreshing" label="上传任务列表">
        {table}
      </PageState>
    ) : (
      <PageState
        state={resolvedListState}
        label="上传任务列表"
        requestId={requestId(list.error)}
        onRetry={resolvedListState === 'error' || resolvedListState === 'offline' || resolvedListState === 'rate-limited' ? () => void list.refetch() : undefined}
        action={
          resolvedListState === 'filtered-empty' ? (
            <Button
              onClick={() =>
                apply({
                  tab: 'all',
                  q: undefined,
                  lifecycleStatus: [],
                  verificationStatus: [],
                  dataSourceId: undefined,
                  datasetId: undefined,
                })
              }
            >
              清除筛选
            </Button>
          ) : undefined
        }
      />
    );
  const operationError = safeOperationError(create.error ?? pauseBatch.error ?? cancelBatch.error);
  const metricState = { loading: list.isPending, failed: list.isError && list.data === undefined };
  const uploadingCount = list.data?.items.filter((item) => item.lifecycleStatus === 'UPLOADING').length ?? 0;
  const verifyingCount = list.data?.items.filter((item) => item.lifecycleStatus === 'VERIFYING').length ?? 0;
  const completedCount = list.data?.items.filter((item) => item.lifecycleStatus === 'AVAILABLE').length ?? 0;
  const visibleBytes = list.data?.items.reduce((total, item) => total + BigInt(item.progress.confirmedReceivedBytes), 0n) ?? 0n;
  const visibleGigabytes = Number((visibleBytes * 10n) / 1_073_741_824n) / 10;

  return (
    <main className={styles.page}>
      <StandardPageScaffold
        header={{
          title: '上传任务',
          breadcrumbs: [
            { key: 'ingest', label: '数据接入', to: routes.uploadJobs.build() },
            { key: 'uploads', label: '上传任务' },
          ],
          actions: (
            <>
              <Button href={routes.sources.build()}>数据源</Button>
              {capabilities.has('upload.manage') ? (
                <Button type="primary" icon={<Plus aria-hidden="true" size={16} />} onClick={() => apply({ intent: 'create' })}>
                  新建上传
                </Button>
              ) : null}
            </>
          ),
        }}
        summary={
          <div className={styles.summaryStack}>
            <nav className={styles.statusTabs} aria-label="上传状态">
              <Segmented
                value={search.tab}
                options={[
                  { value: 'all', label: '全部' },
                  { value: 'uploading', label: '上传中' },
                  { value: 'verifying', label: '校验中' },
                  { value: 'available', label: '已完成' },
                  { value: 'failed', label: '失败' },
                ]}
                onChange={(tab) =>
                  apply({
                    tab: tab as UploadJobsSearch['tab'],
                    lifecycleStatus: [],
                    verificationStatus: [],
                  })
                }
              />
            </nav>
            <section className={styles.metricGrid} aria-label="当前窗口">
              <MetricTile {...metricState} eyebrow="UPLOADING" label="上传中" value={uploadingCount} icon={<HardDriveUpload size={28} />} />
              <MetricTile {...metricState} eyebrow="VERIFYING" label="校验中" value={verifyingCount} icon={<ShieldCheck size={28} />} />
              <MetricTile {...metricState} eyebrow="COMPLETED" label="当前窗口已完成" value={completedCount} icon={<CircleCheckBig size={28} />} />
              <MetricTile {...metricState} eyebrow="THROUGHPUT" label="窗口确认流量" value={`${visibleGigabytes.toFixed(1)} GB`} icon={<Database size={28} />} />
            </section>
          </div>
        }
        filters={
          <div className={styles.filterStack}>
            <FilterToolbar
              label="上传任务筛选"
              onApply={() =>
                apply({
                  q: draft.q.trim() || undefined,
                  dataSourceId: draft.dataSourceId.trim() || undefined,
                  lifecycleStatus: draft.lifecycleStatus ? [draft.lifecycleStatus] : [],
                  verificationStatus: [],
                  sort: draft.sort,
                  limit: draft.limit,
                })
              }
              onReset={() => {
                const next = {
                  q: '',
                  dataSourceId: '',
                  lifecycleStatus: '',
                  sort: 'createdAt:desc' as const,
                  limit: 20 as const,
                };
                setDraft(next);
                apply({
                  tab: 'all',
                  q: undefined,
                  sort: next.sort,
                  limit: next.limit,
                  lifecycleStatus: [],
                  verificationStatus: [],
                  dataSourceId: undefined,
                  datasetId: undefined,
                });
              }}
            >
              <label className={styles.filterField}>
                搜索
                <Input
                  allowClear
                  placeholder="任务 ID、Dataset"
                  value={draft.q}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      q: event.target.value,
                    }))
                  }
                />
              </label>
              <label className={styles.filterField}>
                数据源
                <Input
                  allowClear
                  placeholder="全部数据源"
                  value={draft.dataSourceId}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      dataSourceId: event.target.value,
                    }))
                  }
                />
              </label>
              <label className={styles.filterField}>
                状态
                <Select
                  value={draft.lifecycleStatus}
                  onChange={(lifecycleStatus) => setDraft((current) => ({ ...current, lifecycleStatus }))}
                  options={[
                    { value: '', label: '全部状态' },
                    { value: 'UPLOADING', label: '上传中' },
                    { value: 'VERIFYING', label: '校验中' },
                    { value: 'AVAILABLE', label: '已完成' },
                    { value: 'FAILED', label: '失败' },
                    { value: 'QUARANTINED', label: '隔离' },
                  ]}
                />
              </label>
              <label className={styles.filterField}>
                排序
                <Select
                  value={draft.sort}
                  onChange={(sort) => setDraft((current) => ({ ...current, sort }))}
                  options={[
                    { value: 'createdAt:desc', label: '最近创建' },
                    { value: 'updatedAt:desc', label: '最近更新' },
                  ]}
                />
              </label>
              <label className={styles.filterField}>
                每页
                <Select
                  value={draft.limit}
                  onChange={(limit) => setDraft((current) => ({ ...current, limit }))}
                  options={[10, 20, 50].map((value) => ({
                    value,
                    label: String(value),
                  }))}
                />
              </label>
            </FilterToolbar>
          </div>
        }
        state={
          <Space className={styles.contentStack} orientation="vertical" size="middle">
            {operationError ? <Alert type="error" showIcon title={operationError} /> : null}
            {staleListSnapshot ? (
              <Alert
                type="warning"
                showIcon
                title={'\u5217\u8868\u5237\u65b0\u5931\u8d25\uff0c\u5f53\u524d\u663e\u793a\u4e0a\u6b21\u6210\u529f\u5feb\u7167\u3002'}
                description={
                  requestId(list.error)
                    ? `\u8bf7\u6c42 ID\uff1a${requestId(list.error)}`
                    : '\u8bf7\u786e\u8ba4\u5f53\u524d\u9879\u76ee\u548c Region \u540e\u91cd\u8bd5\u3002'
                }
                action={<Button onClick={() => void list.refetch()}>{'\u91cd\u65b0\u52a0\u8f7d'}</Button>}
              />
            ) : null}
            <BatchOperationBar
              count={selected.size}
              pauseDisabled={staleListSnapshot || !capabilities.has('upload.manage') || selectedSessions.some((session) => !session.allowedActions.includes('PAUSE'))}
              cancelDisabled={staleListSnapshot || !capabilities.has('upload.manage') || selectedSessions.some((session) => !session.allowedActions.includes('CANCEL'))}
              pending={pauseBatch.isPending || cancelBatch.isPending}
              result={pauseBatch.data ?? cancelBatch.data}
              onClear={() => setSelected(new Set())}
              onPause={() =>
                pauseBatch.mutate({
                  scope,
                  sessions: selectedSessions,
                  reason: '用户批量暂停',
                })
              }
              onCancel={() => setConfirmCancel(true)}
            />
            {listContent}
            {resolvedListState === 'ready' && focusedSession ? (
              <div className={styles.sessionFacts} aria-label="当前选中任务详情">
                <UploadSessionFacts session={focusedSession} onOpen={handleOpenUpload} />
              </div>
            ) : null}
          </Space>
        }
        pagination={pager}
      />
      <CreateUploadDialog
        open={search.intent === 'create' && capabilities.has('upload.manage')}
        pending={create.isPending}
        optionsPending={creationOptions.isPending}
        dataSources={creationOptions.data?.dataSources ?? []}
        datasets={creationOptions.data?.datasets ?? []}
        blockedReasons={creationBlockedReasons}
        initialDataSourceId={search.targetDataSourceId}
        initialDatasetId={search.targetDatasetId}
        onClose={() =>
          apply({
            intent: undefined,
            targetDataSourceId: undefined,
            targetDatasetId: undefined,
          })
        }
        onSubmit={submitCreate}
      />
      <DangerConfirmModal
        open={confirmCancel}
        title="确认批量取消上传"
        actionLabel="确认取消"
        resourceId={selectedSessions.map((session) => session.uploadId).join(', ')}
        impact="逐项清除内存上传授权，并携带各自 If-Match 与 Idempotency-Key 请求取消；服务端完成前任务保持 CANCELLING，不会从列表乐观移除。"
        blockers={
          selectedSessions.length !== selected.size
            ? [
                {
                  code: 'SELECTION_STALE',
                  message: '选择中包含已离开当前权威窗口的任务，请清除后重新选择。',
                },
              ]
            : []
        }
        preflight={cancelPreflight}
        currentScopeKey={scopeKey ?? ''}
        pending={cancelBatch.isPending}
        onCancel={() => setConfirmCancel(false)}
        onConfirm={() => {
          cancelBatch.mutate(
            { scope, sessions: selectedSessions, reason: '用户确认批量取消' },
            {
              onSuccess: (result) => {
                setConfirmCancel(false);
                clearAfterFullSuccess(result);
              },
            },
          );
        }}
      />
    </main>
  );
}
