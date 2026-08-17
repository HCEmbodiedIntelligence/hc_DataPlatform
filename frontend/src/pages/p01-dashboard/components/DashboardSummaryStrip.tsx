import {
  ArrowDownToLine,
  ArrowUpRight,
  CircleGauge,
  Clapperboard,
  Database,
  FilePenLine,
  ShieldAlert,
} from 'lucide-react';
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

function formatDateTime(value: string, timeZone: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
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
        eyebrow="INGEST"
        icon={<ArrowDownToLine size={19} strokeWidth={1.8} />}
        label="本期接收数据"
        value={bytesMetric(activity?.acceptedUniqueBytes)}
        state={activityState}
        tone="primary"
        description="去重后进入原始数据域"
        asOf={activity ? <time dateTime={activity.asOf}>{formatDateTime(activity.asOf, activity.timezone)}</time> : undefined}
      />
      <UiMetricCard
        eyebrow="QUALITY"
        icon={<CircleGauge size={19} strokeWidth={1.8} />}
        label="上传成功率"
        value={success}
        state={activityState}
        tone="info"
        description="按当前统计周期计算"
      />
      <UiMetricCard
        eyebrow="RAW"
        icon={<Database size={19} strokeWidth={1.8} />}
        label="原始数据容量"
        value={bytesMetric(rawBytes)}
        state={snapshotState}
        tone="primary"
        description="接收后保留的不可变原始对象"
        action={rawBytes !== undefined ? (
          <Link to={storageOverviewRoute.build({ tab: 'objects', objectRole: 'SOURCE' })}>
            查看原始对象
            <ArrowUpRight aria-hidden="true" size={14} />
          </Link>
        ) : undefined}
      />
      <UiMetricCard
        eyebrow="VIEWABLE"
        icon={<Clapperboard size={19} strokeWidth={1.8} />}
        label="可查看 Episode"
        value={metric(snapshot?.episodes.viewableCount)}
        state={snapshotState}
        tone="info"
        description="已校验且当前授权范围内可查看"
      />
      <UiMetricCard
        eyebrow="ISSUES"
        icon={<ShieldAlert size={19} strokeWidth={1.8} />}
        label="待处理质量问题"
        value={metric(snapshot?.work.openManualIssueCount)}
        state={snapshotState}
        tone="warning"
        description="仍需人工复核或处理"
      />
      <UiMetricCard
        eyebrow="CLEANING"
        icon={<FilePenLine size={19} strokeWidth={1.8} />}
        label="可处理清洗草稿"
        value={metric(snapshot?.work.activeCleaningDraftCount)}
        state={snapshotState}
        tone="neutral"
        description="排除区间，不改写 Lance 基线"
      />
    </section>
  );
}
