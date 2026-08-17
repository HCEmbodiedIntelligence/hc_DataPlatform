import { Archive, Box, Database, FileText, Layers3, WalletCards } from 'lucide-react';
import type { ReactNode } from 'react';
import type { StorageMetric } from '../../../entities/storage-inventory';
import type { StorageOverview } from '../../../features/storage-overview/types';
import {
  displayByteMetric,
  displayDecimalMetric,
  displayMoneyMetric,
} from '../../../features/storage-overview/metrics-contract';
import type { MetricState, UiMetricCardProps } from '../../../shared/ui';
import styles from '../styles.module.css';

function stateOf(metric: StorageMetric<unknown>): MetricState {
  if (metric.state === 'KNOWN') return 'ready';
  if (metric.state === 'FORBIDDEN') return 'forbidden';
  if (metric.state === 'FAILED') return 'error';
  return 'unknown';
}

function metricValue(value: string | undefined, state: MetricState): string {
  if (state === 'loading') return '加载中';
  if (state === 'forbidden') return '无权查看';
  if (state === 'error') return '暂不可用';
  if (state === 'unknown') return '未知';
  return value ?? '—';
}

function StorageMetricTile({
  eyebrow,
  label,
  value,
  detail,
  icon,
  tone = 'default',
}: Readonly<{
  eyebrow: string;
  label: string;
  value: NonNullable<UiMetricCardProps['value']>;
  detail: string;
  icon: ReactNode;
  tone?: 'default' | 'success' | 'warning';
}>) {
  const toneClass =
    tone === 'success'
      ? styles.storageMetricSuccess
      : tone === 'warning'
        ? styles.storageMetricWarning
        : '';
  return (
    <section className={`${styles.storageMetric} ${toneClass}`} aria-label={label}>
      <span className={styles.storageMetricIcon} aria-hidden="true">
        {icon}
      </span>
      <div className={styles.storageMetricCopy}>
        <span className={styles.storageMetricEyebrow}>{eyebrow}</span>
        <h2>{label}</h2>
        <strong>{value}</strong>
        <span className={styles.storageMetricDetail}>{detail}</span>
      </div>
    </section>
  );
}

export function StorageSummaryStrip({
  overview,
  state,
}: Readonly<{
  overview?: StorageOverview;
  state: MetricState;
}>) {
  const cards = overview
    ? [
        {
          eyebrow: 'PHYSICAL',
          label: '实际 OSS 容量',
          value: displayByteMetric(overview.totals.actualOssPhysicalBytes),
          state: stateOf(overview.totals.actualOssPhysicalBytes),
          detail: '服务端 Inventory 快照',
          icon: <Database size={29} strokeWidth={1.65} />,
        },
        {
          eyebrow: 'LOGICAL',
          label: '逻辑引用容量',
          value: displayByteMetric(overview.totals.logicalReferencedBytes),
          state: stateOf(overview.totals.logicalReferencedBytes),
          detail: '服务端授权聚合',
          icon: <Layers3 size={29} strokeWidth={1.65} />,
        },
        {
          eyebrow: 'BILLING',
          label: '计费容量',
          value: displayByteMetric(overview.totals.billedBytes),
          state: stateOf(overview.totals.billedBytes),
          detail: '服务端计费事实',
          icon: <Archive size={29} strokeWidth={1.65} />,
        },
        {
          eyebrow: 'REUSE',
          label: '复用率',
          value: displayDecimalMetric(overview.totals.reuseRate),
          state: stateOf(overview.totals.reuseRate),
          detail: overview.formulaVersion,
          icon: <Box size={29} strokeWidth={1.65} />,
          tone: 'success' as const,
        },
        {
          eyebrow: 'COST',
          label: '月度费用',
          value: displayMoneyMetric(overview.totals.monthlyCost),
          state: stateOf(overview.totals.monthlyCost),
          detail: '服务端 minor-unit 事实',
          icon: <WalletCards size={29} strokeWidth={1.65} />,
        },
        {
          eyebrow: 'FRESHNESS',
          label: '数据新鲜度',
          value: overview.freshness,
          state: 'ready' as const,
          detail: '服务端快照状态',
          icon: <FileText size={29} strokeWidth={1.65} />,
          tone: 'success' as const,
        },
      ]
    : [
        {
          eyebrow: 'PHYSICAL',
          label: '实际 OSS 容量',
          detail: '服务端 Inventory 快照',
          icon: <Database size={29} strokeWidth={1.65} />,
        },
        {
          eyebrow: 'LOGICAL',
          label: '逻辑引用容量',
          detail: '服务端授权聚合',
          icon: <Layers3 size={29} strokeWidth={1.65} />,
        },
        {
          eyebrow: 'BILLING',
          label: '计费容量',
          detail: '服务端计费事实',
          icon: <Archive size={29} strokeWidth={1.65} />,
        },
        { eyebrow: 'REUSE', label: '复用率', detail: '版本化口径', icon: <Box size={29} strokeWidth={1.65} /> },
        {
          eyebrow: 'COST',
          label: '月度费用',
          detail: '服务端 minor-unit 事实',
          icon: <WalletCards size={29} strokeWidth={1.65} />,
        },
        {
          eyebrow: 'FRESHNESS',
          label: '数据新鲜度',
          detail: '服务端快照状态',
          icon: <FileText size={29} strokeWidth={1.65} />,
        },
      ];

  return (
    <section className={styles.summaryGrid} aria-label="存储指标">
      {cards.map((card) => (
        <StorageMetricTile
          key={card.label}
          eyebrow={card.eyebrow}
          label={card.label}
          value={metricValue(
            'value' in card ? card.value : undefined,
            'state' in card ? card.state : state,
          )}
          detail={card.detail}
          icon={card.icon}
          tone={'tone' in card ? card.tone : undefined}
        />
      ))}
    </section>
  );
}
