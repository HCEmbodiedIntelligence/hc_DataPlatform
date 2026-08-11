/**
 * Cross-page destinations whose owner builders are not available yet.
 * Keeping them here prevents provisional paths from leaking into page components.
 */
export const pendingDashboardLinks = {
  uploads: null,
  uploadDetail: null,
  versionReview: null,
  manualIssues: null,
  cleaningDrafts: null,
  cleaningWorkbench: null,
  lifecycle: null,
} as const;

export type PendingDashboardLink = keyof typeof pendingDashboardLinks;
