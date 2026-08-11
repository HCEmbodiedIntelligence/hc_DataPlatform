const hash0 = `sha256:${'0'.repeat(64)}` as const;
const hash1 = `sha256:${'1'.repeat(64)}` as const;

export const annotationFixtureScope = {
  organization_id: 'org_fx_01',
  project_id: 'prj_fx_01',
  region_code: 'cn-shanghai',
} as const;

export const annotationFixtureIds = {
  dataset: 'dataset_fx_01',
  version: 'version_fx_01',
  episode: 'episode_fx_01',
  revision: 'revision_fx_01',
  replacementRevision: 'revision_fx_02',
  task: 'ann-task-progress-01',
  queuedTask: 'ann-task-unassigned-01',
  submittedTask: 'ann-task-submitted-01',
  staleTask: 'ann-task-stale-01',
  successorTask: 'ann-task-successor-01',
  stream: 'stream_fx_rgb_front',
  jointStream: 'stream_fx_joint',
  ontology: 'ontology_fx_assembly',
} as const;

export function makeAnnotationTask(overrides: Readonly<Record<string, unknown>> = {}) {
  return {
    task_id: annotationFixtureIds.task,
    scope: annotationFixtureScope,
    task_source: 'DIRECT_ASSIGNMENT',
    workflow_status: 'IN_PROGRESS',
    source_status: 'CURRENT',
    block_source: null,
    block_reason: null,
    source: {
      dataset_id: annotationFixtureIds.dataset,
      dataset_version_id: annotationFixtureIds.version,
      episode_id: annotationFixtureIds.episode,
      base_revision_id: annotationFixtureIds.revision,
      stream_ids: [annotationFixtureIds.stream, annotationFixtureIds.jointStream],
      start_ns: '0',
      end_ns: '120000000000',
    },
    ontology: { ontology_id: annotationFixtureIds.ontology, ontology_version: '2.1', ontology_hash: hash1 },
    assignment: { mode: 'DIRECT', assignee_id: 'usr_fx_developer', assigned_by: 'usr_fx_admin', assigned_at: '2026-08-05T08:00:00Z' },
    priority: 900,
    current_draft_revision: 3,
    current_draft_hash: hash0,
    current_submission_id: null,
    submitted_annotation_set_id: null,
    correction_of_annotation_set_id: null,
    predecessor_task_id: null,
    successor_task_id: null,
    latest_review_id: null,
    created_at: '2026-08-05T08:00:00Z',
    updated_at: '2026-08-05T08:05:00Z',
    etag: '"task-rv-7"',
    allowed_actions: ['EDIT_DRAFT', 'SAVE_DRAFT', 'PREFLIGHT_SUBMIT', 'SUBMIT', 'REPORT_ISSUE', 'VIEW_MANUAL_ISSUE'],
    action_reasons: [],
    ...overrides,
  };
}

export const annotationTaskFixtures = {
  progress: makeAnnotationTask(),
  queued: makeAnnotationTask({
    task_id: annotationFixtureIds.queuedTask,
    task_source: 'COVERAGE_GAP',
    workflow_status: 'QUEUED',
    assignment: { mode: 'UNASSIGNED', assignee_id: null, assigned_by: null, assigned_at: null },
    current_draft_revision: 0,
    allowed_actions: ['CLAIM'],
    etag: '"task-rv-1"',
  }),
  submitted: makeAnnotationTask({
    task_id: annotationFixtureIds.submittedTask,
    workflow_status: 'SUBMITTED',
    current_submission_id: 'submission_fx_01',
    submitted_annotation_set_id: 'annotation_set_fx_01',
    allowed_actions: ['REVIEW', 'VIEW_ANNOTATION_SET', 'VIEW_MANUAL_ISSUE'],
    etag: '"task-rv-9"',
  }),
  stale: makeAnnotationTask({
    task_id: annotationFixtureIds.staleTask,
    task_source: 'REVISION_REBASE',
    source_status: 'STALE',
    allowed_actions: ['REBASE', 'VIEW_MANUAL_ISSUE'],
    successor_task_id: null,
    etag: '"task-rv-stale"',
  }),
} as const;

export function makeAnnotationDraft(taskId: string = annotationFixtureIds.task, revision = 3) {
  return {
    task_id: taskId,
    draft_revision: revision,
    state: 'ACTIVE',
    entries: revision === 0 ? [] : [{
      annotation_id: 'annotation_fx_phase_01',
      label_code: 'GRASP_PART',
      attributes: { confidence: 0.92, outcome: 'SUCCESS' },
      semantic_type: 'PHASE',
      anchor: { anchor_type: 'TIME_RANGE', coordinate_system: 'REVISION_TIME_NS', start_ns: '12000000000', end_ns: '36000000000' },
    }],
    content_hash: revision === 3 ? hash0 : hash1,
    saved_by: revision === 0 ? null : 'usr_fx_developer',
    saved_at: '2026-08-05T08:05:00Z',
    etag: `"draft-rv-${revision}"`,
  };
}

const axes = (count: number) => Array.from({ length: count }, (_, index) => ({
  axis_id: `joint_${index + 1}`,
  source_name: `joint_${index + 1}`,
  display_name: `关节 ${index + 1}`,
  sample_index: index,
  unit: 'rad',
  mapped_joint_name: `joint_${index + 1}`,
  mapping_status: 'MAPPED',
}));

export const annotationViewerStreams = {
  sevenAxis: [
    { stream_id: annotationFixtureIds.stream, channel_definition_id: 'channel_fx_rgb_front', canonical_path: 'camera/front/rgb', display_name: '头部相机', modality: 'RGB', semantic_role: 'PRIMARY_RGB', schema: { schema_id: 'image/rgb', schema_version: '1', encoding: 'h264' }, start_ns: '0', end_ns: '120000000000', availability: 'READY' },
    { stream_id: annotationFixtureIds.jointStream, channel_definition_id: 'channel_fx_joint', canonical_path: 'robot/joints', display_name: '关节状态', modality: 'JOINT_STATE', schema: { schema_id: 'joint/state', schema_version: '3', shape: [7], axes: axes(7) }, start_ns: '0', end_ns: '120000000000', availability: 'READY' },
  ],
  sixAxisPointcloud: [
    { stream_id: annotationFixtureIds.stream, channel_definition_id: 'channel_fx_rgb_front', canonical_path: 'camera/front/rgb', display_name: '头部相机', modality: 'RGB', schema: { schema_id: 'image/rgb', schema_version: '1', encoding: 'h264' }, start_ns: '0', end_ns: '120000000000', availability: 'READY' },
    { stream_id: annotationFixtureIds.jointStream, channel_definition_id: 'channel_fx_joint', canonical_path: 'robot/joints', display_name: '关节状态', modality: 'JOINT_STATE', schema: { schema_id: 'joint/state', schema_version: '3', shape: [6], axes: axes(6) }, start_ns: '0', end_ns: '120000000000', availability: 'READY' },
    { stream_id: 'stream_fx_pointcloud', channel_definition_id: 'channel_fx_pointcloud', canonical_path: 'lidar/points', display_name: '点云 Preview', modality: 'POINTCLOUD', schema: { schema_id: 'pointcloud/xyz', schema_version: '1', encoding: 'xyz-f32' }, start_ns: '0', end_ns: '120000000000', availability: 'READY' },
  ],
  fourteenAxis: [
    ...[1, 2, 3].map((index) => ({ stream_id: `stream_fx_rgb_${index}`, channel_definition_id: `channel_fx_rgb_${index}`, canonical_path: `camera/${index}/rgb`, display_name: `相机 ${index}`, modality: 'RGB', schema: { schema_id: 'image/rgb', schema_version: '1', encoding: 'h264' }, start_ns: '0', end_ns: '120000000000', availability: 'READY' })),
    { stream_id: annotationFixtureIds.jointStream, channel_definition_id: 'channel_fx_joint', canonical_path: 'robot/joints', display_name: '双臂关节状态', modality: 'JOINT_STATE', schema: { schema_id: 'joint/state', schema_version: '3', shape: [14], axes: axes(14) }, start_ns: '0', end_ns: '120000000000', availability: 'READY' },
  ],
  axisMismatch: [
    { stream_id: annotationFixtureIds.stream, channel_definition_id: 'channel_fx_rgb_front', canonical_path: 'camera/front/rgb', display_name: '头部相机', modality: 'RGB', schema: { schema_id: 'image/rgb', schema_version: '1' }, start_ns: '0', end_ns: '120000000000', availability: 'READY' },
    { stream_id: annotationFixtureIds.jointStream, channel_definition_id: 'channel_fx_joint', canonical_path: 'robot/joints', display_name: '关节状态', modality: 'JOINT_STATE', schema: { schema_id: 'joint/state', schema_version: '3', shape: [7], axes: axes(6) }, start_ns: '0', end_ns: '120000000000', availability: 'READY' },
  ],
  pointcloudPending: [{ stream_id: 'stream_fx_pointcloud', channel_definition_id: 'channel_fx_pointcloud', canonical_path: 'lidar/points', display_name: '点云 Preview', modality: 'POINTCLOUD', schema: { schema_id: 'pointcloud/xyz', schema_version: '1' }, start_ns: '0', end_ns: '120000000000', availability: 'PREVIEW_GENERATING' }],
  unknown: [{ stream_id: 'stream_fx_future', channel_definition_id: 'channel_fx_future', canonical_path: 'future/channel', display_name: '未来模态', modality: 'NEURAL_FIELD', schema: { schema_id: 'future/schema', schema_version: '1' }, start_ns: '0', end_ns: '120000000000', availability: 'READY' }],
} as const;

export const annotationFormDefinitionWire = {
  schema_version_id: 'ontology_fx_assembly:2.1',
  title: '装配动作 Schema',
  fields: [
    { key: 'semanticType', label: '语义类型', type: 'enum', required: true, local_cache: true, options: ['ACTION','PHASE','OBJECT','EVENT','KEYFRAME'].map((value) => ({ value, label: value })) },
    { key: 'labelCode', label: '标签', type: 'string', required: true, max_length: 128, local_cache: true },
    { key: 'startNs', label: '开始纳秒', type: 'time-point', required: true, local_cache: true },
    { key: 'endNs', label: '结束纳秒', type: 'time-point', required: true, local_cache: true },
    { key: 'confidence', label: '置信度', type: 'number', min: 0, max: 1, local_cache: true },
  ],
} as const;

export const annotationUnknownFormDefinitionWire = {
  ...annotationFormDefinitionWire,
  fields: [
    ...annotationFormDefinitionWire.fields,
    { key: 'futureField', label: '未来字段', type: 'future-vector-v9', read_only: true },
  ],
} as const;

export function makeAnnotationListEnvelope(items = [annotationTaskFixtures.progress, annotationTaskFixtures.queued]) {
  return { items, page_info: { has_next_page: false, has_previous_page: false, start_cursor: null, end_cursor: null }, snapshot_id: 'snapshot_annotation_fx_01', snapshot_at: '2026-08-05T08:10:00Z', scope: annotationFixtureScope, request_id: 'req_annotation_list_fx_01', contract_version: 'data-annotation.v1' };
}

export function makeAnnotationDetailEnvelope(task = annotationTaskFixtures.progress, streams: readonly unknown[] = annotationViewerStreams.sevenAxis) {
  const stale = task.source_status === 'STALE';
  const streamIds = streams.flatMap((stream) => {
    if (!stream || typeof stream !== 'object') return [];
    const streamId = (stream as Readonly<Record<string, unknown>>).stream_id;
    return typeof streamId === 'string' ? [streamId] : [];
  });
  const resolvedTask = streamIds.length ? { ...task, source: { ...task.source, stream_ids: streamIds } } : task;
  return {
    data: {
      task: resolvedTask,
      draft: { ...makeAnnotationDraft(resolvedTask.task_id, resolvedTask.current_draft_revision), ...(stale ? { state: 'STALE_READ_ONLY' } : {}) },
      latest_submission: null,
      annotation_reviews: [],
      manual_issues: [],
      submission_gate: { status: 'PASS', policy_version: 'issue-gate-v1', issue_watermark: 'watermark_fx_01', evaluated_at: '2026-08-05T08:10:00Z', blocking_issue_ids: [], advisory_issue_ids: [], issues: [], blocked_reasons: [] },
      reference_annotation_sets: [],
      stale_info: stale ? { stale_reason: 'BASE_REVISION_SUPERSEDED', superseded_revision_id: task.source.base_revision_id, replacement_revision_id: annotationFixtureIds.replacementRevision, stale_at: '2026-08-05T08:09:00Z' } : null,
      viewer: { streams, form_definition: annotationFormDefinitionWire },
    },
    scope: annotationFixtureScope,
    request_id: 'req_annotation_detail_fx_01',
    contract_version: 'data-annotation.v1',
  };
}

export const acceptedAnnotationJobFixture = {
  job: { job_id: 'job_fx_annotation_submit', job_type: 'ANNOTATION_SUBMIT', status: 'QUEUED', resource_type: 'annotation_task', resource_id: annotationFixtureIds.task, progress: null, result_ref: null, error: null, created_at: '2026-08-05T08:10:00Z', updated_at: '2026-08-05T08:10:00Z', resource_version: '1' },
  request_id: 'req_annotation_job_fx_01',
} as const;
