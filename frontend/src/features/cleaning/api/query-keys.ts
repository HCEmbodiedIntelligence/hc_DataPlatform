import { makeQueryKey } from '../../../shared/api/query-keys';

export const manualIssueKeys = {
  page: (filters: Readonly<Record<string, unknown>>) =>
    makeQueryKey('manual-issues', 'page', filters),
  list: (filters: Readonly<Record<string, unknown>>, cursor?: string) =>
    makeQueryKey('manual-issues', 'list', filters, cursor),
  detail: (manualIssueId: string) =>
    makeQueryKey('manual-issues', 'detail', manualIssueId),
  preview: (manualIssueId: string, resourceVersion: string) =>
    makeQueryKey('manual-issues', 'preview', { manualIssueId, resourceVersion }),
} as const;

export const cleaningDraftKeys = {
  summary: (filters: Readonly<Record<string, unknown>>) =>
    makeQueryKey('cleaning', 'draft-summary', filters),
  list: (filters: Readonly<Record<string, unknown>>, cursor?: string) =>
    makeQueryKey('cleaning', 'draft-list', filters, cursor),
  detail: (draftId: string) => makeQueryKey('cleaning', 'draft-detail', draftId),
  events: (draftId: string, limit: 5 | 10 | 20) =>
    makeQueryKey('cleaning', 'draft-events', { draftId, limit }),
  bootstrap: (draftId: string) => makeQueryKey('cleaning', 'bootstrap', draftId),
  edl: (draftId: string, revision: string, operationHash: string) =>
    makeQueryKey('cleaning', 'edl', { draftId, revision, operationHash }),
  preview: (draftId: string, previewId: string, revision: string, operationHash: string) =>
    makeQueryKey('cleaning', 'preview', { draftId, previewId, revision, operationHash }),
  commit: (draftId: string, commitId: string) =>
    makeQueryKey('cleaning', 'commit', { draftId, commitId }),
} as const;

/** P07 immutable ReviewFinding cache uses its own root and is never mutation-invalidated here. */
export const readonlyReviewFindingKeys = {
  decision: (decisionId: string) => makeQueryKey('review-findings', 'decision', decisionId),
} as const;
