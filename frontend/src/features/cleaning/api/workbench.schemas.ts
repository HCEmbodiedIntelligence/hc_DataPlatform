import { z } from 'zod';
import { cleaningDraftWireSchema, cleaningDraftOriginWireSchema } from './cleaning-drafts.schemas';
import { cleaningEdlWireSchema, cleaningSummaryWireSchema, cleaningValidationWireSchema, sourceToOutputMapWireSchema } from './edl.schemas';
import {
  etagWireSchema, idWireSchema, instantWireSchema, int64WireSchema,
  scopeEnvelopeWireSchema, scopeWireSchema, sha256WireSchema,
} from './wire-common';

const viewerManifestWireSchema = z.object({
  manifest_id: idWireSchema,
  manifest_hash: sha256WireSchema,
  expires_at: instantWireSchema,
  streams: z.array(idWireSchema),
}).strict();

const previewIdentity = {
  preview_id: idWireSchema,
  draft_id: idWireSchema,
  base_revision_id: idWireSchema,
  edl_revision: int64WireSchema,
  operation_hash: sha256WireSchema,
  job_id: idWireSchema,
  created_at: instantWireSchema,
} as const;

export const cleaningPreviewWireSchema = z.discriminatedUnion('status', [
  z.object({ ...previewIdentity, status: z.enum(['QUEUED', 'RUNNING']), expires_at: z.null() }).strict(),
  z.object({
    ...previewIdentity,
    status: z.literal('READY'),
    viewer_manifest: viewerManifestWireSchema,
    source_to_output_map: sourceToOutputMapWireSchema,
    validation: cleaningValidationWireSchema,
    summary: cleaningSummaryWireSchema,
    expires_at: instantWireSchema,
  }).strict(),
  z.object({ ...previewIdentity, status: z.literal('FAILED'), error_ref: idWireSchema, expires_at: z.null() }).strict(),
  z.object({ ...previewIdentity, status: z.enum(['EXPIRED', 'STALE']), expires_at: instantWireSchema.nullable() }).strict(),
]);

const outputRevisionWireSchema = z.object({
  revision_id: idWireSchema,
  ordinal: z.number().int().nonnegative(),
  episode_id: idWireSchema,
  episode_stream_ids: z.array(idWireSchema).min(1),
  source_revision_id: idWireSchema,
  member_mode: z.enum(['EDIT_RESULT', 'CARRY_FORWARD']),
}).strict();

const outputVersionWireSchema = z.object({
  version_id: idWireSchema,
  status: z.enum(['REVIEWING', 'READY', 'RETURNED']),
  draft_id: idWireSchema,
  commit_id: idWireSchema,
}).strict();

const commitBase = {
  commit_id: idWireSchema,
  draft_id: idWireSchema,
  preview_id: idWireSchema,
  job_id: idWireSchema,
  created_at: instantWireSchema,
  successor_composition_hash: sha256WireSchema.nullable(),
} as const;

export const cleaningCommitWireSchema = z.discriminatedUnion('status', [
  z.object({
    ...commitBase, status: z.literal('QUEUED'), output_revisions: z.tuple([]),
    output_version: z.null(), materialization_status: z.enum(['NOT_STARTED', 'RUNNING']),
  }).strict(),
  z.object({
    ...commitBase, status: z.literal('SUCCEEDED'), completed_at: instantWireSchema,
    output_revisions: z.array(outputRevisionWireSchema).min(1), output_version: outputVersionWireSchema,
    materialization_status: z.enum(['NOT_STARTED', 'RUNNING', 'SUCCEEDED', 'FAILED']),
    operation_hash: sha256WireSchema,
  }).strict().superRefine((commit, ctx) => {
    commit.output_revisions.forEach((revision, index) => {
      if (revision.ordinal !== index) ctx.addIssue({ code: 'custom', message: 'Output Revision ordinals must be 0..N' });
    });
    if (commit.output_version.status !== 'REVIEWING') {
      ctx.addIssue({ code: 'custom', message: 'Commit completion Version state must be REVIEWING' });
    }
  }),
  z.object({
    ...commitBase, status: z.literal('FAILED'), error_ref: idWireSchema,
    output_revisions: z.tuple([]), output_version: z.null(),
    materialization_status: z.enum(['NOT_STARTED', 'FAILED']),
  }).strict(),
]);

export const reviewFindingProjectionWireSchema = z.object({
  id: idWireSchema,
  output_revision_id: idWireSchema,
  episode_stream_id: idWireSchema,
  start_ns: int64WireSchema,
  end_ns: int64WireSchema,
  finding_type: z.string().regex(/^[A-Z][A-Z0-9_:-]*$/).max(96),
  severity: z.enum(['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']),
  note: z.string().min(1).max(8192),
  immutable: z.literal(true),
  created_at: instantWireSchema,
}).strict().superRefine((finding, ctx) => {
  if (BigInt(finding.start_ns) >= BigInt(finding.end_ns)) ctx.addIssue({ code: 'custom', message: 'Finding must use [start,end)' });
});

export const reviewReturnFeedbackWireSchema = z.object({
  review_decision: z.object({
    id: idWireSchema, output_version_id: idWireSchema, decision: z.literal('RETURNED'),
    immutable: z.literal(true), created_at: instantWireSchema,
  }).strict(),
  output_version: outputVersionWireSchema,
  output_version_id: idWireSchema,
  output_version_status: z.literal('RETURNED'),
  findings: z.array(reviewFindingProjectionWireSchema).min(1),
  review_finding_ids: z.array(idWireSchema).min(1),
  successor_draft_id: idWireSchema,
  supersedes_draft_id: idWireSchema,
  returned_from_version_id: idWireSchema,
  returned_from_review_decision_id: idWireSchema,
}).strict().superRefine((feedback, ctx) => {
  if (feedback.review_decision.id !== feedback.returned_from_review_decision_id ||
      feedback.review_decision.output_version_id !== feedback.returned_from_version_id ||
      feedback.output_version_id !== feedback.returned_from_version_id ||
      feedback.output_version.version_id !== feedback.returned_from_version_id ||
      feedback.output_version.status !== 'RETURNED') {
    ctx.addIssue({ code: 'custom', message: 'Review Return lineage mismatch' });
  }
  if (feedback.successor_draft_id === feedback.supersedes_draft_id) {
    ctx.addIssue({ code: 'custom', message: 'Review successor must be a new Draft ID' });
  }
  if (feedback.review_finding_ids.length !== feedback.findings.length ||
      feedback.findings.some((finding, index) => finding.id !== feedback.review_finding_ids[index])) {
    ctx.addIssue({ code: 'custom', message: 'Finding ID sequence mismatch' });
  }
});

const successorCompositionWireSchema = z.object({
  schema_version: z.literal(1),
  source_version_id: idWireSchema,
  editable_base_revision_id: idWireSchema,
  members: z.array(z.object({
    source_revision_id: idWireSchema,
    source_ordinal: z.number().int().nonnegative(),
    handling: z.enum(['EDITABLE_BASE', 'CARRY_FORWARD']),
  }).strict()).min(1),
  composition_hash: sha256WireSchema,
}).strict();

const leaseWireSchema = z.object({
  session_id: idWireSchema,
  draft_id: idWireSchema,
  etag: etagWireSchema,
  lease_revision: int64WireSchema,
  holder_summary: z.object({ id: idWireSchema, display_name: z.string().min(1).max(256) }).strict().nullable(),
  expires_at: instantWireSchema,
  read_only: z.boolean(),
}).strict();

export const cleaningDraftBootstrapEnvelopeWireSchema = scopeEnvelopeWireSchema(z.object({
  draft: cleaningDraftWireSchema,
  origin: cleaningDraftOriginWireSchema,
  base: z.object({
    dataset_id: idWireSchema,
    version_id: idWireSchema,
    episode_id: idWireSchema,
    revision_id: idWireSchema,
    schema_snapshot_id: idWireSchema,
    robot_model_version_id: idWireSchema.nullable(),
    calibration_set_id: idWireSchema.nullable(),
  }).strict(),
  streams: z.array(z.object({
    stream_id: idWireSchema, channel_path: z.string().min(1).max(512),
    kind: z.string().min(1).max(64), duration_ns: int64WireSchema,
  }).strict()).min(1),
  edl: cleaningEdlWireSchema,
  successor_composition: successorCompositionWireSchema.nullable(),
  active_preview: cleaningPreviewWireSchema.nullable(),
  active_commit: cleaningCommitWireSchema.nullable(),
  review_feedback: reviewReturnFeedbackWireSchema.nullable(),
  lease: leaseWireSchema.nullable(),
  allowed_actions: z.array(z.enum(['VIEW', 'ACQUIRE_LEASE', 'SAVE_EDL', 'CREATE_PREVIEW', 'COMMIT', 'OPEN_REVIEW', 'OPEN_SUCCESSOR'])),
}).strict().superRefine((bootstrap, ctx) => {
  if (bootstrap.draft.origin.origin_type !== bootstrap.origin.origin_type) {
    ctx.addIssue({ code: 'custom', message: 'Bootstrap origin mismatch' });
  }
  if (bootstrap.draft.base_version_id !== bootstrap.base.version_id ||
      bootstrap.draft.base_revision_id !== bootstrap.base.revision_id ||
      bootstrap.draft.episode_id !== bootstrap.base.episode_id) {
    ctx.addIssue({ code: 'custom', message: 'Bootstrap base identity mismatch' });
  }
  const reviewSuccessor = bootstrap.origin.origin_type === 'REVIEW_RETURN';
  if (reviewSuccessor !== (bootstrap.successor_composition !== null)) {
    ctx.addIssue({ code: 'custom', message: 'Review successor composition mismatch' });
  }
}));

export const createCleaningPreviewRequestWireSchema = z.object({
  base_revision_id: idWireSchema,
  edl_revision: int64WireSchema,
  operation_hash: sha256WireSchema,
}).strict();

export const commitCleaningDraftRequestWireSchema = z.object({
  preview_id: idWireSchema,
  base_revision_id: idWireSchema,
  edl_revision: int64WireSchema,
  operation_hash: sha256WireSchema,
  successor_composition_hash: sha256WireSchema.nullable(),
  acknowledgement: z.object({ reviewed_summary: z.literal(true), compared_preview: z.literal(true) }).strict(),
}).strict();

const asyncJobWireSchema = z.object({
  job_id: idWireSchema,
  kind: z.enum(['CLEANING_PREVIEW', 'CLEANING_COMMIT']),
  status: z.enum(['QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLING', 'CANCELLED', 'EXPIRED']),
  stage: z.string().min(1).max(128),
  progress: z.unknown().nullable(),
  result_ref: z.record(z.string(), z.string()).nullable(),
  error: z.unknown().nullable(),
  scope: z.object({ organization_id: idWireSchema, project_id: idWireSchema.nullable(), region_code: z.string().nullable() }).strict(),
  resource_ref: z.object({ resource_type: z.string(), resource_id: idWireSchema, version: z.string().nullable().optional(), etag: z.string().nullable().optional() }).strict(),
  created_at: instantWireSchema,
  started_at: instantWireSchema.nullable().optional(),
  finished_at: instantWireSchema.nullable().optional(),
  updated_at: instantWireSchema,
  expires_at: instantWireSchema.nullable().optional(),
  etag: z.string().min(3).max(512),
  cancellable: z.boolean(),
  retry_of_job_id: idWireSchema.nullable().optional(),
}).strict();

export const previewAcceptedEnvelopeWireSchema = z.object({
  preview: cleaningPreviewWireSchema,
  job: asyncJobWireSchema,
  scope: scopeWireSchema,
  request_id: idWireSchema,
  contract_version: z.literal('manual-cleaning.v1'),
}).strict();

export const commitAcceptedEnvelopeWireSchema = z.object({
  commit: cleaningCommitWireSchema,
  job: asyncJobWireSchema,
  scope: scopeWireSchema,
  request_id: idWireSchema,
  contract_version: z.literal('manual-cleaning.v1'),
}).strict();

export const reviewFindingsEnvelopeWireSchema = scopeEnvelopeWireSchema(z.object({
  feedback: reviewReturnFeedbackWireSchema,
}).strict());

export const saveCleaningEdlEnvelopeWireSchema = scopeEnvelopeWireSchema(z.object({
  draft: cleaningDraftWireSchema,
  edl: cleaningEdlWireSchema,
  request_id: idWireSchema,
}).strict());

export type CleaningDraftBootstrapEnvelopeWire = z.infer<typeof cleaningDraftBootstrapEnvelopeWireSchema>;
