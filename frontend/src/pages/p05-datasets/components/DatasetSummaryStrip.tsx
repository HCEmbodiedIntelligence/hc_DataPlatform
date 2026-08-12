import type { DatasetSummaryVm } from '../../../features/datasets/api';
import { UiMetricCard, type MetricState } from '../../../shared/ui';

export function DatasetSummaryStrip({
  summary,
  state,
}: Readonly<{ summary?: DatasetSummaryVm; state: MetricState }>) {
  return (
    <>
      <UiMetricCard label="数据集" value={summary?.datasetCount} state={state} description="服务端授权聚合" />
      <UiMetricCard label="Episodes" value={summary?.episodeCount} state={state} description="服务端授权聚合" />
      <UiMetricCard label="待复核版本" value={summary?.pendingReviewVersionCount} state={state} description="服务端授权聚合" />
      <UiMetricCard label="已退回版本" value={summary?.returnedVersionCount} state={state} description="服务端授权聚合" />
      <UiMetricCard label="可处理草稿" value={summary?.actionableDraftCount} state={state} description="服务端授权聚合" />
    </>
  );
}
