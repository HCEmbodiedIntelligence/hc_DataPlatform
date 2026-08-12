import type { StorageMetric } from '../../../entities/storage-inventory';
import type { StorageOverview } from '../../../features/storage-overview/types';
import {
  displayByteMetric,
  displayDecimalMetric,
  displayMoneyMetric,
} from '../../../features/storage-overview/metrics-contract';
import { UiMetricCard, type MetricState } from '../../../shared/ui';
import styles from '../styles.module.css';

function stateOf(metric: StorageMetric<unknown>): MetricState {
  if (metric.state === 'KNOWN') return 'ready';
  if (metric.state === 'FORBIDDEN') return 'forbidden';
  if (metric.state === 'FAILED') return 'error';
  return 'unknown';
}

export function StorageSummaryStrip({
  overview,
  state,
}: Readonly<{
  overview?: StorageOverview;
  state: MetricState;
}>) {
  const asOf = overview ? <time dateTime={overview.asOf}>{overview.asOf}</time> : undefined;
  const cards = overview
    ? [
        {
          label: '实际 OSS 容量',
          value: displayByteMetric(overview.totals.actualOssPhysicalBytes),
          state: stateOf(overview.totals.actualOssPhysicalBytes),
          basis: '服务端不可变 Inventory 快照',
        },
        {
          label: '逻辑引用容量',
          value: displayByteMetric(overview.totals.logicalReferencedBytes),
          state: stateOf(overview.totals.logicalReferencedBytes),
          basis: '服务端授权聚合',
        },
        {
          label: '计费容量',
          value: displayByteMetric(overview.totals.billedBytes),
          state: stateOf(overview.totals.billedBytes),
          basis: '服务端计费事实',
        },
        {
          label: '复用率',
          value: displayDecimalMetric(overview.totals.reuseRate),
          state: stateOf(overview.totals.reuseRate),
          basis: overview.formulaVersion,
        },
        {
          label: '月度费用',
          value: displayMoneyMetric(overview.totals.monthlyCost),
          state: stateOf(overview.totals.monthlyCost),
          basis: '服务端 minor-unit 事实',
        },
      ]
    : [
        { label: '实际 OSS 容量' },
        { label: '逻辑引用容量' },
        { label: '计费容量' },
        { label: '复用率' },
        { label: '月度费用' },
      ];

  return (
    <section className={styles.summaryGrid} aria-label="存储指标">
      {cards.map((card) => (
        <UiMetricCard
          key={card.label}
          label={card.label}
          value={'value' in card ? card.value : undefined}
          state={'state' in card ? card.state : state}
          basis={'basis' in card ? card.basis : undefined}
          asOf={asOf}
        />
      ))}
    </section>
  );
}
