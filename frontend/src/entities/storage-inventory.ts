import type { Int64String } from '../shared/lib/bigint-string';

export const STORAGE_OBJECT_ROLES = ['SOURCE', 'DERIVED', 'PREVIEW', 'EXPORT', 'ROBOT_ASSET', 'UNCLASSIFIED'] as const;
export const STORAGE_CLASSES = ['STANDARD', 'IA', 'ARCHIVE', 'COLD_ARCHIVE'] as const;

export type StorageObjectRole = (typeof STORAGE_OBJECT_ROLES)[number] | 'UNKNOWN';
export type StorageClass = (typeof STORAGE_CLASSES)[number] | 'UNKNOWN';
export type StorageMetricState = 'KNOWN' | 'UNKNOWN' | 'COMPUTING' | 'FORBIDDEN' | 'NOT_SETTLED' | 'FAILED';

export type StorageMetric<T> =
  | Readonly<{ state: 'KNOWN'; value: T }>
  | Readonly<{ state: Exclude<StorageMetricState, 'KNOWN'>; value: null }>;

export type StorageInventoryFact = Readonly<{
  objectId: string;
  snapshotId: string;
  objectRole: StorageObjectRole;
  wireObjectRole: string;
  displayKey: string;
  masked: true;
  physicalBytes: Int64String;
  storageClass: StorageClass;
  wireStorageClass: string;
  status: string;
  referenceCount: Int64String;
  createdAt: string;
  lastAccessedAt: string | null;
  protectionReasons: readonly string[];
  allowedActions: readonly string[];
  readOnly: true;
}>;

export function isStorageObjectRole(value: string): value is Exclude<StorageObjectRole, 'UNKNOWN'> {
  return STORAGE_OBJECT_ROLES.includes(value as Exclude<StorageObjectRole, 'UNKNOWN'>);
}

export function isStorageClass(value: string): value is Exclude<StorageClass, 'UNKNOWN'> {
  return STORAGE_CLASSES.includes(value as Exclude<StorageClass, 'UNKNOWN'>);
}
