import {
  CLEANING_COMMIT_STATUSES,
  CLEANING_DRAFT_STATUSES,
  CLEANING_OUTPUT_VERSION_STATUSES,
  CLEANING_PREVIEW_STATUSES,
  asCleaningDraftId,
  type CleaningDraft,
  type CleaningDraftOrigin,
  type ReadonlyReviewProjection,
} from '../../../entities/cleaning-draft';
import { asManualIssueId } from '../../../entities/manual-issue';
import type { CleaningDraftListItemWire, CleaningDraftWire } from './cleaning-drafts.schemas';
import { adaptCleaningOperation } from './edl.adapter';
import {
  cleaningDraftDetailEnvelopeWireSchema,
  cleaningDraftEventsEnvelopeWireSchema,
  cleaningDraftListEnvelopeWireSchema,
  cleaningDraftSummaryEnvelopeWireSchema,
} from './cleaning-drafts.schemas';
import { assertCleaningScope, type ExpectedCleaningScope } from './wire-common';

function known<T extends string>(value: string, catalog: readonly T[]): T | 'UNKNOWN' {
  return (catalog as readonly string[]).includes(value) ? value as T : 'UNKNOWN';
}

export function adaptCleaningDraftEventsEnvelope(
  raw: unknown,
  expectedScope: ExpectedCleaningScope,
) {
  const wire = cleaningDraftEventsEnvelopeWireSchema.parse(raw);
  assertCleaningScope(wire.scope, expectedScope);
  return {
    items: wire.items.map((event) => ({
      eventId: event.event_id,
      eventType: event.event_type,
      occurredAt: event.occurred_at,
      result: event.result,
      safeSummary: event.safe_summary,
      requestId: event.request_id,
    })),
    snapshotAt: wire.snapshot_at,
    requestId: wire.request_id,
  };
}

function adaptOrigin(wire: CleaningDraftWire['origin']): CleaningDraftOrigin {
  if (wire.origin_type === 'ISSUE_DERIVED') {
    const context = wire.manual_issue_context;
    return {
      kind: 'ISSUE_DERIVED',
      schemaVersion: 1,
      datasetId: context.dataset_id,
      baseVersionId: context.base_version_id,
      episodeId: context.episode_id,
      baseRevisionId: context.base_revision_id,
      selectedStreamId: context.selected_stream_id,
      selectedChannelPath: context.selected_channel_path,
      startNs: context.start_ns,
      endNs: context.end_ns,
      manualIssueIds: [asManualIssueId(context.manual_issue_ids[0])],
    };
  }
  return {
    kind: 'REVIEW_RETURN',
    supersedesDraftId: asCleaningDraftId(wire.review_return_lineage.supersedes_draft_id),
    returnedFromVersionId: wire.review_return_lineage.returned_from_version_id,
    returnedFromReviewDecisionId: wire.review_return_lineage.returned_from_review_decision_id,
  };
}

function adaptReview(
  wire: CleaningDraftWire | CleaningDraftListItemWire,
): ReadonlyReviewProjection | null {
  const summary = wire.review_summary;
  if (!summary) return null;
  return {
    reviewDecisionId: summary.review_decision_id,
    findingIds: summary.review_finding_ids,
    findingCount: summary.finding_count,
    successorDraftId: asCleaningDraftId(summary.successor_draft_id),
  };
}

export function adaptCleaningDraftWire(wire: CleaningDraftWire): CleaningDraft {
  const status = known(wire.status, CLEANING_DRAFT_STATUSES);
  const previewStatus = known(wire.preview_status, CLEANING_PREVIEW_STATUSES);
  const commitStatus = known(wire.commit_status, CLEANING_COMMIT_STATUSES);
  const outputVersionStatus = wire.output_version_status === null
    ? null
    : known(wire.output_version_status, CLEANING_OUTPUT_VERSION_STATUSES);
  const hasUnknownState = [status, previewStatus, commitStatus, outputVersionStatus].includes('UNKNOWN');
  return {
    id: asCleaningDraftId(wire.draft_id),
    etag: wire.etag,
    status,
    origin: adaptOrigin(wire.origin),
    baseVersionId: wire.base_version_id,
    baseRevisionId: wire.base_revision_id,
    episodeId: wire.episode_id,
    previewStatus,
    commitStatus,
    outputVersionStatus,
    hasUnknownState,
    review: adaptReview(wire),
    allowedActions: hasUnknownState ? [] : wire.allowed_actions,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
  };
}

export function adaptCleaningDraftListItemWire(wire: CleaningDraftListItemWire): Omit<CleaningDraft, 'createdAt'> {
  const synthesized: CleaningDraftWire = { ...wire, created_at: wire.updated_at };
  const draft = adaptCleaningDraftWire(synthesized);
  const { createdAt, ...listItem } = draft;
  void createdAt;
  return listItem;
}

export function adaptCleaningDraftListEnvelope(raw: unknown, expectedScope: ExpectedCleaningScope) {
  const wire = cleaningDraftListEnvelopeWireSchema.parse(raw);
  assertCleaningScope(wire.scope, expectedScope);
  return {
    items: wire.items.map(adaptCleaningDraftListItemWire),
    pageInfo: {
      after: wire.page_info.after,
      before: wire.page_info.before,
      hasNext: wire.page_info.has_next,
      hasPrevious: wire.page_info.has_previous,
    },
    snapshotAt: wire.snapshot_at,
    querySignature: wire.query_signature,
    requestId: wire.request_id,
  };
}

export function adaptCleaningDraftSummaryEnvelope(raw: unknown, expectedScope: ExpectedCleaningScope) {
  const wire = cleaningDraftSummaryEnvelopeWireSchema.parse(raw);
  assertCleaningScope(wire.scope, expectedScope);
  return {
    scopeCounts: {
      editing: wire.data.scope_counts.EDITING,
      committed: wire.data.scope_counts.COMMITTED,
      returned: wire.data.scope_counts.RETURNED,
      reviewing: wire.data.scope_counts.REVIEWING,
    },
    metrics: {
      activeDraftCount: wire.data.metrics.active_draft_count,
      manualIssueDerivedCount: wire.data.metrics.manual_issue_derived_count,
      reviewReturnCount: wire.data.metrics.review_return_count,
    },
    jobs: {
      previewQueued: wire.data.jobs.preview_queued,
      previewRunning: wire.data.jobs.preview_running,
      commitQueued: wire.data.jobs.commit_queued,
    },
    asOf: wire.data.as_of,
    requestId: wire.request_id,
  };
}

export function adaptCleaningDraftDetailEnvelope(
  raw: unknown,
  expectedScope: ExpectedCleaningScope,
  expectedDraftId: string,
) {
  const wire = cleaningDraftDetailEnvelopeWireSchema.parse(raw);
  assertCleaningScope(wire.scope, expectedScope);
  if (wire.data.draft.draft_id !== expectedDraftId) throw new Error('RESOURCE_ID_MISMATCH');
  const draft = adaptCleaningDraftWire(wire.data.draft);
  return {
    draft,
    orderedOperations: wire.data.ordered_operations.map(adaptCleaningOperation),
    relationships: {
      commitId: wire.data.relationships.commit_id,
      outputVersionId: wire.data.relationships.output_version_id,
      outputRevisionIds: wire.data.relationships.output_revision_ids,
      reviewDecisionId: wire.data.relationships.review_decision_id,
      reviewFindingIds: wire.data.relationships.review_finding_ids,
      successorDraftId: wire.data.relationships.successor_draft_id,
      supersedesDraftId: wire.data.relationships.supersedes_draft_id,
      returnedFromVersionId: wire.data.relationships.returned_from_version_id,
      returnedFromReviewDecisionId: wire.data.relationships.returned_from_review_decision_id,
    },
    requestId: wire.request_id,
  };
}
