import { z } from 'zod';

const id = z.string().min(1).max(160);
const instant = z.string().datetime({ offset: true });
export const int64TextWireSchema = z.string().regex(/^(0|[1-9]\d*)$/);
const decimalText = z.string().regex(/^-?(0|[1-9]\d*)(\.\d+)?$/);
const scope = z.object({
  organization_id: id,
  project_id: id,
  region_code: z.string().min(2).max(32),
  timezone: z.string().min(1).max(64),
}).strict();

const metric = <T extends z.ZodType>(knownValue: T) => z.discriminatedUnion('state', [
  z.object({ state: z.literal('KNOWN'), value: knownValue }).strict(),
  z.object({ state: z.enum(['UNKNOWN', 'COMPUTING', 'FORBIDDEN', 'NOT_SETTLED', 'FAILED']), value: z.null() }).strict(),
]);

export const int64MetricWireSchema = metric(int64TextWireSchema);
export const decimalMetricWireSchema = metric(decimalText);

const moneyMetric = z.union([
  z.object({
    availability: z.literal('KNOWN'),
    amount_minor: int64TextWireSchema,
    currency: z.string().regex(/^[A-Z]{3}$/),
    kind: z.enum(['ACTUAL', 'FORECAST', 'ESTIMATE']),
    billing_period: z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/),
    source_revision: id,
    formula_version: id,
    as_of: instant,
  }).strict(),
  z.object({ availability: z.literal('FORBIDDEN') }).strict(),
  z.object({
    availability: z.enum(['COMPUTING', 'NOT_SETTLED', 'FAILED']),
    currency: z.string().regex(/^[A-Z]{3}$/).nullable(),
    formula_version: z.string().nullable(),
  }).strict(),
]);

const watermarks = z.object({
  provider_as_of: instant,
  reference_as_of: instant,
  protection_as_of: instant,
  classification_as_of: instant,
  billing_as_of: instant.nullable(),
  evaluated_at: instant,
  stale_after_seconds: z.number().int().positive(),
}).strict();

const reconciliation = z.object({
  status: z.enum(['MATCHED', 'HAS_GAP', 'INCOMPLETE', 'RUNNING']),
  registered_physical_bytes: int64MetricWireSchema,
  actual_oss_physical_bytes: int64MetricWireSchema,
  unclassified_bytes: int64MetricWireSchema,
  multipart_bytes: int64MetricWireSchema,
  formula_version: id,
  note: z.string().max(512).nullable(),
}).strict();

export const storageOverviewWireSchema = z.object({
  data: z.object({
    snapshot_id: id,
    as_of: instant,
    freshness: z.enum(['CURRENT', 'STALE', 'EXPIRED', 'UNKNOWN']),
    formula_version: id,
    data_completeness: z.enum(['COMPLETE', 'PARTIAL', 'UNKNOWN']),
    inventory: z.object({
      status: z.enum(['FRESH', 'STALE', 'REFRESHING', 'FAILED', 'NEVER_RUN']),
      last_completed_at: instant.nullable(),
      running_job_id: id.nullable(),
      staleness_seconds: z.number().int().nonnegative().nullable(),
    }).strict(),
    watermarks,
    reconciliation,
    totals: z.object({
      logical_referenced_bytes: int64MetricWireSchema,
      actual_oss_physical_bytes: int64MetricWireSchema,
      billed_bytes: int64MetricWireSchema,
      object_count: int64MetricWireSchema,
      reference_count: int64MetricWireSchema,
      reuse_rate: decimalMetricWireSchema,
      monthly_cost: moneyMetric,
    }).strict(),
    roles: z.array(z.object({
      role: z.string().min(1),
      physical_bytes: int64MetricWireSchema,
      object_count: int64MetricWireSchema,
      average_object_bytes: int64MetricWireSchema,
      primary_storage_class: z.string().min(1),
      growth_30d_bytes: int64MetricWireSchema,
      growth_30d_rate: decimalMetricWireSchema,
      status: z.enum(['AVAILABLE', 'PARTIAL', 'UNAVAILABLE']),
      allowed_actions: z.array(id),
      display_scope: z.object({ storage_area_id: id, area_label: z.string().nullable(), masked: z.literal(true) }).strict(),
    }).strict()),
    classification_gaps: z.array(z.object({
      status: z.literal('UNCLASSIFIED'),
      physical_bytes: int64MetricWireSchema,
      object_count: int64MetricWireSchema,
      as_of: instant,
    }).strict()),
    growth: z.array(z.object({
      month: z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/),
      role: z.string().min(1),
      physical_bytes: int64TextWireSchema.nullable(),
      completeness: z.enum(['COMPLETE', 'PARTIAL', 'MISSING']),
    }).strict()),
    storage_classes: z.array(z.object({
      storage_class: z.string().min(1),
      physical_bytes: int64TextWireSchema.nullable(),
      object_count: int64TextWireSchema.nullable(),
      availability: z.enum(['AVAILABLE', 'RESTORING', 'MIGRATING', 'UNAVAILABLE']),
    }).strict()),
    alerts: z.array(z.object({
      type: z.string().min(1),
      candidate_count: int64TextWireSchema.nullable(),
      candidate_bytes: int64TextWireSchema.nullable(),
      threshold_bytes: int64TextWireSchema.nullable(),
      as_of: instant,
    }).strict()),
    allowed_actions: z.array(id),
    partial_errors: z.array(z.object({ section: z.string(), code: id, request_id: id }).strict()),
  }).strict(),
  scope,
  request_id: id,
  contract_version: z.literal('v1'),
}).strict();

const allowedAction = z.object({ action: id, allowed: z.boolean(), reason_code: z.string().nullable() }).strict();
const protectedReason = z.object({
  code: id,
  label: z.string().min(1).max(256),
  blocking: z.boolean(),
  object_count: int64TextWireSchema.nullable(),
  physical_bytes: int64TextWireSchema.nullable(),
}).strict();

export const storageObjectWireSchema = z.object({
  object_id: id,
  snapshot_id: id,
  object_role: z.string().min(1),
  classification_status: z.literal('CLASSIFIED'),
  display_key: z.string().min(1).max(256),
  masked: z.literal(true),
  physical_bytes: int64TextWireSchema,
  storage_class: z.string().min(1),
  media_type: z.string().nullable().optional(),
  status: z.string().min(1),
  reference_count: int64TextWireSchema,
  business_references: z.array(z.object({ resource_type: id, resource_id: id, relation: id, version: id }).strict()).optional(),
  protection_reasons: z.array(protectedReason),
  retention: z.object({ retain_until: instant.nullable(), source: z.string() }).strict().optional(),
  legal_hold: z.object({ active: z.boolean(), count: int64TextWireSchema, as_of: instant }).strict().optional(),
  lifecycle_rule_id: z.string().nullable().optional(),
  created_at: instant,
  last_accessed_at: instant.nullable(),
  expires_at: instant.nullable(),
  allowed_actions: z.array(allowedAction),
}).strict();

export const storageObjectPageWireSchema = z.object({
  items: z.array(storageObjectWireSchema),
  page_info: z.object({
    start_cursor: z.string().nullable(), end_cursor: z.string().nullable(),
    has_previous_page: z.boolean(), has_next_page: z.boolean(),
  }).strict(),
  snapshot_at: instant,
  snapshot_id: id,
  scope,
  request_id: id,
  contract_version: z.literal('v1'),
}).strict();

export const storageObjectEnvelopeWireSchema = z.object({
  data: storageObjectWireSchema,
  scope,
  request_id: id,
  contract_version: z.literal('v1'),
}).strict();

export const storageMultipartWireSchema = z.object({
  multipart_id: id,
  upload_id: id.nullable(),
  upload_version: id.nullable(),
  upload_session_id: id.nullable().optional(),
  data_source_id: id.nullable().optional(),
  source_format: z.string().max(128).nullable().optional(),
  status: z.string().min(1).max(64),
  received_bytes: int64TextWireSchema,
  expected_bytes: int64TextWireSchema.nullable(),
  part_count: int64TextWireSchema,
  started_at: instant,
  last_activity_at: instant.nullable(),
  resumable_until: instant.nullable(),
  protection_reasons: z.array(protectedReason),
  allowed_actions: z.array(allowedAction),
}).strict();

export const storageMultipartPageWireSchema = z.object({
  items: z.array(storageMultipartWireSchema),
  page_info: z.object({
    start_cursor: z.string().nullable(), end_cursor: z.string().nullable(),
    has_previous_page: z.boolean(), has_next_page: z.boolean(),
  }).strict(),
  snapshot_at: instant,
  snapshot_id: id,
  scope,
  request_id: id,
  contract_version: z.literal('v1'),
}).strict();

export const storageCostWireSchema = z.object({
  data: z.object({
    billing_period: z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/),
    as_of: instant,
    currency: z.string().regex(/^[A-Z]{3}$/),
    storage: int64MetricWireSchema,
    request: int64MetricWireSchema,
    transfer: int64MetricWireSchema,
    tax: int64MetricWireSchema,
    total: int64MetricWireSchema,
    formula_version: id,
  }).strict(),
  scope,
  request_id: id,
  contract_version: z.literal('v1'),
}).strict();

export type StorageOverviewWire = z.infer<typeof storageOverviewWireSchema>;
export type StorageObjectWire = z.infer<typeof storageObjectWireSchema>;
export type StorageObjectPageWire = z.infer<typeof storageObjectPageWireSchema>;
export type StorageObjectEnvelopeWire = z.infer<typeof storageObjectEnvelopeWireSchema>;
export type StorageMultipartPageWire = z.infer<typeof storageMultipartPageWireSchema>;
export type StorageCostWire = z.infer<typeof storageCostWireSchema>;
