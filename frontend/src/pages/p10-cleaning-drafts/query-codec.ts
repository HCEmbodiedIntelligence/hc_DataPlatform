import {
  routes,
  type CleaningDraftScope,
  type CleaningDraftSort,
  type CleaningDraftStatusFilter,
  type CleaningDraftsRouteParams,
} from '../../features/cleaning/routing';

const SCOPES = ['mine', 'review', 'returned', 'submitted', 'all', 'actionable'] as const;
const STATUSES = ['active', 'submitted', 'failed', 'archived'] as const;
const SORTS = [
  'updatedAtDesc', 'updatedAtAsc', 'createdAtDesc', 'reuseRatioDesc', 'effectiveDurationDesc',
] as const;
const PREVIEW = ['NONE', 'QUEUED', 'RUNNING', 'READY', 'FAILED', 'EXPIRED', 'STALE'] as const;
const COMMIT = ['NONE', 'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED'] as const;
const REVIEW = ['REVIEWING', 'READY', 'RETURNED'] as const;
const ID = /^[A-Za-z][A-Za-z0-9_-]{1,127}$/;

export interface CleaningDraftsSearch {
  readonly scope: CleaningDraftScope;
  readonly status: CleaningDraftStatusFilter;
  readonly q?: string;
  readonly datasetId?: string;
  readonly baseVersionId?: string;
  readonly episodeId?: string;
  readonly robotId?: string;
  readonly creatorId?: string;
  readonly updatedFrom?: string;
  readonly updatedTo?: string;
  readonly previewStatus?: readonly string[];
  readonly commitStatus?: readonly string[];
  readonly versionReviewStatus?: readonly ('REVIEWING' | 'READY' | 'RETURNED')[];
  readonly findingType?: readonly string[];
  readonly findingSeverity?: readonly string[];
  readonly sort: CleaningDraftSort;
  readonly after?: string;
  readonly before?: string;
  readonly limit: 20 | 50 | 100;
  readonly draftId?: string;
}

function oneOf<T extends string>(value: string | null, catalog: readonly T[]): T | undefined {
  return value !== null && (catalog as readonly string[]).includes(value) ? value as T : undefined;
}

function normalizedText(value: string | null, max: number): string | undefined {
  const normalized = value?.trim().normalize('NFC');
  return normalized && normalized.length <= max && !/\p{Cc}/u.test(normalized)
    ? normalized
    : undefined;
}

function normalizedId(value: string | null): string | undefined {
  const normalized = normalizedText(value, 128);
  return normalized && ID.test(normalized) ? normalized : undefined;
}

function list<T extends string>(params: URLSearchParams, key: string, catalog: readonly T[]): T[] | undefined {
  const rank = new Map(catalog.map((value, index) => [value, index]));
  const values = [...new Set(params.getAll(key).filter((value): value is T => rank.has(value as T)))]
    .sort((a, b) => rank.get(a)! - rank.get(b)!);
  return values.length ? values : undefined;
}

function cursor(value: string | null): string | undefined {
  const normalized = normalizedText(value, 2048);
  return normalized && !normalized.includes('/') && !normalized.includes('\\') ? normalized : undefined;
}

function dateRange(params: URLSearchParams): readonly [string, string] | undefined {
  const from = normalizedText(params.get('updatedFrom'), 64);
  const to = normalizedText(params.get('updatedTo'), 64);
  if (!from || !to) return undefined;
  const fromMs = Date.parse(from);
  const toMs = Date.parse(to);
  return Number.isFinite(fromMs) && Number.isFinite(toMs) && fromMs < toMs
    ? [new Date(fromMs).toISOString(), new Date(toMs).toISOString()]
    : undefined;
}

export const cleaningDraftsQueryCodec = {
  parse(input: string | URLSearchParams): CleaningDraftsSearch {
    const params = input instanceof URLSearchParams
      ? new URLSearchParams(input)
      : new URLSearchParams(input.startsWith('?') ? input.slice(1) : input);
    let after = cursor(params.get('after'));
    let before = cursor(params.get('before'));
    if (after && before) [after, before] = [undefined, undefined];
    const limitText = oneOf(params.get('limit'), ['20', '50', '100'] as const);
    const updated = dateRange(params);
    return {
      scope: oneOf(params.get('scope'), SCOPES) ?? 'mine',
      status: oneOf(params.get('status'), STATUSES) ?? 'active',
      q: normalizedText(params.get('q'), 200),
      datasetId: normalizedId(params.get('datasetId')),
      baseVersionId: normalizedId(params.get('baseVersionId')),
      episodeId: normalizedId(params.get('episodeId')),
      robotId: normalizedId(params.get('robotId')),
      creatorId: normalizedId(params.get('creatorId')),
      updatedFrom: updated?.[0],
      updatedTo: updated?.[1],
      previewStatus: list(params, 'previewStatus', PREVIEW),
      commitStatus: list(params, 'commitStatus', COMMIT),
      versionReviewStatus: list(params, 'versionReviewStatus', REVIEW),
      findingType: list(params, 'findingType', ['POSE_DISCONTINUITY', 'TIMESTAMP_DRIFT', 'OTHER'] as const),
      findingSeverity: list(params, 'findingSeverity', ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'] as const),
      sort: oneOf(params.get('sort'), SORTS) ?? 'updatedAtDesc',
      after,
      before,
      limit: limitText ? Number.parseInt(limitText, 10) as 20 | 50 | 100 : 50,
      draftId: normalizedId(params.get('draftId')),
    };
  },
  build(input: CleaningDraftsRouteParams): string {
    return routes.cleaningDrafts.build(input);
  },
  canonicalize(input: string | URLSearchParams): string {
    return routes.cleaningDrafts.build(this.parse(input));
  },
  withChanges(current: CleaningDraftsSearch, changes: Partial<CleaningDraftsRouteParams>): CleaningDraftsSearch {
    const dimensions = [
      'scope', 'status', 'q', 'datasetId', 'baseVersionId', 'episodeId', 'robotId',
      'creatorId', 'updatedFrom', 'updatedTo', 'previewStatus', 'commitStatus',
      'versionReviewStatus', 'findingType', 'findingSeverity', 'sort', 'limit',
    ] as const;
    const clearCursors = dimensions.some((key) =>
      key in changes && JSON.stringify(current[key]) !== JSON.stringify(changes[key]),
    );
    const href = routes.cleaningDrafts.build({
      ...current,
      ...changes,
      ...(clearCursors ? { after: undefined, before: undefined } : {}),
    });
    return this.parse(href.split('?')[1] ?? '');
  },
} as const;

export const cleaningDraftProjections = {
  pendingReview: {
    route: () => routes.cleaningDrafts.build({ projection: 'pendingReview' }),
    emptyMessage: '暂无待复核草稿。提交完成后会在这里等待版本复核。',
  },
  returned: {
    route: () => routes.cleaningDrafts.build({ projection: 'returned' }),
    emptyMessage: '暂无已退回草稿。Review 退回后会显示不可变 Finding 摘要与后继草稿。',
  },
} as const;
