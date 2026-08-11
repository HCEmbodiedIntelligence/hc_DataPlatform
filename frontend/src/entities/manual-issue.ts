/**
 * ManualIssue is a P09-owned, triageable fact discovered before cleaning or
 * independently of review. It must never share IDs, status, DTOs, query keys,
 * capabilities, mutations, or audit events with P07-owned ReviewFinding.
 *
 * Deliberately do not import `review-finding.ts` from this module.
 */

declare const manualIssueIdBrand: unique symbol;

export type ManualIssueId = string & { readonly [manualIssueIdBrand]: 'ManualIssueId' };

export const MANUAL_ISSUE_STATUSES = ['OPEN', 'IN_PROGRESS', 'RESOLVED'] as const;
export type ManualIssueStatus = (typeof MANUAL_ISSUE_STATUSES)[number];

export const MANUAL_ISSUE_SEVERITIES = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'] as const;
export type ManualIssueSeverity = (typeof MANUAL_ISSUE_SEVERITIES)[number];

export const MANUAL_ISSUE_TYPES = [
  'POSE_JITTER',
  'TIMESTAMP_DRIFT',
  'MISSING_FRAME',
  'STREAM_GAP',
  'CALIBRATION_MISMATCH',
  'INVALID_MASK',
  'OTHER',
] as const;
export type ManualIssueType = (typeof MANUAL_ISSUE_TYPES)[number];

export const MANUAL_ISSUE_ALLOWED_ACTIONS = [
  'VIEW_EPISODE',
  'TRIAGE',
  'START_WORK',
  'CREATE_DRAFT',
  'CONTINUE_DRAFT',
  'RESOLVE',
  'PREVIEW_RANGE',
] as const;
export type ManualIssueAllowedAction = (typeof MANUAL_ISSUE_ALLOWED_ACTIONS)[number];

export type KnownManualIssueStatus =
  | { readonly kind: 'known'; readonly value: ManualIssueStatus }
  | { readonly kind: 'unknown'; readonly raw: string; readonly readOnly: true };

export interface ManualIssueScope {
  readonly organizationId: string;
  readonly projectId: string;
  readonly regionCode: string;
}

export interface ManualIssueSource {
  readonly datasetId: string;
  readonly versionId: string;
  readonly episodeId: string;
  readonly revisionId: string;
  readonly streamId: string;
  readonly schemaSnapshotId: string;
  readonly robotModelVersionId: string | null;
  readonly calibrationSetId: string | null;
  readonly startNs: string;
  readonly endNs: string;
}

export interface ManualIssueDraftRef {
  readonly draftId: string;
  readonly status: 'EDITING' | 'COMMITTED';
  readonly updatedAt: string;
}

export interface ManualIssue {
  readonly id: ManualIssueId;
  readonly etag: string;
  readonly scope: ManualIssueScope;
  readonly source: ManualIssueSource;
  readonly issueType: ManualIssueType;
  readonly severity: ManualIssueSeverity;
  readonly status: KnownManualIssueStatus;
  readonly note: string;
  readonly assignee: { readonly id: string; readonly displayName: string } | null;
  readonly relatedDrafts: readonly ManualIssueDraftRef[];
  readonly resolutionVersion: {
    readonly versionId: string;
    readonly producerDraftId: string;
    readonly rootIssueDraftId: string;
    readonly lineageDepth: string;
    readonly resolvedAt: string;
  } | null;
  readonly allowedActions: readonly ManualIssueAllowedAction[];
  readonly blockedReasons: readonly { readonly code: string; readonly message: string }[];
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface ManualIssueListItem {
  readonly id: ManualIssueId;
  readonly etag: string;
  readonly scope: ManualIssueScope;
  readonly source: Pick<ManualIssueSource, 'datasetId' | 'versionId' | 'episodeId' | 'revisionId' | 'streamId' | 'startNs' | 'endNs'>;
  readonly issueType: ManualIssueType;
  readonly severity: ManualIssueSeverity;
  readonly status: KnownManualIssueStatus;
  readonly assignee: { readonly id: string; readonly displayName: string } | null;
  readonly relatedDraftCount: string;
  readonly resolutionVersion: ManualIssue['resolutionVersion'];
  readonly allowedActions: readonly ManualIssueAllowedAction[];
  readonly updatedAt: string;
}

export interface ManualIssuePage {
  readonly items: readonly ManualIssueListItem[];
  readonly pageInfo: {
    readonly after: string | null;
    readonly before: string | null;
    readonly hasNext: boolean;
    readonly hasPrevious: boolean;
  };
  readonly snapshotAt: string;
  readonly requestId: string;
}

export function asManualIssueId(value: string): ManualIssueId {
  // manual-cleaning.v1 IDs are opaque. Prefixes are fixture/debug conventions,
  // never a browser-side discriminator between ManualIssue and ReviewFinding.
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)) {
    throw new TypeError('Invalid ManualIssue ID');
  }
  return value as ManualIssueId;
}

export function projectManualIssueStatus(value: string): KnownManualIssueStatus {
  return (MANUAL_ISSUE_STATUSES as readonly string[]).includes(value)
    ? { kind: 'known', value: value as ManualIssueStatus }
    : { kind: 'unknown', raw: value, readOnly: true };
}
