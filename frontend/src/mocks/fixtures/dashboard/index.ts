import type { components } from "../../../shared/api/generated/platform";

const base = {
  schema_version: "1" as const,
  project_id: "prj_fx_01",
  region_code: "cn-shanghai",
  from: "2026-08-04T08:00:00Z",
  to: "2026-08-05T08:00:00Z",
  timezone: "Asia/Shanghai",
  as_of: "2026-08-05T08:00:00Z",
};
const pageInfo = {
  has_next_page: false,
  has_previous_page: false,
  start_cursor: null,
  end_cursor: null,
};
const ready = { status: "READY" as const, as_of: base.as_of, error: null };
const empty = { status: "EMPTY" as const, as_of: base.as_of, error: null };

function taskStage(
  stage: components["schemas"]["TaskProcessingStage"],
  changes: Partial<
    Omit<components["schemas"]["TaskStageCounts"], "stage">
  > = {},
): components["schemas"]["TaskStageCounts"] {
  return {
    stage,
    waiting: 0,
    running: 0,
    succeeded: 0,
    risk: 0,
    isolated: 0,
    blocked: 0,
    failed: 0,
    unavailable: 0,
    ...changes,
  };
}

const dashboardTaskPipelineStages = [
  taskStage("TASK_EXECUTION", { succeeded: 12 }),
  taskStage("PACKAGE_UPLOAD", { succeeded: 12 }),
  taskStage("RAW_RECEIPT", { succeeded: 12 }),
  taskStage("AUTOMATIC_VALIDATION", {
    waiting: 1,
    succeeded: 8,
    isolated: 3,
  }),
  taskStage("STANDARDIZATION", {
    waiting: 2,
    running: 2,
    succeeded: 3,
    isolated: 3,
    failed: 2,
  }),
  taskStage("ANNOTATION", {
    waiting: 6,
    running: 2,
    succeeded: 1,
    isolated: 3,
  }),
  taskStage("REVIEW", { waiting: 8, succeeded: 1, isolated: 3 }),
  taskStage("PUBLICATION", { waiting: 8, succeeded: 1, isolated: 3 }),
];

const dashboardEmptyTaskPipelineStages = [
  taskStage("TASK_EXECUTION"),
  taskStage("PACKAGE_UPLOAD"),
  taskStage("RAW_RECEIPT"),
  taskStage("AUTOMATIC_VALIDATION"),
  taskStage("STANDARDIZATION"),
  taskStage("ANNOTATION"),
  taskStage("REVIEW"),
  taskStage("PUBLICATION"),
];

export const dashboardActivityFixture = {
  ...base,
  activity: {
    ...ready,
    page_info: pageInfo,
    items: [
      {
        event_id: "event-upload-1",
        event_type: "UPLOAD_COMMITTED" as const,
        source_id: "upload-1",
        deduplication_key: "UPLOAD_COMMITTED:upload-1",
        occurred_at: "2026-08-05T07:58:00Z",
        source_state: "RAW_COMMITTED",
        title: "上传已提交",
        summary: "Raw 对象和数据清单已持久化",
        target: {
          resource_type: "UPLOAD_SESSION" as const,
          resource_id: "upload-1",
          resource_version: null,
          deep_link: "/ingest/uploads/upload-1",
        },
      },
      {
        event_id: "event-publish-1",
        event_type: "DATASET_PUBLISHED" as const,
        source_id: "dataset-1:v1",
        deduplication_key: "DATASET_PUBLISHED:dataset-1:v1",
        occurred_at: "2026-08-05T07:59:00Z",
        source_state: "PUBLISHED",
        title: "数据集已发布",
        summary: "发布血缘已登记",
        target: {
          resource_type: "DATASET_VERSION" as const,
          resource_id: "dataset-1",
          resource_version: "1",
          deep_link: "/datasets/dataset-1/versions/1",
        },
      },
    ],
  },
} satisfies components["schemas"]["DashboardActivityResponse"];

export const dashboardSnapshotFixture = {
  ...base,
  sections: {
    signal_pipeline: {
      ...ready,
      stages: [
        "COLLECTED",
        "RECEIVED",
        "AUTO_QC",
        "ALIGNED_30_HZ",
        "LANCE",
        "ANNOTATION",
        "REVIEW",
        "PUBLISHED",
      ],
      stage_counts: [
        { stage: "COLLECTED" as const, count: 1248 },
        { stage: "RECEIVED" as const, count: 1106 },
        { stage: "AUTO_QC" as const, count: 1062 },
        { stage: "ALIGNED_30_HZ" as const, count: 1030 },
        { stage: "LANCE" as const, count: 908 },
        { stage: "ANNOTATION" as const, count: 75 },
        { stage: "REVIEW" as const, count: 24 },
        { stage: "PUBLISHED" as const, count: 8 },
      ],
      published_region: {
        ...ready,
        lineage_count: 8,
        publication_count: 3,
        unresolved_history_count: 0,
      },
    },
    episodes: ready,
    work: ready,
  },
} satisfies components["schemas"]["DashboardSnapshotResponse"];

export const dashboardTaskStatusFixture = {
  schema_version: "1" as const,
  project_id: base.project_id,
  region_code: base.region_code,
  as_of: base.as_of,
  section: ready,
  tasks: [
    {
      task_id: "task-assembly-01",
      task_code: "00000042",
      name: "双臂装配采集",
      lifecycle: "ACTIVE" as const,
      target: { package_count: 12, duration_seconds: null },
      registered_count: 12,
      received_count: 12,
      device_progress: {
        source: "DEVICE_ATTESTED_FACT" as const,
        captured_count: 12,
        saved_count: 11,
        confirmed_duration_seconds: 330,
      },
    },
  ],
  pipeline: {
    task_count: 1,
    package_count: 12,
    qc: {
      waiting: 1,
      passed: 8,
      risk: 2,
      rejected: 1,
      duplicate: 0,
      unavailable: 0,
    },
    stages: dashboardTaskPipelineStages,
    unavailable_sources: [],
  },
  selected_task_id: "task-assembly-01",
  selected: {
    task: {
      task_id: "task-assembly-01",
      task_code: "00000042",
      name: "双臂装配采集",
      lifecycle: "ACTIVE" as const,
      target: { package_count: 12, duration_seconds: null },
      registered_count: 12,
      received_count: 12,
      device_progress: {
        source: "DEVICE_ATTESTED_FACT" as const,
        captured_count: 12,
        saved_count: 11,
        confirmed_duration_seconds: 330,
      },
    },
    attainment: "ATTAINED" as const,
    current_stage: "AUTOMATIC_VALIDATION" as const,
    current_stage_label: "自动校验",
    next_step: "处理质量风险并安排重新采集。",
    qc: {
      waiting: 1,
      passed: 8,
      risk: 2,
      rejected: 1,
      duplicate: 0,
      unavailable: 0,
    },
    standardization: {
      waiting: 2,
      aligning: 1,
      alignment_failed: 1,
      lance_writing: 1,
      lance_failed: 1,
      ready: 3,
      blocked_by_quality: 0,
      isolated_by_quality: 3,
      unavailable: 0,
    },
    stages: [
      {
        stage: "TASK_EXECUTION" as const,
        waiting: 0,
        running: 0,
        succeeded: 12,
        risk: 0,
        isolated: 0,
        blocked: 0,
        failed: 0,
        unavailable: 0,
      },
      {
        stage: "PACKAGE_UPLOAD" as const,
        waiting: 0,
        running: 0,
        succeeded: 12,
        risk: 0,
        isolated: 0,
        blocked: 0,
        failed: 0,
        unavailable: 0,
      },
      {
        stage: "RAW_RECEIPT" as const,
        waiting: 0,
        running: 0,
        succeeded: 12,
        risk: 0,
        isolated: 0,
        blocked: 0,
        failed: 0,
        unavailable: 0,
      },
      {
        stage: "AUTOMATIC_VALIDATION" as const,
        waiting: 1,
        running: 0,
        succeeded: 8,
        risk: 0,
        isolated: 3,
        blocked: 0,
        failed: 0,
        unavailable: 0,
      },
      {
        stage: "STANDARDIZATION" as const,
        waiting: 2,
        running: 2,
        succeeded: 3,
        risk: 0,
        isolated: 3,
        blocked: 0,
        failed: 2,
        unavailable: 0,
      },
      {
        stage: "ANNOTATION" as const,
        waiting: 6,
        running: 2,
        succeeded: 1,
        risk: 0,
        isolated: 3,
        blocked: 0,
        failed: 0,
        unavailable: 0,
      },
      {
        stage: "REVIEW" as const,
        waiting: 8,
        running: 0,
        succeeded: 1,
        risk: 0,
        isolated: 3,
        blocked: 0,
        failed: 0,
        unavailable: 0,
      },
      {
        stage: "PUBLICATION" as const,
        waiting: 8,
        running: 0,
        succeeded: 1,
        risk: 0,
        isolated: 3,
        blocked: 0,
        failed: 0,
        unavailable: 0,
      },
    ],
    main_state_counts: {
      QC_RISK: 2,
      QC_REJECT: 1,
      ALIGNMENT_FAILED: 1,
      LANCE_FAILED: 1,
      PUBLISHED: 1,
    },
    blocker_count: 2,
    blockers: [
      {
        reason_code: "ALIGNMENT_IO_ERROR",
        label: "30 Hz对齐技术失败",
        category: "TECHNICAL" as const,
        count: 1,
        retryable: true,
        deep_link: "/ingest/uploads/records?task_id=task-assembly-01",
      },
      {
        reason_code: "LANCE_WRITE_FAILED",
        label: "Lance写入技术失败",
        category: "TECHNICAL" as const,
        count: 1,
        retryable: true,
        deep_link: "/ingest/uploads/records?task_id=task-assembly-01",
      },
    ],
    actions: [
      {
        action: "VIEW_QC_ANOMALIES" as const,
        label: "查看问题数据",
        deep_link: "/manual/issues?source=AUTO_QC",
      },
      {
        action: "RETRY_TECHNICAL_PROCESSING" as const,
        label: "重试技术处理",
        deep_link: "/ingest/uploads/records?task_id=task-assembly-01",
      },
      {
        action: "CLOSE_TASK" as const,
        label: "查看任务并关闭",
        deep_link: "/collection-tasks?task_id=task-assembly-01",
      },
    ],
    unavailable_sources: [],
  },
} satisfies components["schemas"]["DashboardTaskStatusResponse"];

export const dashboardTaskStatusAllTasksFixture = {
  ...dashboardTaskStatusFixture,
  tasks: [
    ...dashboardTaskStatusFixture.tasks,
    {
      task_id: "task-packing-02",
      task_code: "00000043",
      name: "装箱采集",
      lifecycle: "ACTIVE" as const,
      target: { package_count: 4, duration_seconds: null },
      registered_count: 2,
      received_count: 2,
      device_progress: {
        source: "DEVICE_ATTESTED_FACT" as const,
        captured_count: 2,
        saved_count: 2,
        confirmed_duration_seconds: 60,
      },
    },
  ],
  pipeline: {
    task_count: 2,
    package_count: 14,
    qc: {
      waiting: 1,
      passed: 10,
      risk: 2,
      rejected: 1,
      duplicate: 0,
      unavailable: 0,
    },
    stages: [
      taskStage("TASK_EXECUTION", { succeeded: 14 }),
      taskStage("PACKAGE_UPLOAD", { succeeded: 14 }),
      taskStage("RAW_RECEIPT", { succeeded: 14 }),
      taskStage("AUTOMATIC_VALIDATION", {
        waiting: 1,
        succeeded: 10,
        isolated: 3,
      }),
      taskStage("STANDARDIZATION", {
        waiting: 4,
        running: 2,
        succeeded: 3,
        isolated: 3,
        failed: 2,
      }),
      taskStage("ANNOTATION", {
        waiting: 8,
        running: 2,
        succeeded: 1,
        isolated: 3,
      }),
      taskStage("REVIEW", { waiting: 10, succeeded: 1, isolated: 3 }),
      taskStage("PUBLICATION", { waiting: 10, succeeded: 1, isolated: 3 }),
    ],
    unavailable_sources: [],
  },
  selected_task_id: null,
  selected: null,
} satisfies components["schemas"]["DashboardTaskStatusResponse"];

export const dashboardTaskStatusEmptyFixture = {
  schema_version: "1" as const,
  project_id: base.project_id,
  region_code: base.region_code,
  as_of: base.as_of,
  section: empty,
  tasks: [],
  pipeline: {
    task_count: 0,
    package_count: 0,
    qc: {
      waiting: 0,
      passed: 0,
      risk: 0,
      rejected: 0,
      duplicate: 0,
      unavailable: 0,
    },
    stages: dashboardEmptyTaskPipelineStages,
    unavailable_sources: [],
  },
  selected_task_id: null,
  selected: null,
} satisfies components["schemas"]["DashboardTaskStatusResponse"];

const pendingItem = (
  item_type: components["schemas"]["DashboardPendingItemType"],
  source_id: string,
  resource_type: components["schemas"]["DashboardResourceType"],
  deep_link: string,
) => ({
  item_type,
  source_id,
  deduplication_key: `${item_type}:${source_id}`,
  source_state:
    item_type === "UPLOAD_FAILED"
      ? "FAILED"
      : item_type === "QC_ANOMALY"
        ? "RISK"
        : item_type === "TAG_REVIEW_PENDING"
          ? "SUBMITTED"
          : "APPROVED",
  severity: "HIGH" as const,
  opened_at: "2026-08-05T07:57:00Z",
  target: {
    resource_type,
    resource_id: source_id,
    resource_version: null,
    deep_link,
  },
});

export const dashboardPendingFixture = {
  ...base,
  pending_items: {
    ...ready,
    authorized_source_types: [
      "UPLOAD_FAILED",
      "QC_ANOMALY",
      "TAG_REVIEW_PENDING",
      "PUBLICATION_PENDING",
    ],
    page_info: pageInfo,
    items: [
      pendingItem(
        "UPLOAD_FAILED",
        "upload-1",
        "UPLOAD_SESSION",
        "/ingest/uploads/upload-1",
      ),
      pendingItem(
        "QC_ANOMALY",
        "rollout-1",
        "ROLLOUT",
        "/datasets/dataset-1/rollouts/rollout-1",
      ),
      pendingItem(
        "TAG_REVIEW_PENDING",
        "annotation-1",
        "ANNOTATION_TASK",
        "/annotations/tasks/annotation-1",
      ),
      pendingItem(
        "PUBLICATION_PENDING",
        "dataset-1",
        "DATASET_VERSION",
        "/datasets/dataset-1/versions/1",
      ),
    ],
  },
} satisfies components["schemas"]["DashboardPendingItemsResponse"];

export const dashboardEmptyFixtures = {
  activity: {
    ...base,
    activity: { ...empty, page_info: pageInfo, items: [] },
  } satisfies components["schemas"]["DashboardActivityResponse"],
  snapshot: {
    ...base,
    sections: {
      signal_pipeline: {
        ...empty,
        stages: [
          "COLLECTED",
          "RECEIVED",
          "AUTO_QC",
          "ALIGNED_30_HZ",
          "LANCE",
          "ANNOTATION",
          "REVIEW",
          "PUBLISHED",
        ],
        stage_counts: [
          { stage: "COLLECTED" as const, count: 0 },
          { stage: "RECEIVED" as const, count: 0 },
          { stage: "AUTO_QC" as const, count: 0 },
          { stage: "ALIGNED_30_HZ" as const, count: 0 },
          { stage: "LANCE" as const, count: 0 },
          { stage: "ANNOTATION" as const, count: 0 },
          { stage: "REVIEW" as const, count: 0 },
          { stage: "PUBLISHED" as const, count: 0 },
        ],
        published_region: {
          ...empty,
          lineage_count: 0,
          publication_count: 0,
          unresolved_history_count: 0,
        },
      },
      episodes: empty,
      work: empty,
    },
  } satisfies components["schemas"]["DashboardSnapshotResponse"],
  pending: {
    ...base,
    pending_items: {
      ...empty,
      authorized_source_types: [],
      page_info: pageInfo,
      items: [],
    },
  } satisfies components["schemas"]["DashboardPendingItemsResponse"],
};

export const dashboardUnknownFixtures = {
  snapshot: dashboardSnapshotFixture,
  pending: dashboardPendingFixture,
};
