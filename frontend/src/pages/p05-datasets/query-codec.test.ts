// @vitest-environment jsdom

import { describe, expect, it } from 'vitest';
import { routes } from '../../features/datasets/routing';
import { activeDatasetFilterCount } from './components/DatasetFilterPanel';
import datasetsQueryCodec, { type DatasetsSearch } from './query-codec';

const defaults: DatasetsSearch = {
  sort: 'activityDesc',
  limit: 20,
};

describe('P05 datasets query codec', () => {
  it.each(['pendingReview', 'returned', 'actionableDraft'] as const)(
    'parses and builds workflowState=%s',
    (workflowState) => {
      const parsed = datasetsQueryCodec.parse(`workflowState=${workflowState}`);

      expect(parsed.workflowState).toBe(workflowState);
      expect(datasetsQueryCodec.build(parsed).get('workflowState')).toBe(workflowState);
      expect(routes.datasets.build(parsed)).toContain(`workflowState=${workflowState}`);
    },
  );

  it('drops invalid workflow and deprecated Channel filters from canonical URLs', () => {
    const input =
      'q=assembly&workflowState=invalid&channels=%2Fcamera%2Ffront&channelMatch=any&sort=nameAsc';
    const parsed = datasetsQueryCodec.parse(input);
    const canonical = datasetsQueryCodec.canonicalize(input);

    expect(parsed.workflowState).toBeUndefined();
    expect(parsed).not.toHaveProperty('channels');
    expect(parsed).not.toHaveProperty('channelMatch');
    expect(canonical).toBe('q=assembly&sort=nameAsc');
  });

  it.each([
    ['q', { q: 'new name' }],
    ['task', { task: 'pick' }],
    ['workflowState', { workflowState: 'returned' as const }],
    ['date range', { datasetCreatedFrom: '2026-08-01' }],
  ])('clears both cursors when %s changes', (_label, changes) => {
    const next = datasetsQueryCodec.withChanges(
      { ...defaults, after: 'next-cursor' },
      changes,
    );

    expect(next.after).toBeUndefined();
    expect(next.before).toBeUndefined();
  });

  it('normalizes conflicting cursors to the first page', () => {
    const parsed = datasetsQueryCodec.parse('after=next&before=previous');

    expect(parsed.after).toBeUndefined();
    expect(parsed.before).toBeUndefined();
  });

  it('counts only applied filter dimensions and treats a date range as one', () => {
    expect(
      activeDatasetFilterCount({
        ...defaults,
        collectionTaskId: 'collection-1',
        q: 'assembly',
        task: 'pick',
        workflowState: 'pendingReview',
        datasetCreatedFrom: '2026-08-01',
        datasetCreatedTo: '2026-08-24',
        sort: 'nameAsc',
        limit: 100,
        after: 'cursor',
      }),
    ).toBe(4);
  });

  it('trims task text and treats an empty task as unfiltered', () => {
    expect(datasetsQueryCodec.parse('task=%20pick%20').task).toBe('pick');
    expect(datasetsQueryCodec.parse('task=%20%20').task).toBeUndefined();
  });
});
