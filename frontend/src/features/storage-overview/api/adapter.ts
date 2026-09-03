import { int64String, type Int64String } from '../../../shared/lib/bigint-string';
import {
  isStorageClass,
  isStorageObjectRole,
  type StorageMetric,
  type StorageObjectRole,
  type StorageClass,
} from '../../../entities/storage-inventory';
import type { StorageCostBreakdown, StorageInventoryPage, StorageMoney, StorageMultipartPage, StorageOverview } from '../types';
import type { StorageCostWire, StorageMultipartPageWire, StorageObjectEnvelopeWire, StorageObjectPageWire, StorageObjectWire, StorageOverviewWire } from './schemas';

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`STORAGE_CONTRACT_MISMATCH:${message}`);
}

const int64 = (value: string): Int64String => int64String(value);

function adaptMetric(wire: { state: 'KNOWN'; value: string } | { state: 'UNKNOWN' | 'COMPUTING' | 'FORBIDDEN' | 'NOT_SETTLED' | 'FAILED'; value: null }): StorageMetric<Int64String> {
  return wire.state === 'KNOWN' ? { state: 'KNOWN', value: int64(wire.value) } : wire;
}

function adaptDecimalMetric(wire: { state: 'KNOWN'; value: string } | { state: 'UNKNOWN' | 'COMPUTING' | 'FORBIDDEN' | 'NOT_SETTLED' | 'FAILED'; value: null }): StorageMetric<string> {
  return wire.state === 'KNOWN' ? { state: 'KNOWN', value: wire.value } : wire;
}

export function adaptStorageOverview(wire: StorageOverviewWire): StorageOverview {
  let hasUnknownEnum = false;
  const roles: StorageOverview['roles'] = wire.data.roles.map((item) => {
    const role: StorageObjectRole = isStorageObjectRole(item.role) ? item.role : 'UNKNOWN';
    const primaryStorageClass: StorageClass | 'MIXED' = item.primary_storage_class === 'MIXED'
      ? 'MIXED'
      : isStorageClass(item.primary_storage_class) ? item.primary_storage_class : 'UNKNOWN';
    hasUnknownEnum ||= role === 'UNKNOWN' || primaryStorageClass === 'UNKNOWN';
    return {
      role,
      wireRole: item.role,
      physicalBytes: adaptMetric(item.physical_bytes),
      objectCount: adaptMetric(item.object_count),
      primaryStorageClass,
      status: item.status,
    };
  });
  invariant(new Set(roles.map((item) => item.wireRole)).size === roles.length, 'duplicate_role');

  const monthlyCost: StorageMetric<StorageMoney> = wire.data.totals.monthly_cost.availability === 'KNOWN'
    ? {
        state: 'KNOWN',
        value: {
          minorUnits: int64(wire.data.totals.monthly_cost.amount_minor),
          currency: wire.data.totals.monthly_cost.currency,
          kind: wire.data.totals.monthly_cost.kind,
          billingPeriod: wire.data.totals.monthly_cost.billing_period,
          asOf: wire.data.totals.monthly_cost.as_of,
        },
      }
    : { state: wire.data.totals.monthly_cost.availability, value: null };

  return {
    snapshotId: wire.data.snapshot_id,
    asOf: wire.data.as_of,
    freshness: wire.data.freshness,
    formulaVersion: wire.data.formula_version,
    dataCompleteness: wire.data.data_completeness,
    inventoryStatus: wire.data.inventory.status,
    reconciliation: {
      status: wire.data.reconciliation.status,
      registeredPhysicalBytes: adaptMetric(wire.data.reconciliation.registered_physical_bytes),
      actualOssPhysicalBytes: adaptMetric(wire.data.reconciliation.actual_oss_physical_bytes),
      unclassifiedBytes: adaptMetric(wire.data.reconciliation.unclassified_bytes),
      multipartBytes: adaptMetric(wire.data.reconciliation.multipart_bytes),
      note: wire.data.reconciliation.note,
    },
    totals: {
      logicalReferencedBytes: adaptMetric(wire.data.totals.logical_referenced_bytes),
      actualOssPhysicalBytes: adaptMetric(wire.data.totals.actual_oss_physical_bytes),
      billedBytes: adaptMetric(wire.data.totals.billed_bytes),
      objectCount: adaptMetric(wire.data.totals.object_count),
      referenceCount: adaptMetric(wire.data.totals.reference_count),
      reuseRate: adaptDecimalMetric(wire.data.totals.reuse_rate),
      monthlyCost,
    },
    roles,
    growth: wire.data.growth.map((item) => ({
      month: item.month,
      role: isStorageObjectRole(item.role) ? item.role : 'UNKNOWN',
      physicalBytes: item.physical_bytes === null ? null : int64(item.physical_bytes),
      completeness: item.completeness,
    })),
    storageClasses: wire.data.storage_classes.map((item) => {
      const storageClass = isStorageClass(item.storage_class) ? item.storage_class : 'UNKNOWN';
      hasUnknownEnum ||= storageClass === 'UNKNOWN';
      return {
        storageClass,
        physicalBytes: item.physical_bytes === null ? null : int64(item.physical_bytes),
        objectCount: item.object_count === null ? null : int64(item.object_count),
        availability: item.availability,
      };
    }),
    alerts: wire.data.alerts.map((item) => ({
      type: item.type,
      candidateCount: item.candidate_count === null ? null : int64(item.candidate_count),
      candidateBytes: item.candidate_bytes === null ? null : int64(item.candidate_bytes),
      thresholdBytes: item.threshold_bytes === null ? null : int64(item.threshold_bytes),
      asOf: item.as_of,
    })),
    partialErrors: wire.data.partial_errors.map((item) => ({ section: item.section, code: item.code, requestId: item.request_id })),
    hasUnknownEnum,
    requestId: wire.request_id,
  };
}

export function adaptStorageInventoryPage(wire: StorageObjectPageWire): StorageInventoryPage {
  const items: StorageInventoryPage['items'] = wire.items.map((item) => {
    invariant(item.snapshot_id === wire.snapshot_id, 'mixed_snapshot');
    return adaptStorageObject(item);
  });
  invariant(new Set(items.map((item) => item.objectId)).size === items.length, 'duplicate_object');
  if (items.length === 0) invariant(wire.page_info.start_cursor === null && wire.page_info.end_cursor === null, 'empty_cursor');
  return {
    items,
    snapshotId: wire.snapshot_id,
    snapshotAt: wire.snapshot_at,
    pageInfo: {
      hasNextPage: wire.page_info.has_next_page,
      hasPreviousPage: wire.page_info.has_previous_page,
      startCursor: wire.page_info.start_cursor,
      endCursor: wire.page_info.end_cursor,
    },
    hasUnknownEnum: items.some((item) => item.objectRole === 'UNKNOWN' || item.storageClass === 'UNKNOWN'),
    requestId: wire.request_id,
  };
}

function adaptStorageObject(item: StorageObjectWire): StorageInventoryPage['items'][number] {
  const objectRole: StorageObjectRole = isStorageObjectRole(item.object_role) ? item.object_role : 'UNKNOWN';
  const storageClass: StorageClass = isStorageClass(item.storage_class) ? item.storage_class : 'UNKNOWN';
  return {
    objectId: item.object_id,
    snapshotId: item.snapshot_id,
    objectRole,
    wireObjectRole: item.object_role,
    displayKey: item.display_key,
    masked: true as const,
    physicalBytes: int64(item.physical_bytes),
    storageClass,
    wireStorageClass: item.storage_class,
    status: item.status,
    referenceCount: int64(item.reference_count),
    createdAt: item.created_at,
    lastAccessedAt: item.last_accessed_at,
    protectionReasons: item.protection_reasons.map((reason) => reason.code),
    allowedActions: item.allowed_actions.filter((action) => action.allowed).map((action) => action.action),
    readOnly: true as const,
  };
}

export function adaptStorageObjectEnvelope(wire: StorageObjectEnvelopeWire): StorageInventoryPage['items'][number] {
  return adaptStorageObject(wire.data);
}

const multipartStatuses = new Set([
  'ACTIVE', 'PAUSED_RESUMABLE', 'INACTIVE', 'USER_CANCELLED', 'COMPLETING',
  'VALIDATING', 'ABORTING', 'ABORT_FAILED', 'UNKNOWN',
]);

export function adaptStorageMultipartPage(wire: StorageMultipartPageWire): StorageMultipartPage {
  const items = wire.items.map((item) => ({
    multipartId: item.multipart_id,
    uploadId: item.upload_id,
    uploadVersion: item.upload_version,
    uploadSessionId: item.upload_session_id ?? null,
    dataSourceId: item.data_source_id ?? null,
    sourceFormat: item.source_format ?? null,
    status: multipartStatuses.has(item.status) ? item.status : 'UNKNOWN',
    statusKnown: multipartStatuses.has(item.status),
    receivedBytes: int64(item.received_bytes),
    expectedBytes: item.expected_bytes === null ? null : int64(item.expected_bytes),
    partCount: int64(item.part_count),
    startedAt: item.started_at,
    lastActivityAt: item.last_activity_at,
    resumableUntil: item.resumable_until,
    protectionReasons: item.protection_reasons.map((reason) => reason.code),
    allowedActions: item.allowed_actions.filter((action) => action.allowed).map((action) => action.action),
    readOnly: true as const,
  }));
  invariant(new Set(items.map((item) => item.multipartId)).size === items.length, 'duplicate_multipart');
  if (items.length === 0) invariant(wire.page_info.start_cursor === null && wire.page_info.end_cursor === null, 'empty_multipart_cursor');
  return {
    items,
    snapshotId: wire.snapshot_id,
    snapshotAt: wire.snapshot_at,
    pageInfo: {
      hasNextPage: wire.page_info.has_next_page,
      hasPreviousPage: wire.page_info.has_previous_page,
      startCursor: wire.page_info.start_cursor,
      endCursor: wire.page_info.end_cursor,
    },
    hasUnknownEnum: items.some((item) => !item.statusKnown),
    requestId: wire.request_id,
  };
}

export function adaptStorageCost(wire: StorageCostWire): StorageCostBreakdown {
  return {
    billingPeriod: wire.data.billing_period,
    asOf: wire.data.as_of,
    currency: wire.data.currency,
    storage: adaptMetric(wire.data.storage),
    request: adaptMetric(wire.data.request),
    transfer: adaptMetric(wire.data.transfer),
    tax: adaptMetric(wire.data.tax),
    total: adaptMetric(wire.data.total),
    formulaVersion: wire.data.formula_version,
    requestId: wire.request_id,
  };
}
