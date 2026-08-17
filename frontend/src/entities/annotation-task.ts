export type AnnotationTaskWorkflowStatus =
  | 'QUEUED'
  | 'ASSIGNED'
  | 'IN_PROGRESS'
  | 'BLOCKED'
  | 'SUBMITTED'
  | 'RETURNED'
  | 'APPROVED'
  | 'CANCELLED';

export type AnnotationTaskSourceStatus = 'CURRENT' | 'STALE';

export type AnnotationTaskDisplayState =
  | 'UNASSIGNED'
  | 'ASSIGNED'
  | 'IN_PROGRESS'
  | 'BLOCKED'
  | 'SUBMITTED'
  | 'RETURNED'
  | 'COMPLETED'
  | 'CANCELLED'
  | 'STALE'
  | 'UNKNOWN';

const annotationTaskDisplayStateLabels: Readonly<Record<AnnotationTaskDisplayState, string>> = {
  UNASSIGNED: '未分配',
  ASSIGNED: '已分配',
  IN_PROGRESS: '进行中',
  BLOCKED: '已阻塞',
  SUBMITTED: '已提交',
  RETURNED: '已退回',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
  STALE: '已失效',
  UNKNOWN: '未知',
};

export function annotationTaskDisplayStateLabel(state: AnnotationTaskDisplayState): string {
  return annotationTaskDisplayStateLabels[state];
}

export type AnnotationTaskAction =
  | 'CLAIM'
  | 'ASSIGN'
  | 'EDIT_DRAFT'
  | 'SAVE_DRAFT'
  | 'PREFLIGHT_SUBMIT'
  | 'SUBMIT'
  | 'REVIEW'
  | 'REBASE'
  | 'REPORT_ISSUE'
  | 'VIEW_ANNOTATION_SET'
  | 'VIEW_MANUAL_ISSUE';

export interface AnnotationTaskSource {
  readonly datasetId: string;
  readonly datasetVersionId: string;
  readonly episodeId: string;
  readonly baseRevisionId: string;
  readonly streamIds: readonly string[];
  readonly startNs: string;
  readonly endNs: string;
}

export interface AnnotationTask {
  readonly id: string;
  readonly scope: { readonly organizationId: string; readonly projectId: string; readonly regionCode: string };
  readonly workflowStatus: AnnotationTaskWorkflowStatus | 'UNKNOWN';
  readonly sourceStatus: AnnotationTaskSourceStatus | 'UNKNOWN';
  readonly displayState: AnnotationTaskDisplayState;
  readonly source: AnnotationTaskSource;
  readonly ontology: { readonly id: string; readonly version: string; readonly hash: string };
  readonly priority: number;
  readonly assigneeId: string | null;
  readonly currentDraftRevision: number;
  readonly currentDraftHash: string;
  readonly etag: string;
  readonly allowedActions: ReadonlySet<AnnotationTaskAction>;
  readonly blockedReasons: readonly string[];
  readonly predecessorTaskId: string | null;
  readonly successorTaskId: string | null;
  readonly updatedAt: string;
}

export function toAnnotationTaskDisplayState(
  workflowStatus: AnnotationTask['workflowStatus'],
  sourceStatus: AnnotationTask['sourceStatus'],
): AnnotationTaskDisplayState {
  if (sourceStatus === 'STALE') return 'STALE';
  if (workflowStatus === 'QUEUED') return 'UNASSIGNED';
  if (workflowStatus === 'APPROVED') return 'COMPLETED';
  if (workflowStatus === 'UNKNOWN' || sourceStatus === 'UNKNOWN') return 'UNKNOWN';
  return workflowStatus;
}

export function taskCan(
  task: AnnotationTask,
  capabilities: ReadonlySet<string>,
  capability: string,
  action: AnnotationTaskAction,
): boolean {
  return capabilities.has(capability)
    && task.allowedActions.has(action)
    && task.sourceStatus === 'CURRENT'
    && task.workflowStatus !== 'UNKNOWN';
}
