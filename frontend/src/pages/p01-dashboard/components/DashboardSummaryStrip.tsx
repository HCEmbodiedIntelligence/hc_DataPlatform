import { Link } from 'react-router-dom';
import type { DashboardActivity, DashboardSnapshot } from '../../../features/dashboard/types';
import { storageOverviewRoute } from '../../../features/storage-overview/routing';
import { UiMetricCard, type MetricState } from '../../../shared/ui';
import styles from '../styles.module.css';

function metric(value: bigint | null | undefined, suffix = ''): string | undefined {
  if (value === undefined) return undefined;
  if (value === null) return '未知';
  return `${value.toLocaleString('zh-CN')}${suffix}`;
}

function bytesMetric(value: bigint | null | undefined): string | undefined {
  if (value === undefined) return undefined;
  if (value === null) return '未知';
  const units = ['bytes', 'KB', 'MB', 'GB', 'TB', 'PB'] as const;
  let amount = Number(value);
  let unitIndex = 0;
  while (amount >= 1_024 && unitIndex < units.length - 1) {
    amount /= 1_024;
    unitIndex += 1;
  }
  const digits = amount >= 100 || unitIndex === 0 ? 0 : amount >= 10 ? 1 : 2;
  return `${amount.toLocaleString('zh-CN', { maximumFractionDigits: digits })} ${units[unitIndex]}`;
}

export function DashboardSummaryStrip({
  activity,
  snapshot,
  activityState,
  snapshotState,
}: Readonly<{
  activity?: DashboardActivity;
  snapshot?: DashboardSnapshot;
  activityState: MetricState;
  snapshotState: MetricState;
}>) {
  const rawBytes = snapshot?.roles.find((role) => role.role === 'RAW')?.bytes;
  const success = activity?.successRatio === null
    ? '未知'
    : activity
      ? `${(activity.successRatio * 100).toLocaleString('zh-CN', { maximumFractionDigits: 1 })}%`
      : undefined;

  return (
    <section className={styles.summaryGrid} aria-label="关键指标">
      <UiMetricCard
        label="期间上传量"
        value={bytesMetric(activity?.acceptedUniqueBytes)}
        state={activityState}
        basis="服务端去重接受字节"
        asOf={activity ? <time dateTime={activity.asOf}>{activity.asOf}</time> : undefined}
      />
      <UiMetricCard
        label="上传成功率"
        value={success}
        state={activityState}
        basis="成功终态 / 全部终态"
      />
      <UiMetricCard
        label="Raw 物理容量"
        value={bytesMetric(rawBytes)}
        state={snapshotState}
        basis="服务端快照"
        description={rawBytes !== undefined ? (
          <Link to={storageOverviewRoute.build({ tab: 'objects', objectRole: 'SOURCE' })}>
            查看对象
          </Link>
        ) : undefined}
      />
      <UiMetricCard
        label="可查看 Episode"
        value={metric(snapshot?.episodes.viewableCount)}
        state={snapshotState}
      />
      <UiMetricCard
        label="开放人工问题"
        value={metric(snapshot?.work.openManualIssueCount)}
        state={snapshotState}
      />
      <UiMetricCard
        label="可行动清洗草稿"
        value={metric(snapshot?.work.activeCleaningDraftCount)}
        state={snapshotState}
      />
    </section>
  );
}
