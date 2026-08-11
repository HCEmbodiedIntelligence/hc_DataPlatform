import type { CleaningDraft } from '../../../entities/cleaning-draft';
import type { EditingDecisionList, SourceToOutputMap } from '../../../entities/edl';
import { adaptCleaningDraftWire } from './cleaning-drafts.adapter';
import { adaptCleaningEdl, adaptSourceToOutputMap } from './edl.adapter';
import { assertCleaningScope, type ExpectedCleaningScope } from './wire-common';
import { cleaningDraftBootstrapEnvelopeWireSchema } from './workbench.schemas';
import type { CleaningDraftBootstrapEnvelopeWire } from './workbench.schemas';

export interface CleaningWorkbenchPreview {
  readonly previewId: string;
  readonly status: 'QUEUED' | 'RUNNING' | 'READY' | 'FAILED' | 'EXPIRED' | 'STALE';
  readonly jobId: string;
  readonly edlRevision: string;
  readonly operationHash: string;
  readonly expiresAt: string | null;
  readonly mapping: SourceToOutputMap | null;
}

export interface CleaningWorkbenchCommit {
  readonly commitId: string;
  readonly status: 'QUEUED' | 'SUCCEEDED' | 'FAILED';
  readonly jobId: string;
  readonly materializationStatus: string;
  readonly outputVersionId: string | null;
  readonly outputRevisions: readonly {
    readonly revisionId: string;
    readonly ordinal: number;
    readonly sourceRevisionId: string;
    readonly memberMode: 'EDIT_RESULT' | 'CARRY_FORWARD';
  }[];
}

export interface CleaningReviewFeedback {
  readonly decisionId: string;
  readonly findings: readonly {
    readonly id: string;
    readonly outputRevisionId: string;
    readonly episodeStreamId: string;
    readonly startNs: string;
    readonly endNs: string;
    readonly findingType: string;
    readonly severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
    readonly note: string;
    readonly immutable: true;
    readonly createdAt: string;
  }[];
  readonly successorDraftId: string;
  readonly supersedesDraftId: string;
  readonly returnedFromVersionId: string;
}

export interface CleaningWorkbenchModel {
  readonly draft: CleaningDraft;
  readonly base: {
    readonly datasetId: string;
    readonly versionId: string;
    readonly episodeId: string;
    readonly revisionId: string;
    readonly schemaSnapshotId: string;
  };
  readonly streams: readonly {
    readonly streamId: string;
    readonly channelPath: string;
    readonly kind: string;
    readonly durationNs: string;
  }[];
  readonly edl: EditingDecisionList;
  readonly validation: {
    readonly status: 'PASSED' | 'FAILED';
    readonly issues: readonly {
      readonly code: string;
      readonly severity: 'BLOCKER' | 'WARNING' | 'INFO';
      readonly operationId: string | null;
      readonly jsonPointer: string | null;
      readonly message: string;
    }[];
  };
  readonly summary: {
    readonly sourceDurationNs: string;
    readonly outputDurationNs: string;
    readonly outputSegmentCount: string;
    readonly reusedSourceBytes: string;
    readonly newDerivedBytes: string;
    readonly reuseRate: string;
    readonly requiresMaterialization: boolean;
    readonly estimateStatus: 'ESTIMATED' | 'CONFIRMED' | 'COMPUTING' | 'FAILED';
  };
  readonly preview: CleaningWorkbenchPreview | null;
  readonly commit: CleaningWorkbenchCommit | null;
  readonly reviewFeedback: CleaningReviewFeedback | null;
  readonly successorCompositionHash: string | null;
  readonly lease: { readonly readOnly: boolean; readonly expiresAt: string } | null;
  readonly allowedActions: readonly string[];
  readonly requestId: string;
}

export function adaptCleaningReviewFeedback(
  feedback: NonNullable<CleaningDraftBootstrapEnvelopeWire['data']['review_feedback']>,
): CleaningReviewFeedback {
  return {
    decisionId: feedback.review_decision.id,
    findings: feedback.findings.map((finding) => ({
      id: finding.id,
      outputRevisionId: finding.output_revision_id,
      episodeStreamId: finding.episode_stream_id,
      startNs: finding.start_ns,
      endNs: finding.end_ns,
      findingType: finding.finding_type,
      severity: finding.severity,
      note: finding.note,
      immutable: true,
      createdAt: finding.created_at,
    })),
    successorDraftId: feedback.successor_draft_id,
    supersedesDraftId: feedback.supersedes_draft_id,
    returnedFromVersionId: feedback.returned_from_version_id,
  };
}

export function adaptCleaningWorkbench(
  raw: unknown,
  expectedScope: ExpectedCleaningScope,
  expectedDraftId: string,
): CleaningWorkbenchModel {
  const wire = cleaningDraftBootstrapEnvelopeWireSchema.parse(raw);
  assertCleaningScope(wire.scope, expectedScope);
  if (wire.data.draft.draft_id !== expectedDraftId) throw new Error('RESOURCE_ID_MISMATCH');
  const preview = wire.data.active_preview;
  const commit = wire.data.active_commit;
  const feedback = wire.data.review_feedback;
  const reviewFeedback = feedback ? adaptCleaningReviewFeedback(feedback) : null;
  return {
    draft: adaptCleaningDraftWire(wire.data.draft),
    base: {
      datasetId: wire.data.base.dataset_id,
      versionId: wire.data.base.version_id,
      episodeId: wire.data.base.episode_id,
      revisionId: wire.data.base.revision_id,
      schemaSnapshotId: wire.data.base.schema_snapshot_id,
    },
    streams: wire.data.streams.map((stream) => ({
      streamId: stream.stream_id,
      channelPath: stream.channel_path,
      kind: stream.kind,
      durationNs: stream.duration_ns,
    })),
    edl: adaptCleaningEdl(wire.data.edl),
    validation: {
      status: wire.data.edl.validation.status,
      issues: wire.data.edl.validation.issues.map((issue) => ({
        code: issue.code,
        severity: issue.severity,
        operationId: issue.operation_id,
        jsonPointer: issue.json_pointer,
        message: issue.message,
      })),
    },
    summary: {
      sourceDurationNs: wire.data.edl.summary.source_duration_ns,
      outputDurationNs: wire.data.edl.summary.output_duration_ns,
      outputSegmentCount: wire.data.edl.summary.output_segment_count,
      reusedSourceBytes: wire.data.edl.summary.reused_source_bytes,
      newDerivedBytes: wire.data.edl.summary.new_derived_bytes,
      reuseRate: wire.data.edl.summary.reuse_rate,
      requiresMaterialization: wire.data.edl.summary.requires_materialization,
      estimateStatus: wire.data.edl.summary.estimate_status,
    },
    preview: preview ? {
      previewId: preview.preview_id,
      status: preview.status,
      jobId: preview.job_id,
      edlRevision: preview.edl_revision,
      operationHash: preview.operation_hash,
      expiresAt: preview.expires_at,
      mapping: preview.status === 'READY' ? adaptSourceToOutputMap(preview.source_to_output_map) : null,
    } : null,
    commit: commit ? {
      commitId: commit.commit_id,
      status: commit.status,
      jobId: commit.job_id,
      materializationStatus: commit.materialization_status,
      outputVersionId: commit.output_version?.version_id ?? null,
      outputRevisions: commit.output_revisions.map((revision) => ({
        revisionId: revision.revision_id,
        ordinal: revision.ordinal,
        sourceRevisionId: revision.source_revision_id,
        memberMode: revision.member_mode,
      })),
    } : null,
    reviewFeedback,
    successorCompositionHash: wire.data.successor_composition?.composition_hash ?? null,
    lease: wire.data.lease && { readOnly: wire.data.lease.read_only, expiresAt: wire.data.lease.expires_at },
    allowedActions: wire.data.allowed_actions,
    requestId: wire.request_id,
  };
}
