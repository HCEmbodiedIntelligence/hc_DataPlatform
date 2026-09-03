import type { EpisodeListItemVm } from '../../features/datasets/api';
import type { StatusTone } from '../../shared/ui';

export interface EpisodeStatusPresentation {
  readonly label: string;
  readonly tone: StatusTone;
  readonly known: boolean;
}

export function episodeSuccessPresentation(state: EpisodeListItemVm['successState']): EpisodeStatusPresentation {
  if (state === 'SUCCEEDED') return { label: '处理成功', tone: 'success', known: true };
  if (state === 'FAILED') return { label: '处理失败', tone: 'danger', known: true };
  return { label: '处理结果未知', tone: 'warning', known: false };
}

export function episodeReviewPresentation(
  status: EpisodeListItemVm['reviewStatus'],
  findingCount: EpisodeListItemVm['reviewFindingCount'],
): EpisodeStatusPresentation {
  if (status === 'UNREVIEWED') return { label: '尚未复核', tone: 'neutral', known: true };
  if (status === 'ACCEPTED') return { label: '复核通过', tone: 'success', known: true };
  if (status === 'HAS_FINDING') {
    return {
      label: findingCount === null ? '发现问题 · 数量未知' : `发现问题 · ${findingCount} 项`,
      tone: 'warning',
      known: true,
    };
  }
  return { label: '复核结果未知', tone: 'warning', known: false };
}

export function episodeInclusionLabel(included: boolean): string {
  return included ? '已纳入当前版本' : '未纳入当前版本';
}
