import { z } from "zod";
import {
  blockedReasonWireSchema,
  decimalNsWireSchema,
  etagWireSchema,
  idWireSchema,
  instantWireSchema,
  int64WireSchema,
  pageInfoWireSchema,
  principalSummaryWireSchema,
  scopeEnvelopeWireSchema,
  scopeWireSchema,
} from "./wire-common";

export const manualIssueSeverityWireSchema = z.enum([
  "LOW",
  "MEDIUM",
  "HIGH",
  "CRITICAL",
]);
export const manualIssueDiscoverySourceWireSchema = z.enum([
  "DATA_VIEWER",
  "ANNOTATOR",
  "REVIEWER",
]);
export const manualIssueTypeWireSchema = z.enum([
  "POSE_JITTER",
  "TIMESTAMP_DRIFT",
  "MISSING_FRAME",
  "STREAM_GAP",
  "CALIBRATION_MISMATCH",
  "INVALID_MASK",
  "OTHER",
]);

// Accept a future code here so the Adapter can project UNKNOWN/read-only safely.
export const manualIssueStatusWireSchema = z
  .string()
  .regex(/^[A-Z][A-Z0-9_]{0,63}$/);
export const manualIssueActionWireSchema = z.enum([
  "VIEW_EPISODE",
  "TRIAGE",
  "START_WORK",
  "CREATE_DRAFT",
  "CONTINUE_DRAFT",
  "RESOLVE",
  "PREVIEW_RANGE",
]);

export const relatedDraftRefWireSchema = z
  .object({
    draft_id: idWireSchema,
    status: z.enum(["EDITING", "COMMITTED"]),
    updated_at: instantWireSchema,
  })
  .strict();

export const resolutionVersionRefWireSchema = z
  .object({
    version_id: idWireSchema,
    producer_draft_id: idWireSchema,
    root_issue_draft_id: idWireSchema,
    lineage_depth: int64WireSchema,
    resolved_at: instantWireSchema,
  })
  .strict();

const manualIssueCoreShape = {
  id: idWireSchema,
  etag: etagWireSchema,
  scope: scopeWireSchema,
  dataset_id: idWireSchema,
  origin_dataset_version_id: idWireSchema,
  episode_id: idWireSchema,
  episode_revision_id: idWireSchema,
  episode_stream_id: idWireSchema,
  context_status: z.literal("VALID"),
  schema_snapshot_id: idWireSchema,
  robot_model_version_id: idWireSchema.nullable(),
  calibration_set_id: idWireSchema.nullable(),
  start_ns: decimalNsWireSchema,
  end_ns: decimalNsWireSchema,
  issue_type: manualIssueTypeWireSchema,
  severity: manualIssueSeverityWireSchema,
  discovery_source: manualIssueDiscoverySourceWireSchema,
  annotation_task_id: idWireSchema.nullable(),
  status: manualIssueStatusWireSchema,
  note: z.string().max(8192),
  assignee: principalSummaryWireSchema.nullable(),
  related_drafts: z.array(relatedDraftRefWireSchema),
  resolution_version: resolutionVersionRefWireSchema.nullable(),
  resolution_note: z.string().min(1).max(4096).nullable(),
  resolved_at: instantWireSchema.nullable(),
  resolved_by: principalSummaryWireSchema.nullable(),
  allowed_actions: z.array(manualIssueActionWireSchema),
  blocked_reasons: z.array(blockedReasonWireSchema),
  created_at: instantWireSchema,
  updated_at: instantWireSchema,
} as const;

export const manualIssueWireSchema = z
  .object(manualIssueCoreShape)
  .strict()
  .superRefine((issue, ctx) => {
    if (BigInt(issue.start_ns) >= BigInt(issue.end_ns)) {
      ctx.addIssue({
        code: "custom",
        message: "ManualIssue range must be [start,end)",
      });
    }
    if (issue.status === "IN_PROGRESS" && issue.assignee === null) {
      ctx.addIssue({
        code: "custom",
        message: "IN_PROGRESS requires assignee",
      });
    }
    const resolved = issue.status === "RESOLVED";
    if (
      resolved !==
      (issue.resolution_version !== null &&
        issue.resolution_note !== null &&
        issue.resolved_at !== null &&
        issue.resolved_by !== null)
    ) {
      ctx.addIssue({
        code: "custom",
        message: "Resolution facts must match RESOLVED status",
      });
    }
  });

export const manualIssueDetailEnvelopeWireSchema = scopeEnvelopeWireSchema(
  manualIssueWireSchema,
);

export const manualIssuesPageEnvelopeWireSchema = scopeEnvelopeWireSchema(
  z
    .object({
      counts: z
        .object({
          total: int64WireSchema,
          open: int64WireSchema,
          in_progress: int64WireSchema,
          resolved: int64WireSchema,
        })
        .strict(),
      facets: z
        .object({
          issue_types: z.array(manualIssueTypeWireSchema),
          severities: z.array(manualIssueSeverityWireSchema),
          statuses: z.array(z.enum(["OPEN", "IN_PROGRESS", "RESOLVED"])),
          discovery_sources: z.array(manualIssueDiscoverySourceWireSchema),
          assignees: z.array(principalSummaryWireSchema),
        })
        .strict(),
      allowed_actions: z.array(manualIssueActionWireSchema),
      snapshot_at: instantWireSchema,
    })
    .strict(),
);

export const manualIssueListItemWireSchema = z
  .object({
    id: idWireSchema,
    etag: etagWireSchema,
    scope: scopeWireSchema,
    dataset_id: idWireSchema,
    origin_dataset_version_id: idWireSchema,
    episode_id: idWireSchema,
    episode_revision_id: idWireSchema,
    episode_stream_id: idWireSchema,
    start_ns: decimalNsWireSchema,
    end_ns: decimalNsWireSchema,
    issue_type: manualIssueTypeWireSchema,
    severity: manualIssueSeverityWireSchema,
    discovery_source: manualIssueDiscoverySourceWireSchema,
    annotation_task_id: idWireSchema.nullable(),
    status: manualIssueStatusWireSchema,
    assignee: principalSummaryWireSchema.nullable(),
    related_draft_count: int64WireSchema,
    resolution_version: resolutionVersionRefWireSchema.nullable(),
    resolution_note: z.string().min(1).max(4096).nullable(),
    resolved_at: instantWireSchema.nullable(),
    resolved_by: principalSummaryWireSchema.nullable(),
    allowed_actions: z.array(manualIssueActionWireSchema),
    updated_at: instantWireSchema,
  })
  .strict();

export const manualIssueListEnvelopeWireSchema = z
  .object({
    items: z.array(manualIssueListItemWireSchema),
    page_info: pageInfoWireSchema,
    snapshot_at: instantWireSchema,
    scope: scopeWireSchema,
    request_id: idWireSchema,
    contract_version: z.literal("manual-cleaning.v1"),
  })
  .strict();

export const createManualIssueRequestWireSchema = z
  .object({
    source_kind: z.enum(["DATASET", "ANNOTATION_TASK"]).default("DATASET"),
    discovery_source:
      manualIssueDiscoverySourceWireSchema.default("DATA_VIEWER"),
    origin_dataset_version_id: idWireSchema.nullish(),
    episode_id: idWireSchema.nullish(),
    episode_revision_id: idWireSchema.nullish(),
    episode_stream_id: idWireSchema.nullish(),
    start_ns: decimalNsWireSchema.nullish(),
    end_ns: decimalNsWireSchema.nullish(),
    annotation_task_id: idWireSchema.nullish(),
    stream_ref: z.string().min(1).max(512).nullish(),
    relative_start_ns: decimalNsWireSchema.nullish(),
    relative_end_ns: decimalNsWireSchema.nullish(),
    issue_type: manualIssueTypeWireSchema,
    severity: manualIssueSeverityWireSchema,
    note: z.string().max(8192),
  })
  .strict()
  .superRefine((request, ctx) => {
    const datasetFields = [
      request.origin_dataset_version_id,
      request.episode_id,
      request.episode_revision_id,
      request.episode_stream_id,
      request.start_ns,
      request.end_ns,
    ];
    const annotationFields = [
      request.annotation_task_id,
      request.stream_ref,
      request.relative_start_ns,
      request.relative_end_ns,
    ];
    if (request.source_kind === "DATASET") {
      if (
        datasetFields.some((value) => value == null) ||
        annotationFields.some((value) => value != null)
      ) {
        ctx.addIssue({
          code: "custom",
          message: "DATASET source fields are incomplete",
        });
        return;
      }
      if (BigInt(request.start_ns!) >= BigInt(request.end_ns!)) {
        ctx.addIssue({
          code: "custom",
          message: "ManualIssue range must be [start,end)",
        });
      }
      return;
    }
    if (
      annotationFields.some((value) => value == null) ||
      datasetFields.some((value) => value != null)
    ) {
      ctx.addIssue({
        code: "custom",
        message: "ANNOTATION_TASK source fields are incomplete",
      });
      return;
    }
    if (
      BigInt(request.relative_start_ns!) >= BigInt(request.relative_end_ns!)
    ) {
      ctx.addIssue({
        code: "custom",
        message: "ManualIssue relative range must be [start,end)",
      });
    }
  });

export const triageManualIssueRequestWireSchema = z
  .object({
    target_status: z.enum(["OPEN", "IN_PROGRESS"]),
    severity: manualIssueSeverityWireSchema,
    assignee_id: idWireSchema.nullable(),
    reason: z.string().trim().min(1).max(4096),
  })
  .strict()
  .superRefine((request, ctx) => {
    if (
      request.target_status === "IN_PROGRESS" &&
      request.assignee_id === null
    ) {
      ctx.addIssue({
        code: "custom",
        message: "IN_PROGRESS requires assignee_id",
      });
    }
  });

export const resolveManualIssueRequestWireSchema = z
  .object({
    resolution_version_id: idWireSchema,
    resolution_note: z.string().trim().min(1).max(4096),
  })
  .strict();

export const issueDerivedContextWireSchema = z
  .object({
    schema_version: z.literal(1),
    dataset_id: idWireSchema,
    base_version_id: idWireSchema,
    episode_id: idWireSchema,
    base_revision_id: idWireSchema,
    selected_stream_id: idWireSchema,
    selected_channel_path: z.string().max(512).nullable(),
    start_ns: decimalNsWireSchema,
    end_ns: decimalNsWireSchema,
    manual_issue_ids: z.tuple([idWireSchema]),
  })
  .strict();

const draftCandidateWireSchema = z
  .object({
    draft_id: idWireSchema,
    status: z.literal("EDITING"),
    base_version_id: idWireSchema,
    base_revision_id: idWireSchema,
    manual_issue_ids: z.tuple([idWireSchema]),
    updated_at: instantWireSchema,
  })
  .strict();

export const createDraftFromIssueResultWireSchema = z.discriminatedUnion(
  "disposition",
  [
    z
      .object({
        disposition: z.enum(["CREATED", "ALREADY_LINKED"]),
        draft_id: idWireSchema,
        context: issueDerivedContextWireSchema,
        selection_token: z.null(),
        expires_at: z.null(),
        candidates: z.tuple([]),
      })
      .strict(),
    z
      .object({
        disposition: z.literal("SELECTION_REQUIRED"),
        draft_id: z.null(),
        context: z.null(),
        selection_token: idWireSchema,
        expires_at: instantWireSchema,
        candidates: z.array(draftCandidateWireSchema).min(2),
      })
      .strict(),
  ],
);

export const createDraftFromIssueEnvelopeWireSchema = scopeEnvelopeWireSchema(
  createDraftFromIssueResultWireSchema,
);

export type ManualIssueWire = z.infer<typeof manualIssueWireSchema>;
export type ManualIssueListItemWire = z.infer<
  typeof manualIssueListItemWireSchema
>;
export type CreateManualIssueRequestWire = z.infer<
  typeof createManualIssueRequestWireSchema
>;
