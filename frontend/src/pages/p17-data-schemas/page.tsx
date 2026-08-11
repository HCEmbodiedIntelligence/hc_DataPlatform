import { useMemo, useRef, useState } from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { useSearchParams } from 'react-router-dom';
import type { DataSchemaVersion } from '../../entities/data-schema';
import { useDataSchemas, useDataSchemaVersion, usePreflightDataSchemaPublish, usePublishDataSchema, useResolveDataSchemaRoute } from '../../features/data-schemas/api';
import { canPublishSchema, schemaTelemetryProjection } from '../../features/data-schemas/registry-rules';
import { dataSchemasQueryCodec } from '../../features/data-schemas/routing';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { ConfirmDialog, DetailTabs, EmptyState, ErrorPanel, PageHeader, SkeletonBlock, StandardTable, StatusBadge } from '../../shared/ui';
import { pageDataSchemasQueryCodec } from './query-codec';
import './page.css';

const detailTabs = [
  { id: 'fields', label: '字段' }, { id: 'encoding', label: '编码' }, { id: 'compatibility', label: '兼容性' }, { id: 'references', label: '引用' },
] as const;

function readOnlyDefinition(definition: Readonly<Record<string, unknown>>) {
  return <pre className="schema-code" tabIndex={0} aria-label="Schema 定义，只读">{JSON.stringify(definition, null, 2)}</pre>;
}

export function Component() {
  const [params, setParams] = useSearchParams();
  const search = pageDataSchemasQueryCodec.parse(params);
  const strictRoute = dataSchemasQueryCodec.parse(params);
  const relation = strictRoute.kind === 'unresolved' || strictRoute.kind === 'resolved' ? strictRoute.params : null;
  const mustResolveRelation = Boolean(search.componentId);
  const routeResolution = useResolveDataSchemaRoute(mustResolveRelation ? relation : null);
  const list = useDataSchemas(search.q ? { q: search.q } : {});
  const detail = useDataSchemaVersion(search.schemaId ?? null, search.schemaVersion ?? null);
  const capabilities = useCapabilities();
  const preflight = usePreflightDataSchemaPublish();
  const publish = usePublishDataSchema();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [intent, setIntent] = useState<{ readonly key: string; readonly token: string; readonly impact: string } | null>(null);
  const intentKey = useRef<string | null>(null);

  const columns = useMemo<ColumnDef<DataSchemaVersion, unknown>[]>(() => [
    { id: 'displayName', header: 'Schema', cell: ({ row }) => <button className="link-button" type="button" onClick={() => setParams(pageDataSchemasQueryCodec.build({ ...search, schemaId: row.original.schemaId, schemaVersion: row.original.version, componentId: undefined }, search))}>{row.original.displayName}</button> },
    { id: 'version', header: '版本', cell: ({ row }) => `v${row.original.version}` },
    { id: 'logicalType', header: '逻辑类型', cell: ({ row }) => row.original.logicalType },
    { id: 'status', header: '生命周期', cell: ({ row }) => <StatusBadge status={row.original.status} tone={row.original.status === 'PUBLISHED' ? 'success' : row.original.status === 'UNKNOWN' ? 'warning' : 'neutral'} /> },
    { id: 'compatibility', header: '兼容性', cell: ({ row }) => <StatusBadge status={row.original.compatibilityResult ?? 'NOT_RUN'} tone={row.original.compatibilityResult === 'UNKNOWN' ? 'warning' : 'info'} /> },
  ], [search, setParams]);

  if (list.isPending || capabilities.loading) return <section className="management-page"><PageHeader title="数据 Schema" /><SkeletonBlock width="100%" height="28rem" label="Schema Registry 加载中" /></section>;
  if (list.error && isDomainError(list.error)) return <section className="management-page"><PageHeader title="数据 Schema" /><ErrorPanel error={list.error} onRetry={() => void list.refetch()} /></section>;
  if (mustResolveRelation && (strictRoute.kind === 'not-found' || !relation)) return <section className="management-page"><PageHeader title="数据 Schema" /><EmptyState kind="no-data" title="Schema 深链无效" description="schemaId/schemaVersion/componentId 必须完整，版本会移除 v 前缀后验证。" /></section>;
  if (mustResolveRelation && routeResolution.error) return <section className="management-page"><PageHeader title="数据 Schema" /><EmptyState kind="no-data" title="组件未引用此 Schema 版本" description="引用解析失败，不会替换为 latest/current。" /></section>;

  const selected = detail.data;
  const evidence = selected?.hash && selected.compatibilityResult ? {
    status: 'SUCCEEDED' as const, result: selected.compatibilityResult, baselineHash: selected.hash.value, targetHash: selected.hash.value, mode: selected.compatibilityMode, rulesetVersion: selected.hash.canonicalizationVersion,
  } : null;
  const decision = selected ? canPublishSchema(selected, evidence) : { allowed: false, reasons: ['请选择固定 Schema 版本。'] };
  const canPublish = Boolean(selected && decision.allowed && capabilities.has('data_schema.publish'));

  const startPreflight = () => {
    if (!selected?.hash || !evidence || !canPublish) return;
    const key = crypto.randomUUID();
    intentKey.current = key;
    preflight.mutate({ schemaId: selected.schemaId, schemaVersion: selected.version, etag: selected.etag, expectedHash: selected.hash.value, validationReportId: `${selected.schemaId}:${selected.version}:validation`, compatibilityCheckId: `${selected.schemaId}:${selected.version}:compatibility`, changeSummary: '发布不可变 Schema 版本', idempotencyKey: key }, {
      onSuccess(result) {
        if (!result.allowed || !result.preflight_token || result.blockers.length) { setIntent(null); return; }
        setIntent({ key, token: result.preflight_token, impact: [...result.impacts, ...result.warnings].map((entry) => entry.message).join('；') || '发布后定义与 canonical hash 不可原地修改。' });
        setConfirmOpen(true);
      },
    });
  };

  return (
    <section className="management-page">
      <PageHeader title="数据 Schema" description="组织级 Registry、固定版本、服务端兼容性与引用事实" breadcrumbs={[{ label: '系统管理' }, { label: '数据 Schema' }]} actions={<button type="button" disabled={!canPublish || preflight.isPending} onClick={startPreflight}>{preflight.isPending ? '正在预检…' : '预检发布'}</button>} />
      <nav className="local-tabs" aria-label="Schema 区域">{(['registry', 'snapshots', 'compatibility'] as const).map((tab) => <button type="button" key={tab} aria-current={search.tab === tab ? 'page' : undefined} onClick={() => setParams(pageDataSchemasQueryCodec.build({ ...search, tab }, search))}>{tab}</button>)}</nav>
      <div className="workspace-grid schema-layout">
        <section><StandardTable data={list.data?.items ?? []} columns={columns} getRowId={(item) => `${item.schemaId}:${item.version}`} caption="Schema Registry" empty={<EmptyState kind={search.q ? 'filtered-empty' : 'no-data'} />} /></section>
        <aside className="detail-panel">
          <h2>{selected?.displayName ?? '选择固定版本'}</h2>
          {selected ? <>
            <p>ID：<code>{selected.schemaId}</code> / v{selected.version}</p><p>Family：<code>{selected.familyId}</code></p>
            <p>Hash：<code>{selected.hash ? `${selected.hash.algorithm}:${selected.hash.value}` : '—'}</code></p>
            <p>Canonicalization：{selected.hash?.canonicalizationVersion ?? '—'}</p>
            <p>Telemetry 安全投影：{Object.keys(schemaTelemetryProjection(selected)).join('、')}；Schema 原文永不进入遥测。</p>
            <DetailTabs tabs={detailTabs} activeTab={search.detailTab} onChange={(detailTab) => setParams(pageDataSchemasQueryCodec.build({ ...search, detailTab: detailTab as typeof search.detailTab }, search))} />
            <div id={`tabpanel-${search.detailTab}`} role="tabpanel" aria-labelledby={`tab-${search.detailTab}`}>
              {search.detailTab === 'fields' || search.detailTab === 'encoding' ? readOnlyDefinition(selected.definition) : null}
              {search.detailTab === 'compatibility' ? <p>Mode：{selected.compatibilityMode}；结果：{selected.compatibilityResult ?? 'NOT_RUN'}。未知 verdict 必须只读。</p> : null}
              {search.detailTab === 'references' ? <p>Component：<code>{search.componentId ?? '未从组件上下文进入'}</code>；引用计数与分页由服务端授权投影决定。</p> : null}
            </div>
            {decision.reasons.map((reason) => <p role="note" key={reason}>{reason}</p>)}
          </> : <EmptyState kind="no-data" title="选择固定 Schema 版本" description="页面不会自动使用 latest/current。" />}
        </aside>
      </div>
      <ConfirmDialog open={confirmOpen} title="发布不可变 Schema 版本" resourceId={selected ? `${selected.schemaId}:v${selected.version}` : 'unknown'} impact={`${intent?.impact ?? '缺少预检证据'}；blocked reasons：${selected?.blockedReasons.map((reason) => reason.message).join('；') || '无'}`} confirmLabel="确认发布" pending={publish.isPending} onCancel={() => { setConfirmOpen(false); setIntent(null); intentKey.current = null; }} onConfirm={() => {
        if (!selected || !intent || intentKey.current !== intent.key) return;
        publish.mutate({ schemaId: selected.schemaId, schemaVersion: selected.version, etag: selected.etag, idempotencyKey: intent.key, preflightToken: intent.token }, { onSettled: () => { setConfirmOpen(false); setIntent(null); intentKey.current = null; } });
      }} />
    </section>
  );
}

export default Component;
