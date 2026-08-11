import {
  MANUAL_ISSUE_STATUSES,
  type KnownManualIssueStatus,
  type ManualIssueStatus,
} from '../../entities/manual-issue';

export const MANUAL_ISSUE_TRANSITIONS = {
  OPEN: ['OPEN', 'IN_PROGRESS'],
  IN_PROGRESS: ['IN_PROGRESS', 'OPEN', 'RESOLVED'],
  RESOLVED: [],
} as const satisfies Record<ManualIssueStatus, readonly ManualIssueStatus[]>;

export function isManualIssueStatus(value: string): value is ManualIssueStatus {
  return (MANUAL_ISSUE_STATUSES as readonly string[]).includes(value);
}

export function toKnownManualIssueStatus(value: string): KnownManualIssueStatus {
  return isManualIssueStatus(value)
    ? { kind: 'known', value }
    : { kind: 'unknown', raw: value, readOnly: true };
}

export function canTransitionManualIssue(
  from: KnownManualIssueStatus,
  to: ManualIssueStatus,
): boolean {
  if (from.kind === 'unknown') return false;
  return (MANUAL_ISSUE_TRANSITIONS[from.value] as readonly ManualIssueStatus[]).includes(to);
}

export function canMutateManualIssue(status: KnownManualIssueStatus): boolean {
  return status.kind === 'known' && status.value !== 'RESOLVED';
}

