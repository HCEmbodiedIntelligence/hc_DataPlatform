import { describe, expect, it } from 'vitest';
import { episodePageFixture } from '../../../mocks/fixtures/datasets/core';
import { adaptEpisodePage } from './adapters';
import { episodePageEnvelopeWireSchema } from './wire-schemas';

describe('adaptEpisodePage', () => {
  it('preserves a missing review finding count as unknown', () => {
    const wire = episodePageEnvelopeWireSchema.parse({
      ...episodePageFixture,
      items: episodePageFixture.items.map((item) => ({
        ...item,
        review_status: null,
        review_finding_count: null,
      })),
    });

    const [episode] = adaptEpisodePage(wire).items;

    expect(episode).toBeDefined();
    if (!episode) throw new Error('Expected one Episode fixture');
    expect(episode.reviewStatus).toBe('UNKNOWN');
    expect(episode.reviewFindingCount).toBeNull();
  });
});
