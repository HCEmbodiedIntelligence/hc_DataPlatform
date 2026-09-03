import { describe, expect, it } from 'vitest';
import { episodeInclusionLabel, episodeReviewPresentation, episodeSuccessPresentation } from './episode-presentation';

describe('Episode presentation', () => {
  it('does not present an unknown review result as zero findings', () => {
    expect(episodeReviewPresentation('UNKNOWN', null)).toEqual({
      label: '复核结果未知',
      tone: 'warning',
      known: false,
    });
  });

  it('only adds a finding count when the server supplied one', () => {
    expect(episodeReviewPresentation('HAS_FINDING', '2').label).toBe('发现问题 · 2 项');
    expect(episodeReviewPresentation('HAS_FINDING', null).label).toBe('发现问题 · 数量未知');
  });

  it('uses readable labels for processing and inclusion states', () => {
    expect(episodeSuccessPresentation('SUCCEEDED').label).toBe('处理成功');
    expect(episodeSuccessPresentation('FAILED').label).toBe('处理失败');
    expect(episodeInclusionLabel(true)).toBe('已纳入当前版本');
  });
});
