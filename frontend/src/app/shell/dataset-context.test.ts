import { describe, expect, it } from 'vitest';
import {
  datasetContextKind,
  datasetSelectionLocation,
  selectedDatasetId,
} from './dataset-context';

describe('dataset context routing', () => {
  it('only enables the selector for dataset and cleaning overview pages', () => {
    expect(datasetContextKind('/dashboard')).toBeNull();
    expect(datasetContextKind('/datasets')).toBe('browse');
    expect(datasetContextKind('/datasets/ds-01/versions/v1')).toBe('browse');
    expect(datasetContextKind('/manual/issues')).toBe('cleaning-filter');
    expect(datasetContextKind('/manual/drafts')).toBe('cleaning-filter');
    expect(datasetContextKind('/manual/drafts/draft-01')).toBeNull();
  });

  it('reads the current dataset from either the path or cleaning filter', () => {
    expect(selectedDatasetId('/datasets/ds%20one/versions/v1', '')).toBe('ds one');
    expect(selectedDatasetId('/manual/issues', '?datasetId=ds-02')).toBe('ds-02');
  });

  it('opens a selected dataset from dataset pages', () => {
    expect(datasetSelectionLocation('/datasets/ds-01/versions/v1', '?tab=review', 'ds-02')).toEqual({
      pathname: '/datasets/ds-02',
      search: '',
    });
  });

  it('filters cleaning lists and clears resource-dependent parameters', () => {
    expect(
      datasetSelectionLocation(
        '/manual/drafts',
        '?scope=mine&datasetId=ds-01&baseVersionId=v1&draftId=d1&after=cursor',
        'ds-02',
      ),
    ).toEqual({ pathname: '/manual/drafts', search: '?scope=mine&datasetId=ds-02' });
  });

  it('clears the dataset filter without leaving the cleaning page', () => {
    expect(
      datasetSelectionLocation('/manual/issues', '?datasetId=ds-01&status=OPEN', undefined),
    ).toEqual({ pathname: '/manual/issues', search: '?status=OPEN' });
  });
});
