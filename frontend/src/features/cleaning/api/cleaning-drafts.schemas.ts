import { z } from 'zod';
import {
  etagWireSchema,
  idWireSchema,
  instantWireSchema,
  int64WireSchema,
  pageInfoWireSchema,
  scopeEnvelopeWireSchema,
  scopeWireSchema,
  sha256WireSchema,
} from './wire-common';
import { issueDerivedContextWireSchema } from './manual-issues.schemas';
import { cleaningOperationWireSchema, cleaningSummaryWireSchema } from './edl.schemas';

const enumCode = z.string().regex(/^[A-Z][A-Z0-9_]{0,63}$/);

export const reviewReturnLineageWireSchema = z.object({
  lineage_type: z.literal('REVIEW_RETURN'),
  supersedes_draft_id: idWireSchema,
  returned_from_version_id: idWireSchema,
  returned_from_review_decision_id: idWireSchema,
}).strict();

export const cleaningDraftOriginWireSchema = z.discriminatedUnion('origin_type', [
  z.object({
    origin_type: z.literal('ISSUE_DERIVED'),
    manual_issue_context: issueDerivedContextWireSchema,
    review_return_lineage: z.null(),
  }).strict(),
  z.object({
    origin_type: z.literal('REVIEW_RETURN'),
    manual_issue_context: z.null(),
    review_return_lineage: reviewReturnLineageWireSchema,
  }).strict(),
]);

export const reviewReturnSummaryWireSchema = z.object({
  review_decision_id: idWireSchema,
  review_finding_ids: z.array(idWireSchema).min(1),
  finding_count: z.string().regex(/^[1-9][0-9]*$/),
  successor_draft_id: idWireSchema,
  supersedes_draft_id: idWireSchema,
  returned_from_version_id: idWireSchema,
  returned_from_review_decision_id: idWireSchema,
  output_version_status: z.literal('RETURNED'),
}).strict().superRefine((summary, ctx) => {
  if (summary.review_decision_id !== summary.returned_from_review_decision_id) {
    ctx.addIssue({ code: 'custom', message: 'Review decision lineage mismatch' });
  }
  if (BigInt(summary.finding_count) !== BigInt(summary.review_finding_ids.length)) {
    ctx.addIssue({ code: 'custom', message: 'Review finding count mismatch' });
  }
  if (summary.successor_draft_id === summary.supersedes_draft_id) {
    ctx.addIssue({ code: 'custom', message: 'Successor Draft must have a new ID' });
  }
});

const cleaningDraftShape = {
  draft_id: idWireSchema,
  etag: etagWireSchema,
  status: enumCode,
  origin: cleaningDraftOriginWireSchema,
  base_version_id: idWireSchema,
  base_revision_id: idWireSchema,
  episode_id: idWireSchema,
  manual_issue_count: z.enum(['0', '1']),
  preview_status: enumCode,
  commit_status: enumCode,
  output_version_status: enumCode.nullable(),
  review_decision_id: idWireSchema.nullable(),
  successor_draft_id: idWireSchema.nullable(),
  review_finding_count: int64WireSchema,
  review_summary: reviewReturnSummaryWireSchema.nullable(),
  allowed_actions: z.array(enumCode),
} as const;

function validateCleaningDraftProjection(
  draft: z.infer<z.ZodObject<typeof cleaningDraftShape>> & { created_at?: string },
  ctx: z.RefinementCtx,
): void {
  const issueDerived = draft.origin.origin_type === 'ISSUE_DERIVED';
  if ((draft.manual_issue_count === '1') !== issueDerived) {
    ctx.addIssue({ code: 'custom', message: 'ManualIssue count must match Draft origin' });
  }
  if (draft.output_version_status === 'RETURNED') {
    if (!draft.review_decision_id || !draft.successor_draft_id || !draft.review_summary || draft.review_finding_count === '0') {
      ctx.addIssue({ code: 'custom', message: 'RETURNED requires complete immutable Review summary and successor' });
    }
  } else if (draft.review_decision_id || draft.successor_draft_id || draft.review_summary || draft.review_finding_count !== '0') {
    ctx.addIssue({ code: 'custom', message: 'Only RETURNED may carry Review Return facts' });
  }
}

export const cleaningDraftListItemWireSchema = z.object({
  ...cleaningDraftShape,
  updated_at: instantWireSchema,
}).strict().superRefine(validateCleaningDraftProjection);

export const cleaningDraftWireSchema = z.object({
  ...cleaningDraftShape,
  created_at: instantWireSchema,
  updated_at: instantWireSchema,
}).strict().superRefine(validateCleaningDraftProjection);

export const cleaningDraftSummaryEnvelopeWireSchema = scopeEnvelopeWireSchema(z.object({
  scope_counts: z.object({
    EDITING: int64WireSchema,
    COMMITTED: int64WireSchema,
    RETURNED: int64WireSchema,
    REVIEWING: int64WireSchema,
  }).strict(),
  metrics: z.object({
    active_draft_count: int64WireSchema,
    manual_issue_derived_count: int64WireSchema,
    review_return_count: int64WireSchema,
  }).strict(),
  jobs: z.object({
    preview_queued: int64WireSchema,
    preview_running: int64WireSchema,
    commit_queued: int64WireSchema,
  }).strict(),
  as_of: instantWireSchema,
}).strict());

export const cleaningDraftListEnvelopeWireSchema = z.object({
  items: z.array(cleaningDraftListItemWireSchema),
  page_info: pageInfoWireSchema,
  snapshot_at: instantWireSchema,
  query_signature: sha256WireSchema,
  scope: scopeWireSchema,
  request_id: idWireSchema,
  contract_version: z.literal('manual-cleaning.v1'),
}).strict();

export const cleaningDraftRelationshipsWireSchema = z.object({
  commit_id: idWireSchema.nullable(),
  output_version_id: idWireSchema.nullable(),
  output_revision_ids: z.array(idWireSchema),
  review_decision_id: idWireSchema.nullable(),
  review_finding_ids: z.array(idWireSchema),
  successor_draft_id: idWireSchema.nullable(),
  supersedes_draft_id: idWireSchema.nullable(),
  returned_from_version_id: idWireSchema.nullable(),
  returned_from_review_decision_id: idWireSchema.nullable(),
}).strict();

export const cleaningDraftEventWireSchema = z.object({
  event_id: idWireSchema,
  event_type: z.enum([
    'cleaning.draft.created', 'cleaning.draft.updated', 'cleaning.preview.requested',
    'cleaning.preview.completed', 'cleaning.submit.requested', 'cleaning.submit.completed',
    'dataset_version.review.returned', 'cleaning.draft.successor_created',
  ]),
  occurred_at: instantWireSchema,
  result: z.enum(['SUCCESS', 'FAILURE']),
  safe_summary: z.string().max(2048).nullable(),
  request_id: idWireSchema,
}).strict();

export const cleaningDraftEventsEnvelopeWireSchema = z.object({
  items: z.array(cleaningDraftEventWireSchema),
  page_info: pageInfoWireSchema,
  snapshot_at: instantWireSchema,
  scope: scopeWireSchema,
  request_id: idWireSchema,
  contract_version: z.literal('manual-cleaning.v1'),
}).strict();

export const cleaningDraftDetailEnvelopeWireSchema = scopeEnvelopeWireSchema(z.object({
  draft: cleaningDraftWireSchema,
  origin: cleaningDraftOriginWireSchema,
  ordered_operations: z.array(cleaningOperationWireSchema),
  preview_summary: cleaningSummaryWireSchema.nullable(),
  review_summary: reviewReturnSummaryWireSchema.nullable(),
  relationships: cleaningDraftRelationshipsWireSchema,
}).strict().superRefine((detail, ctx) => {
  if (detail.draft.origin.origin_type !== detail.origin.origin_type) {
    ctx.addIssue({ code: 'custom', message: 'Detail origin mismatch' });
  }
  const returned = detail.draft.output_version_status === 'RETURNED';
  if (returned && (!detail.review_summary || detail.relationships.review_finding_ids.length === 0 || !detail.relationships.successor_draft_id)) {
    ctx.addIssue({ code: 'custom', message: 'RETURNED detail chain is incomplete' });
  }
  if (!returned && (detail.review_summary || detail.relationships.review_finding_ids.length > 0 || detail.relationships.successor_draft_id)) {
    ctx.addIssue({ code: 'custom', message: 'Non-returned detail cannot carry Review Return facts' });
  }
}));

export type CleaningDraftWire = z.infer<typeof cleaningDraftWireSchema>;
export type CleaningDraftListItemWire = z.infer<typeof cleaningDraftListItemWireSchema>;
