import { useMemo, useRef, useState } from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { useSearchParams } from 'react-router-dom';
import type { CalibrationSet } from '../../entities/calibration';
import { useCalibrationSet, useCalibrationSets, usePreflightCalibrationPublish, usePublishCalibration } from '../../features/calibrations/api';
import { canPublishCalibration, resolveCalibrationFallback, validateCovariance } from '../../features/calibrations/publish-rules';
import { calibrationsQueryCodec } from '../../features/calibrations/routing';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { ConfirmDialog, DetailTabs, EmptyState, ErrorPanel, PageHeader, SkeletonBlock, StandardTable, StatusBadge } from '../../shared/ui';
import { pageCalibrationsQueryCodec } from './query-codec';
import './page.css';

const sectionTabs = [
  { id: 'overview', label: '概览' },
  { id: 'intrinsics', label: 'Transform' },
  { id: 'transforms', label: 'Frame Graph' },
  { id: 'timeCalibrations', label: 'Covariance' },
  { id: 'jointCalibrations', label: 'Fallback' },
] as const;

function precisionFields(readOnly: boolean) {
  const translation = ['0.000000000000', '0.000000000000', '0.000000000000'];
  const quaternion = ['0.000000000000', '0.000000000000', '0.000000000000', '1.000000000000'];
  return (
    <div className="precision-editor">
      <h3>Transform（字符串精度）</h3>
      <fieldset disabled={readOnly}>
        <legend>Translation XYZ</legend>
        {translation.map((value, index) => <label key={`t-${index}`}>轴 {index + 1}<input inputMode="decimal" defaultValue={value} /></label>)}
      </fieldset>
      <fieldset disabled={readOnly}>
        <legend>Quaternion XYZW</legend>
        {quaternion.map((value, index) => <label key={`q-${index}`}>分量 {index + 1}<input inputMode="decimal" defaultValue={value} /></label>)}
      </fieldset>
      <p>浏览器不对十进制字符串做隐式浮点归一化；权威校验与 hash 由服务端生成。</p>
    </div>
  );
}

export function Component() {
  const [params, setParams] = useSearchParams();
  const search = pageCalibrationsQueryCodec.parse(params);
  const routeResolution = calibrationsQueryCodec.parse(params);
  const routeParams = routeResolution.kind === 'unresolved' || routeResolution.kind === 'resolved' ? routeResolution.params : null;
  const sets = useCalibrationSets(routeParams ? { robot_id: routeParams.robotId, component_id: routeParams.componentId } : {});
  const selectedQuery = useCalibrationSet(routeParams?.setId ?? null);
  const capabilities = useCapabilities();
  const preflight = usePreflightCalibrationPublish();
  const publish = usePublishCalibration();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [intent, setIntent] = useState<{ readonly key: string; readonly token: string; readonly impact: string } | null>(null);
  const intentKey = useRef<string | null>(null);
  const covariance = useMemo(() => Array.from({ length: 36 }, (_, index) => index % 7 === 0 ? '0.000001000000' : '0.000000000000'), []);
  const covarianceErrors = validateCovariance(covariance);

  const columns = useMemo<ColumnDef<CalibrationSet, unknown>[]>(() => [
    { id: 'id', header: '标定集', cell: ({ row }) => <button className="link-button" type="button" onClick={() => setParams(pageCalibrationsQueryCodec.build({ ...search, robotId: row.original.robotId, componentId: row.original.componentId ?? 'component-unbound', setId: row.original.id }, search))}>{row.original.id}</button> },
    { id: 'version', header: '版本', cell: ({ row }) => row.original.version },
    { id: 'status', header: '快照', cell: ({ row }) => <StatusBadge status={row.original.snapshotStatus} tone={row.original.snapshotStatus === 'READY' ? 'success' : row.original.snapshotStatus === 'UNKNOWN' ? 'warning' : 'neutral'} /> },
    { id: 'availability', header: 'Availability（只读）', cell: ({ row }) => row.original.availability ?? 'Draft 未生效' },
  ], [search, setParams]);

  if (sets.isPending || capabilities.loading) return <section className="management-page"><PageHeader title="标定管理" /><SkeletonBlock width="100%" height="28rem" label="标定管理加载中" /></section>;
  if (sets.error && isDomainError(sets.error)) return <section className="management-page"><PageHeader title="标定管理" /><ErrorPanel error={sets.error} onRetry={() => void sets.refetch()} /></section>;
  if (routeResolution.kind === 'not-found') return <section className="management-page"><PageHeader title="标定管理" /><EmptyState kind="no-data" title="标定深链无效" description={`未找到严格的 robot/component/set 关系：${routeResolution.reason}`} /></section>;

  const selected = selectedQuery.data;
  const relationMismatch = Boolean(selected && routeParams && (selected.robotId !== routeParams.robotId || selected.componentId !== routeParams.componentId));
  if (relationMismatch) return <section className="management-page"><PageHeader title="标定管理" /><EmptyState kind="no-data" title="标定引用关系不存在" description="组件、机器人与标定集不属于同一权威关系；不会回退到 latest。" /></section>;
  const evidence = selected?.validation ? { ...selected.validation, blockedReasons: selected.blockedReasons } : null;
  const decision = selected ? canPublishCalibration(selected, evidence) : { allowed: false, reasons: ['请选择固定标定集。'] };
  const canPublish = Boolean(selected && decision.allowed && capabilities.has('calibration.publish'));

  const startPreflight = () => {
    if (!selected?.contentHash || !selected.validationContextHash || !selected.validation || !canPublish) return;
    const key = crypto.randomUUID();
    intentKey.current = key;
    preflight.mutate({ setId: selected.id, version: selected.version, etag: selected.etag, expectedHash: selected.contentHash, validationContextHash: selected.validationContextHash, validationReportId: selected.validation.reportId, changeSummary: '发布不可变标定版本', idempotencyKey: key }, {
      onSuccess(result) {
        if (!result.allowed || !result.preflight_token || result.blockers.length) { setIntent(null); return; }
        setIntent({ key, token: result.preflight_token, impact: [...result.impacts, ...result.warnings].map((entry) => entry.message).join('；') || 'Ready 内容与 content hash 将不可变。' });
        setConfirmOpen(true);
      },
    });
  };

  return (
    <section className="management-page">
      <PageHeader title="标定管理" description="固定版本、结构化高精度事实、Frame Graph 与发布证据" breadcrumbs={[{ label: '系统管理' }, { label: '标定管理' }]} actions={<button type="button" disabled={!canPublish || preflight.isPending} onClick={startPreflight}>{preflight.isPending ? '正在预检…' : '预检发布'}</button>} />
      <div className="three-pane calibration-layout">
        <section><h2>标定集</h2><StandardTable data={sets.data?.items ?? []} columns={columns} getRowId={(item) => item.id} caption="标定集" empty={<EmptyState kind="no-data" />} /></section>
        <section className="tree-panel"><h2>Frame Graph</h2><div className="frame-graph" role="img" aria-label="Frame Graph 结构化预览"><span>world</span><span aria-hidden="true">→</span><span>base_link</span><span aria-hidden="true">→</span><span>{routeParams?.componentId ?? 'component'}</span></div><p>图形不是唯一事实源；方向为 parent → child，循环由服务端校验。</p></section>
        <aside className="detail-panel">
          <h2>{selected?.id ?? '选择固定标定集'}</h2>
          {selected ? <>
            <p>Robot：<code>{selected.robotId}</code></p><p>Component：<code>{selected.componentId ?? 'unbound'}</code></p><p>版本：{selected.version}</p>
            <p>Content hash：<code>{selected.contentHash ?? '—'}</code></p><p>Context hash：<code>{selected.validationContextHash ?? '—'}</code></p>
            <DetailTabs tabs={sectionTabs} activeTab={search.section} onChange={(section) => setParams(pageCalibrationsQueryCodec.build({ ...search, section: section as typeof search.section }, search))} />
            <div id={`tabpanel-${search.section}`} role="tabpanel" aria-labelledby={`tab-${search.section}`}>
              {search.section === 'intrinsics' ? precisionFields(selected.snapshotStatus !== 'DRAFT') : null}
              {search.section === 'timeCalibrations' ? <div><h3>Covariance 6×6</h3><pre className="matrix-view">{covariance.map((value, index) => `${value}${(index + 1) % 6 === 0 ? '\n' : '  '}`).join('')}</pre>{covarianceErrors.map((error) => <p role="alert" key={error}>{error}</p>)}</div> : null}
              {search.section === 'jointCalibrations' ? <p>{resolveCalibrationFallback(selected, [selected]).kind === 'resolved' ? '显式绑定优先；唯一候选才允许推导。' : 'Fallback 已阻断。'}</p> : null}
              {search.section === 'overview' || search.section === 'transforms' ? <p>Validation：{selected.validation?.status ?? 'NOT_RUN'}。Availability 仅为派生只读投影，无停用或恢复入口。</p> : null}
            </div>
            {decision.reasons.map((reason) => <p role="note" key={reason}>{reason}</p>)}
          </> : <EmptyState kind="no-data" title="选择标定集" description="深链必须包含 robotId、componentId 与 setId。" />}
        </aside>
      </div>
      <ConfirmDialog open={confirmOpen} title="发布不可变标定版本" resourceId={selected?.id ?? 'unknown'} impact={`${intent?.impact ?? '缺少预检证据'}；blocked reasons：${selected?.blockedReasons.map((reason) => reason.message).join('；') || '无'}`} confirmLabel="确认发布" pending={publish.isPending} onCancel={() => { setConfirmOpen(false); setIntent(null); intentKey.current = null; }} onConfirm={() => {
        if (!selected || !intent || intentKey.current !== intent.key) return;
        publish.mutate({ setId: selected.id, version: selected.version, etag: selected.etag, idempotencyKey: intent.key, preflightToken: intent.token }, { onSettled: () => { setConfirmOpen(false); setIntent(null); intentKey.current = null; } });
      }} />
    </section>
  );
}

export default Component;
