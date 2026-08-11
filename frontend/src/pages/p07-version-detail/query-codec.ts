import type { DatasetVersionId } from '../../entities/dataset-version';
import type { EpisodeRevisionId } from '../../entities/episode';
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
import type { VersionDetailTab } from '../../features/datasets/routing';

const TABS = [
  'revisions',
  'changes',
  'review',
  'manifest',
  'schema',
  'capacity',
  'exports',
] as const;
const VERSION_ID = /^version_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/;
const REVISION_ID = /^revision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/;
const DATASET_DETAIL_PATH = /^\/datasets\/dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/;

function safeDatasetDetailReturnTo(value: string | null | undefined): string | undefined {
  const safe = safeInternalReturnTo(value, ['/datasets']);
  if (!safe) return undefined;
  const parsed = new URL(safe, 'https://local.invalid');
  return DATASET_DETAIL_PATH.test(parsed.pathname)
    ? `${parsed.pathname}${parsed.search}`
    : undefined;
}

export type VersionDetailSearch = Readonly<{
  tab: VersionDetailTab;
  q?: string;
  included?: boolean;
  reviewStatus?: readonly string[];
  hasFinding?: boolean;
  robotId?: string;
  changeType?: readonly string[];
  compareTo?: DatasetVersionId;
  revisionId?: EpisodeRevisionId;
  sort?: string;
  after?: string;
  before?: string;
  limit: 20 | 50 | 100;
  returnTo?: string;
}>;

function list(params: URLSearchParams, key: string): readonly string[] | undefined {
  const values = [
    ...new Set(
      params
        .getAll(key)
        .map((item) => cleanText(item, 96))
        .filter((item): item is string => Boolean(item)),
    ),
  ].sort();
  return values.length ? values : undefined;
}

function parseVersionDetail(input: string | URLSearchParams): VersionDetailSearch {
  const params = asSearchParams(input);
  const rawTab = params.get('tab');
  const parsedTab = enumValue(rawTab, TABS);
  const tab = parsedTab ?? 'revisions';
  const cursor = canonicalCursorPair(params);
  const limitRaw = enumValue(params.get('limit'), ['20', '50', '100'] as const);
  const common = {
    tab,
    ...cursor,
    limit: limitRaw ? (Number(limitRaw) as 20 | 50 | 100) : 20,
    returnTo: safeDatasetDetailReturnTo(params.get('returnTo')),
  };
  if (rawTab !== null && parsedTab === undefined) {
    return {
      tab: 'revisions',
      limit: 20,
      returnTo: common.returnTo,
    };
  }
  if (tab === 'revisions') {
    return {
      ...common,
      q: cleanText(params.get('q'), 200),
      included:
        params.get('included') === 'true'
          ? true
          : params.get('included') === 'false'
            ? false
            : undefined,
      reviewStatus: list(params, 'reviewStatus'),
      hasFinding:
        params.get('hasFinding') === 'true'
          ? true
          : params.get('hasFinding') === 'false'
            ? false
            : undefined,
      robotId: cleanText(params.get('robotId'), 128),
      changeType: list(params, 'changeType'),
      revisionId: cleanId(params.get('revisionId'), REVISION_ID) as EpisodeRevisionId | undefined,
      sort: cleanText(params.get('sort'), 96),
    };
  }
  if (tab === 'changes') {
    const compare = cleanId(params.get('compareTo'), VERSION_ID);
    return { ...common, compareTo: compare as DatasetVersionId | undefined };
  }
  return common;
}

function buildVersionDetail(input: Partial<VersionDetailSearch>): URLSearchParams {
  const params = new URLSearchParams();
  const tab = input.tab ?? 'revisions';
  if (tab !== 'revisions') params.set('tab', tab);
  if (tab === 'revisions') {
    for (const key of ['q', 'robotId', 'revisionId', 'sort'] as const) {
      const value = input[key];
      if (typeof value === 'string' && value) params.set(key, value);
    }
    if (input.included !== undefined) params.set('included', String(input.included));
    if (input.hasFinding !== undefined) params.set('hasFinding', String(input.hasFinding));
    input.reviewStatus?.forEach((value) => params.append('reviewStatus', value));
    input.changeType?.forEach((value) => params.append('changeType', value));
  }
  if (tab === 'changes' && input.compareTo) params.set('compareTo', input.compareTo);
  if (input.after && !input.before) params.set('after', input.after);
  if (input.before && !input.after) params.set('before', input.before);
  if (input.limit && input.limit !== 20) params.set('limit', String(input.limit));
  const returnTo = safeDatasetDetailReturnTo(input.returnTo);
  if (returnTo) params.set('returnTo', returnTo);
  return params;
}

const DIMENSIONS: readonly (keyof VersionDetailSearch)[] = [
  'tab',
  'q',
  'included',
  'reviewStatus',
  'hasFinding',
  'robotId',
  'changeType',
  'compareTo',
  'sort',
  'limit',
];

export const versionDetailQueryCodec = defineQueryCodec<
  VersionDetailSearch,
  Partial<VersionDetailSearch>
>({
  parse: parseVersionDetail,
  build: buildVersionDetail,
  canonicalize(input) {
    return buildVersionDetail(parseVersionDetail(input)).toString();
  },
  withChanges(current, changes, scopeChanged = false) {
    const clear = scopeChanged || valuesChanged(current, changes, DIMENSIONS);
    return parseVersionDetail(
      buildVersionDetail({
        ...current,
        ...changes,
        ...(clear ? { after: undefined, before: undefined } : {}),
      }),
    );
  },
});

export default versionDetailQueryCodec;
