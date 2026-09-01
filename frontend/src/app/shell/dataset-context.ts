export type DatasetContextKind = 'browse' | 'cleaning-filter';

export interface DatasetSelectionLocation {
  pathname: string;
  search: string;
}

const cleaningFilterPaths = new Set(['/manual/issues', '/annotations/revisions']);

export function datasetContextKind(pathname: string): DatasetContextKind | null {
  if (pathname === '/datasets' || pathname.startsWith('/datasets/')) return 'browse';
  return cleaningFilterPaths.has(pathname) ? 'cleaning-filter' : null;
}

export function selectedDatasetId(pathname: string, search: string): string | undefined {
  const context = datasetContextKind(pathname);
  if (context === 'cleaning-filter') {
    return new URLSearchParams(search).get('datasetId')?.trim() || undefined;
  }
  if (context !== 'browse') return undefined;
  const segment = pathname.split('/')[2];
  if (!segment) return undefined;
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

export function datasetSelectionLocation(
  pathname: string,
  search: string,
  datasetId?: string,
): DatasetSelectionLocation {
  const context = datasetContextKind(pathname);
  const normalizedDatasetId = datasetId?.trim();

  if (context === 'browse') {
    return {
      pathname: normalizedDatasetId
        ? `/datasets/${encodeURIComponent(normalizedDatasetId)}`
        : '/datasets',
      search: '',
    };
  }

  if (context === 'cleaning-filter') {
    const params = new URLSearchParams(search);
    if (normalizedDatasetId) params.set('datasetId', normalizedDatasetId);
    else params.delete('datasetId');

    for (const dependentKey of [
      'versionId',
      'baseVersionId',
      'episodeId',
      'issueId',
      'draftId',
      'after',
      'before',
    ]) {
      params.delete(dependentKey);
    }

    const nextSearch = params.toString();
    return { pathname, search: nextSearch ? `?${nextSearch}` : '' };
  }

  return { pathname, search };
}
