import { z } from 'zod';

export const idWireSchema = z.string().min(1).max(128).regex(/^[A-Za-z0-9][A-Za-z0-9._:-]*$/);
export const nsWireSchema = z.string().regex(/^(0|[1-9][0-9]*)$/);
export const shaWireSchema = z.string().regex(/^sha256:[a-f0-9]{64}$/);
export const etagWireSchema = z.string().min(3).max(160).regex(/^"[^"]+"$/);

export const scopeWireSchema = z.object({
  organization_id: idWireSchema,
  project_id: idWireSchema,
  region_code: z.string().min(1).max(32),
}).strict();

export const taskSourceWireSchema = z.object({
  dataset_id: idWireSchema,
  dataset_version_id: idWireSchema,
  episode_id: idWireSchema,
  base_revision_id: idWireSchema,
  stream_ids: z.array(idWireSchema),
  start_ns: nsWireSchema,
  end_ns: nsWireSchema,
}).strict().superRefine((source, context) => {
  if (BigInt(source.start_ns) >= BigInt(source.end_ns)) context.addIssue({ code: 'custom', path: ['end_ns'], message: 'range must be non-empty and half-open' });
  if (new Set(source.stream_ids).size !== source.stream_ids.length) context.addIssue({ code: 'custom', path: ['stream_ids'], message: 'stream IDs must be unique' });
});

const assignmentWireSchema = z.object({
  mode: z.enum(['UNASSIGNED', 'CLAIMED', 'DIRECT']),
  assignee_id: idWireSchema.nullable(),
  assigned_by: idWireSchema.nullable(),
  assigned_at: z.iso.datetime().nullable(),
}).strict();

const actionReasonWireSchema = z.object({
  action: z.string().min(1),
  reason_code: z.string().min(1),
  message: z.string().min(1),
}).strict();

export const annotationTaskWireSchema = z.object({
  task_id: idWireSchema,
  scope: scopeWireSchema,
  task_source: z.enum(['COVERAGE_GAP', 'DIRECT_ASSIGNMENT', 'CORRECTION', 'REVISION_REBASE']),
  workflow_status: z.string().min(1),
  source_status: z.string().min(1),
  block_source: z.enum(['ADMINISTRATIVE', 'DEPENDENCY']).nullable(),
  block_reason: z.string().max(512).nullable(),
  source: taskSourceWireSchema,
  ontology: z.object({ ontology_id: idWireSchema, ontology_version: z.string().min(1), ontology_hash: shaWireSchema }).strict(),
  assignment: assignmentWireSchema,
  priority: z.number().int().min(0).max(1000),
  current_draft_revision: z.number().int().nonnegative(),
  current_draft_hash: shaWireSchema,
  current_submission_id: idWireSchema.nullable(),
  submitted_annotation_set_id: idWireSchema.nullable(),
  correction_of_annotation_set_id: idWireSchema.nullable(),
  predecessor_task_id: idWireSchema.nullable(),
  successor_task_id: idWireSchema.nullable(),
  latest_review_id: idWireSchema.nullable(),
  created_at: z.iso.datetime(),
  updated_at: z.iso.datetime(),
  etag: etagWireSchema,
  allowed_actions: z.array(z.string().min(1)),
  action_reasons: z.array(actionReasonWireSchema),
}).strict();

const rawEntryWireSchema = z.record(z.string(), z.unknown()).refine((entry) => typeof entry.semantic_type === 'string', { message: 'semantic_type is required' });

export const annotationDraftWireSchema = z.object({
  task_id: idWireSchema,
  draft_revision: z.number().int().nonnegative(),
  state: z.string().min(1),
  entries: z.array(rawEntryWireSchema).max(100000),
  content_hash: shaWireSchema,
  saved_by: idWireSchema.nullable(),
  saved_at: z.iso.datetime(),
  etag: etagWireSchema,
}).strict();

export const manualIssueProjectionWireSchema = z.object({
  manual_issue_id: idWireSchema,
  status: z.string().min(1),
  issue_type: z.string().min(1),
  severity: z.string().min(1),
  range: z.object({ base_revision_id: idWireSchema, stream_id: idWireSchema.nullable(), start_ns: nsWireSchema, end_ns: nsWireSchema }).strict(),
  summary: z.string().min(1).max(512),
  submission_impact: z.string().min(1),
  updated_at: z.iso.datetime(),
  aggregate_revision: z.number().int().positive(),
}).strict();

export const submissionGateWireSchema = z.object({
  status: z.string().min(1),
  policy_version: z.string().min(1),
  issue_watermark: z.string().min(1),
  evaluated_at: z.iso.datetime(),
  blocking_issue_ids: z.array(idWireSchema),
  advisory_issue_ids: z.array(idWireSchema),
  issues: z.array(manualIssueProjectionWireSchema),
  blocked_reasons: z.array(z.string()),
}).strict();

const pageInfoWireSchema = z.object({
  has_next_page: z.boolean(),
  has_previous_page: z.boolean(),
  start_cursor: z.string().nullable(),
  end_cursor: z.string().nullable(),
}).strict();

const metadata = {
  scope: scopeWireSchema,
  request_id: idWireSchema,
  contract_version: z.literal('data-annotation.v1'),
} as const;

const ontologyBindingWireSchema = z.object({
  ontology_id: idWireSchema,
  ontology_version: z.string().min(1),
  ontology_hash: shaWireSchema,
}).strict();

export const annotationEntryResolutionEnvelopeWireSchema = z.object({
  data: z.object({
    resolution_id: idWireSchema,
    state: z.enum(['OPEN_EXISTING', 'CLAIMABLE', 'CAN_CREATE', 'ASSIGNED_TO_OTHER', 'FORBIDDEN']),
    resolved_context: z.object({
      source: taskSourceWireSchema,
      ontology: ontologyBindingWireSchema,
      coverage_key_hash: shaWireSchema,
    }).strict(),
    task_id: idWireSchema.nullable(),
    task_etag: etagWireSchema.nullable(),
    entry_resolution_token: z.string().min(32).max(4096).nullable(),
    next_action: z.enum(['OPEN_TASK', 'CLAIM_TASK', 'CREATE_TASK', 'NONE']),
    blocked_reason: z.string().max(512).nullable(),
    expires_at: z.iso.datetime(),
  }).strict().superRefine((resolution, context) => {
    const valid = resolution.state === 'OPEN_EXISTING'
      ? !!resolution.task_id && !!resolution.task_etag && resolution.next_action === 'OPEN_TASK' && resolution.entry_resolution_token === null
      : resolution.state === 'CLAIMABLE'
        ? !!resolution.task_id && !!resolution.task_etag && resolution.next_action === 'CLAIM_TASK' && resolution.entry_resolution_token === null
        : resolution.state === 'CAN_CREATE'
          ? resolution.task_id === null && resolution.task_etag === null && !!resolution.entry_resolution_token && resolution.next_action === 'CREATE_TASK'
          : resolution.task_id === null && resolution.task_etag === null && resolution.entry_resolution_token === null && resolution.next_action === 'NONE';
    if (!valid) context.addIssue({ code: 'custom', message: 'entry resolution state/action/identity mismatch' });
  }),
  ...metadata,
}).strict();

export const materializeAnnotationTaskEnvelopeWireSchema = z.object({
  data: z.object({
    disposition: z.enum(['CREATED', 'OPEN_EXISTING', 'CLAIMED_EXISTING']),
    task: annotationTaskWireSchema,
    draft: annotationDraftWireSchema,
  }).strict(),
  ...metadata,
}).strict();

export const annotationTaskListEnvelopeWireSchema = z.object({
  items: z.array(annotationTaskWireSchema),
  page_info: pageInfoWireSchema,
  snapshot_id: idWireSchema,
  snapshot_at: z.iso.datetime(),
  ...metadata,
}).strict();

export const annotationTaskEnvelopeWireSchema = z.object({ data: annotationTaskWireSchema, ...metadata }).strict();

export const annotationTaskDetailEnvelopeWireSchema = z.object({
  data: z.object({
    task: annotationTaskWireSchema,
    draft: annotationDraftWireSchema,
    latest_submission: z.record(z.string(), z.unknown()).nullable(),
    annotation_reviews: z.array(z.record(z.string(), z.unknown())),
    manual_issues: z.array(manualIssueProjectionWireSchema),
    submission_gate: submissionGateWireSchema,
    reference_annotation_sets: z.array(z.record(z.string(), z.unknown())),
    stale_info: z.object({
      stale_reason: z.string().min(1),
      superseded_revision_id: idWireSchema,
      replacement_revision_id: idWireSchema,
      stale_at: z.iso.datetime(),
    }).strict().nullable().optional(),
    viewer: z.object({
      streams: z.array(z.record(z.string(), z.unknown())),
      form_definition: z.record(z.string(), z.unknown()).optional(),
      model_ref: z.record(z.string(), z.unknown()).optional(),
    }).optional(),
  }).strict(),
  ...metadata,
}).strict();

export const saveDraftEnvelopeWireSchema = z.object({
  data: z.object({ task: annotationTaskWireSchema, draft: annotationDraftWireSchema }).strict(),
  ...metadata,
}).strict();

export const submitPreflightEnvelopeWireSchema = z.object({
  data: z.object({
    preflight_id: idWireSchema,
    task_id: idWireSchema,
    draft_revision: z.number().int().nonnegative(),
    content_hash: shaWireSchema,
    valid: z.boolean(),
    submission_gate: submissionGateWireSchema,
    validation_errors: z.array(z.object({ code: z.string(), path: z.string(), message: z.string() }).strict()),
    expires_at: z.iso.datetime(),
  }).strict(),
  ...metadata,
}).strict();

export const submitTaskEnvelopeWireSchema = z.object({
  data: z.object({ task: annotationTaskWireSchema, submission: z.record(z.string(), z.unknown()), annotation_set: z.record(z.string(), z.unknown()) }).strict(),
  ...metadata,
}).strict();

export const acceptedAnnotationJobEnvelopeWireSchema = z.object({
  job: z.object({
    job_id: idWireSchema,
    job_type: z.string().min(1),
    status: z.string().min(1),
    resource_type: z.string().min(1),
    resource_id: idWireSchema,
  }).passthrough(),
  request_id: idWireSchema,
}).passthrough();

export const reviewEnvelopeWireSchema = z.object({
  data: z.object({ task: annotationTaskWireSchema, submission: z.record(z.string(), z.unknown()), review: z.record(z.string(), z.unknown()), active_draft: annotationDraftWireSchema.nullable() }).strict(),
  ...metadata,
}).strict();

export const rebaseEnvelopeWireSchema = z.object({
  data: z.object({
    stale_task_id: idWireSchema,
    successor_task: annotationTaskWireSchema,
    successor_draft: annotationDraftWireSchema,
    migration_mode: z.literal('EMPTY'),
    migrated_annotation_count: z.literal(0),
  }).strict(),
  ...metadata,
}).strict();

export type AnnotationTaskWire = z.infer<typeof annotationTaskWireSchema>;
export type AnnotationDraftWire = z.infer<typeof annotationDraftWireSchema>;
export type AnnotationTaskDetailEnvelopeWire = z.infer<typeof annotationTaskDetailEnvelopeWireSchema>;
