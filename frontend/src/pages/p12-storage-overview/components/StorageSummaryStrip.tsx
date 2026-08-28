import { Archive, Database, FileText, Layers3 } from 'lucide-react';
import type { ReactNode } from 'react';
import type { StorageMetric } from '../../../entities/storage-inventory';
import type { StorageOverview } from '../../../features/storage-overview/types';
import { displayByteMetric } from '../../../features/storage-overview/metrics-contract';
import type { MetricState, UiMetricCardProps } from '../../../shared/ui';
import { freshnessLabel } from '../display-labels';
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
  const rawCapacity = overview?.roles.find((item) => item.role === 'SOURCE')?.physicalBytes;
  const lanceCapacity = overview?.roles.find((item) => item.role === 'DERIVED')?.physicalBytes;
  const cards = overview
    ? [
        {
          eyebrow: '项目总览',
          label: '当前项目总储量',
          value: displayByteMetric(overview.totals.actualOssPhysicalBytes),
          state: stateOf(overview.totals.actualOssPhysicalBytes),
          detail: '该项目全部物理对象实际占用',
          icon: <Database size={29} strokeWidth={1.65} />,
        },
        {
          eyebrow: '原始存档',
          label: '原始 MCAP 容量',
          value: rawCapacity ? displayByteMetric(rawCapacity) : undefined,
          state: rawCapacity ? stateOf(rawCapacity) : 'unknown' as const,
          detail: 'OSS 中不可变的机器人采集文件',
          icon: <Archive size={29} strokeWidth={1.65} />,
        },
        {
          eyebrow: '对齐加工',
          label: 'Lance 加工数据容量',
          value: lanceCapacity ? displayByteMetric(lanceCapacity) : undefined,
          state: lanceCapacity ? stateOf(lanceCapacity) : 'unknown' as const,
          detail: '质检通过并完成多模态对齐的数据',
          icon: <Layers3 size={29} strokeWidth={1.65} />,
        },
        {
          eyebrow: '盘点状态',
          label: '数据新鲜度',
          value: freshnessLabel(overview.freshness),
          state: 'ready' as const,
          detail: '对象盘点更新时间',
          icon: <FileText size={29} strokeWidth={1.65} />,
          tone: 'success' as const,
        },
      ]
    : [
        {
          eyebrow: '项目总览',
          label: '当前项目总储量',
          detail: '该项目全部物理对象实际占用',
          icon: <Database size={29} strokeWidth={1.65} />,
        },
        {
          eyebrow: '原始存档',
          label: '原始 MCAP 容量',
          detail: 'OSS 中不可变的机器人采集文件',
          icon: <Archive size={29} strokeWidth={1.65} />,
        },
        {
          eyebrow: '对齐加工',
          label: 'Lance 加工数据容量',
          detail: '质检通过并完成多模态对齐的数据',
          icon: <Layers3 size={29} strokeWidth={1.65} />,
        },
        {
          eyebrow: '盘点状态',
          label: '数据新鲜度',
          detail: '对象盘点更新时间',
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
