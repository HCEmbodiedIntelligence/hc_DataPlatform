import {
  asManualIssueId,
  projectManualIssueStatus,
  type ManualIssue,
  type ManualIssueListItem,
  type ManualIssuePage,
} from '../../../entities/manual-issue';
import { assertCleaningScope, type ExpectedCleaningScope } from './wire-common';
import {
  manualIssueDetailEnvelopeWireSchema,
  manualIssueListEnvelopeWireSchema,
  type ManualIssueListItemWire,
  type ManualIssueWire,
} from './manual-issues.schemas';

export function adaptManualIssueWire(wire: ManualIssueWire): ManualIssue {
  return {
    id: asManualIssueId(wire.id),
    etag: wire.etag,
    scope: {
      organizationId: wire.scope.organization_id,
      projectId: wire.scope.project_id,
      regionCode: wire.scope.region_code,
    },
    source: {
      datasetId: wire.dataset_id,
      versionId: wire.origin_dataset_version_id,
      episodeId: wire.episode_id,
      revisionId: wire.episode_revision_id,
      streamId: wire.episode_stream_id,
      schemaSnapshotId: wire.schema_snapshot_id,
      robotModelVersionId: wire.robot_model_version_id,
      calibrationSetId: wire.calibration_set_id,
      startNs: wire.start_ns,
      endNs: wire.end_ns,
    },
    issueType: wire.issue_type,
    severity: wire.severity,
    status: projectManualIssueStatus(wire.status),
    note: wire.note,
    assignee: wire.assignee && { id: wire.assignee.id, displayName: wire.assignee.display_name },
    relatedDrafts: wire.related_drafts.map((draft) => ({
      draftId: draft.draft_id,
      status: draft.status,
      updatedAt: draft.updated_at,
    })),
    resolutionVersion: wire.resolution_version && {
      versionId: wire.resolution_version.version_id,
      producerDraftId: wire.resolution_version.producer_draft_id,
      rootIssueDraftId: wire.resolution_version.root_issue_draft_id,
      lineageDepth: wire.resolution_version.lineage_depth,
      resolvedAt: wire.resolution_version.resolved_at,
    },
    allowedActions: wire.allowed_actions,
    blockedReasons: wire.blocked_reasons,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
  };
}

export function adaptManualIssueEnvelope(raw: unknown, expectedScope: ExpectedCleaningScope): ManualIssue {
  const wire = manualIssueDetailEnvelopeWireSchema.parse(raw);
  assertCleaningScope(wire.scope, expectedScope);
  assertCleaningScope(wire.data.scope, expectedScope);
  return adaptManualIssueWire(wire.data);
}

export function adaptManualIssueListItem(
  wire: ManualIssueListItemWire,
): ManualIssueListItem {
  return {
    id: asManualIssueId(wire.id),
    etag: wire.etag,
    scope: {
      organizationId: wire.scope.organization_id,
      projectId: wire.scope.project_id,
      regionCode: wire.scope.region_code,
    },
    source: {
      datasetId: wire.dataset_id,
      versionId: wire.origin_dataset_version_id,
      episodeId: wire.episode_id,
      revisionId: wire.episode_revision_id,
      streamId: wire.episode_stream_id,
      startNs: wire.start_ns,
      endNs: wire.end_ns,
    },
    issueType: wire.issue_type,
    severity: wire.severity,
    status: projectManualIssueStatus(wire.status),
    assignee: wire.assignee && { id: wire.assignee.id, displayName: wire.assignee.display_name },
    relatedDraftCount: wire.related_draft_count,
    resolutionVersion: wire.resolution_version && {
      versionId: wire.resolution_version.version_id,
      producerDraftId: wire.resolution_version.producer_draft_id,
      rootIssueDraftId: wire.resolution_version.root_issue_draft_id,
      lineageDepth: wire.resolution_version.lineage_depth,
      resolvedAt: wire.resolution_version.resolved_at,
    },
    allowedActions: wire.allowed_actions,
    updatedAt: wire.updated_at,
  };
}

export function adaptManualIssueListEnvelope(raw: unknown, expectedScope: ExpectedCleaningScope): ManualIssuePage {
  const wire = manualIssueListEnvelopeWireSchema.parse(raw);
  assertCleaningScope(wire.scope, expectedScope);
  wire.items.forEach((item) => assertCleaningScope(item.scope, expectedScope));
  return {
    items: wire.items.map(adaptManualIssueListItem),
    pageInfo: {
      after: wire.page_info.after,
      before: wire.page_info.before,
      hasNext: wire.page_info.has_next,
      hasPrevious: wire.page_info.has_previous,
    },
    snapshotAt: wire.snapshot_at,
    requestId: wire.request_id,
  };
}
