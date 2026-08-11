import type { DatasetId } from '../../entities/dataset';
import type { DatasetVersionId } from '../../entities/dataset-version';
import type { EpisodeId } from '../../entities/episode';

export type DatasetsSort = 'activityDesc' | 'createdDesc' | 'nameAsc';
export type DatasetAssetState =
  | 'ready'
  | 'validating'
  | 'problem'
  | 'frozen'
  | 'pending_ingest'
  | 'unknown';
export type DatasetStorageClass = 'standard' | 'ia' | 'archive';

export type DatasetsRouteFilters = Readonly<{
  q?: string;
  robotModelId?: string;
  robotId?: string;
  task?: string;
  scene?: string;
  assetState?: DatasetAssetState;
  storageClass?: DatasetStorageClass;
  channels?: readonly string[];
  channelMatch?: 'all' | 'any';
  datasetCreatedFrom?: string;
  datasetCreatedTo?: string;
  sort?: DatasetsSort;
  after?: string;
  before?: string;
  limit?: 20 | 50 | 100;
}>;

export type DatasetDetailTab =
  | 'overview'
  | 'episodes'
  | 'versions'
  | 'schema'
  | 'sources'
  | 'capacity';

export type VersionDetailTab =
  | 'revisions'
  | 'changes'
  | 'review'
  | 'manifest'
  | 'schema'
  | 'capacity'
  | 'exports';

export type DatasetDetailRouteInput = Readonly<{
  datasetId: DatasetId;
  tab?: DatasetDetailTab;
  versionId?: DatasetVersionId;
  returnTo?: string;
}>;

export type VersionDetailRouteInput = Readonly<{
  datasetId: DatasetId;
  versionId: DatasetVersionId;
  tab?: VersionDetailTab;
  returnTo?: string;
}>;

export type EpisodeViewerRouteInput = Readonly<{
  datasetId: DatasetId;
  versionId: DatasetVersionId;
  episodeId: EpisodeId;
  returnTo?: string;
}>;

function appendText(search: URLSearchParams, key: string, value: string | undefined): void {
  const normalized = value?.trim();
  if (normalized) search.set(key, normalized);
}

function isSafeAppRelativeUrl(value: string): boolean {
  if (!value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return false;
  try {
    const decoded = decodeURIComponent(value);
    return decoded.startsWith('/') && !decoded.startsWith('//') && !decoded.includes('\\');
  } catch {
    return false;
  }
}

function withSearch(path: string, search: URLSearchParams): string {
  const serialized = search.toString();
  return serialized ? `${path}?${serialized}` : path;
}

function buildDatasets(filters: DatasetsRouteFilters = {}): string {
  const search = new URLSearchParams();
  appendText(search, 'q', filters.q);
  appendText(search, 'robotModelId', filters.robotModelId);
  appendText(search, 'robotId', filters.robotId);
  appendText(search, 'task', filters.task);
  appendText(search, 'scene', filters.scene);
  if (filters.assetState) search.set('assetState', filters.assetState);
  if (filters.storageClass) search.set('storageClass', filters.storageClass);
  for (const channel of [...new Set(filters.channels ?? [])]
    .map((item) => item.trim())
    .filter(Boolean)
    .sort()) {
    search.append('channels', channel);
  }
  if (filters.channelMatch && filters.channelMatch !== 'all') {
    search.set('channelMatch', filters.channelMatch);
  }
  appendText(search, 'datasetCreatedFrom', filters.datasetCreatedFrom);
  appendText(search, 'datasetCreatedTo', filters.datasetCreatedTo);
  if (filters.sort && filters.sort !== 'activityDesc') search.set('sort', filters.sort);
  if (filters.limit && filters.limit !== 20) search.set('limit', String(filters.limit));

  // Cursor direction is canonical and mutually exclusive. A conflicting runtime input
  // is normalized to the first window rather than choosing one cursor implicitly.
  if (!(filters.after && filters.before)) {
    appendText(search, 'after', filters.after);
    appendText(search, 'before', filters.before);
  }
  return withSearch('/datasets', search);
}

function buildDatasetDetail(input: DatasetDetailRouteInput): string {
  const search = new URLSearchParams();
  if (input.tab && input.tab !== 'overview') search.set('tab', input.tab);
  if (input.versionId) search.set('versionId', input.versionId);
  if (input.returnTo && isSafeAppRelativeUrl(input.returnTo))
    search.set('returnTo', input.returnTo);
  return withSearch(`/datasets/${encodeURIComponent(input.datasetId)}`, search);
}

function buildVersionDetail(input: VersionDetailRouteInput): string {
  const search = new URLSearchParams();
  if (input.tab && input.tab !== 'revisions') search.set('tab', input.tab);
  if (input.returnTo && isSafeAppRelativeUrl(input.returnTo))
    search.set('returnTo', input.returnTo);
  return withSearch(
    `/datasets/${encodeURIComponent(input.datasetId)}/versions/${encodeURIComponent(input.versionId)}`,
    search,
  );
}

function buildEpisodeViewer(input: EpisodeViewerRouteInput): string {
  const search = new URLSearchParams();
  if (input.returnTo && isSafeAppRelativeUrl(input.returnTo)) {
    search.set('returnTo', input.returnTo);
  }
  return withSearch(
    `/datasets/${encodeURIComponent(input.datasetId)}/versions/${encodeURIComponent(input.versionId)}/episodes/${encodeURIComponent(input.episodeId)}/view`,
    search,
  );
}

export const routes = {
  datasets: { build: buildDatasets },
  datasetDetail: { build: buildDatasetDetail },
  versionDetail: { build: buildVersionDetail },
  episodeViewer: { build: buildEpisodeViewer },
} as const;

export { isSafeAppRelativeUrl };
