export type DashboardScope = Readonly<{
  organizationId: string;
  projectId: string;
  regionCode: string;
  timezone: string;
}>;

// The current shared Scope omits the project timezone. Keep the temporary value
// centralized until T1 exposes the frozen project-timezone field.
export const DASHBOARD_PROJECT_TIMEZONE_ASSUMPTION = 'Asia/Shanghai' as const;

export type DashboardDatasetRef = Readonly<{
  datasetId: string;
  versionId?: string;
}>;

export type DashboardManualIssueRef = Readonly<{
  issueId: string;
  versionId: string;
  episodeId: string;
}>;

export type DashboardActivity = Readonly<{
  from: string;
  to: string;
  timezone: string;
  asOf: string;
  acceptedUniqueBytes: bigint;
  succeededCount: bigint;
  terminalCount: bigint;
  successRatio: number | null;
  buckets: readonly Readonly<{
    start: string;
    end: string;
    acceptedUniqueBytes: bigint;
    failedCount: bigint;
  }>[];
  requestId: string;
}>;

export type DashboardStorageRole = 'RAW' | 'REVISION' | 'PREVIEW' | 'EXPORT' | 'UNKNOWN';

export type DashboardSnapshot = Readonly<{
  timezone: string;
  asOf: string;
  dataPhysicalBytes: bigint;
  roles: readonly Readonly<{ role: DashboardStorageRole; wireRole: string; bytes: bigint }>[];
  history: readonly Readonly<{
    month: string;
    standardBytes: bigint;
    iaBytes: bigint;
    archiveBytes: bigint;
    dataPhysicalBytes: bigint;
  }>[];
  episodes: Readonly<{ uploadedCount: bigint; validatedCount: bigint; viewableCount: bigint }>;
  work: Readonly<{
    openManualIssueCount: bigint;
    pendingReviewVersionCount: bigint;
    returnedActionableDraftCount: bigint;
    activeCleaningDraftCount: bigint;
  }>;
  hasUnknownEnum: boolean;
  requestId: string;
}>;

export type DashboardCoverage = Readonly<{
  timezone: string;
  asOf: string;
  robotGroups: readonly Readonly<{ id: string; name: string; order: number }>[];
  tasks: readonly Readonly<{ id: string; name: string; order: number }>[];
  cells: readonly Readonly<{
    robotGroupId: string;
    taskId: string;
    ratio: number | null;
    numerator: bigint;
    denominator: bigint;
  }>[];
  requestId: string;
}>;

export type DashboardPendingKind =
  | 'UPLOAD_FAILED'
  | 'MANIFEST_VALIDATION_FAILED'
  | 'MANUAL_ISSUE'
  | 'REVIEW_WORK_ITEM'
  | 'CLEANING_DRAFT_ACTIONABLE'
  | 'STORAGE_LIFECYCLE_ALERT'
  | 'UNKNOWN';

export type DashboardPendingItem = Readonly<{
  itemId: string;
  kind: DashboardPendingKind;
  wireType: string;
  title: string;
  summary: string | null;
  status: string;
  priority: string;
  updatedAt: string;
  targetId: string | null;
  clickable: boolean;
  hasUnknownEnum: boolean;
}>;

export type DashboardPendingPage = Readonly<{
  asOf: string;
  snapshotAt: string;
  totalCount: bigint;
  items: readonly DashboardPendingItem[];
  pageInfo: Readonly<{
    hasNextPage: boolean;
    hasPreviousPage: boolean;
    startCursor: string | null;
    endCursor: string | null;
  }>;
  hasUnknownEnum: boolean;
  requestId: string;
}>;
