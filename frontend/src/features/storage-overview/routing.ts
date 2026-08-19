import type { StorageClass, StorageObjectRole } from '../../entities/storage-inventory';

export type StorageOverviewTab = 'overview' | 'objects' | 'multipart';
export type StorageOverviewSearch = Readonly<{
  tab: StorageOverviewTab;
  objectRole?: Exclude<StorageObjectRole, 'UNKNOWN'>;
  storageClass?: Exclude<StorageClass, 'UNKNOWN'>;
  anomaly: readonly string[];
  status: readonly string[];
  objectId?: string;
  months: 3 | 6 | 12;
  sort: 'physicalBytes:desc,objectId:desc' | 'createdAt:desc,objectId:desc';
  after?: string;
  before?: string;
  limit: 20 | 50 | 100;
  returnTo?: string;
}>;

export const STORAGE_OVERVIEW_DEFAULTS: StorageOverviewSearch = {
  tab: 'overview',
  anomaly: [],
  status: [],
  months: 6,
  sort: 'physicalBytes:desc,objectId:desc',
  limit: 50,
};

export function buildStorageSearchParams(value: Partial<StorageOverviewSearch>): URLSearchParams {
  const normalized = { ...STORAGE_OVERVIEW_DEFAULTS, ...value };
  const params = new URLSearchParams();
  if (normalized.tab !== 'overview') params.set('tab', normalized.tab);
  if (normalized.objectRole) params.set('objectRole', normalized.objectRole);
  if (normalized.storageClass) params.set('storageClass', normalized.storageClass);
  for (const item of [...new Set(normalized.anomaly)].sort()) params.append('anomaly', item);
  for (const item of [...new Set(normalized.status)].sort()) params.append('status', item);
  if (normalized.objectId) params.set('objectId', normalized.objectId);
  if (normalized.months !== 6) params.set('months', String(normalized.months));
  if (normalized.sort !== STORAGE_OVERVIEW_DEFAULTS.sort) params.set('sort', normalized.sort);
  if (normalized.after) params.set('after', normalized.after);
  else if (normalized.before) params.set('before', normalized.before);
  if (normalized.limit !== 50) params.set('limit', String(normalized.limit));
  if (normalized.returnTo) params.set('returnTo', normalized.returnTo);
  return params;
}

export const storageOverviewRoute = {
  path: '/storage/overview',
  build(search: Partial<StorageOverviewSearch> = {}): string {
    const query = buildStorageSearchParams(search).toString();
    return query ? `/storage/overview?${query}` : '/storage/overview';
  },
} as const;

const cursorResetKeys: readonly (keyof StorageOverviewSearch)[] = [
  'tab', 'objectRole', 'storageClass', 'anomaly', 'status', 'months', 'sort', 'limit',
];

export function patchStorageOverviewSearch(current: StorageOverviewSearch, patch: Partial<StorageOverviewSearch>): StorageOverviewSearch {
  const changed = cursorResetKeys.some((key) => key in patch && JSON.stringify(patch[key]) !== JSON.stringify(current[key]));
  const merged = { ...current, ...patch };
  const next = merged.tab === 'objects' ? merged : { ...merged, objectId: undefined };
  if (!changed) return next;
  return { ...next, after: undefined, before: undefined };
}
