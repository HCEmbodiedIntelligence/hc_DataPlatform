import type { components } from "../../shared/api/generated/platform";

export type DashboardScope = Readonly<{
  organizationId: string;
  projectId: string;
  regionCode: string;
  timezone: string;
}>;

export const DASHBOARD_PROJECT_TIMEZONE_ASSUMPTION = "Asia/Shanghai" as const;

export type DashboardSectionStatus =
  components["schemas"]["DashboardSectionStatus"];
export type DashboardActivityEventType =
  components["schemas"]["DashboardActivityEventType"];
export type DashboardPendingKind =
  components["schemas"]["DashboardPendingItemType"];
export type DashboardPendingSeverity =
  components["schemas"]["DashboardPendingSeverity"];
export type DashboardTarget = components["schemas"]["DashboardTargetResource"];
export type DashboardPageInfo = Readonly<{
  hasNextPage: boolean;
  hasPreviousPage: boolean;
  startCursor: string | null;
  endCursor: string | null;
}>;

export type DashboardSection = Readonly<{
  status: DashboardSectionStatus;
  asOf: string | null;
  error: components["schemas"]["DashboardSectionError"] | null;
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

export type DashboardTaskListItem = Readonly<{
  taskId: string;
  taskCode: string;
  name: string;
  lifecycle: components["schemas"]["TaskLifecycle"];
  target: Readonly<{
    packageCount: number | null;
    durationSeconds: number | null;
  }> | null;
  registeredCount: number;
  receivedCount: number;
  deviceProgress: Readonly<{
    source: "DEVICE_ATTESTED_FACT";
    capturedCount: number;
    savedCount: number;
    confirmedDurationSeconds: number;
  }>;
}>;

export type DashboardTaskQc = Readonly<{
  waiting: number;
  passed: number;
  risk: number;
  rejected: number;
  duplicate?: number;
  unavailable: number;
}>;

export type DashboardTaskStage = Readonly<{
  stage: components["schemas"]["TaskProcessingStage"];
  waiting: number;
  running: number;
  succeeded: number;
  risk: number;
  isolated: number;
  blocked: number;
  failed: number;
  unavailable: number;
}>;

export type DashboardTaskStatus = Readonly<{
  asOf: string;
  section: DashboardSection;
  tasks: readonly DashboardTaskListItem[];
  pipeline: Readonly<{
    taskCount: number;
    packageCount: number;
    qc: DashboardTaskQc;
    stages: readonly DashboardTaskStage[];
    unavailableSources: readonly string[];
  }>;
  selectedTaskId: string | null;
  selected: null | Readonly<{
    task: DashboardTaskListItem;
    attainment: components["schemas"]["TaskAttainment"];
    currentStage: components["schemas"]["TaskProcessingStage"];
    currentStageLabel: string;
    nextStep: string;
    qc: DashboardTaskQc;
    standardization: Readonly<{
      waiting: number;
      aligning: number;
      alignmentFailed: number;
      lanceWriting: number;
      lanceFailed: number;
      ready: number;
      isolatedByQuality: number;
      unavailable: number;
    }>;
    stages: readonly DashboardTaskStage[];
    mainStateCounts: Readonly<Record<string, number>>;
    blockerCount: number;
    blockers: readonly Readonly<{
      reasonCode: string;
      label: string;
      category: components["schemas"]["TaskStatusBlocker"]["category"];
      count: number;
      retryable: boolean;
      deepLink: string | null;
    }>[];
    actions: readonly Readonly<{
      action: components["schemas"]["TaskStatusAction"]["action"];
      label: string;
      deepLink: string;
    }>[];
    unavailableSources: readonly string[];
  }>;
}>;
