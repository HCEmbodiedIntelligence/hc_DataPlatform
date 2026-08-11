import type { DatasetSummaryVm } from '../../../features/datasets/api';

export function DatasetSummaryStrip({ summary }: Readonly<{ summary?: DatasetSummaryVm }>) {
  const metrics = [
    ['数据集', summary?.datasetCount ?? '—'],
    ['Episodes', summary?.episodeCount ?? '—'],
    ['待复核版本', summary?.pendingReviewVersionCount ?? '—'],
    ['已退回版本', summary?.returnedVersionCount ?? '—'],
    ['可处理草稿', summary?.actionableDraftCount ?? '—'],
  ] as const;
  return (
    <dl className="dataset-metrics" aria-label="授权筛选摘要">
      {metrics.map(([label, value]) => (
        <div className="dataset-metric" key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
          <small>服务端授权聚合</small>
        </div>
      ))}
    </dl>
  );
}
