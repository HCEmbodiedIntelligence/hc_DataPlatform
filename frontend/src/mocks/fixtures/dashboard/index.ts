import type { components } from '../../../shared/api/generated/platform';

const base = {
  schema_version: '1' as const,
  project_id: 'prj_fx_01',
  region_code: 'cn-shanghai',
  from: '2026-08-04T08:00:00Z',
  to: '2026-08-05T08:00:00Z',
  timezone: 'Asia/Shanghai',
  as_of: '2026-08-05T08:00:00Z',
};
const pageInfo = { has_next_page: false, has_previous_page: false, start_cursor: null, end_cursor: null };
const ready = { status: 'READY' as const, as_of: base.as_of, error: null };
const empty = { status: 'EMPTY' as const, as_of: base.as_of, error: null };

export const dashboardActivityFixture = {
  ...base,
  activity: {
    ...ready,
    page_info: pageInfo,
    items: [
      {
        event_id: 'event-upload-1', event_type: 'UPLOAD_COMMITTED' as const,
        source_id: 'upload-1', deduplication_key: 'UPLOAD_COMMITTED:upload-1',
        occurred_at: '2026-08-05T07:58:00Z', source_state: 'RAW_COMMITTED',
        title: '上传已提交', summary: 'Raw 对象和 Manifest 已持久化',
        target: { resource_type: 'UPLOAD_SESSION' as const, resource_id: 'upload-1', resource_version: null, deep_link: '/ingest/uploads/upload-1' },
      },
      {
        event_id: 'event-publish-1', event_type: 'DATASET_PUBLISHED' as const,
        source_id: 'dataset-1:v1', deduplication_key: 'DATASET_PUBLISHED:dataset-1:v1',
        occurred_at: '2026-08-05T07:59:00Z', source_state: 'PUBLISHED',
        title: '数据集已发布', summary: '发布血缘已登记',
        target: { resource_type: 'DATASET_VERSION' as const, resource_id: 'dataset-1', resource_version: '1', deep_link: '/datasets/dataset-1/versions/1' },
      },
    ],
  },
} satisfies components['schemas']['DashboardActivityResponse'];

export const dashboardSnapshotFixture = {
  ...base,
  sections: {
    signal_pipeline: {
      ...ready,
      stages: ['COLLECTED', 'RECEIVED', 'AUTO_QC', 'ALIGNED_30_HZ', 'LANCE', 'ANNOTATION', 'REVIEW', 'PUBLISHED'],
      published_region: { ...ready, lineage_count: 8, publication_count: 3, unresolved_history_count: 0 },
    },
    episodes: ready,
    work: ready,
  },
} satisfies components['schemas']['DashboardSnapshotResponse'];

export const dashboardCoverageFixture = {
  ...base,
  coverage: {
    status: 'BLOCKED' as const,
    as_of: null,
    error: {
      code: 'COVERAGE_PRODUCT_DECISION_REQUIRED',
      message: '覆盖率口径尚未确认',
      retryable: false,
      needs_product_confirmation: true,
    },
  },
} satisfies components['schemas']['DashboardCoverageResponse'];

const pendingItem = (
  item_type: components['schemas']['DashboardPendingItemType'],
  source_id: string,
  resource_type: components['schemas']['DashboardResourceType'],
  deep_link: string,
) => ({
  item_type,
  source_id,
  deduplication_key: `${item_type}:${source_id}`,
  source_state: item_type === 'UPLOAD_FAILED' ? 'FAILED' : item_type === 'QC_ANOMALY' ? 'RISK' : item_type === 'TAG_REVIEW_PENDING' ? 'SUBMITTED' : 'APPROVED',
  severity: 'HIGH' as const,
  opened_at: '2026-08-05T07:57:00Z',
  target: { resource_type, resource_id: source_id, resource_version: null, deep_link },
});

export const dashboardPendingFixture = {
  ...base,
  pending_items: {
    ...ready,
    authorized_source_types: ['UPLOAD_FAILED', 'QC_ANOMALY', 'TAG_REVIEW_PENDING', 'PUBLICATION_PENDING'],
    page_info: pageInfo,
    items: [
      pendingItem('UPLOAD_FAILED', 'upload-1', 'UPLOAD_SESSION', '/ingest/uploads/upload-1'),
      pendingItem('QC_ANOMALY', 'rollout-1', 'ROLLOUT', '/datasets/dataset-1/rollouts/rollout-1'),
      pendingItem('TAG_REVIEW_PENDING', 'annotation-1', 'ANNOTATION_TASK', '/annotations/tasks/annotation-1'),
      pendingItem('PUBLICATION_PENDING', 'dataset-1', 'DATASET_VERSION', '/datasets/dataset-1/versions/1'),
    ],
  },
} satisfies components['schemas']['DashboardPendingItemsResponse'];

export const dashboardEmptyFixtures = {
  activity: { ...base, activity: { ...empty, page_info: pageInfo, items: [] } } satisfies components['schemas']['DashboardActivityResponse'],
  snapshot: {
    ...base,
    sections: {
      signal_pipeline: {
        ...empty,
        stages: ['COLLECTED', 'RECEIVED', 'AUTO_QC', 'ALIGNED_30_HZ', 'LANCE', 'ANNOTATION', 'REVIEW', 'PUBLISHED'],
        published_region: { ...empty, lineage_count: 0, publication_count: 0, unresolved_history_count: 0 },
      },
      episodes: empty,
      work: empty,
    },
  } satisfies components['schemas']['DashboardSnapshotResponse'],
  coverage: dashboardCoverageFixture,
  pending: { ...base, pending_items: { ...empty, authorized_source_types: [], page_info: pageInfo, items: [] } } satisfies components['schemas']['DashboardPendingItemsResponse'],
};

export const dashboardUnknownFixtures = {
  snapshot: dashboardSnapshotFixture,
  pending: dashboardPendingFixture,
};
