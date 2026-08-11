const datasetIdPattern = /^dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/;

declare const datasetIdBrand: unique symbol;

/** Stable project-scoped Dataset identity. */
export type DatasetId = string & { readonly [datasetIdBrand]: 'DatasetId' };

export const DATASET_AVAILABILITIES = ['ACTIVE', 'FROZEN', 'UNKNOWN'] as const;
export type DatasetAvailability = (typeof DATASET_AVAILABILITIES)[number];

export type DatasetLabel = Readonly<{
  key: string;
  value: string;
}>;

export type DatasetOwnerSummary = Readonly<{
  id: string;
  displayName: string;
}>;

/** Pure domain projection. HTTP casing and resource actions belong to adapters/features. */
export type Dataset = Readonly<{
  id: DatasetId;
  name: string;
  description: string;
  labels: readonly DatasetLabel[];
  availability: DatasetAvailability;
  owner: DatasetOwnerSummary | null;
  createdAt: string;
  updatedAt: string;
  etag: string;
}>;

export function isDatasetId(value: unknown): value is DatasetId {
  return typeof value === 'string' && datasetIdPattern.test(value);
}

export function isDatasetAvailability(value: unknown): value is DatasetAvailability {
  return typeof value === 'string' && DATASET_AVAILABILITIES.includes(value as DatasetAvailability);
}

export function isDataset(value: unknown): value is Dataset {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Partial<Dataset>;
  return (
    isDatasetId(candidate.id) &&
    typeof candidate.name === 'string' &&
    typeof candidate.description === 'string' &&
    Array.isArray(candidate.labels) &&
    isDatasetAvailability(candidate.availability) &&
    typeof candidate.createdAt === 'string' &&
    typeof candidate.updatedAt === 'string' &&
    typeof candidate.etag === 'string'
  );
}
