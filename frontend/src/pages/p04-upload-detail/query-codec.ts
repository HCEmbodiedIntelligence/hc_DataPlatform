import { defineQueryCodec, safeReturnTo } from '../../shared/routing/route-registry';

type Level = 'INFO' | 'WARNING' | 'ERROR';
export interface UploadDetailSearch extends Record<string, unknown> {
  readonly tab: 'objects' | 'parts' | 'manifest' | 'verification' | 'events';
  readonly objectId?: string;
  readonly findingSeverity: readonly Level[];
  readonly eventLevel: readonly Level[];
  readonly after?: string;
  readonly before?: string;
  readonly limit: 50;
  readonly returnTo?: string;
}

export const uploadDetailDefaults: UploadDetailSearch = { tab: 'objects', findingSeverity: [], eventLevel: [], limit: 50 };
const tabs = ['objects', 'parts', 'manifest', 'verification', 'events'];
const levels = new Set(['INFO', 'WARNING', 'ERROR']);
const base = defineQueryCodec<UploadDetailSearch>({
  defaults: uploadDetailDefaults,
  allowedKeys: ['tab', 'objectId', 'findingSeverity', 'eventLevel', 'after', 'before', 'limit', 'returnTo'],
  cursorResetKeys: ['tab', 'objectId', 'findingSeverity', 'eventLevel', 'limit'],
  fields: {
    tab: { parse: (raw) => (tabs.includes(raw) ? raw : 'objects') },
    limit: { parse: () => 50 },
    returnTo: { parse: (raw) => safeReturnTo(raw) ?? undefined },
  },
  normalize(value) {
    const list = (items: readonly Level[]) => [...new Set(items)].filter((item) => levels.has(item)).sort();
    return {
      ...value,
      objectId: value.tab === 'parts' ? value.objectId : undefined,
      findingSeverity: value.tab === 'verification' ? list(value.findingSeverity) : [],
      eventLevel: value.tab === 'events' ? list(value.eventLevel) : [],
      limit: 50,
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

export const uploadDetailQueryCodec = {
  normalize: base.normalize,
  parse(raw: RawQuery): UploadDetailSearch {
    const sp = params(raw);
    const hasAmbiguousCursor = sp.has('after') && sp.has('before');
    const scalar = new URLSearchParams(sp); scalar.delete('findingSeverity'); scalar.delete('eventLevel');
    return base.normalize({
      ...base.parse(scalar),
      findingSeverity: sp.getAll('findingSeverity').filter((value): value is Level => levels.has(value)),
      eventLevel: sp.getAll('eventLevel').filter((value): value is Level => levels.has(value)),
      ...(hasAmbiguousCursor ? { after: undefined, before: undefined } : {}),
    });
  },
  build(value: Partial<UploadDetailSearch>, previous?: Partial<UploadDetailSearch>): string {
    const normalized = base.normalize(value, previous);
    const sp = new URLSearchParams();
    if (normalized.tab !== 'objects') sp.set('tab', normalized.tab);
    if (normalized.tab === 'parts' && normalized.objectId) sp.set('objectId', normalized.objectId);
    if (normalized.tab === 'verification') for (const item of normalized.findingSeverity) sp.append('findingSeverity', item);
    if (normalized.tab === 'events') for (const item of normalized.eventLevel) sp.append('eventLevel', item);
    if (normalized.after) sp.set('after', normalized.after); else if (normalized.before) sp.set('before', normalized.before);
    if (normalized.returnTo && safeReturnTo(normalized.returnTo)) sp.set('returnTo', normalized.returnTo);
    return sp.toString();
  },
};

export function updateUploadDetailSearch(current: UploadDetailSearch, patch: Partial<UploadDetailSearch>, scopeChanged = false): UploadDetailSearch {
  if (scopeChanged) return base.normalize({ ...current, ...patch, after: undefined, before: undefined });
  return base.normalize({ ...current, ...patch }, current);
}
