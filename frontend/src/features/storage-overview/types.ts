import type { Int64String } from '../../shared/lib/bigint-string';
import type { StorageClass, StorageInventoryFact, StorageMetric, StorageObjectRole } from '../../entities/storage-inventory';

export type StorageScope = Readonly<{
  organizationId: string;
  projectId: string;
  regionCode: string;
}>;

export type StorageMoney = Readonly<{
  minorUnits: Int64String;
  currency: string;
  kind: 'ACTUAL' | 'FORECAST' | 'ESTIMATE';
  billingPeriod: string;
  asOf: string;
}>;

export type StorageOverview = Readonly<{
  snapshotId: string;
  asOf: string;
  freshness: string;
  formulaVersion: string;
  dataCompleteness: string;
  inventoryStatus: string;
  reconciliation: Readonly<{
    status: string;
    registeredPhysicalBytes: StorageMetric<Int64String>;
    actualOssPhysicalBytes: StorageMetric<Int64String>;
    unclassifiedBytes: StorageMetric<Int64String>;
    multipartBytes: StorageMetric<Int64String>;
    note: string | null;
  }>;
  totals: Readonly<{
    logicalReferencedBytes: StorageMetric<Int64String>;
    actualOssPhysicalBytes: StorageMetric<Int64String>;
    billedBytes: StorageMetric<Int64String>;
    objectCount: StorageMetric<Int64String>;
    referenceCount: StorageMetric<Int64String>;
    reuseRate: StorageMetric<string>;
    monthlyCost: StorageMetric<StorageMoney>;
  }>;
  roles: readonly Readonly<{
    role: StorageObjectRole;
    wireRole: string;
    physicalBytes: StorageMetric<Int64String>;
    objectCount: StorageMetric<Int64String>;
    primaryStorageClass: StorageClass | 'MIXED';
    status: string;
  }>[];
  growth: readonly Readonly<{ month: string; role: StorageObjectRole; physicalBytes: Int64String | null; completeness: string }>[];
  storageClasses: readonly Readonly<{ storageClass: StorageClass; physicalBytes: Int64String | null; objectCount: Int64String | null; availability: string }>[];
  alerts: readonly Readonly<{ type: string; candidateCount: Int64String | null; candidateBytes: Int64String | null; thresholdBytes: Int64String | null; asOf: string }>[];
  partialErrors: readonly Readonly<{ section: string; code: string; requestId: string }>[];
  hasUnknownEnum: boolean;
  requestId: string;
}>;

export type StorageInventoryPage = Readonly<{
  items: readonly StorageInventoryFact[];
  snapshotId: string;
  snapshotAt: string;
  pageInfo: Readonly<{
    hasNextPage: boolean;
    hasPreviousPage: boolean;
    startCursor: string | null;
    endCursor: string | null;
  }>;
  hasUnknownEnum: boolean;
  requestId: string;
}>;

export type StorageMultipartFact = Readonly<{
  multipartId: string;
  uploadId: string | null;
  uploadVersion: string | null;
  uploadSessionId: string | null;
  dataSourceId: string | null;
  sourceFormat: string | null;
  status: string;
  statusKnown: boolean;
  receivedBytes: Int64String;
  expectedBytes: Int64String | null;
  partCount: Int64String;
  startedAt: string;
  lastActivityAt: string | null;
  resumableUntil: string | null;
  protectionReasons: readonly string[];
  allowedActions: readonly string[];
  readOnly: true;
}>;

export type StorageMultipartPage = Readonly<{
  items: readonly StorageMultipartFact[];
  snapshotId: string;
  snapshotAt: string;
  pageInfo: StorageInventoryPage['pageInfo'];
  hasUnknownEnum: boolean;
  requestId: string;
}>;

export type StorageCostBreakdown = Readonly<{
  billingPeriod: string;
  asOf: string;
  currency: string;
  storage: StorageMetric<Int64String>;
  request: StorageMetric<Int64String>;
  transfer: StorageMetric<Int64String>;
  tax: StorageMetric<Int64String>;
  total: StorageMetric<Int64String>;
  formulaVersion: string;
  requestId: string;
}>;
