import { defineQueryCodec, safeReturnTo } from '../../shared/routing/route-registry';

export const lifecycleTabs = ['policies', 'executions', 'restores', 'multipart'] as const;
export type LifecycleTab = (typeof lifecycleTabs)[number];
export type LifecycleIntent = 'review' | 'simulate' | 'restore' | 'abortMultipart';

export interface StorageLifecycleSearch {
  readonly tab: LifecycleTab;
  readonly intent?: LifecycleIntent;
  readonly objectRole?: readonly string[];
  readonly status?: readonly string[];
  readonly q?: string;
  readonly policyId?: string;
  readonly simulationId?: string;
  readonly executionId?: string;
  readonly restoreTaskId?: string;
  readonly uploadId?: string;
  readonly sort: string;
  readonly after?: string;
  readonly before?: string;
  readonly limit: 20 | 50 | 100;
  readonly returnTo?: string;
}

const defaults: StorageLifecycleSearch = { tab: 'policies', sort: 'updatedAt:desc,id:asc', limit: 50 };
const intents = new Set<LifecycleIntent>(['review', 'simulate', 'restore', 'abortMultipart']);
const allowedRoles = new Set(['SOURCE', 'DERIVED', 'PREVIEW', 'EXPORT', 'INCOMPLETE_MULTIPART']);

function list(sp: URLSearchParams, key: string, allow?: ReadonlySet<string>): readonly string[] | undefined {
  const values = [...new Set(sp.getAll(key).map((value) => value.trim()).filter((value) => value && (!allow || allow.has(value))))].sort();
  return values.length ? values : undefined;
}

function limit(raw: string | null): 20 | 50 | 100 {
  return raw === '20' ? 20 : raw === '100' ? 100 : 50;
}

export const storageLifecycleQueryCodec = defineQueryCodec<StorageLifecycleSearch>({
  defaults,
  parse(sp) {
    const rawTab = sp.get('tab');
    const tab = lifecycleTabs.includes(rawTab as LifecycleTab) ? rawTab as LifecycleTab : 'policies';
    const q = sp.get('q')?.trim().slice(0, 100);
    const rawIntent = sp.get('intent');
    const intent = intents.has(rawIntent as LifecycleIntent) ? rawIntent as LifecycleIntent : undefined;
    const after = sp.get('after')?.trim() || undefined;
    const before = sp.get('before')?.trim() || undefined;
    const returnTo = safeReturnTo(sp.get('returnTo')) ?? undefined;
    const base: StorageLifecycleSearch = {
      tab,
      sort: sp.get('sort')?.trim() || defaults.sort,
      limit: limit(sp.get('limit')),
      ...(intent ? { intent } : {}),
      ...(q ? { q } : {}),
      ...(list(sp, 'objectRole', allowedRoles) ? { objectRole: list(sp, 'objectRole', allowedRoles) } : {}),
      ...(list(sp, 'status') ? { status: list(sp, 'status') } : {}),
      ...(after && !before ? { after } : {}),
      ...(before && !after ? { before } : {}),
      ...(returnTo ? { returnTo } : {}),
    };
    if (tab === 'policies') return { ...base, ...(sp.get('policyId') ? { policyId: sp.get('policyId') ?? undefined } : {}), ...(sp.get('simulationId') ? { simulationId: sp.get('simulationId') ?? undefined } : {}) };
    if (tab === 'executions') return { ...base, ...(sp.get('executionId') ? { executionId: sp.get('executionId') ?? undefined } : {}) };
    if (tab === 'restores') return { ...base, ...(sp.get('restoreTaskId') ? { restoreTaskId: sp.get('restoreTaskId') ?? undefined } : {}) };
    return { ...base, ...(sp.get('uploadId') ? { uploadId: sp.get('uploadId') ?? undefined } : {}) };
  },
  build(value) {
    const merged = { ...defaults, ...value };
    const sp = new URLSearchParams();
    if (merged.tab !== defaults.tab) sp.set('tab', merged.tab);
    if (merged.intent) sp.set('intent', merged.intent);
    merged.objectRole?.forEach((entry) => sp.append('objectRole', entry));
    merged.status?.forEach((entry) => sp.append('status', entry));
    for (const key of ['q', 'policyId', 'simulationId', 'executionId', 'restoreTaskId', 'uploadId', 'sort', 'after', 'before', 'returnTo'] as const) {
      const entry = merged[key];
      if (entry && !(key === 'sort' && entry === defaults.sort)) sp.set(key, entry);
    }
    if (merged.limit !== defaults.limit) sp.set('limit', String(merged.limit));
    return sp;
  },
});

export function updateLifecycleSearch(current: StorageLifecycleSearch, patch: Partial<StorageLifecycleSearch>): StorageLifecycleSearch {
  const cursorInvalidators: readonly (keyof StorageLifecycleSearch)[] = ['tab', 'objectRole', 'status', 'q', 'sort', 'limit'];
  const invalidates = cursorInvalidators.some((key) => key in patch && patch[key] !== current[key]);
  const next = { ...current, ...patch };
  if (!invalidates) return storageLifecycleQueryCodec.normalize(next);
  return storageLifecycleQueryCodec.normalize({ ...next, after: undefined, before: undefined });
}
