import { defineQueryCodec, safeReturnTo } from '../../shared/routing/route-registry';

export interface UploadJobsSearch extends Record<string, unknown> {
  readonly tab: 'all' | 'uploading' | 'verifying' | 'available' | 'failed';
  readonly q?: string;
  readonly dataSourceId?: string;
  readonly datasetId?: string;
  readonly lifecycleStatus: readonly string[];
  readonly verificationStatus: readonly string[];
  readonly sort: 'createdAt:desc' | 'updatedAt:desc';
  readonly after?: string;
  readonly before?: string;
  readonly limit: 10 | 20 | 50;
  readonly intent?: 'create';
  readonly targetDataSourceId?: string;
  readonly targetDatasetId?: string;
  readonly returnTo?: string;
}

export const uploadJobsDefaults: UploadJobsSearch = {
  tab: 'all', lifecycleStatus: [], verificationStatus: [], sort: 'createdAt:desc', limit: 20,
};
const allowedKeys = [
  'tab', 'q', 'dataSourceId', 'datasetId', 'lifecycleStatus', 'verificationStatus', 'sort', 'after', 'before', 'limit',
  'intent', 'targetDataSourceId', 'targetDatasetId', 'returnTo',
] as const;
const lifecycleStates = new Set(['CREATED', 'AUTHORIZING', 'UPLOADING', 'PAUSED', 'FINALIZING', 'PENDING_VERIFY', 'VERIFYING', 'AVAILABLE', 'FAILED', 'QUARANTINED', 'CANCELLING', 'CANCELLED', 'EXPIRED']);
const verificationStates = new Set(['NOT_STARTED', 'QUEUED', 'RUNNING', 'PASSED', 'FAILED', 'CANCELLED']);

const base = defineQueryCodec<UploadJobsSearch>({
  defaults: uploadJobsDefaults,
  allowedKeys,
  cursorResetKeys: ['tab', 'q', 'dataSourceId', 'datasetId', 'lifecycleStatus', 'verificationStatus', 'sort', 'limit'],
  fields: {
    tab: { parse: (raw) => (['all', 'uploading', 'verifying', 'available', 'failed'].includes(raw) ? raw : 'all') },
    sort: { parse: (raw) => (['createdAt:desc', 'updatedAt:desc'].includes(raw) ? raw : 'createdAt:desc') },
    limit: { parse: (raw) => ([10, 20, 50].includes(Number(raw)) ? Number(raw) : 20) },
    intent: { parse: (raw) => (raw === 'create' ? 'create' : undefined) },
    returnTo: { parse: (raw) => safeReturnTo(raw) ?? undefined },
  },
  normalize(value) {
    const list = (items: readonly string[], allowed: ReadonlySet<string>) => [...new Set(items)].filter((item) => allowed.has(item)).sort();
    const intent = value.intent === 'create' ? 'create' : undefined;
    const lifecycleStatus = list(value.lifecycleStatus, lifecycleStates);
    const verificationStatus = list(value.verificationStatus, verificationStates);
    return {
      ...value,
      tab: lifecycleStatus.length || verificationStatus.length ? 'all' : value.tab,
      q: value.q?.trim().slice(0, 200) || undefined,
      lifecycleStatus,
      verificationStatus,
      intent,
      targetDataSourceId: intent ? value.targetDataSourceId : undefined,
      targetDatasetId: intent ? value.targetDatasetId : undefined,
      returnTo: intent ? value.returnTo : undefined,
      ...(value.after && value.before ? { after: undefined, before: undefined } : {}),
    };
  },
});

type RawQuery = string | URLSearchParams | Readonly<Record<string, string | readonly string[]>>;
function params(raw: RawQuery): URLSearchParams {
  if (raw instanceof URLSearchParams) return new URLSearchParams(raw);
  if (typeof raw === 'string') return new URLSearchParams(raw.startsWith('?') ? raw.slice(1) : raw);
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(raw)) for (const item of typeof value === 'string' ? [value] : value) sp.append(key, item);
  return sp;
}

export const uploadJobsQueryCodec = {
  normalize: base.normalize,
  parse(raw: RawQuery): UploadJobsSearch {
    const sp = params(raw);
    const hasAmbiguousCursor = sp.has('after') && sp.has('before');
    const scalar = new URLSearchParams(sp);
    scalar.delete('lifecycleStatus'); scalar.delete('verificationStatus');
    return base.normalize({
      ...base.parse(scalar),
      lifecycleStatus: sp.getAll('lifecycleStatus'),
      verificationStatus: sp.getAll('verificationStatus'),
      ...(hasAmbiguousCursor ? { after: undefined, before: undefined } : {}),
    });
  },
  build(value: Partial<UploadJobsSearch>, previous?: Partial<UploadJobsSearch>): string {
    const normalized = base.normalize(value, previous);
    const sp = new URLSearchParams();
    if (normalized.tab !== 'all') sp.set('tab', normalized.tab);
    if (normalized.q) sp.set('q', normalized.q);
    if (normalized.dataSourceId) sp.set('dataSourceId', normalized.dataSourceId);
    if (normalized.datasetId) sp.set('datasetId', normalized.datasetId);
    for (const key of ['lifecycleStatus', 'verificationStatus'] as const) for (const item of normalized[key]) sp.append(key, item);
    if (normalized.sort !== 'createdAt:desc') sp.set('sort', normalized.sort);
    if (normalized.after) sp.set('after', normalized.after); else if (normalized.before) sp.set('before', normalized.before);
    if (normalized.limit !== 20) sp.set('limit', String(normalized.limit));
    if (normalized.intent === 'create') {
      sp.set('intent', 'create');
      if (normalized.targetDataSourceId) sp.set('targetDataSourceId', normalized.targetDataSourceId);
      if (normalized.targetDatasetId) sp.set('targetDatasetId', normalized.targetDatasetId);
      if (normalized.returnTo && safeReturnTo(normalized.returnTo)) sp.set('returnTo', normalized.returnTo);
    }
    return sp.toString();
  },
};

export function updateUploadJobsSearch(current: UploadJobsSearch, patch: Partial<UploadJobsSearch>, scopeChanged = false): UploadJobsSearch {
  if (scopeChanged) return base.normalize({ ...current, ...patch, after: undefined, before: undefined });
  return base.normalize({ ...current, ...patch }, current);
}
