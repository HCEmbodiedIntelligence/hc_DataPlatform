import type { DatasetVersionId } from './dataset-version';
import type { EpisodeRevisionId, EpisodeStreamId } from './episode';

const reviewDecisionIdPattern = /^review_decision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/;
const reviewFindingIdPattern = /^review_finding_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/;

declare const reviewDecisionIdBrand: unique symbol;
declare const reviewFindingIdBrand: unique symbol;

export type ReviewDecisionId = string & {
  readonly [reviewDecisionIdBrand]: 'ReviewDecisionId';
};
export type ReviewFindingId = string & {
  readonly [reviewFindingIdBrand]: 'ReviewFindingId';
};

export const REVIEW_DECISIONS = ['APPROVED', 'RETURNED'] as const;
export type ReviewDecisionValue = (typeof REVIEW_DECISIONS)[number];

export const REVIEW_FINDING_SEVERITIES = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'] as const;
export type ReviewFindingSeverity = (typeof REVIEW_FINDING_SEVERITIES)[number];

/**
 * Isolation invariant: ReviewFinding is an immutable fact created only by P07 Return.
 * It is not a ManualIssue and must never share ManualIssue IDs, statuses, DTOs,
 * query keys, capabilities, mutations, or audit event names. P11 may only read it.
 * Deliberately do not import manual-issue.ts here.
 */
export type ReviewFinding = Readonly<{
  id: ReviewFindingId;
  outputRevisionId: EpisodeRevisionId;
  episodeStreamId: EpisodeStreamId;
  startNs: string;
  endNs: string;
  findingType: string;
  severity: ReviewFindingSeverity;
  note: string;
  immutable: true;
  createdAt: string;
}>;

export type ReviewFindingInput = Readonly<Omit<ReviewFinding, 'id' | 'immutable' | 'createdAt'>>;

export type ReviewDecision = Readonly<{
  id: ReviewDecisionId;
  outputVersionId: DatasetVersionId;
  decision: ReviewDecisionValue;
  immutable: true;
  createdAt: string;
}>;

export function isReviewDecisionId(value: unknown): value is ReviewDecisionId {
  return typeof value === 'string' && reviewDecisionIdPattern.test(value);
}

export function isReviewFindingId(value: unknown): value is ReviewFindingId {
  return typeof value === 'string' && reviewFindingIdPattern.test(value);
}

export function isReviewDecisionValue(value: unknown): value is ReviewDecisionValue {
  return typeof value === 'string' && REVIEW_DECISIONS.includes(value as ReviewDecisionValue);
}

export function isReviewFindingSeverity(value: unknown): value is ReviewFindingSeverity {
  return (
    typeof value === 'string' && REVIEW_FINDING_SEVERITIES.includes(value as ReviewFindingSeverity)
  );
}

export function isReviewFinding(value: unknown): value is ReviewFinding {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Partial<ReviewFinding>;
  return (
    isReviewFindingId(candidate.id) &&
    typeof candidate.outputRevisionId === 'string' &&
    typeof candidate.episodeStreamId === 'string' &&
    typeof candidate.startNs === 'string' &&
    typeof candidate.endNs === 'string' &&
    typeof candidate.findingType === 'string' &&
    isReviewFindingSeverity(candidate.severity) &&
    typeof candidate.note === 'string' &&
    candidate.immutable === true &&
    typeof candidate.createdAt === 'string'
  );
}

export function isReviewDecision(value: unknown): value is ReviewDecision {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Partial<ReviewDecision>;
  return (
    isReviewDecisionId(candidate.id) &&
    typeof candidate.outputVersionId === 'string' &&
    isReviewDecisionValue(candidate.decision) &&
    candidate.immutable === true &&
    typeof candidate.createdAt === 'string'
  );
}
