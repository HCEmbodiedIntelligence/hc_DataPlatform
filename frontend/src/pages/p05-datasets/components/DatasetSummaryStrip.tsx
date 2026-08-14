import { Archive, Database, FileStack, ListChecks, NotebookTabs } from 'lucide-react';
import type { ReactNode } from 'react';
import type { DatasetSummaryVm } from '../../../features/datasets/api';
import type { MetricState } from '../../../shared/ui';
import styles from '../styles.module.css';

function summaryValue(value: string | undefined, state: MetricState): string {
  if (state === 'loading') return '加载中';
  if (state === 'forbidden') return '无权查看';
  if (state === 'error') return '暂不可用';
  if (state === 'unknown') return '未知';
  return value ?? '—';
}

function DatasetMetric({
  label,
  value,
  detail,
  icon,
  tone = 'default',
}: Readonly<{
  label: string;
  value: string;
  detail: string;
  icon: ReactNode;
  tone?: 'default' | 'success' | 'warning';
}>) {
  const toneClass =
    tone === 'success'
      ? styles.datasetMetricSuccess
      : tone === 'warning'
        ? styles.datasetMetricWarning
        : '';
  return (
    <section className={`${styles.datasetMetric} ${toneClass}`} aria-label={label}>
      <span className={styles.datasetMetricIcon} aria-hidden="true">
        {icon}
      </span>
      <div className={styles.datasetMetricCopy}>
        <h2>{label}</h2>
        <strong>{value}</strong>
        <span>{detail}</span>
      </div>
    </section>
  );
}

export function DatasetSummaryStrip({
  summary,
  state,
}: Readonly<{ summary?: DatasetSummaryVm; state: MetricState }>) {
  return (
    <>
      <DatasetMetric
        label="数据集"
        value={summaryValue(summary?.datasetCount, state)}
        detail="服务端授权聚合"
        icon={<Database size={28} strokeWidth={1.65} />}
      />
      <DatasetMetric
        label="Episodes"
        value={summaryValue(summary?.episodeCount, state)}
        detail="当前可读窗口"
        icon={<FileStack size={28} strokeWidth={1.65} />}
      />
      <DatasetMetric
        label="待复核版本"
        value={summaryValue(summary?.pendingReviewVersionCount, state)}
        detail="等待人工确认"
        icon={<ListChecks size={28} strokeWidth={1.65} />}
        tone="warning"
      />
      <DatasetMetric
        label="已退回版本"
        value={summaryValue(summary?.returnedVersionCount, state)}
        detail="需要重新处理"
        icon={<Archive size={28} strokeWidth={1.65} />}
        tone="warning"
      />
      <DatasetMetric
        label="可处理草稿"
        value={summaryValue(summary?.actionableDraftCount, state)}
        detail="已授权的下一步"
        icon={<NotebookTabs size={28} strokeWidth={1.65} />}
        tone="success"
      />
    </>
  );
}
