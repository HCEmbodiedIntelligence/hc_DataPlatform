import { z } from 'zod';
import {
  decimalNsWireSchema,
  etagWireSchema,
  idWireSchema,
  instantWireSchema,
  int64WireSchema,
  sha256WireSchema,
  signedNsWireSchema,
} from './wire-common';

const base = {
  id: idWireSchema,
  sequence_no: z.number().int().nonnegative(),
  enabled: z.boolean(),
  schema_version: z.literal('1'),
} as const;

const range = {
  start_ns: decimalNsWireSchema,
  end_ns: decimalNsWireSchema,
} as const;

function validRange<T extends { start_ns: string; end_ns: string }>(value: T, ctx: z.RefinementCtx) {
  if (BigInt(value.start_ns) >= BigInt(value.end_ns)) {
    ctx.addIssue({ code: 'custom', message: 'EDL range must be [start,end)' });
  }
}

export const cleaningOperationWireSchema = z.discriminatedUnion('type', [
  z.object({ ...base, type: z.literal('TRIM'), ...range }).strict().superRefine(validRange),
  z.object({
    ...base, type: z.literal('EXCLUDE_RANGE'), ...range,
    reason: z.string().max(2048).nullable(),
  }).strict().superRefine(validRange),
  z.object({ ...base, type: z.literal('SPLIT'), at_ns: decimalNsWireSchema }).strict(),
  z.object({
    ...base,
    type: z.literal('TIME_OFFSET'),
    episode_stream_id: idWireSchema,
    offset_ns: signedNsWireSchema,
    scope: z.literal('EPISODE'),
    reference_stream_id: idWireSchema.nullable(),
  }).strict(),
  z.object({ ...base, type: z.literal('DISABLE_CHANNEL'), episode_stream_id: idWireSchema }).strict(),
  z.object({
    ...base,
    type: z.literal('SET_METADATA'),
    patch: z.record(z.string().regex(/^[a-z][a-z0-9_.-]{0,63}$/), z.union([z.string(), z.boolean(), z.null()])),
  }).strict(),
  z.object({
    ...base,
    type: z.literal('INVALIDATE_EPISODE'),
    reason_code: z.string().min(1).max(96),
    note: z.string().max(2048).nullable(),
  }).strict(),
  z.object({
    ...base,
    type: z.literal('INVALID_MASK'),
    episode_stream_id: idWireSchema.nullable(),
    ...range,
    reason_code: z.string().max(96).nullable(),
  }).strict().superRefine(validRange),
]);

export const validationIssueWireSchema = z.object({
  code: z.string().regex(/^[A-Z0-9_:-]+$/),
  severity: z.enum(['BLOCKER', 'WARNING', 'INFO']),
  operation_id: idWireSchema.nullable(),
  json_pointer: z.string().regex(/^\/[^\s]*$/).nullable(),
  message: z.string().min(1).max(2048),
}).strict();

export const cleaningValidationWireSchema = z.object({
  status: z.enum(['PASSED', 'FAILED']),
  issues: z.array(validationIssueWireSchema),
  validated_edl_revision: int64WireSchema,
  validated_operation_hash: sha256WireSchema,
}).strict();

export const cleaningSummaryWireSchema = z.object({
  source_duration_ns: decimalNsWireSchema,
  trimmed_domain_duration_ns: decimalNsWireSchema,
  excluded_union_duration_ns: decimalNsWireSchema,
  invalid_mask_union_duration_ns: decimalNsWireSchema,
  output_duration_ns: decimalNsWireSchema,
  output_segment_count: int64WireSchema,
  disabled_stream_count: int64WireSchema,
  reused_source_bytes: int64WireSchema,
  new_derived_bytes: int64WireSchema,
  reuse_rate: z.string().regex(/^(0|1|0\.[0-9]+)$/),
  requires_materialization: z.boolean(),
  estimate_status: z.enum(['ESTIMATED', 'CONFIRMED', 'COMPUTING', 'FAILED']),
  calculated_at: instantWireSchema,
}).strict();

export const cleaningEdlWireSchema = z.object({
  edl_revision: int64WireSchema,
  etag: etagWireSchema,
  operation_hash: sha256WireSchema,
  operations: z.array(cleaningOperationWireSchema),
  validation: cleaningValidationWireSchema,
  summary: cleaningSummaryWireSchema,
  updated_at: instantWireSchema,
}).strict().superRefine((edl, ctx) => {
  if (new Set(edl.operations.map((operation) => operation.id)).size !== edl.operations.length) {
    ctx.addIssue({ code: 'custom', message: 'EDL operation IDs must be unique' });
  }
  edl.operations.forEach((operation, index) => {
    if (operation.sequence_no !== index) {
      ctx.addIssue({ code: 'custom', message: 'EDL sequence_no must be contiguous from zero' });
    }
  });
  if (edl.validation.validated_edl_revision !== edl.edl_revision ||
      edl.validation.validated_operation_hash !== edl.operation_hash) {
    ctx.addIssue({ code: 'custom', message: 'EDL validation identity mismatch' });
  }
});

export const saveCleaningEdlRequestWireSchema = z.object({
  expected_edl_revision: int64WireSchema,
  expected_operation_hash: sha256WireSchema.nullable(),
  operations: z.array(cleaningOperationWireSchema),
  client_mutation_id: idWireSchema,
}).strict();

export const timeMappingSegmentWireSchema = z.object({
  source_start_ns: decimalNsWireSchema,
  source_end_ns: decimalNsWireSchema,
  output_revision_id: idWireSchema,
  output_start_ns: decimalNsWireSchema,
  output_end_ns: decimalNsWireSchema,
}).strict().superRefine((segment, ctx) => {
  validRange({ start_ns: segment.source_start_ns, end_ns: segment.source_end_ns }, ctx);
  validRange({ start_ns: segment.output_start_ns, end_ns: segment.output_end_ns }, ctx);
});

export const sourceToOutputMapWireSchema = z.object({
  segments: z.array(timeMappingSegmentWireSchema).min(1),
  mapping_version: int64WireSchema,
}).strict();

export type CleaningOperationWire = z.infer<typeof cleaningOperationWireSchema>;
export type CleaningEdlWire = z.infer<typeof cleaningEdlWireSchema>;

