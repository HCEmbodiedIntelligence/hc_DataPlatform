import type {
  DashboardActivity,
  DashboardPendingPage,
  DashboardSection,
  DashboardTaskListItem,
  DashboardTaskStatus,
} from "../types";
import type {
  DashboardActivityWire,
  DashboardPendingPageWire,
  DashboardTaskStatusWire,
} from "./schemas";

function taskListItem(
  value: DashboardTaskStatusWire["tasks"][number],
): DashboardTaskListItem {
  return {
    taskId: value.task_id,
    taskCode: value.task_code,
    name: value.name,
    lifecycle: value.lifecycle,
    target: value.target
      ? {
          packageCount: value.target.package_count ?? null,
          durationSeconds: value.target.duration_seconds ?? null,
        }
      : null,
    registeredCount: value.registered_count,
    receivedCount: value.received_count,
    deviceProgress: {
      source: value.device_progress.source,
      capturedCount: value.device_progress.captured_count,
      savedCount: value.device_progress.saved_count,
      confirmedDurationSeconds:
        value.device_progress.confirmed_duration_seconds,
    },
  };
}

function section(
  value: Readonly<{
    status: DashboardSection["status"];
    as_of?: string | null;
    error?: DashboardSection["error"];
  }>,
): DashboardSection {
  return {
    status: value.status,
    asOf: value.as_of ?? null,
    error: value.error ?? null,
  };
}

function pageInfo(
  value:
    | Readonly<{
        has_next_page: boolean;
        has_previous_page: boolean;
        start_cursor?: string | null;
        end_cursor?: string | null;
      }>
    | null
    | undefined,
) {
  return value
    ? {
        hasNextPage: value.has_next_page,
        hasPreviousPage: value.has_previous_page,
        startCursor: value.start_cursor ?? null,
        endCursor: value.end_cursor ?? null,
      }
    : null;
}

export function adaptDashboardActivity(
  wire: DashboardActivityWire,
): DashboardActivity {
  return {
    from: wire.from,
    to: wire.to,
    timezone: wire.timezone,
    asOf: wire.as_of,
    section: section(wire.activity),
    pageInfo: pageInfo(wire.activity.page_info),
    items: wire.activity.items.map((item) => ({
      eventId: item.event_id,
      eventType: item.event_type,
      sourceId: item.source_id,
      sourceState: item.source_state,
      occurredAt: item.occurred_at,
      title: item.title,
      summary: item.summary,
      target: item.target,
    })),
  };
}

export function adaptDashboardPendingPage(
  wire: DashboardPendingPageWire,
): DashboardPendingPage {
  return {
    asOf: wire.as_of,
    section: section(wire.pending_items),
    authorizedSourceTypes: wire.pending_items.authorized_source_types,
    pageInfo: pageInfo(wire.pending_items.page_info),
    items: wire.pending_items.items.map((item) => ({
      itemId: item.deduplication_key,
      kind: item.item_type,
      sourceState: item.source_state,
      severity: item.severity,
      openedAt: item.opened_at,
      target: item.target,
    })),
  };
}

export function adaptDashboardTaskStatus(
  wire: DashboardTaskStatusWire,
): DashboardTaskStatus {
  const selected = wire.selected;
  return {
    asOf: wire.as_of,
    section: section(wire.section),
    tasks: wire.tasks.map(taskListItem),
    pipeline: {
      taskCount: wire.pipeline.task_count,
      packageCount: wire.pipeline.package_count,
      qc: {
        waiting: wire.pipeline.qc.waiting,
        passed: wire.pipeline.qc.passed,
        risk: wire.pipeline.qc.risk,
        rejected: wire.pipeline.qc.rejected,
        duplicate: wire.pipeline.qc.duplicate ?? 0,
        unavailable: wire.pipeline.qc.unavailable,
      },
      stages: wire.pipeline.stages.map((stage) => ({
        ...stage,
        isolated: stage.isolated ?? 0,
      })),
      unavailableSources: wire.pipeline.unavailable_sources,
    },
    selectedTaskId: wire.selected_task_id ?? null,
    selected: selected
      ? {
          task: taskListItem(selected.task),
          attainment: selected.attainment,
          currentStage: selected.current_stage,
          currentStageLabel: selected.current_stage_label,
          nextStep: selected.next_step,
          qc: {
            waiting: selected.qc.waiting,
            passed: selected.qc.passed,
            risk: selected.qc.risk,
            rejected: selected.qc.rejected,
            duplicate: selected.qc.duplicate ?? 0,
            unavailable: selected.qc.unavailable,
          },
          standardization: {
            waiting: selected.standardization.waiting,
            aligning: selected.standardization.aligning,
            alignmentFailed: selected.standardization.alignment_failed,
            lanceWriting: selected.standardization.lance_writing,
            lanceFailed: selected.standardization.lance_failed,
            ready: selected.standardization.ready,
            isolatedByQuality:
              selected.standardization.isolated_by_quality ?? 0,
            unavailable: selected.standardization.unavailable,
          },
          stages: selected.stages.map((stage) => ({
            ...stage,
            isolated: stage.isolated ?? 0,
          })),
          mainStateCounts: selected.main_state_counts,
          blockerCount: selected.blocker_count,
          blockers: selected.blockers.map((item) => ({
            reasonCode: item.reason_code,
            label: item.label,
            category: item.category,
            count: item.count,
            retryable: item.retryable,
            deepLink: item.deep_link ?? null,
          })),
          actions: selected.actions.map((item) => ({
            action: item.action,
            label: item.label,
            deepLink: item.deep_link,
          })),
          unavailableSources: selected.unavailable_sources,
        }
      : null,
  };
}
