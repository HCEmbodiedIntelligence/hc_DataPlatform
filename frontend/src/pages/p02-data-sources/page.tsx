import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { useAsyncJob } from '../../shared/jobs/use-async-job';
import { useIngestScope } from '../../features/ingest/use-ingest-scope';
import { routes } from '../../features/ingest/routing';
import { IngestRegion, type IngestRegionState } from '../../features/ingest/region-state';
import { useDataSource, useDataSourceMutation, useDataSourcesPage, useTestDataSourceConnection } from '../../features/ingest/api';
import { createMutationIntentKey } from '../../features/ingest/mutation-machine';
import { formText } from '../../features/ingest/form-data';
import { canMutateDataSource, isUnknownEnum } from '../../entities/data-source';
import {
  isConnectorEditable,
  toWritableBindingWire,
  toWritableConnectorWire,
  type WritableConnectorBinding,
} from '../../features/ingest/connectors/registry';
import { dataSourcesQueryCodec, updateDataSourcesSearch } from './query-codec';
import { DataSourceTable } from './components/DataSourceTable';
import { SourceEditorDialog, type SourceDraft } from './components/SourceEditorDialog';
import { ConfirmSourceStateDialog, ConnectorDeleteConfirmDialog, CredentialRotationDialog } from './components/SourceActionDialogs';
import '../../features/ingest/styles.css';
import './styles.css';

function queryState(query: { isPending: boolean; isError: boolean; isFetching: boolean; data?: unknown }, filtered: boolean): IngestRegionState {
  if (query.isPending) return 'first-loading';
  if (query.isError) return 'fatal-error';
  if (Array.isArray((query.data as { items?: unknown[] } | undefined)?.items) && (query.data as { items: unknown[] }).items.length === 0) return filtered ? 'filtered-empty' : 'empty';
  return query.isFetching ? 'refreshing' : 'ready';
}

function ConnectionJob({ jobId }: { readonly jobId: string }) {
  const job = useAsyncJob(jobId);
  if (!jobId) return null;
  return <p role="status">连接测试：{job.data?.status ?? 'QUEUED'}{job.connectionStatus !== 'connected' ? `（${job.connectionStatus}）` : ''}</p>;
}

export default function DataSourcesPage() {
  const scope = useIngestScope();
  const capabilities = useCapabilities();
  const [params, setParams] = useSearchParams();
  const search = useMemo(() => dataSourcesQueryCodec.parse(params), [params]);
  const scopeKey = scope ? `${scope.organizationId}/${scope.projectId}/${scope.regionCode}` : null;
  const previousScopeKey = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (previousScopeKey.current !== undefined && previousScopeKey.current !== scopeKey) {
      setParams(dataSourcesQueryCodec.build(updateDataSourcesSearch(search, { sourceId: undefined, intent: undefined }, true)), { replace: true });
    }
    previousScopeKey.current = scopeKey;
  }, [scopeKey, search, setParams]);
  const canRead = capabilities.has('ingest_source.read');
  const canManage = capabilities.has('ingest_source.manage');
  const listFilters = useMemo(() => ({ q: search.q, sourceType: search.sourceType, administrativeState: search.administrativeState, connectivity: search.connectivity, credentialState: search.credentialState, sort: search.sort, after: search.after, before: search.before, limit: search.limit }), [search]);
  const page = useDataSourcesPage(scope, listFilters, canRead);
  const detail = useDataSource(scope, search.sourceId ?? null, canRead);
  const createMutation = useDataSourceMutation('create');
  const updateMutation = useDataSourceMutation('update');
  const rotateMutation = useDataSourceMutation('rotate-credential');
  const stateMutation = useDataSourceMutation(detail.data?.administrativeState === 'ENABLED' ? 'disable' : 'enable');
  const testMutation = useTestDataSourceConnection();
  const [stateDialog, setStateDialog] = useState(false);
  const [deleteDialog, setDeleteDialog] = useState(false);
  const [editDialog, setEditDialog] = useState(false);
  const [rotateDialog, setRotateDialog] = useState(false);
  const [connectionJobId, setConnectionJobId] = useState('');

  if (!scope) return <IngestRegion label="数据源" state="feature-unavailable" />;
  if (!canRead && !capabilities.loading) return <IngestRegion label="数据源" state="forbidden" />;

  const applySearch = (next: Partial<typeof search>) => setParams(dataSourcesQueryCodec.build({ ...search, ...next }, search));
  const createSource = (draft: SourceDraft) => {
    createMutation.mutate({
      scope,
      idempotencyKey: createMutationIntentKey(),
      body: {
        name: draft.name,
        source_type: draft.configuration.kind,
        source_format: draft.sourceFormat,
        source_format_version: draft.sourceFormatVersion,
        binding: toWritableBindingWire(draft.binding),
        configuration: toWritableConnectorWire(draft.configuration),
        upload_policy_code: draft.uploadPolicyCode,
        ...(draft.credentialToken ? { credential_input: { kind: 'TOKEN', token: draft.credentialToken } } : {}),
      },
    }, { onSuccess: (source) => applySearch({ intent: undefined, sourceId: source.id }) });
  };

  const updateSource = (draft: SourceDraft) => {
    if (!detail.data || !draft.changeReason) return;
    updateMutation.mutate({
      scope,
      sourceId: detail.data.id,
      etag: detail.data.etag,
      idempotencyKey: createMutationIntentKey(),
      body: {
        name: draft.name,
        source_format: draft.sourceFormat,
        source_format_version: draft.sourceFormatVersion,
        binding: toWritableBindingWire(draft.binding),
        configuration: toWritableConnectorWire(draft.configuration),
        upload_policy_code: draft.uploadPolicyCode,
        change_reason: draft.changeReason,
      },
    }, { onSuccess: () => setEditDialog(false) });
  };

  const editorInitial = detail.data && isConnectorEditable(detail.data.configuration)
    ? {
        name: detail.data.name,
        sourceFormat: detail.data.sourceFormat,
        sourceFormatVersion: detail.data.sourceFormatVersion,
        uploadPolicyCode: detail.data.uploadPolicy.code,
        configuration: detail.data.configuration,
        binding: (detail.data.binding.kind === 'ROBOT'
          ? { kind: 'ROBOT', robotId: detail.data.binding.robotId }
          : detail.data.binding.kind === 'EDGE_AGENT'
            ? { kind: 'EDGE_AGENT', agentId: detail.data.binding.agentId }
            : detail.data.binding.kind === 'OSS_IMPORT'
              ? { kind: 'OSS_IMPORT', sourceAlias: detail.data.binding.sourceAlias }
              : null) as WritableConnectorBinding | null,
      }
    : null;

  return (
    <main className="ingest-page p02-page">
      <header className="page-header"><div><p className="eyebrow">数据接入</p><h1>数据源</h1><p>管理机器人、边缘代理与 OSS 导入连接器。</p></div><div className="header-actions"><Link to={routes.uploadJobs.build()}>上传任务</Link>{canManage && page.data?.allowedActions.includes('CREATE') ? <button type="button" onClick={() => applySearch({ intent: 'create' })}>新建数据源</button> : null}</div></header>
      <section className="metric-grid" aria-label="数据源摘要">
        <article><span>数据源总数</span><strong>{page.data?.summary.totalCount ?? '—'}</strong></article>
        <article><span>在线</span><strong>{page.data?.summary.onlineCount ?? '—'}</strong></article>
        <article><span>今日验证字节</span><strong>{page.data?.summary.verifiedBytesToday ?? '—'}</strong></article>
        <article><span>异常</span><strong>{page.data?.summary.abnormalCount ?? '—'}</strong></article>
      </section>
      {page.data?.componentErrors.map((error) => <p role="alert" key={`${error.component}-${error.requestId}`}>{error.component} 区域暂不可用：{error.message}（请求 ID：<code>{error.requestId}</code>）</p>)}
      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); const sourceType = formText(data, 'sourceType'); const administrativeState = formText(data, 'administrativeState'); const connectivity = formText(data, 'connectivity'); applySearch({ q: formText(data, 'q') || undefined, sourceType: sourceType ? [sourceType] : [], administrativeState: administrativeState ? [administrativeState] : [], connectivity: connectivity ? [connectivity] : [], sort: formText(data, 'sort') as typeof search.sort, limit: Number(formText(data, 'limit')) as typeof search.limit }); }}>
        <label>搜索<input name="q" defaultValue={search.q} placeholder="名称或稳定 ID" /></label><label>连接器<select name="sourceType" defaultValue={search.sourceType[0] ?? ''}><option value="">全部</option><option value="ROBOT">机器人</option><option value="EDGE_AGENT">边缘代理</option><option value="OSS_IMPORT">OSS 导入</option></select></label><label>启用状态<select name="administrativeState" defaultValue={search.administrativeState[0] ?? ''}><option value="">全部</option><option value="ENABLED">已启用</option><option value="DISABLED">已停用</option></select></label><label>连通状态<select name="connectivity" defaultValue={search.connectivity[0] ?? ''}><option value="">全部</option><option value="ONLINE">在线</option><option value="DEGRADED">降级</option><option value="OFFLINE">离线</option></select></label><label>排序<select name="sort" defaultValue={search.sort}><option value="updatedAt:desc">最近更新</option><option value="name:asc">名称</option><option value="lastTestAt:desc">最近测试</option></select></label><label>每页<select name="limit" defaultValue={search.limit}><option value="10">10</option><option value="20">20</option><option value="50">50</option></select></label><button type="submit">筛选</button>
      </form>
      <IngestRegion label="数据源列表" state={queryState(page, Boolean(search.q || search.sourceType.length || search.administrativeState.length || search.connectivity.length || search.credentialState.length))} onRetry={() => void page.refetch()} onClearFilters={() => applySearch({ q: undefined, sourceType: [], administrativeState: [], connectivity: [], credentialState: [] })}>
        <DataSourceTable items={page.data?.items ?? []} selectedId={search.sourceId} onSelect={(sourceId) => applySearch({ sourceId })} />
      </IngestRegion>
      <div className="cursor-bar"><button type="button" disabled={!page.data?.pageInfo.hasPreviousPage} onClick={() => applySearch({ before: page.data?.pageInfo.startCursor ?? undefined, after: undefined })}>上一组</button><button type="button" disabled={!page.data?.pageInfo.hasNextPage} onClick={() => applySearch({ after: page.data?.pageInfo.endCursor ?? undefined, before: undefined })}>下一组</button></div>
      {detail.data ? <aside className="inspector" aria-label="数据源详情"><button type="button" onClick={() => applySearch({ sourceId: undefined })}>关闭</button><h2>{detail.data.name}</h2><code>{detail.data.id}</code><dl><dt>连接状态</dt><dd>{typeof detail.data.connectivity.state === 'string' ? detail.data.connectivity.state : 'UNKNOWN'}</dd><dt>配置版本</dt><dd>{detail.data.configVersion}</dd><dt>凭据</dt><dd>{detail.data.credential.configured ? '已配置' : '未配置'} {detail.data.credential.maskedHint}</dd></dl>{isUnknownEnum(detail.data.sourceType) ? <p role="alert">未知连接器类型，仅支持只读查看且编辑、测试与启停全部禁用。</p> : null}<div className="action-row"><button type="button" disabled={!canManage || !editorInitial?.binding || !detail.data.allowedActions.includes('EDIT_CONFIGURATION')} onClick={() => setEditDialog(true)}>编辑</button><button type="button" disabled={!canManage || !canMutateDataSource(detail.data) || !detail.data.allowedActions.includes('ROTATE_CREDENTIAL')} onClick={() => setRotateDialog(true)}>轮换凭据</button><button type="button" disabled={!canManage || !canMutateDataSource(detail.data) || !detail.data.allowedActions.includes('TEST_CONNECTION')} onClick={() => { const key = createMutationIntentKey(); testMutation.mutate({ scope, sourceId: detail.data.id, etag: detail.data.etag, idempotencyKey: key, body: { observed_config_version: detail.data.configVersion, observed_credential_version: detail.data.credentialVersion } }, { onSuccess: (wire) => setConnectionJobId(wire.job.job_id) }); }}>测试连接</button><button type="button" disabled={!canManage || !canMutateDataSource(detail.data) || !detail.data.allowedActions.includes(detail.data.administrativeState === 'ENABLED' ? 'DISABLE' : 'ENABLE')} onClick={() => setStateDialog(true)}>{detail.data.administrativeState === 'ENABLED' ? '停用' : '启用'}</button>{detail.data.allowedActions.includes('DELETE') ? <button type="button" onClick={() => setDeleteDialog(true)}>删除</button> : null}</div><ConnectionJob jobId={connectionJobId} /></aside> : null}
      <SourceEditorDialog open={search.intent === 'create' && canManage && Boolean(page.data?.allowedActions.includes('CREATE'))} mode="create" pending={createMutation.isPending} onClose={() => applySearch({ intent: undefined })} onSubmit={createSource} />
      {editorInitial?.binding ? <SourceEditorDialog open={editDialog} mode="update" pending={updateMutation.isPending} initial={{ ...editorInitial, binding: editorInitial.binding }} onClose={() => setEditDialog(false)} onSubmit={updateSource} /> : null}
      {detail.data ? <CredentialRotationDialog open={rotateDialog} sourceId={detail.data.id} pending={rotateMutation.isPending} onClose={() => setRotateDialog(false)} onConfirm={(token, reason) => rotateMutation.mutate({ scope, sourceId: detail.data.id, etag: detail.data.etag, idempotencyKey: createMutationIntentKey(), body: { credential_input: { kind: 'TOKEN', token }, reason } }, { onSuccess: () => setRotateDialog(false) })} /> : null}
      {detail.data ? <ConfirmSourceStateDialog open={stateDialog} sourceId={detail.data.id} action={detail.data.administrativeState === 'ENABLED' ? 'disable' : 'enable'} pending={stateMutation.isPending} blockedReasons={detail.data.blockedReasons} onClose={() => setStateDialog(false)} onConfirm={() => stateMutation.mutate({ scope, sourceId: detail.data.id, etag: detail.data.etag, idempotencyKey: createMutationIntentKey(), body: { reason: '用户确认', expected_administrative_state: detail.data.administrativeState } }, { onSuccess: () => setStateDialog(false) })} /> : null}
      {detail.data ? <ConnectorDeleteConfirmDialog open={deleteDialog} sourceId={detail.data.id} blockedReasons={detail.data.blockedReasons} onClose={() => setDeleteDialog(false)} /> : null}
    </main>
  );
}
