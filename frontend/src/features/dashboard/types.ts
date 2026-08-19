import type { components } from '../../shared/api/generated/platform';

export type DashboardScope = Readonly<{
  organizationId: string;
  projectId: string;
  regionCode: string;
  timezone: string;
}>;

export const DASHBOARD_PROJECT_TIMEZONE_ASSUMPTION = 'Asia/Shanghai' as const;

export type DashboardSectionStatus = components['schemas']['DashboardSectionStatus'];
export type DashboardSignalStage = components['schemas']['SignalStage'];
export type DashboardActivityEventType = components['schemas']['DashboardActivityEventType'];
export type DashboardPendingKind = components['schemas']['DashboardPendingItemType'];
export type DashboardPendingSeverity = components['schemas']['DashboardPendingSeverity'];
export type DashboardTarget = components['schemas']['DashboardTargetResource'];
export type DashboardPageInfo = Readonly<{
  hasNextPage: boolean;
  hasPreviousPage: boolean;
  startCursor: string | null;
  endCursor: string | null;
}>;

export type DashboardSection = Readonly<{
  status: DashboardSectionStatus;
  asOf: string | null;
  error: components['schemas']['DashboardSectionError'] | null;
}>;

export type DashboardSnapshot = Readonly<{
  from: string;
  to: string;
  timezone: string;
  asOf: string;
  signalPipeline: DashboardSection & Readonly<{
    stages: readonly DashboardSignalStage[];
    publishedRegion: DashboardSection & Readonly<{
      lineageCount: number | null;
      publicationCount: number | null;
      unresolvedHistoryCount: number;
    }>;
  }>;
  episodes: DashboardSection;
  work: DashboardSection;
}>;

export type DashboardActivityEvent = Readonly<{
  eventId: string;
  eventType: DashboardActivityEventType;
  sourceId: string;
  sourceState: string;
  occurredAt: string;
  title: string;
  summary: string;
  target: DashboardTarget;
}>;

export type DashboardActivity = Readonly<{
  from: string;
  to: string;
  timezone: string;
  asOf: string;
  section: DashboardSection;
  items: readonly DashboardActivityEvent[];
  pageInfo: DashboardPageInfo | null;
}>;

export type DashboardCoverage = Readonly<{
  from: string;
  to: string;
  timezone: string;
  asOf: string;
  section: DashboardSection;
}>;

export type DashboardPendingItem = Readonly<{
  itemId: string;
  kind: DashboardPendingKind;
  sourceState: string;
  severity: DashboardPendingSeverity;
  openedAt: string;
  target: DashboardTarget;
}>;

export type DashboardPendingPage = Readonly<{
  asOf: string;
  section: DashboardSection;
  authorizedSourceTypes: readonly DashboardPendingKind[];
  items: readonly DashboardPendingItem[];
  pageInfo: DashboardPageInfo | null;
}>;
