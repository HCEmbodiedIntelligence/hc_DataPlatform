import { Alert, Button, Card, Descriptions, Space, Tabs } from 'antd';
import type { ColumnDef } from '@tanstack/react-table';
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { LifecyclePolicy } from '../../entities/lifecycle-policy';
import { useCreateLifecycleSimulation, useEnableLifecyclePolicy, useLifecyclePage, useLifecycleSimulation } from '../../features/lifecycle/api';
import { storageOverviewPendingLink } from '../../features/lifecycle/pending-links';
import { simulationAuthorizesDangerousAction, type SimulationEvidence } from '../../features/lifecycle/state-machines';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { useAsyncJob } from '../../shared/jobs/use-async-job';
import {
  ConfirmDialog,
  DataTable,
  PageState,
  StandardPageScaffold,
  StatusTag,
  UiMetricCard,
  type PageStateKind,
} from '../../shared/ui';
import { lifecycleTabs, storageLifecycleQueryCodec } from './query-codec';
import styles from './styles.module.css';

const tabLabels = {
  policies: '策略',
  executions: '执行记录',
  restores: '恢复任务',
  multipart: 'Multipart 诊断',
} as const;

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

  const selectPolicy = (policy: LifecyclePolicy) => {
    setSelectedPolicy(policy);
    setSimulationIdentity(null);
    setParams(storageLifecycleQueryCodec.build({ ...search, policyId: policy.id, simulationId: undefined }, search));
  };

  const columns: ColumnDef<LifecyclePolicy, unknown>[] = [
    { id: 'name', header: '策略', cell: ({ row }) => <Button type="link" size="small" onClick={() => selectPolicy(row.original)}>{row.original.name}</Button> },
    { id: 'target', header: '对象角色', cell: ({ row }) => row.original.objectRole },
    { id: 'timeline', header: '时间线', cell: ({ row }) => row.original.actions.map((action) => `${action.afterDays} 天 ${action.type}`).join(' → ') },
    { id: 'status', header: '状态', cell: ({ row }) => <StatusTag status={row.original.status} tone={row.original.status === 'ACTIVE' ? 'success' : row.original.status === 'UNKNOWN' ? 'warning' : 'neutral'} known={row.original.status !== 'UNKNOWN'} /> },
    { id: 'version', header: '版本', cell: ({ row }) => row.original.version },
  ];

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
        setParams(storageLifecycleQueryCodec.build({ ...search, simulationId: accepted.id }, search));
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

  const loading = capabilities.loading || lifecycle.isPending;
  const pageState: PageStateKind | 'ready' = loading
    ? 'loading'
    : lifecycle.error
      ? stateFromError(lifecycle.error)
      : lifecycle.data ? 'ready' : 'empty';
  const data = lifecycle.data;

  const simulationPanel = data ? (
    <Card size="small" title="策略影响概览" className={styles.simulationPanel}>
      <div className={styles.impactMetrics} aria-label="策略影响概览">
        <UiMetricCard label="当前 Standard" value={formatBytes(data.impact.standardBytes)} asOf={data.snapshotAt} />
        <UiMetricCard label="将转为 IA" value={formatBytes(data.impact.toIaBytes)} />
        <UiMetricCard label="将转为 Archive" value={formatBytes(data.impact.toArchiveBytes)} />
        <UiMetricCard label="可回收空间" value={formatBytes(data.impact.reclaimableBytes)} />
      </div>
      <Descriptions column={1} size="small">
        <Descriptions.Item label="策略">{selectedPolicy?.id ?? '尚未选择'}</Descriptions.Item>
        <Descriptions.Item label="任务">{simulationIdentity?.jobId ?? '尚未运行'}</Descriptions.Item>
        <Descriptions.Item label="状态">{simulationJob.data?.status ?? (simulation.isError ? 'FAILED' : 'IDLE')}</Descriptions.Item>
        <Descriptions.Item label="受影响对象">{completedSimulation?.objectCount ?? '—'}</Descriptions.Item>
        <Descriptions.Item label="受影响字节">{formatBytes(completedSimulation?.physicalBytes ?? data.impact.reclaimableBytes)}</Descriptions.Item>
        <Descriptions.Item label="不可逆">{selectedPolicy?.actions.some((action) => action.type === 'DELETE_OBJECT' || action.type === 'ABORT_MULTIPART') ? '包含不可逆动作' : '无'}</Descriptions.Item>
      </Descriptions>
      {simulation.error ? <Alert type="error" showIcon title="Simulation 提交失败" description={isDomainError(simulation.error) ? simulation.error.message : '服务端未接受本次模拟意图。'} /> : null}
      {selectedPolicy?.blockedReasons.map((reason) => <Alert key={reason.code} type={reason.blocking ? 'error' : 'warning'} showIcon title={reason.code} description={reason.message} />)}
      <Button danger type="primary" disabled={!canExecute} onClick={() => setConfirmOpen(true)}>启用并进入执行窗口</Button>
    </Card>
  ) : null;

  const content = pageState === 'ready' && data ? (
    search.tab === 'policies' ? (
      <div className={styles.workspace}>
        <DataTable data={data.policies} columns={columns} getRowId={(policy) => policy.id} caption="生命周期策略" />
        {simulationPanel}
      </div>
    ) : (
      <PageState
        state="feature-unavailable"
        label={tabLabels[search.tab]}
        title={`${tabLabels[search.tab]}只读投影尚未开放`}
        description="现有前端合同尚未提供该区域的列表 DTO；未知状态保持只读，活动 Job 继续由任务中心跟踪。"
      />
    )
  ) : (
    <PageState
      state={pageState === 'ready' ? 'empty' : pageState}
      label="生命周期页面"
      requestId={requestId(lifecycle.error)}
      onRetry={lifecycle.error ? () => void lifecycle.refetch() : undefined}
    />
  );

  return (
    <main className={styles.page} data-page-id="P13">
      <StandardPageScaffold
        header={{
          title: '生命周期策略',
          description: '危险动作以当前 Simulation 证据、ETag 与幂等意图为边界。',
          breadcrumbs: [{ key: 'storage', label: <a href={storageOverviewPendingLink.build()}>存储管理</a> }, { key: 'lifecycle', label: '生命周期' }],
          actions: <Button type="primary" disabled={!capabilities.has('storage.lifecycle.simulate') || simulation.isPending || !data} loading={simulation.isPending} onClick={startSimulation}>运行 Simulation</Button>,
        }}
        summary={data ? <Alert className={styles.safetyBanner} type="warning" showIcon title="Ready 版本引用的 Source 对象禁止直接删除" description="执行或恢复前必须确认稳定资源 ID、受影响对象数、字节数、不可逆部分与 blocked reasons；页面不会直接调用 OSS 删除或 Abort。" /> : undefined}
        filters={data ? <Tabs className={styles.lifecycleTabs} activeKey={search.tab} onChange={(value) => setParams(storageLifecycleQueryCodec.build({ ...search, tab: value as typeof search.tab }, search))} items={lifecycleTabs.map((tab) => ({ key: tab, label: tabLabels[tab] }))} aria-label="生命周期区域" /> : undefined}
      >
        <Space orientation="vertical" size="middle" className={styles.content}>
          {content}
          {data ? <section className={styles.guidanceGrid} aria-label="生命周期执行说明">
            <div><strong>执行说明</strong><ul><li>策略按固定窗口执行。</li><li>删除与清理为最终操作，无法恢复。</li><li>策略修改后等待下一次执行窗口生效。</li></ul></div>
            <div><strong>恢复时效</strong><p>IA：标准恢复约 1–3 天；加急恢复约 3–6 小时。</p><p>Archive：标准恢复约 3–5 天；加急恢复约 5–12 小时。</p></div>
            <div><strong>重要提示</strong><p>Source、Preview、Export 与 Incomplete Multipart 继续受现有安全边界保护。</p></div>
          </section> : null}
        </Space>
      </StandardPageScaffold>
      {data ? <ConfirmDialog
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
      /> : null}
      {enablePolicy.error ? <Alert className={styles.operationAlert} type="error" showIcon title="策略启用未完成" description={isDomainError(enablePolicy.error) ? enablePolicy.error.message : '服务端事实没有被乐观推进。'} /> : null}
    </main>
  );
}

export default Component;
