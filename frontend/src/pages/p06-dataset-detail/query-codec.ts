import type { DatasetVersionId } from '../../entities/dataset-version';
import type { EpisodeId, EpisodeStreamId } from '../../entities/episode';
import {
  asSearchParams,
  canonicalCursorPair,
  cleanId,
  cleanText,
  defineQueryCodec,
  enumValue,
  safeInternalReturnTo,
  valuesChanged,
} from '../../features/datasets/query-codec';
import type { DatasetDetailTab } from '../../features/datasets/routing';

const VERSION_ID = /^version_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/;
const EPISODE_ID = /^episode_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/;
const STREAM_ID = /^stream_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/;
const DATASET_DETAIL_PATH = /^\/datasets\/dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/;
const VERSION_DETAIL_PATH =
  /^\/datasets\/dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}\/versions\/version_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/;
const TABS = ['overview', 'episodes', 'versions', 'schema', 'sources', 'capacity'] as const;
const LIMITS = ['10', '20', '50'] as const;

export type DatasetDetailSearch = Readonly<{
  tab: DatasetDetailTab;
  versionId?: DatasetVersionId;
  episodeId?: EpisodeId;
  sourceId?: string;
  q?: string;
  task?: string;
  robotId?: string;
  successState?: 'succeeded' | 'failed' | 'unknown';
  startedFrom?: string;
  startedTo?: string;
  versionKind?: 'raw' | 'cleaned';
  versionStatus?: 'reviewing' | 'returned' | 'ready';
  sort?: string;
  after?: string;
  before?: string;
  limit: 10 | 20 | 50;
  returnTo?: string;
}>;

export type DatasetDetailChanges = Partial<DatasetDetailSearch>;

function parseDetail(input: string | URLSearchParams): DatasetDetailSearch {
  const params = asSearchParams(input);
  const tab = enumValue(params.get('tab'), TABS) ?? 'overview';
  const versionIdRaw = cleanId(params.get('versionId'), VERSION_ID);
  const versionId =
    versionIdRaw !== 'version_current' && versionIdRaw !== 'version_latest'
      ? (versionIdRaw as DatasetVersionId | undefined)
      : undefined;
  const cursor = canonicalCursorPair(params);
  const startedFrom = cleanText(params.get('startedFrom'), 40);
  const startedTo = cleanText(params.get('startedTo'), 40);
  const timeRange = startedFrom && startedTo ? { startedFrom, startedTo } : {};
  const limit = enumValue(params.get('limit'), LIMITS);
  const common = {
    tab,
    versionId,
    ...cursor,
    limit: limit ? (Number(limit) as 10 | 20 | 50) : 20,
    returnTo: safeInternalReturnTo(params.get('returnTo'), ['/datasets']),
  };
  if (tab === 'episodes') {
    return {
      ...common,
      episodeId: cleanId(params.get('episodeId'), EPISODE_ID) as EpisodeId | undefined,
      q: cleanText(params.get('q'), 200),
      task: cleanText(params.get('task'), 128),
      robotId: cleanText(params.get('robotId'), 128),
      successState: enumValue(params.get('successState'), [
        'succeeded',
        'failed',
        'unknown',
      ] as const),
      ...timeRange,
      sort:
        enumValue(params.get('sort'), ['ordinal-asc', 'started-desc', 'started-asc'] as const) ??
        'ordinal-asc',
    };
  }
  if (tab === 'versions') {
    return {
      ...common,
      q: cleanText(params.get('q'), 200),
      versionKind: enumValue(params.get('versionKind'), ['raw', 'cleaned'] as const),
      versionStatus: enumValue(params.get('versionStatus'), [
        'reviewing',
        'returned',
        'ready',
      ] as const),
      sort:
        enumValue(params.get('sort'), [
          'created-desc',
          'created-asc',
          'version-desc',
          'version-asc',
        ] as const) ?? 'created-desc',
    };
  }
  if (tab === 'sources') {
    return {
      ...common,
      q: cleanText(params.get('q'), 200),
      sourceId: cleanText(params.get('sourceId'), 128),
      sort:
        enumValue(params.get('sort'), [
          'registered-desc',
          'registered-asc',
          'source-name-asc',
        ] as const) ?? 'registered-desc',
    };
  }
  return common;
}

function buildDetail(input: DatasetDetailChanges): URLSearchParams {
  const params = new URLSearchParams();
  const tab = input.tab ?? 'overview';
  if (tab !== 'overview') params.set('tab', tab);
  if (input.versionId) params.set('versionId', input.versionId);
  const allowedByTab: Readonly<Record<DatasetDetailTab, readonly (keyof DatasetDetailSearch)[]>> = {
    overview: [],
    episodes: [
      'episodeId',
      'q',
      'task',
      'robotId',
      'successState',
      'startedFrom',
      'startedTo',
      'sort',
    ],
    versions: ['q', 'versionKind', 'versionStatus', 'sort'],
    schema: [],
    sources: ['q', 'sourceId', 'sort'],
    capacity: [],
  };
  for (const key of allowedByTab[tab]) {
    const value = input[key];
    if (typeof value === 'string' && value) params.set(key, value);
  }
  if (input.after && !input.before) params.set('after', input.after);
  if (input.before && !input.after) params.set('before', input.before);
  if (input.limit && input.limit !== 20) params.set('limit', String(input.limit));
  const returnTo = safeInternalReturnTo(input.returnTo, ['/datasets']);
  if (returnTo) params.set('returnTo', returnTo);
  return new URLSearchParams(parseDetail(params).tab === tab ? params : '');
}

const DIMENSIONS: readonly (keyof DatasetDetailSearch)[] = [
  'tab',
  'versionId',
  'q',
  'task',
  'robotId',
  'successState',
  'startedFrom',
  'startedTo',
  'versionKind',
  'versionStatus',
  'sourceId',
  'sort',
  'limit',
];

export const datasetDetailQueryCodec = defineQueryCodec<DatasetDetailSearch, DatasetDetailChanges>({
  parse: parseDetail,
  build: buildDetail,
  canonicalize(input) {
    return buildDetail(parseDetail(input)).toString();
  },
  withChanges(current, changes, scopeChanged = false) {
    const clear = scopeChanged || valuesChanged(current, changes, DIMENSIONS);
    return parseDetail(
      buildDetail({
        ...current,
        ...changes,
        ...(clear ? { after: undefined, before: undefined } : {}),
      }),
    );
  },
});

export type AssetEpisodeViewerSearch = Readonly<{
  t?: string;
  layout?: string;
  streamId?: EpisodeStreamId;
  issueId?: string;
  selectionStartNs?: string;
  selectionEndNs?: string;
  returnTo?: string;
}>;

function safeViewerReturnTo(value: string | null | undefined): string | undefined {
  const safe = safeInternalReturnTo(value, ['/datasets', '/manual/issues']);
  if (!safe) return undefined;
  const parsed = new URL(safe, 'https://local.invalid');
  const p06Episodes =
    DATASET_DETAIL_PATH.test(parsed.pathname) && parsed.searchParams.get('tab') === 'episodes';
  const p07Version = VERSION_DETAIL_PATH.test(parsed.pathname);
  const p09Issues = parsed.pathname === '/manual/issues';
  return p06Episodes || p07Version || p09Issues ? `${parsed.pathname}${parsed.search}` : undefined;
}

function parseViewer(input: string | URLSearchParams): AssetEpisodeViewerSearch {
  const params = asSearchParams(input);
  const start = cleanText(params.get('selectionStartNs'), 32);
  const end = cleanText(params.get('selectionEndNs'), 32);
  const hasRange = Boolean(
    start && end && /^\d+$/.test(start) && /^\d+$/.test(end) && BigInt(start) < BigInt(end),
  );
  return {
    t: (() => {
      const value = cleanText(params.get('t'), 32);
      return value && /^(0|[1-9][0-9]*)(\.[0-9]{1,9})?$/.test(value) ? value : undefined;
    })(),
    layout: cleanText(params.get('layout'), 64),
    streamId: cleanId(params.get('streamId'), STREAM_ID) as EpisodeStreamId | undefined,
    issueId: cleanText(params.get('issueId'), 128),
    ...(hasRange ? { selectionStartNs: start, selectionEndNs: end } : {}),
    returnTo: safeViewerReturnTo(params.get('returnTo')),
  };
}

function buildViewer(input: AssetEpisodeViewerSearch): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of ['t', 'layout', 'streamId', 'issueId'] as const) {
    const value = input[key];
    if (value) params.set(key, value);
  }
  if (
    input.selectionStartNs &&
    input.selectionEndNs &&
    /^\d+$/.test(input.selectionStartNs) &&
    /^\d+$/.test(input.selectionEndNs) &&
    BigInt(input.selectionStartNs) < BigInt(input.selectionEndNs)
  ) {
    params.set('selectionStartNs', input.selectionStartNs);
    params.set('selectionEndNs', input.selectionEndNs);
  }
  const returnTo = safeViewerReturnTo(input.returnTo);
  if (returnTo) params.set('returnTo', returnTo);
  return params;
}

export const assetEpisodeViewerQueryCodec = defineQueryCodec<
  AssetEpisodeViewerSearch,
  AssetEpisodeViewerSearch
>({
  parse: parseViewer,
  build: buildViewer,
  canonicalize(input) {
    return buildViewer(parseViewer(input)).toString();
  },
  withChanges(current, changes) {
    return parseViewer(buildViewer({ ...current, ...changes }));
  },
});

export default datasetDetailQueryCodec;
