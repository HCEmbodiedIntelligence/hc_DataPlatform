import { defineQueryCodec, safeReturnTo } from '../../shared/routing/route-registry';

export interface DataSourcesSearch extends Record<string, unknown> {
  readonly q?: string;
  readonly sourceType: readonly string[];
  readonly administrativeState: readonly string[];
  readonly connectivity: readonly string[];
  readonly credentialState: readonly string[];
  readonly sort: 'updatedAt:desc' | 'name:asc' | 'lastTestAt:desc';
  readonly after?: string;
  readonly before?: string;
  readonly limit: 10 | 20 | 50;
  readonly sourceId?: string;
  readonly intent?: 'create';
  readonly returnTo?: string;
}

export const dataSourcesDefaults: DataSourcesSearch = {
  sourceType: [],
  administrativeState: [],
  connectivity: [],
  credentialState: [],
  sort: 'updatedAt:desc',
  limit: 20,
};

const allowedKeys = [
  'q', 'sourceType', 'administrativeState', 'connectivity', 'credentialState', 'sort', 'after', 'before', 'limit',
  'sourceId', 'intent', 'returnTo',
] as const;
const sourceTypes = new Set(['ROBOT', 'EDGE_AGENT', 'OSS_IMPORT']);
const administrativeStates = new Set(['ENABLED', 'DISABLED']);
const connectivityStates = new Set(['UNKNOWN', 'ONLINE', 'DEGRADED', 'OFFLINE', 'AUTH_FAILED', 'CONFIG_ERROR']);
const credentialStates = new Set(['NOT_REQUIRED', 'MISSING', 'CONFIGURED', 'ROTATION_DUE', 'EXPIRED', 'REVOKED', 'INVALID']);

const base = defineQueryCodec<DataSourcesSearch>({
  defaults: dataSourcesDefaults,
  allowedKeys,
  cursorResetKeys: ['q', 'sourceType', 'administrativeState', 'connectivity', 'credentialState', 'sort', 'limit'],
  fields: {
    limit: { parse: (raw) => ([10, 20, 50].includes(Number(raw)) ? Number(raw) : 20) },
    sort: { parse: (raw) => (['updatedAt:desc', 'name:asc', 'lastTestAt:desc'].includes(raw) ? raw : 'updatedAt:desc') },
    intent: { parse: (raw) => (raw === 'create' ? 'create' : undefined) },
    returnTo: { parse: (raw) => safeReturnTo(raw) ?? undefined },
  },
  normalize(value) {
    const normalizeList = (items: readonly string[], allowed: ReadonlySet<string>) => [...new Set(items)].filter((item) => allowed.has(item)).sort();
    return {
      ...value,
      q: value.q?.trim().slice(0, 200) || undefined,
      sourceType: normalizeList(value.sourceType, sourceTypes),
      administrativeState: normalizeList(value.administrativeState, administrativeStates),
      connectivity: normalizeList(value.connectivity, connectivityStates),
      credentialState: normalizeList(value.credentialState, credentialStates),
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

export const dataSourcesQueryCodec = {
  normalize: base.normalize,
  parse(raw: RawQuery): DataSourcesSearch {
    const sp = params(raw);
    const hasAmbiguousCursor = sp.has('after') && sp.has('before');
    const scalar = new URLSearchParams(sp);
    for (const key of ['sourceType', 'administrativeState', 'connectivity', 'credentialState']) scalar.delete(key);
    return base.normalize({
      ...base.parse(scalar),
      sourceType: sp.getAll('sourceType'),
      administrativeState: sp.getAll('administrativeState'),
      connectivity: sp.getAll('connectivity'),
      credentialState: sp.getAll('credentialState'),
      ...(hasAmbiguousCursor ? { after: undefined, before: undefined } : {}),
    });
  },
  build(value: Partial<DataSourcesSearch>, previous?: Partial<DataSourcesSearch>): string {
    const normalized = base.normalize(value, previous);
    const sp = new URLSearchParams();
    if (normalized.q) sp.set('q', normalized.q);
    for (const key of ['sourceType', 'administrativeState', 'connectivity', 'credentialState'] as const) {
      for (const item of normalized[key]) sp.append(key, item);
    }
    if (normalized.sort !== 'updatedAt:desc') sp.set('sort', normalized.sort);
    if (normalized.after) sp.set('after', normalized.after);
    else if (normalized.before) sp.set('before', normalized.before);
    if (normalized.limit !== 20) sp.set('limit', String(normalized.limit));
    if (normalized.sourceId) sp.set('sourceId', normalized.sourceId);
    if (normalized.intent === 'create') sp.set('intent', 'create');
    if (normalized.returnTo && safeReturnTo(normalized.returnTo)) sp.set('returnTo', normalized.returnTo);
    return sp.toString();
  },
};

export function updateDataSourcesSearch(current: DataSourcesSearch, patch: Partial<DataSourcesSearch>, scopeChanged = false): DataSourcesSearch {
  if (scopeChanged) return base.normalize({ ...current, ...patch, after: undefined, before: undefined });
  return base.normalize({ ...current, ...patch }, current);
}
