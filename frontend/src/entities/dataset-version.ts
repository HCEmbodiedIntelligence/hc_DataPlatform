import type { DatasetId } from './dataset';

const datasetVersionIdPattern = /^version_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/;

declare const datasetVersionIdBrand: unique symbol;

/** Stable immutable Version identity. `current` and `latest` are never valid identities. */
export type DatasetVersionId = string & {
  readonly [datasetVersionIdBrand]: 'DatasetVersionId';
};

/** Compatibility name used by the frozen cross-page contracts. */
export type VersionId = DatasetVersionId;

export const DATASET_VERSION_KINDS = ['RAW', 'CLEANED', 'UNKNOWN'] as const;
export type DatasetVersionKind = (typeof DATASET_VERSION_KINDS)[number];

export const DATASET_VERSION_STATUSES = ['REVIEWING', 'RETURNED', 'READY', 'UNKNOWN'] as const;
export type DatasetVersionStatus = (typeof DATASET_VERSION_STATUSES)[number];

export const DATASET_VERSION_DELIVERY_STATUSES = [
  'NOT_STARTED',
  'GENERATING',
  'FAILED',
  'CANDIDATE_READY',
  'UNKNOWN',
] as const;
export type DatasetVersionDeliveryStatus = (typeof DATASET_VERSION_DELIVERY_STATUSES)[number];

/** Pure identity/state projection; ReviewFinding lineage is owned by review-finding.ts. */
export type DatasetVersion = Readonly<{
  id: DatasetVersionId;
  datasetId: DatasetId;
  displayVersion: string;
  kind: DatasetVersionKind;
  status: DatasetVersionStatus;
  deliveryStatus?: DatasetVersionDeliveryStatus;
  createdAt: string;
  publishedAt: string | null;
  etag: string;
  versionToken: string;
}>;

export function isDatasetVersionId(value: unknown): value is DatasetVersionId {
  return (
    typeof value === 'string' &&
    value !== 'version_current' &&
    value !== 'version_latest' &&
    datasetVersionIdPattern.test(value)
  );
}

export const isVersionId = isDatasetVersionId;

export function isDatasetVersionKind(value: unknown): value is DatasetVersionKind {
  return typeof value === 'string' && DATASET_VERSION_KINDS.includes(value as DatasetVersionKind);
}

export function isDatasetVersionStatus(value: unknown): value is DatasetVersionStatus {
  return (
    typeof value === 'string' && DATASET_VERSION_STATUSES.includes(value as DatasetVersionStatus)
  );
}

export function isDatasetVersionDeliveryStatus(
  value: unknown,
): value is DatasetVersionDeliveryStatus {
  return (
    typeof value === 'string' &&
    DATASET_VERSION_DELIVERY_STATUSES.includes(value as DatasetVersionDeliveryStatus)
  );
}

export function isTerminalDatasetVersionStatus(
  status: DatasetVersionStatus,
): status is 'RETURNED' | 'READY' {
  return status === 'RETURNED' || status === 'READY';
}

export function isMutableReviewDatasetVersionStatus(
  status: DatasetVersionStatus,
): status is 'REVIEWING' {
  return status === 'REVIEWING';
}

export function isDatasetVersion(value: unknown): value is DatasetVersion {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Partial<DatasetVersion>;
  return (
    isDatasetVersionId(candidate.id) &&
    typeof candidate.datasetId === 'string' &&
    typeof candidate.displayVersion === 'string' &&
    isDatasetVersionKind(candidate.kind) &&
    isDatasetVersionStatus(candidate.status) &&
    (candidate.deliveryStatus === undefined ||
      isDatasetVersionDeliveryStatus(candidate.deliveryStatus)) &&
    typeof candidate.createdAt === 'string' &&
    (candidate.publishedAt === null || typeof candidate.publishedAt === 'string') &&
    typeof candidate.etag === 'string' &&
    typeof candidate.versionToken === 'string'
  );
}
