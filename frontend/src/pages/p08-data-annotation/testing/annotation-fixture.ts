import type { components } from "../../../shared/api/generated/platform";
import type {
  AnnotationWorkbenchMode,
  RuntimeAnnotationBundle,
  RuntimeAnnotationDraft,
  RuntimeAnnotationHistory,
  RuntimeAnnotationScope,
  RuntimeAnnotationTag,
  RuntimeAnnotationTask,
  RuntimeTagSchemaVersion,
} from "../runtime-annotation-adapter";

export const visualAnnotationScope: RuntimeAnnotationScope = {
  organizationId: "visual-org",
  projectId: "visual-project",
  regionCode: "cn-hz",
};

export const visualSchema: RuntimeTagSchemaVersion = {
  project_id: visualAnnotationScope.projectId,
  schema_id: "robot-operation",
  version: 7,
  name: "机器人操作阶段标签结构",
  status: "PUBLISHED",
  content_hash: "b".repeat(64),
  created_by: "schema-owner",
  created_at: "2026-08-18T08:00:00Z",
  published_by: "schema-owner",
  published_at: "2026-08-18T08:10:00Z",
  compatible_targets: [
    {
      dataset_id: "dataset-robotics",
      dataset_schema_snapshot_id: "snapshot-7",
      region_code: visualAnnotationScope.regionCode,
      task_kind: "TAGGING",
    },
  ],
  document: {
    nodes: [
      {
        tag_id: "operation-stage",
        code: "operation_stage",
        display_name: "操作阶段",
        parent_tag_id: null,
        attributes: [
          {
            key: "station",
            display_name: "工位",
            value_type: "ENUM",
            required: true,
            enum_values: ["装配 A", "装配 B"],
          },
        ],
      },
      {
        tag_id: "grasp-action",
        code: "grasp_action",
        display_name: "抓取动作",
        parent_tag_id: "operation-stage",
        attributes: [
          {
            key: "method",
            display_name: "抓取方式",
            value_type: "ENUM",
            required: true,
            enum_values: ["平行夹爪", "真空吸取"],
          },
        ],
      },
      {
        tag_id: "grasp-success",
        code: "grasp_success",
        display_name: "抓取成功",
        parent_tag_id: "grasp-action",
        attributes: [
          {
            key: "confidence",
            display_name: "置信度",
            value_type: "NUMBER",
            required: true,
            enum_values: [],
          },
        ],
      },
      {
        tag_id: "grasp-failure",
        code: "grasp_failure",
        display_name: "抓取失败",
        parent_tag_id: "grasp-action",
        attributes: [
          {
            key: "failure_reason",
            display_name: "失败原因",
            value_type: "STRING",
            required: true,
            enum_values: [],
          },
        ],
      },
      {
        tag_id: "transfer-action",
        code: "transfer_action",
        display_name: "移动动作（超长层级名称用于验证真实内容不会破坏面板）",
        parent_tag_id: "operation-stage",
        attributes: [],
      },
    ],
    mutual_exclusions: [
      {
        constraint_id: "grasp-outcome",
        tag_ids: ["grasp-success", "grasp-failure"],
      },
    ],
    object_relations: [
      {
        relation_type: "acts_on",
        source_tag_ids: ["grasp-success", "grasp-failure"],
        target_object_types: ["metal-workpiece", "plastic-workpiece"],
        required: true,
      },
    ],
  },
};

export const visualTag: RuntimeAnnotationTag = {
  annotation_id: "annotation-grasp-01",
  tag_id: "grasp-success",
  path: ["operation-stage", "grasp-action", "grasp-success"],
  start_step: 301,
  end_step: 451,
  attributes: { station: "装配 A", method: "平行夹爪", confidence: 0.98 },
  subject: { object_id: "robot-arm-07", object_type: "robot" },
  relations: [
    {
      relation_type: "acts_on",
      target: { object_id: "workpiece-00125", object_type: "metal-workpiece" },
    },
  ],
};

const visualTaskBase: RuntimeAnnotationTask = {
  task_id: "annotation-task-0142",
  project_id: visualAnnotationScope.projectId,
  region_code: visualAnnotationScope.regionCode,
  dataset_id: "dataset-robotics",
  dataset_version: 18,
  rollout_id: "rollout-2026-08-18-0142",
  base_lance_version: 42,
  base_step_count: 26787,
  tag_schema_id: visualSchema.schema_id,
  tag_schema_version: visualSchema.version,
  task_kind: "TAGGING",
  creation_source: "SYSTEM_LANCE",
  source_workflow_id: "ingest/visual/0142",
  assignee_id: "visual-annotator",
  current_revision: 2,
  state_version: 8,
  current_submission_id: null,
  submitted_revision: null,
  submitted_by: null,
  approved_revision: null,
  approved_review_id: null,
  status: "DRAFT",
  schema_version: "1",
  etag: '"annotation-task-0142-v8"',
  created_at: "2026-08-18T08:00:00Z",
  updated_at: "2026-08-18T08:42:00Z",
};

const originalTag: RuntimeAnnotationTag = {
  ...visualTag,
  start_step: 314,
  end_step: 460,
  attributes: { station: "装配 A", method: "平行夹爪", confidence: 0.91 },
};

function historyForTask(task: RuntimeAnnotationTask): RuntimeAnnotationHistory {
  const revisions: components["schemas"]["AnnotationRevision"][] = [
    {
      task_id: task.task_id,
      revision: 1,
      parent_revision: null,
      author_id: "visual-annotator",
      client_mutation_id: "save-visual-1",
      operations: [],
      tags: [originalTag],
      base_lance_version: task.base_lance_version,
      tag_schema_id: task.tag_schema_id,
      tag_schema_version: task.tag_schema_version,
      content_hash: "1".repeat(64),
      origin: "ANNOTATION",
      schema_version: "1",
      created_at: "2026-08-18T08:31:00Z",
      legacy_audit: null,
    },
    {
      task_id: task.task_id,
      revision: 2,
      parent_revision: 1,
      author_id: "visual-annotator",
      client_mutation_id: "save-visual-2",
      operations: [],
      tags: [visualTag],
      base_lance_version: task.base_lance_version,
      tag_schema_id: task.tag_schema_id,
      tag_schema_version: task.tag_schema_version,
      content_hash: "2".repeat(64),
      origin: "ANNOTATION",
      schema_version: "1",
      created_at: "2026-08-18T08:41:00Z",
      legacy_audit: null,
    },
  ];
  const submissions: components["schemas"]["AnnotationSubmission"][] = [
    {
      submission_id: "submission-visual-2",
      task_id: task.task_id,
      episode_version: 1,
      revision: 2,
      submitted_by: "visual-annotator",
      base_lance_version: task.base_lance_version,
      tag_schema_id: task.tag_schema_id,
      tag_schema_version: task.tag_schema_version,
      tag_schema_hash: visualSchema.content_hash,
      revision_content_hash: "2".repeat(64),
      created_at: "2026-08-18T08:42:00Z",
      checks: (
        [
          ["HIERARCHY", "完整路径与已发布数据结构一致"],
          ["BOUNDARY", "区间位于固定基线范围内"],
          ["REQUIRED_ATTRIBUTES", "继承的必填属性完整"],
          ["MUTUAL_EXCLUSION", "互斥集合无重叠冲突"],
          ["OBJECT_RELATIONS", "动作已关联金属工件"],
          ["SCHEMA_VERSION", "robot-operation v7"],
        ] satisfies ReadonlyArray<
          readonly [components["schemas"]["ReviewCheckKind"], string]
        >
      ).map(([kind, evidence]) => ({
        kind,
        evidence,
        status: "PASS" as const,
      })),
    },
  ];
  return { task, revisions, submissions, reviews: [] };
}

function draftForTask(task: RuntimeAnnotationTask): RuntimeAnnotationDraft {
  return {
    task_id: task.task_id,
    revision: task.current_revision,
    author_id: "visual-annotator",
    client_mutation_id: "save-visual-2",
    operations: [],
    tags: [visualTag],
    effective_exclusions: [],
    base_lance_version: task.base_lance_version,
    base_step_count: task.base_step_count,
    tag_schema_id: task.tag_schema_id,
    tag_schema_version: task.tag_schema_version,
    schema_version: "1",
    etag: task.etag,
    updated_at: "2026-08-18T08:41:00Z",
  };
}

function cameras(count: number): components["schemas"]["ManifestCameraV1"][] {
  const names = ["主臂相机", "左腕相机", "右腕相机", "深度流"];
  return Array.from({ length: count }, (_, index) => ({
    camera_id: names[index] ?? `扩展相机 ${index + 1}`,
    topic: `/camera/${index + 1}/image`,
    encoding: index === 3 ? "depth16" : "h264",
    frame_id: `camera_${index + 1}_optical`,
  }));
}

export function createVisualAnnotationBundle(
  input: {
    readonly mode?: AnnotationWorkbenchMode;
    readonly cameraCount?: number;
    readonly invalid?:
      | "missing-required"
      | "mutual-exclusion"
      | "boundary"
      | "cycle";
  } = {},
): RuntimeAnnotationBundle {
  const mode = input.mode ?? "annotation";
  const review = mode === "tag-review";
  const task: RuntimeAnnotationTask = review
    ? {
        ...visualTaskBase,
        status: "SUBMITTED",
        current_submission_id: "submission-visual-2",
        submitted_revision: 2,
        submitted_by: "visual-annotator",
        state_version: 9,
        etag: '"annotation-task-0142-v9"',
      }
    : visualTaskBase;
  let tags: RuntimeAnnotationTag[] = [visualTag];
  let schema = visualSchema;
  if (input.invalid === "missing-required")
    tags = [{ ...visualTag, attributes: { station: "装配 A" } }];
  if (input.invalid === "boundary")
    tags = [{ ...visualTag, end_step: task.base_step_count! + 1 }];
  if (input.invalid === "mutual-exclusion")
    tags = [
      visualTag,
      {
        ...visualTag,
        annotation_id: "annotation-grasp-fail-02",
        tag_id: "grasp-failure",
        path: ["operation-stage", "grasp-action", "grasp-failure"],
        attributes: {
          station: "装配 A",
          method: "平行夹爪",
          failure_reason: "夹持偏移",
        },
      },
    ];
  if (input.invalid === "cycle")
    schema = {
      ...visualSchema,
      document: {
        ...visualSchema.document,
        nodes: [
          {
            ...visualSchema.document.nodes[0]!,
            parent_tag_id: "grasp-success",
          },
          ...visualSchema.document.nodes.slice(1),
        ],
      },
    };
  const draft = { ...draftForTask(task), tags };
  const history = historyForTask(task);
  if (review && input.invalid) {
    history.revisions[history.revisions.length - 1] = {
      ...history.revisions[history.revisions.length - 1]!,
      tags,
    };
  }
  const sibling = (
    index: number,
    status: RuntimeAnnotationTask["status"],
  ): RuntimeAnnotationTask => ({
    ...task,
    task_id: `annotation-task-014${index}`,
    rollout_id: `rollout-2026-08-18-014${index}`,
    status,
    etag: `"annotation-task-014${index}-v3"`,
    assignee_id: status === "DRAFT" ? null : "visual-annotator",
  });
  return {
    task,
    datasetVersion: {
      schema_version: "1",
      project_id: task.project_id,
      dataset_id: task.dataset_id,
      version: task.base_lance_version,
      schema_snapshot_id: "visual-annotation-schema-v1",
      schema_fingerprint: "4".repeat(64),
      frequency_hz: 30,
      content_hash: "5".repeat(64),
      dataset_uri: "s3://visual-test/lance/annotation",
      lance_version: task.base_lance_version,
      storage_commit_id: "6".repeat(64),
      committed_rollouts: [task.rollout_id],
      created_at: "2026-08-18T08:40:00Z",
    },
    draft: review ? null : draft,
    history,
    schema,
    manifest: {
      source: "MANIFEST",
      read_only: true,
      cameras: cameras(input.cameraCount ?? 4),
      topics: [],
      missing_expected_topics: [],
    },
    manifestIssue: null,
    tasks: [task, sibling(3, "DRAFT"), sibling(4, "SUBMITTED")],
  };
}
