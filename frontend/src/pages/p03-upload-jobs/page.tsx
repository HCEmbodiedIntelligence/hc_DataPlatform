import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { ConfirmDialog } from '../../shared/ui/ConfirmDialog';
import { useUploadBatchMutation, useUploadCreationOptions, useUploadSessions, useUploadSessionMutation } from '../../features/ingest/api';
import { IngestRegion, type IngestRegionState } from '../../features/ingest/region-state';
import { routes } from '../../features/ingest/routing';
import { ingestUploadAuthorizationVault } from '../../features/ingest/upload/authorization-vault';
import { useIngestScope } from '../../features/ingest/use-ingest-scope';
import { createMutationIntentKey } from '../../features/ingest/mutation-machine';
import { formText } from '../../features/ingest/form-data';
import { useUploadProgressStream } from '../../features/ingest/upload/use-upload-progress-stream';
import { updateUploadJobsSearch, uploadJobsQueryCodec } from './query-codec';
import { UploadSessionTable } from './components/UploadSessionTable';
import { CreateUploadDialog, type CreateUploadDraft } from './components/CreateUploadDialog';
import { BatchOperationBar } from './components/BatchOperationBar';
import '../../features/ingest/styles.css';

function stateOf(query: { isPending: boolean; isError: boolean; isFetching: boolean; data?: { items: readonly unknown[] } }, filtered: boolean): IngestRegionState {
  if (query.isPending) return 'first-loading';
  if (query.isError) return 'fatal-error';
  if (query.data?.items.length === 0) return filtered ? 'filtered-empty' : 'empty';
  return query.isFetching ? 'refreshing' : 'ready';
}

export default function UploadJobsPage() {
  const scope = useIngestScope();
  const capabilities = useCapabilities();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const search = useMemo(() => uploadJobsQueryCodec.parse(params), [params]);
  const listFilters = useMemo(() => {
    const tabStates = search.tab === 'uploading' ? ['CREATED', 'AUTHORIZING', 'UPLOADING', 'PAUSED', 'FINALIZING']
      : search.tab === 'verifying' ? ['PENDING_VERIFY', 'VERIFYING']
        : search.tab === 'available' ? ['AVAILABLE']
          : search.tab === 'failed' ? ['FAILED', 'QUARANTINED', 'EXPIRED'] : [];
    return { q: search.q, dataSourceId: search.dataSourceId, datasetId: search.datasetId, lifecycleStatus: search.lifecycleStatus.length ? search.lifecycleStatus : tabStates, verificationStatus: search.verificationStatus, sort: search.sort, after: search.after, before: search.before, limit: search.limit };
  }, [search]);
  const list = useUploadSessions(scope, listFilters, capabilities.has('upload.read'));
  const creationOptions = useUploadCreationOptions(scope, { targetDataSourceId: search.targetDataSourceId, targetDatasetId: search.targetDatasetId }, capabilities.has('upload.manage') && search.intent === 'create');
  const progressConnection = useUploadProgressStream(list.data?.items ?? [], capabilities.has('upload.read') && scope !== null);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
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
      setParams(uploadJobsQueryCodec.build(updateUploadJobsSearch(search, { intent: undefined, targetDataSourceId: undefined, targetDatasetId: undefined }, true)), { replace: true });
      setSelected(new Set());
    }
    previousScopeKey.current = scopeKey;
  }, [scopeKey, search, setParams, vault]);

  if (!scope) return <IngestRegion label="上传任务" state="feature-unavailable" />;
  if (!capabilities.has('upload.read') && !capabilities.loading) return <IngestRegion label="上传任务" state="forbidden" />;
  const apply = (next: Partial<typeof search>) => {
    setSelected(new Set());
    setParams(uploadJobsQueryCodec.build({ ...search, ...next }, search));
  };
  const submitCreate = (draft: CreateUploadDraft) => {
    const source = creationOptions.data?.dataSources.find((candidate) => candidate.id === draft.dataSourceId && candidate.allowed);
    if (!source) return;
    create.mutate({
    scope,
    idempotencyKey: createMutationIntentKey(),
    localFiles: draft.files,
    body: {
      data_source_id: draft.dataSourceId,
      target_dataset_id: draft.targetDatasetId,
      source_format: source.sourceFormat,
      source_format_version: creationOptions.data?.formats.find((format) => format.code === source.sourceFormat)?.version ?? null,
      expected_source_versions: {
        configuration_version: source.configurationVersion,
        credential_version: source.credentialVersion,
        upload_policy_version: source.uploadPolicyVersion,
      },
      objects: draft.files.map((file, index) => ({
        client_object_id: `local-${index + 1}`,
        relative_path: file.webkitRelativePath || file.name,
        size_bytes: String(file.size),
        media_type: file.type || 'application/octet-stream',
        last_modified_at: new Date(file.lastModified).toISOString(),
        declared_sha256: null,
      })),
      client_capabilities: { supports_web_worker_hash: true, supports_crc64: true, supports_background_continuation: false },
    },
  }, { onSuccess: (session) => { apply({ intent: undefined, targetDataSourceId: undefined, targetDatasetId: undefined }); void navigate(routes.uploads.build({ uploadId: session.uploadId })); } });
  };
  const selectedSessions = list.data?.items.filter((session) => selected.has(session.uploadId)) ?? [];
  const clearAfterFullSuccess = (result: { readonly failed: number }) => { if (result.failed === 0) setSelected(new Set()); };
  const creationBlockedReasons = creationOptions.data && !creationOptions.data.allowedActions.includes('CREATE')
    ? [...creationOptions.data.blockedReasons, { code: 'ACTION_NOT_ALLOWED', message: '当前作用域未授予创建上传动作。' }]
    : creationOptions.data?.blockedReasons ?? [];
  const hasActiveFilters = search.tab !== 'all' || Boolean(search.q || search.dataSourceId || search.datasetId || search.lifecycleStatus.length || search.verificationStatus.length);

  return <main className="ingest-page p03-page"><header className="page-header"><div><p className="eyebrow">数据接入</p><h1>上传任务</h1><p>服务端筛选、稳定排序与游标分页；进度由 SSE 加速并以查询快照为准（{progressConnection === 'connected' ? '实时' : '轮询兜底'}）。</p></div><div className="header-actions"><Link to={routes.sources.build()}>数据源</Link>{capabilities.has('upload.manage') ? <button type="button" onClick={() => apply({ intent: 'create' })}>新建上传</button> : null}</div></header><section className="metric-grid" aria-label="上传摘要"><article><span>当前窗口</span><strong>{list.data?.items.length ?? '—'}</strong></article><article><span>上传中</span><strong>{list.data?.items.filter((item) => item.lifecycleStatus === 'UPLOADING').length ?? '—'}</strong></article><article><span>校验中</span><strong>{list.data?.items.filter((item) => item.lifecycleStatus === 'VERIFYING').length ?? '—'}</strong></article><article><span>隔离</span><strong>{list.data?.items.filter((item) => item.lifecycleStatus === 'QUARANTINED').length ?? '—'}</strong></article></section><nav className="upload-tabs" aria-label="上传状态">{(['all','uploading','verifying','available','failed'] as const).map((tab) => <button type="button" key={tab} aria-current={search.tab === tab ? 'page' : undefined} onClick={() => apply({ tab, lifecycleStatus: [], verificationStatus: [] })}>{tab}</button>)}</nav><form className="filter-bar" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); apply({ q: formText(data, 'q') || undefined, sort: formText(data, 'sort') as typeof search.sort, limit: Number(formText(data, 'limit')) as typeof search.limit }); }}><label>搜索<input name="q" defaultValue={search.q} /></label><label>排序<select name="sort" defaultValue={search.sort}><option value="createdAt:desc">最近创建</option><option value="updatedAt:desc">最近更新</option></select></label><label>每页<select name="limit" defaultValue={search.limit}><option value="10">10</option><option value="20">20</option><option value="50">50</option></select></label><button type="submit">筛选</button></form><BatchOperationBar count={selected.size} pauseDisabled={!capabilities.has('upload.manage') || selectedSessions.some((session) => !session.allowedActions.includes('PAUSE'))} cancelDisabled={!capabilities.has('upload.manage') || selectedSessions.some((session) => !session.allowedActions.includes('CANCEL'))} pending={pauseBatch.isPending || cancelBatch.isPending} result={pauseBatch.data ?? cancelBatch.data} onClear={() => setSelected(new Set())} onPause={() => pauseBatch.mutate({ scope, sessions: selectedSessions, reason: '用户批量暂停' })} onCancel={() => setConfirmCancel(true)} /><IngestRegion label="上传任务列表" state={stateOf(list, hasActiveFilters)} onRetry={() => void list.refetch()} onClearFilters={() => apply({ tab: 'all', q: undefined, lifecycleStatus: [], verificationStatus: [], dataSourceId: undefined, datasetId: undefined })}><UploadSessionTable items={list.data?.items ?? []} selected={selected} onToggle={(id) => setSelected((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; })} onOpen={(uploadId) => { void navigate(routes.uploads.build({ uploadId })); }} /></IngestRegion><div className="cursor-bar"><button type="button" disabled={!list.data?.pageInfo.hasPreviousPage} onClick={() => apply({ before: list.data?.pageInfo.startCursor ?? undefined, after: undefined })}>上一组</button><button type="button" disabled={!list.data?.pageInfo.hasNextPage} onClick={() => apply({ after: list.data?.pageInfo.endCursor ?? undefined, before: undefined })}>下一组</button></div><CreateUploadDialog open={search.intent === 'create' && capabilities.has('upload.manage')} pending={create.isPending} optionsPending={creationOptions.isPending} dataSources={creationOptions.data?.dataSources ?? []} datasets={creationOptions.data?.datasets ?? []} blockedReasons={creationBlockedReasons} initialDataSourceId={search.targetDataSourceId} initialDatasetId={search.targetDatasetId} onClose={() => apply({ intent: undefined, targetDataSourceId: undefined, targetDatasetId: undefined })} onSubmit={submitCreate} /><ConfirmDialog open={confirmCancel} title="确认批量取消上传" impact="逐项清除内存上传授权，并携带各自 If-Match 与 Idempotency-Key 请求取消；服务端完成前任务保持 CANCELLING，不会从列表乐观移除。" resourceId={selectedSessions.map((session) => session.uploadId).join(', ')} confirmLabel="确认取消" pending={cancelBatch.isPending} onCancel={() => setConfirmCancel(false)} onConfirm={() => { cancelBatch.mutate({ scope, sessions: selectedSessions, reason: '用户确认批量取消' }, { onSuccess: (result) => { setConfirmCancel(false); clearAfterFullSuccess(result); } }); }} /></main>;
}
