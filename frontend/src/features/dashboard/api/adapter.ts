import type {
  DashboardActivity,
  DashboardCoverage,
  DashboardPendingItem,
  DashboardPendingKind,
  DashboardPendingPage,
  DashboardSnapshot,
  DashboardStorageRole,
} from '../types';
import type {
  DashboardActivityWire,
  DashboardCoverageWire,
  DashboardPendingPageWire,
  DashboardSnapshotWire,
} from './schemas';

const knownRoles = new Set(['RAW', 'REVISION', 'PREVIEW', 'EXPORT']);
const knownPendingKinds = new Set<DashboardPendingKind>([
  'UPLOAD_FAILED',
  'MANIFEST_VALIDATION_FAILED',
  'MANUAL_ISSUE',
  'REVIEW_WORK_ITEM',
  'CLEANING_DRAFT_ACTIONABLE',
  'STORAGE_LIFECYCLE_ALERT',
]);
const knownPriorities = new Set(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']);
const knownPendingStatuses = new Set(['FAILED', 'REVIEWING', 'RETURNED', 'EDITING', 'OPEN', 'ALERTING']);
const knownPreviewStatuses = new Set(['NONE', 'QUEUED', 'READY', 'FAILED', 'EXPIRED', 'STALE']);

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`DASHBOARD_AGGREGATE_INCONSISTENT:${message}`);
}

function ratio(numerator: bigint, denominator: bigint): number | null {
  if (denominator === 0n) return null;
  return Number((numerator * 10_000n) / denominator) / 10_000;
}

export function adaptDashboardActivity(wire: DashboardActivityWire): DashboardActivity {
  const succeededCount = BigInt(wire.data.uploads.succeeded_count);
  const terminalCount = BigInt(wire.data.uploads.terminal_count);
  invariant(succeededCount <= terminalCount, 'succeeded_count_gt_terminal_count');
  let previousEnd = wire.data.from;
  let bucketTotal = 0n;
  const buckets = wire.data.uploads.buckets.map((bucket) => {
    invariant(Date.parse(bucket.start) >= Date.parse(previousEnd), 'bucket_overlap');
    invariant(Date.parse(bucket.start) < Date.parse(bucket.end), 'bucket_range');
    invariant(Date.parse(bucket.start) >= Date.parse(wire.data.from) && Date.parse(bucket.end) <= Date.parse(wire.data.to), 'bucket_outside_window');
    previousEnd = bucket.end;
    bucketTotal += BigInt(bucket.accepted_unique_bytes);
    return {
      start: bucket.start,
      end: bucket.end,
      acceptedUniqueBytes: BigInt(bucket.accepted_unique_bytes),
      failedCount: BigInt(bucket.failed_count),
    };
  });
  const acceptedUniqueBytes = BigInt(wire.data.uploads.accepted_unique_bytes);
  invariant(bucketTotal === acceptedUniqueBytes, 'bucket_total');
  return {
    from: wire.data.from,
    to: wire.data.to,
    timezone: wire.data.timezone,
    asOf: wire.data.as_of,
    acceptedUniqueBytes,
    succeededCount,
    terminalCount,
    successRatio: ratio(succeededCount, terminalCount),
    buckets,
    requestId: wire.request_id,
  };
}

export function adaptDashboardSnapshot(wire: DashboardSnapshotWire): DashboardSnapshot {
  const seen = new Set<string>();
  let rolesTotal = 0n;
  let hasUnknownEnum = false;
  const roles = wire.data.storage.by_role.map((item) => {
    invariant(!seen.has(item.role), 'duplicate_role');
    seen.add(item.role);
    invariant(item.role !== 'ROBOT_ASSET', 'robot_asset_in_data_capacity');
    const role: DashboardStorageRole = knownRoles.has(item.role) ? item.role as DashboardStorageRole : 'UNKNOWN';
    hasUnknownEnum ||= role === 'UNKNOWN';
    const bytes = BigInt(item.bytes);
    rolesTotal += bytes;
    return { role, wireRole: item.role, bytes };
  });
  for (const role of knownRoles) invariant(seen.has(role), `missing_role_${role}`);
  const dataPhysicalBytes = BigInt(wire.data.storage.data_physical_bytes);
  invariant(rolesTotal === dataPhysicalBytes, 'role_total');
  let previousMonth = '';
  const history = wire.data.storage.history.map((item) => {
    invariant(item.month > previousMonth, 'history_order');
    previousMonth = item.month;
    const standardBytes = BigInt(item.standard_bytes);
    const iaBytes = BigInt(item.ia_bytes);
    const archiveBytes = BigInt(item.archive_bytes);
    const monthTotal = BigInt(item.data_physical_bytes);
    invariant(standardBytes + iaBytes + archiveBytes === monthTotal, 'history_total');
    return { month: item.month, standardBytes, iaBytes, archiveBytes, dataPhysicalBytes: monthTotal };
  });
  const uploadedCount = BigInt(wire.data.episodes.uploaded_count);
  const validatedCount = BigInt(wire.data.episodes.validated_count);
  const viewableCount = BigInt(wire.data.episodes.viewable_count);
  invariant(uploadedCount >= validatedCount && validatedCount >= viewableCount, 'episode_funnel');
  const returnedActionableDraftCount = BigInt(wire.data.work.returned_actionable_draft_count);
  const activeCleaningDraftCount = BigInt(wire.data.work.active_cleaning_draft_count);
  invariant(returnedActionableDraftCount <= activeCleaningDraftCount, 'returned_gt_actionable');
  return {
    timezone: wire.data.timezone,
    asOf: wire.data.as_of,
    dataPhysicalBytes,
    roles,
    history,
    episodes: { uploadedCount, validatedCount, viewableCount },
    work: {
      openManualIssueCount: BigInt(wire.data.work.open_manual_issue_count),
      pendingReviewVersionCount: BigInt(wire.data.work.pending_review_version_count),
      returnedActionableDraftCount,
      activeCleaningDraftCount,
    },
    hasUnknownEnum,
    requestId: wire.request_id,
  };
}

export function adaptDashboardCoverage(wire: DashboardCoverageWire): DashboardCoverage {
  const robotIds = new Set(wire.data.robot_groups.map((item) => item.id));
  const taskIds = new Set(wire.data.tasks.map((item) => item.id));
  invariant(robotIds.size === wire.data.robot_groups.length, 'duplicate_robot_group');
  invariant(taskIds.size === wire.data.tasks.length, 'duplicate_task');
  const pairs = new Set<string>();
  const cells = wire.data.cells.map((cell) => {
    invariant(robotIds.has(cell.robot_group_id) && taskIds.has(cell.task_id), 'orphan_cell');
    const pair = `${cell.robot_group_id}\u0000${cell.task_id}`;
    invariant(!pairs.has(pair), 'duplicate_cell');
    pairs.add(pair);
    const numerator = BigInt(cell.numerator);
    const denominator = BigInt(cell.denominator);
    if (denominator === 0n) invariant(numerator === 0n && cell.ratio === null, 'zero_denominator');
    else invariant(cell.ratio !== null && Math.abs(cell.ratio - (Number((numerator * 1_000_000n) / denominator) / 1_000_000)) <= 0.000001, 'ratio');
    return { robotGroupId: cell.robot_group_id, taskId: cell.task_id, ratio: cell.ratio, numerator, denominator };
  });
  invariant(pairs.size === robotIds.size * taskIds.size, 'missing_cell');
  return {
    timezone: wire.data.timezone,
    asOf: wire.data.as_of,
    robotGroups: wire.data.robot_groups,
    tasks: wire.data.tasks,
    cells,
    requestId: wire.request_id,
  };
}

function adaptPendingItem(item: DashboardPendingPageWire['data']['items'][number]): DashboardPendingItem {
  const kind = knownPendingKinds.has(item.type as DashboardPendingKind) ? item.type as DashboardPendingKind : 'UNKNOWN';
  let targetId: string | null = null;
  if (kind === 'UPLOAD_FAILED' || kind === 'MANIFEST_VALIDATION_FAILED') targetId = item.upload_id ?? null;
  if (kind === 'MANUAL_ISSUE') targetId = item.issue_id ?? null;
  if (kind === 'CLEANING_DRAFT_ACTIONABLE') targetId = item.draft_id ?? null;
  if (kind === 'REVIEW_WORK_ITEM') targetId = item.target?.successor_draft_id ?? item.target?.version_id ?? null;
  const unknownEnum = kind === 'UNKNOWN'
    || !knownPriorities.has(item.priority)
    || !knownPendingStatuses.has(item.status)
    || (kind === 'CLEANING_DRAFT_ACTIONABLE' && !knownPreviewStatuses.has(item.preview_status ?? ''));
  return {
    itemId: item.item_id,
    kind,
    wireType: item.type,
    title: item.title,
    summary: item.summary,
    status: item.status,
    priority: item.priority,
    updatedAt: item.updated_at,
    targetId,
    clickable: !unknownEnum && targetId !== null,
    hasUnknownEnum: unknownEnum,
  };
}

export function adaptDashboardPendingPage(wire: DashboardPendingPageWire): DashboardPendingPage {
  const items = wire.data.items.map(adaptPendingItem);
  invariant(new Set(items.map((item) => item.itemId)).size === items.length, 'duplicate_pending_item');
  invariant(BigInt(wire.data.total_count) >= BigInt(items.length), 'pending_total');
  if (items.length === 0) invariant(wire.data.page_info.start_cursor === null && wire.data.page_info.end_cursor === null, 'empty_cursor');
  return {
    asOf: wire.data.as_of,
    snapshotAt: wire.data.snapshot_at,
    totalCount: BigInt(wire.data.total_count),
    items,
    pageInfo: {
      hasNextPage: wire.data.page_info.has_next_page,
      hasPreviousPage: wire.data.page_info.has_previous_page,
      startCursor: wire.data.page_info.start_cursor,
      endCursor: wire.data.page_info.end_cursor,
    },
    hasUnknownEnum: items.some((item) => item.hasUnknownEnum),
    requestId: wire.request_id,
  };
}
