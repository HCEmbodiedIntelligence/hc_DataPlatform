import type {
  DashboardActivity,
  DashboardCoverage,
  DashboardPendingPage,
  DashboardSection,
  DashboardSnapshot,
} from '../types';
import type {
  DashboardActivityWire,
  DashboardCoverageWire,
  DashboardPendingPageWire,
  DashboardSnapshotWire,
} from './schemas';

function section(value: Readonly<{
  status: DashboardSection['status'];
  as_of?: string | null;
  error?: DashboardSection['error'];
}>): DashboardSection {
  return { status: value.status, asOf: value.as_of ?? null, error: value.error ?? null };
}

function pageInfo(value: Readonly<{
  has_next_page: boolean;
  has_previous_page: boolean;
  start_cursor?: string | null;
  end_cursor?: string | null;
}> | null | undefined) {
  return value ? {
    hasNextPage: value.has_next_page,
    hasPreviousPage: value.has_previous_page,
    startCursor: value.start_cursor ?? null,
    endCursor: value.end_cursor ?? null,
  } : null;
}

export function adaptDashboardActivity(wire: DashboardActivityWire): DashboardActivity {
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

export function adaptDashboardSnapshot(wire: DashboardSnapshotWire): DashboardSnapshot {
  const pipeline = wire.sections.signal_pipeline;
  const published = pipeline.published_region;
  return {
    from: wire.from,
    to: wire.to,
    timezone: wire.timezone,
    asOf: wire.as_of,
    signalPipeline: {
      ...section(pipeline),
      stages: pipeline.stages,
      publishedRegion: {
        ...section(published),
        lineageCount: published.lineage_count ?? null,
        publicationCount: published.publication_count ?? null,
        unresolvedHistoryCount: published.unresolved_history_count,
      },
    },
    episodes: section(wire.sections.episodes),
    work: section(wire.sections.work),
  };
}

export function adaptDashboardCoverage(wire: DashboardCoverageWire): DashboardCoverage {
  return {
    from: wire.from,
    to: wire.to,
    timezone: wire.timezone,
    asOf: wire.as_of,
    section: section(wire.coverage),
  };
}

export function adaptDashboardPendingPage(wire: DashboardPendingPageWire): DashboardPendingPage {
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
