import { useMemo, useState } from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { useSearchParams } from 'react-router-dom';
import type { LifecyclePolicy } from '../../entities/lifecycle-policy';
import { useCreateLifecycleSimulation, useEnableLifecyclePolicy, useLifecyclePage, useLifecycleSimulation } from '../../features/lifecycle/api';
import { simulationAuthorizesDangerousAction, type SimulationEvidence } from '../../features/lifecycle/state-machines';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { useAsyncJob } from '../../shared/jobs/use-async-job';
import { ConfirmDialog, EmptyState, ErrorPanel, MetricCard, PageHeader, SkeletonBlock, StandardTable, StatusBadge } from '../../shared/ui';
import { lifecycleTabs, storageLifecycleQueryCodec } from './query-codec';
import { storageOverviewPendingLink } from '../../features/lifecycle/pending-links';
import './page.css';

function formatBytes(value: string): string {
  const bytes = BigInt(value);
  if (bytes === 0n) return '0 B';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'] as const;
  let scaled = bytes;
  let unit = 0;
  while (scaled >= 1024n && unit < units.length - 1) {
    scaled /= 1024n;
    unit += 1;
  }
  return `${new Intl.NumberFormat('zh-CN').format(scaled)} ${units[unit]}`;
}

export function Component() {
  const [params, setParams] = useSearchParams();
  const search = storageLifecycleQueryCodec.parse(params);
  const capabilities = useCapabilities();
  const lifecycle = useLifecyclePage();
  const simulation = useCreateLifecycleSimulation();
  const enablePolicy = useEnableLifecyclePolicy();
  const [simulationIdentity, setSimulationIdentity] = useState<{ readonly simulationId: string; readonly jobId: string } | null>(null);
  const [selectedPolicy, setSelectedPolicy] = useState<LifecyclePolicy | null>(null);
  const simulationJob = useAsyncJob(simulationIdentity?.jobId ?? '');
  const simulationReport = useLifecycleSimulation(simulationIdentity?.simulationId ?? null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const columns = useMemo<ColumnDef<LifecyclePolicy, unknown>[]>(() => [
    { id: 'name', header: '策略', cell: ({ row }) => <button className="link-button" type="button" onClick={() => setSelectedPolicy(row.original)}>{row.original.name}</button> },
    { id: 'target', header: '对象角色', cell: ({ row }) => row.original.objectRole },
    { id: 'timeline', header: '时间线', cell: ({ row }) => row.original.actions.map((action) => `${action.afterDays} 天 ${action.type}`).join(' → ') },
    { id: 'status', header: '状态', cell: ({ row }) => <StatusBadge status={row.original.status} tone={row.original.status === 'UNKNOWN' ? 'warning' : row.original.status === 'ACTIVE' ? 'success' : 'neutral'} /> },
    { id: 'version', header: '版本', cell: ({ row }) => row.original.version },
  ], []);

  const startSimulation = () => {
    const data = lifecycle.data;
    if (!data || simulation.isPending) return;
    simulation.mutate({
      idempotencyKey: crypto.randomUUID(),
      body: {
        mode: 'SAVED_POLICIES',
        selection: selectedPolicy ? { kind: 'POLICY_ID', policy_id: selectedPolicy.id } : { kind: 'ALL_ENABLED', policy_ids: [] },
        policy_set_version: data.policySetVersion,
        policy_versions: Object.entries(data.policyVersions).map(([policy_id, version]) => ({ policy_id, version })),
        snapshot_id: data.snapshotId,
        input_hash: selectedPolicy?.simulationInputHash ?? data.simulationInputHash,
      },
    }, {
      onSuccess(accepted) {
        setSimulationIdentity({ simulationId: accepted.id, jobId: accepted.jobId });
      },
    });
  };

  const completedSimulation = simulationReport.data;
  const evidence: SimulationEvidence | null = lifecycle.data && selectedPolicy && completedSimulation && simulationJob.data?.status === 'SUCCEEDED'
    ? {
      status: completedSimulation.status,
      freshness: 'CURRENT',
      inputHash: completedSimulation.inputHash,
      snapshotId: completedSimulation.snapshotId,
      policySetVersion: completedSimulation.policySetVersion,
      policyVersions: completedSimulation.policyVersions,
      unknownObjectCount: completedSimulation.unknownObjectCount,
      blockedReasons: completedSimulation.blockedReasons,
    }
    : null;
  const canExecute = Boolean(evidence && lifecycle.data && selectedPolicy && capabilities.has('storage.lifecycle.manage')
    && simulationAuthorizesDangerousAction(evidence, {
      inputHash: selectedPolicy.simulationInputHash,
      snapshotId: lifecycle.data.snapshotId,
      policySetVersion: lifecycle.data.policySetVersion,
      policyVersions: lifecycle.data.policyVersions,
    }));

  if (capabilities.loading || lifecycle.isPending) {
    return <section className="management-page"><PageHeader title="生命周期" description="策略、模拟、执行与恢复" /><SkeletonBlock width="100%" height="28rem" label="生命周期页面加载中" /></section>;
  }
  if (lifecycle.error && isDomainError(lifecycle.error)) {
    return <section className="management-page"><PageHeader title="生命周期" /><ErrorPanel error={lifecycle.error} onRetry={() => void lifecycle.refetch()} /></section>;
  }
  const data = lifecycle.data;
  if (!data) return <section className="management-page"><PageHeader title="生命周期" /><EmptyState kind="no-data" /></section>;

  return (
    <section className="management-page">
      <PageHeader
        title="生命周期"
        description="危险动作以当前 Simulation 证据、ETag 与幂等意图为边界。"
        breadcrumbs={[{ label: '存储管理', href: storageOverviewPendingLink.build() }, { label: '生命周期' }]}
        actions={<button type="button" disabled={!capabilities.has('storage.lifecycle.simulate') || simulation.isPending} onClick={startSimulation}>{simulation.isPending ? '提交模拟…' : '运行 Simulation'}</button>}
      />
      <div className="safety-banner" role="note">执行或恢复前必须确认稳定资源 ID、受影响对象数、字节数、不可逆部分与 blocked reasons；页面不会直接调用 OSS 删除或 Abort。</div>
      <nav className="local-tabs" aria-label="生命周期区域">
        {lifecycleTabs.map((tab) => <button key={tab} type="button" aria-current={search.tab === tab ? 'page' : undefined} onClick={() => setParams(storageLifecycleQueryCodec.build({ ...search, tab }, search))}>{tab}</button>)}
      </nav>
      <div className="metric-grid">
        <MetricCard label="标准存储" value={formatBytes(data.impact.standardBytes)} detail={`快照 ${data.snapshotAt}`} />
        <MetricCard label="转 IA" value={formatBytes(data.impact.toIaBytes)} />
        <MetricCard label="转归档" value={formatBytes(data.impact.toArchiveBytes)} />
        <MetricCard label="可回收" value={formatBytes(data.impact.reclaimableBytes)} />
      </div>
      {search.tab === 'policies' ? (
        <div className="workspace-grid">
          <StandardTable data={data.policies} columns={columns} getRowId={(policy) => policy.id} caption="生命周期策略" empty={<EmptyState kind={search.q ? 'filtered-empty' : 'no-data'} />} />
          <aside className="detail-panel" aria-label="Simulation 影响">
            <h2>Simulation 影响</h2>
            <p>策略：{selectedPolicy?.id ?? '尚未选择'}</p>
            <p>任务：{simulationIdentity?.jobId ?? '尚未运行'}</p>
            <p>状态：{simulationJob.data?.status ?? (simulation.isError ? 'FAILED' : 'IDLE')}</p>
            <p>受影响对象：{completedSimulation?.objectCount ?? '—'}</p>
            <p>受影响字节：{formatBytes(completedSimulation?.physicalBytes ?? data.impact.reclaimableBytes)}</p>
            <p>不可逆：{selectedPolicy?.actions.some((action) => action.type === 'DELETE_OBJECT' || action.type === 'ABORT_MULTIPART') ? '包含不可逆动作' : '无'}</p>
            {selectedPolicy?.blockedReasons.map((reason) => <p key={reason.code} role={reason.blocking ? 'alert' : 'note'}>{reason.code}：{reason.message}</p>)}
            <button type="button" disabled={!canExecute} onClick={() => setConfirmOpen(true)}>启用并进入执行窗口</button>
          </aside>
        </div>
      ) : <section className="detail-panel"><h2>{search.tab}</h2><p>该列表按稳定游标加载；未知状态保持只读，活动 Job 由任务中心持续跟踪。</p></section>}
      <ConfirmDialog
        open={confirmOpen}
        title="确认启用生命周期策略"
        resourceId={selectedPolicy?.id ?? 'unknown'}
        impact={`Simulation ${simulationIdentity?.simulationId ?? 'missing'}；影响 ${formatBytes(data.impact.reclaimableBytes)}；${selectedPolicy?.blockedReasons.map((reason) => reason.message).join('；') || '无阻断原因'}`}
        confirmLabel="确认启用"
        pending={enablePolicy.isPending}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => {
          if (!selectedPolicy || !completedSimulation || !canExecute) return;
          enablePolicy.mutate({
            policyId: selectedPolicy.id,
            etag: selectedPolicy.etag,
            idempotencyKey: crypto.randomUUID(),
            body: {
              policy_version: selectedPolicy.version,
              simulation_id: completedSimulation.id,
              input_hash: completedSimulation.inputHash,
              snapshot_id: completedSimulation.snapshotId,
              policy_set_version: completedSimulation.policySetVersion,
              policy_versions: Object.entries(completedSimulation.policyVersions).map(([policy_id, version]) => ({ policy_id, version })),
              confirmation: {
                impact_digest: completedSimulation.impactDigest,
                acknowledged_risks: selectedPolicy.actions.flatMap((action) => action.type === 'DELETE_OBJECT' ? ['IRREVERSIBLE_DELETE'] : action.type === 'ABORT_MULTIPART' ? ['IRREVERSIBLE_ABORT'] : action.type === 'TRANSITION' ? ['TRANSITION_COST'] : []),
              },
            },
          }, { onSettled: () => setConfirmOpen(false) });
        }}
      />
    </section>
  );
}

export default Component;
