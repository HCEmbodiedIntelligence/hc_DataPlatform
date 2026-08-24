import type { ManualIssueId } from './manual-issue';

declare const cleaningDraftIdBrand: unique symbol;

export type CleaningDraftId = string & { readonly [cleaningDraftIdBrand]: 'CleaningDraftId' };

export const CLEANING_DRAFT_STATUSES = ['EDITING', 'COMMITTED'] as const;
export type CleaningDraftStatus = (typeof CLEANING_DRAFT_STATUSES)[number];

export const CLEANING_PREVIEW_STATUSES = [
  'NONE',
  'QUEUED',
  'RUNNING',
  'READY',
  'FAILED',
  'EXPIRED',
  'STALE',
] as const;
export type CleaningPreviewStatus = (typeof CLEANING_PREVIEW_STATUSES)[number];

export const CLEANING_COMMIT_STATUSES = ['NONE', 'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED'] as const;
export type CleaningCommitStatus = (typeof CLEANING_COMMIT_STATUSES)[number];

export const CLEANING_OUTPUT_VERSION_STATUSES = ['REVIEWING', 'READY', 'RETURNED'] as const;
export type CleaningOutputVersionStatus = (typeof CLEANING_OUTPUT_VERSION_STATUSES)[number];

export interface IssueDerivedCleaningDraftOrigin {
  readonly kind: 'ISSUE_DERIVED';
  readonly schemaVersion: 1;
  readonly datasetId: string;
  readonly baseVersionId: string;
  readonly episodeId: string;
  readonly baseRevisionId: string;
  readonly selectedStreamId: string;
  readonly selectedChannelPath: string | null;
  readonly startNs: string;
  readonly endNs: string;
  /** manual-cleaning.v1 requires exactly one P09-owned ManualIssue. */
  readonly manualIssueIds: readonly [ManualIssueId];
}

export interface ReviewReturnCleaningDraftOrigin {
  readonly kind: 'REVIEW_RETURN';
  readonly supersedesDraftId: CleaningDraftId;
  readonly returnedFromVersionId: string;
  /** Opaque P07 reference; it is not a shared Review DTO or mutation identity. */
  readonly returnedFromReviewDecisionId: string;
}

export type CleaningDraftOrigin =
  | IssueDerivedCleaningDraftOrigin
  | ReviewReturnCleaningDraftOrigin;

/** P07-owned ReviewFinding facts are represented here by counts/IDs only. */
export interface ReadonlyReviewProjection {
  readonly reviewDecisionId: string;
  readonly findingIds: readonly string[];
  readonly findingCount: string;
  readonly successorDraftId: CleaningDraftId;
}

export interface CleaningDraft {
  readonly id: CleaningDraftId;
  readonly etag: string;
  readonly status: CleaningDraftStatus | 'UNKNOWN';
  readonly origin: CleaningDraftOrigin;
  readonly baseVersionId: string;
  readonly baseRevisionId: string;
  readonly episodeId: string;
  readonly previewStatus: CleaningPreviewStatus | 'UNKNOWN';
  readonly commitStatus: CleaningCommitStatus | 'UNKNOWN';
  readonly outputVersionStatus: CleaningOutputVersionStatus | 'UNKNOWN' | null;
  readonly hasUnknownState: boolean;
  readonly review: ReadonlyReviewProjection | null;
  readonly allowedActions: readonly string[];
  readonly createdAt: string;
  readonly updatedAt: string;
}

export function asCleaningDraftId(value: string): CleaningDraftId {
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)) {
    throw new TypeError('Invalid CleaningDraft ID');
  }
  return value as CleaningDraftId;
}
