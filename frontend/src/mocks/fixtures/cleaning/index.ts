export const cleaningFixtureScope = {
  organization_id: 'org_fx_mc_01',
  project_id: 'prj_fx_mc_01',
  region_code: 'cn-shanghai-1',
} as const;

export const cleaningFixtureIds = {
  dataset: 'dataset_fx_mc_01',
  baseVersion: 'version_fx_mc_base_01',
  outputVersion: 'version_fx_mc_output_01',
  episode: 'episode_fx_mc_01',
  outputEpisode: 'episode_fx_mc_output_01',
  baseRevision: 'revision_fx_mc_base_01',
  outputRevision: 'revision_fx_mc_output_01',
  stream: 'stream_fx_mc_camera_01',
  outputStream: 'stream_fx_mc_output_01',
  issueOpen: 'issue_fx_mc_open_01',
  issueWork: 'issue_fx_mc_work_01',
  issueResolved: 'issue_fx_mc_resolved_01',
  draftIssue: 'draft_fx_mc_issue_01',
  draftSuccessor: 'draft_fx_mc_successor_01',
  decision: 'decision_fx_mc_return_01',
  findingOne: 'finding_fx_mc_01',
  findingTwo: 'finding_fx_mc_02',
  preview: 'preview_fx_mc_01',
  previewJob: 'job_fx_mc_preview_01',
  commit: 'commit_fx_mc_01',
  commitJob: 'job_fx_mc_commit_01',
} as const;

const at = '2026-08-06T13:20:00Z';
const hash0 = `sha256:${'d'.repeat(64)}` as const;
const hash1 = `sha256:${'a'.repeat(64)}` as const;
const hash2 = `sha256:${'b'.repeat(64)}` as const;
const hashComposition = `sha256:${'f'.repeat(64)}` as const;

const actor = { id: 'principal_fx_mc_processor_01', display_name: 'Fixture Processor' } as const;

function manualIssue(overrides: Readonly<Record<string, unknown>> = {}) {
  return {
    id: cleaningFixtureIds.issueOpen,
    etag: '"issue_fx_mc_open_01:v1"',
    scope: cleaningFixtureScope,
    dataset_id: cleaningFixtureIds.dataset,
    origin_dataset_version_id: cleaningFixtureIds.baseVersion,
    episode_id: cleaningFixtureIds.episode,
    episode_revision_id: cleaningFixtureIds.baseRevision,
    episode_stream_id: cleaningFixtureIds.stream,
    context_status: 'VALID',
    schema_snapshot_id: 'schema_snapshot_fx_mc_01',
    robot_model_version_id: 'robot_model_version_fx_mc_01',
    calibration_set_id: 'calibration_set_fx_mc_01',
    start_ns: '1200000000',
    end_ns: '4200000000',
    issue_type: 'POSE_JITTER',
    severity: 'HIGH',
    status: 'OPEN',
    note: '固定相机区间存在位姿抖动。',
    assignee: null,
    related_drafts: [],
    resolution_version: null,
    resolution_note: null,
    resolved_at: null,
    resolved_by: null,
    allowed_actions: ['VIEW_EPISODE', 'TRIAGE', 'START_WORK', 'CREATE_DRAFT', 'PREVIEW_RANGE'],
    blocked_reasons: [],
    created_at: '2026-08-06T13:00:00Z',
    updated_at: at,
    ...overrides,
  };
}

export const cleaningManualIssues = {
  open: manualIssue(),
  inProgress: manualIssue({
    id: cleaningFixtureIds.issueWork,
    etag: '"issue_fx_mc_work_01:v4"',
    status: 'IN_PROGRESS',
    severity: 'CRITICAL',
    note: '问题正在清洗。',
    assignee: actor,
    related_drafts: [{ draft_id: cleaningFixtureIds.draftIssue, status: 'EDITING', updated_at: at }],
    allowed_actions: ['VIEW_EPISODE', 'TRIAGE', 'CONTINUE_DRAFT', 'RESOLVE', 'PREVIEW_RANGE'],
  }),
  resolved: manualIssue({
    id: cleaningFixtureIds.issueResolved,
    etag: '"issue_fx_mc_resolved_01:v7"',
    status: 'RESOLVED',
    severity: 'CRITICAL',
    assignee: actor,
    related_drafts: [{ draft_id: 'draft_fx_mc_resolution_01', status: 'COMMITTED', updated_at: '2026-08-06T13:50:00Z' }],
    resolution_version: { version_id: 'version_fx_mc_resolution_01', producer_draft_id: 'draft_fx_mc_resolution_01', root_issue_draft_id: 'draft_fx_mc_resolution_01', lineage_depth: '0', resolved_at: '2026-08-06T13:58:00Z' },
    resolution_note: 'READY 输出已验证。',
    resolved_at: '2026-08-06T13:58:00Z',
    resolved_by: actor,
    allowed_actions: ['VIEW_EPISODE', 'CONTINUE_DRAFT', 'PREVIEW_RANGE'],
  }),
} as const;

function issueListItem(issue: ReturnType<typeof manualIssue>) {
  return {
    id: issue.id,
    etag: issue.etag,
    scope: issue.scope,
    dataset_id: issue.dataset_id,
    origin_dataset_version_id: issue.origin_dataset_version_id,
    episode_id: issue.episode_id,
    episode_revision_id: issue.episode_revision_id,
    episode_stream_id: issue.episode_stream_id,
    start_ns: issue.start_ns,
    end_ns: issue.end_ns,
    issue_type: issue.issue_type,
    severity: issue.severity,
    status: issue.status,
    assignee: issue.assignee,
    related_draft_count: String(issue.related_drafts.length),
    resolution_version: issue.resolution_version,
    resolution_note: issue.resolution_note,
    resolved_at: issue.resolved_at,
    resolved_by: issue.resolved_by,
    allowed_actions: issue.allowed_actions,
    updated_at: issue.updated_at,
  };
}

export function makeManualIssueListEnvelope(items = Object.values(cleaningManualIssues)) {
  return {
    items: items.map((issue) => issueListItem(issue)),
    page_info: { after: null, before: null, has_next: false, has_previous: false },
    snapshot_at: at,
    scope: cleaningFixtureScope,
    request_id: 'req_fx_mc_issue_list_01',
    contract_version: 'manual-cleaning.v1',
  } as const;
}

export function makeManualIssuePageEnvelope(items = Object.values(cleaningManualIssues)) {
  return {
    data: {
      counts: {
        total: String(items.length),
        open: String(items.filter((issue) => issue.status === 'OPEN').length),
        in_progress: String(items.filter((issue) => issue.status === 'IN_PROGRESS').length),
        resolved: String(items.filter((issue) => issue.status === 'RESOLVED').length),
      },
      facets: { issue_types: ['POSE_JITTER'], severities: ['HIGH', 'CRITICAL'], statuses: ['OPEN', 'IN_PROGRESS', 'RESOLVED'], assignees: [actor] },
      allowed_actions: ['TRIAGE', 'CREATE_DRAFT', 'RESOLVE'],
      snapshot_at: at,
    },
    scope: cleaningFixtureScope,
    request_id: 'req_fx_mc_issue_page_01',
    contract_version: 'manual-cleaning.v1',
  } as const;
}

export function makeManualIssueEnvelope(issue: unknown = cleaningManualIssues.open) {
  return { data: issue, scope: cleaningFixtureScope, request_id: 'req_fx_mc_issue_detail_01', contract_version: 'manual-cleaning.v1' } as const;
}

export const issueOrigin = {
  origin_type: 'ISSUE_DERIVED',
  manual_issue_context: {
    schema_version: 1,
    dataset_id: cleaningFixtureIds.dataset,
    base_version_id: cleaningFixtureIds.baseVersion,
    episode_id: cleaningFixtureIds.episode,
    base_revision_id: cleaningFixtureIds.baseRevision,
    selected_stream_id: cleaningFixtureIds.stream,
    selected_channel_path: '/camera/front/image',
    start_ns: '1200000000',
    end_ns: '4200000000',
    manual_issue_ids: [cleaningFixtureIds.issueWork],
  },
  review_return_lineage: null,
} as const;

export const reviewOrigin = {
  origin_type: 'REVIEW_RETURN',
  manual_issue_context: null,
  review_return_lineage: {
    lineage_type: 'REVIEW_RETURN',
    supersedes_draft_id: cleaningFixtureIds.draftIssue,
    returned_from_version_id: cleaningFixtureIds.outputVersion,
    returned_from_review_decision_id: cleaningFixtureIds.decision,
  },
} as const;

export const reviewSummary = {
  review_decision_id: cleaningFixtureIds.decision,
  review_finding_ids: [cleaningFixtureIds.findingOne, cleaningFixtureIds.findingTwo],
  finding_count: '2',
  successor_draft_id: cleaningFixtureIds.draftSuccessor,
  supersedes_draft_id: cleaningFixtureIds.draftIssue,
  returned_from_version_id: cleaningFixtureIds.outputVersion,
  returned_from_review_decision_id: cleaningFixtureIds.decision,
  output_version_status: 'RETURNED',
} as const;

function draft(overrides: Readonly<Record<string, unknown>> = {}) {
  return {
    draft_id: cleaningFixtureIds.draftIssue,
    etag: '"draft_fx_mc_issue_01:v4"',
    status: 'EDITING',
    origin: issueOrigin,
    base_version_id: cleaningFixtureIds.baseVersion,
    base_revision_id: cleaningFixtureIds.baseRevision,
    episode_id: cleaningFixtureIds.episode,
    manual_issue_count: '1',
    preview_status: 'READY',
    commit_status: 'NONE',
    output_version_status: null,
    review_decision_id: null,
    successor_draft_id: null,
    review_finding_count: '0',
    review_summary: null,
    created_at: at,
    updated_at: at,
    allowed_actions: ['VIEW', 'VIEW_EVENTS', 'EDIT', 'CREATE_PREVIEW', 'COMMIT'],
    ...overrides,
  };
}

export const cleaningDrafts = {
  editing: draft(),
  returned: { ...draft(),
    etag: '"draft_fx_mc_issue_01:v9"',
    status: 'COMMITTED',
    commit_status: 'SUCCEEDED',
    output_version_status: 'RETURNED',
    review_decision_id: cleaningFixtureIds.decision,
    successor_draft_id: cleaningFixtureIds.draftSuccessor,
    review_finding_count: '2',
    review_summary: reviewSummary,
    allowed_actions: ['VIEW', 'VIEW_EVENTS', 'OPEN_REVIEW', 'OPEN_SUCCESSOR'],
    updated_at: '2026-08-06T13:55:00Z',
  },
  successor: { ...draft(),
    draft_id: cleaningFixtureIds.draftSuccessor,
    etag: '"draft_fx_mc_successor_01:v1"',
    origin: reviewOrigin,
    base_version_id: cleaningFixtureIds.outputVersion,
    base_revision_id: cleaningFixtureIds.outputRevision,
    episode_id: cleaningFixtureIds.outputEpisode,
    manual_issue_count: '0',
    preview_status: 'NONE',
    allowed_actions: ['VIEW', 'VIEW_EVENTS', 'EDIT', 'CREATE_PREVIEW', 'COMMIT', 'OPEN_REVIEW'],
    updated_at: '2026-08-06T13:56:00Z',
  },
} as const;

type CleaningDraftFixture = Readonly<Record<string, unknown> & { created_at: string }>;

function listDraft(item: CleaningDraftFixture) {
  const { created_at: createdAt, ...projection } = item;
  void createdAt;
  return projection;
}

export function makeCleaningDraftListEnvelope(
  items: readonly CleaningDraftFixture[] = [cleaningDrafts.editing, cleaningDrafts.successor],
) {
  return {
    items: items.map((item) => listDraft(item)),
    page_info: { after: null, before: null, has_next: false, has_previous: false },
    snapshot_at: at,
    query_signature: `sha256:${'0'.repeat(64)}`,
    scope: cleaningFixtureScope,
    request_id: 'req_fx_mc_draft_list_01',
    contract_version: 'manual-cleaning.v1',
  } as const;
}

export function makeCleaningDraftSummaryEnvelope() {
  return {
    data: {
      scope_counts: { EDITING: '2', COMMITTED: '1', RETURNED: '1', REVIEWING: '0' },
      metrics: { active_draft_count: '2', manual_issue_derived_count: '1', review_return_count: '1' },
      jobs: { preview_queued: '0', preview_running: '0', commit_queued: '0' },
      as_of: at,
    },
    scope: cleaningFixtureScope,
    request_id: 'req_fx_mc_draft_summary_01',
    contract_version: 'manual-cleaning.v1',
  } as const;
}

export const cleaningSummary = {
  source_duration_ns: '10000000000',
  trimmed_domain_duration_ns: '10000000000',
  excluded_union_duration_ns: '400000000',
  invalid_mask_union_duration_ns: '0',
  output_duration_ns: '9600000000',
  output_segment_count: '2',
  disabled_stream_count: '0',
  reused_source_bytes: '960000000',
  new_derived_bytes: '40000000',
  reuse_rate: '0.96',
  requires_materialization: true,
  estimate_status: 'CONFIRMED',
  calculated_at: '2026-08-06T13:30:00Z',
} as const;

export const cleaningEdl = {
  edl_revision: '1',
  etag: '"draft_fx_mc_issue_01:edl:1"',
  operation_hash: hash1,
  operations: [
    { id: 'operation_fx_mc_01', sequence_no: 0, enabled: true, schema_version: '1', type: 'EXCLUDE_RANGE', start_ns: '1800000000', end_ns: '2200000000', reason: '移除位姿跳变。' },
    { id: 'operation_fx_mc_02', sequence_no: 1, enabled: true, schema_version: '1', type: 'SPLIT', at_ns: '6000000000' },
  ],
  validation: { status: 'PASSED', issues: [], validated_edl_revision: '1', validated_operation_hash: hash1 },
  summary: cleaningSummary,
  updated_at: '2026-08-06T13:30:00Z',
} as const;

export const emptyCleaningEdl = {
  edl_revision: '0', etag: '"draft_fx_mc_successor_01:edl:0"', operation_hash: hash0,
  operations: [],
  validation: { status: 'PASSED', issues: [], validated_edl_revision: '0', validated_operation_hash: hash0 },
  summary: { ...cleaningSummary, source_duration_ns: '5600000000', output_duration_ns: '5600000000', excluded_union_duration_ns: '0', output_segment_count: '1', reused_source_bytes: '560000000', new_derived_bytes: '0', reuse_rate: '1', requires_materialization: false },
  updated_at: '2026-08-06T13:56:00Z',
} as const;

export const previewReady = {
  preview_id: cleaningFixtureIds.preview,
  draft_id: cleaningFixtureIds.draftIssue,
  base_revision_id: cleaningFixtureIds.baseRevision,
  edl_revision: '1',
  operation_hash: hash1,
  job_id: cleaningFixtureIds.previewJob,
  created_at: '2026-08-06T13:31:00Z',
  status: 'READY',
  viewer_manifest: { manifest_id: 'manifest_fx_mc_preview_01', manifest_hash: hash2, expires_at: '2026-08-16T14:30:00Z', streams: [cleaningFixtureIds.stream] },
  source_to_output_map: {
    segments: [
      { source_start_ns: '0', source_end_ns: '1800000000', output_revision_id: cleaningFixtureIds.outputRevision, output_start_ns: '0', output_end_ns: '1800000000' },
      { source_start_ns: '2200000000', source_end_ns: '10000000000', output_revision_id: cleaningFixtureIds.outputRevision, output_start_ns: '1800000000', output_end_ns: '9600000000' },
    ],
    mapping_version: '1',
  },
  validation: cleaningEdl.validation,
  summary: cleaningSummary,
  expires_at: '2026-08-16T14:30:00Z',
} as const;

export const reviewFeedback = {
  review_decision: { id: cleaningFixtureIds.decision, output_version_id: cleaningFixtureIds.outputVersion, decision: 'RETURNED', immutable: true, created_at: '2026-08-06T13:55:00Z' },
  output_version: { version_id: cleaningFixtureIds.outputVersion, status: 'RETURNED', draft_id: cleaningFixtureIds.draftIssue, commit_id: cleaningFixtureIds.commit },
  output_version_id: cleaningFixtureIds.outputVersion,
  output_version_status: 'RETURNED',
  findings: [
    { id: cleaningFixtureIds.findingOne, output_revision_id: cleaningFixtureIds.outputRevision, episode_stream_id: cleaningFixtureIds.outputStream, start_ns: '1500000000', end_ns: '1700000000', finding_type: 'POSE_DISCONTINUITY', severity: 'HIGH', note: '第一输出边界仍存在跳变。', immutable: true, created_at: '2026-08-06T13:55:00Z' },
    { id: cleaningFixtureIds.findingTwo, output_revision_id: cleaningFixtureIds.outputRevision, episode_stream_id: cleaningFixtureIds.outputStream, start_ns: '200000000', end_ns: '450000000', finding_type: 'TIMESTAMP_DRIFT', severity: 'MEDIUM', note: '可编辑输出开头存在时间漂移。', immutable: true, created_at: '2026-08-06T13:55:00Z' },
  ],
  review_finding_ids: [cleaningFixtureIds.findingOne, cleaningFixtureIds.findingTwo],
  successor_draft_id: cleaningFixtureIds.draftSuccessor,
  supersedes_draft_id: cleaningFixtureIds.draftIssue,
  returned_from_version_id: cleaningFixtureIds.outputVersion,
  returned_from_review_decision_id: cleaningFixtureIds.decision,
} as const;

export const commitSucceeded = {
  commit_id: cleaningFixtureIds.commit, draft_id: cleaningFixtureIds.draftIssue, preview_id: cleaningFixtureIds.preview,
  job_id: cleaningFixtureIds.commitJob, created_at: '2026-08-06T13:40:00Z', status: 'SUCCEEDED', completed_at: '2026-08-06T13:50:00Z',
  output_revisions: [{ revision_id: cleaningFixtureIds.outputRevision, ordinal: 0, episode_id: cleaningFixtureIds.outputEpisode, episode_stream_ids: [cleaningFixtureIds.outputStream], source_revision_id: cleaningFixtureIds.baseRevision, member_mode: 'EDIT_RESULT' }],
  output_version: { version_id: cleaningFixtureIds.outputVersion, status: 'REVIEWING', draft_id: cleaningFixtureIds.draftIssue, commit_id: cleaningFixtureIds.commit },
  materialization_status: 'SUCCEEDED', operation_hash: hash1, successor_composition_hash: null,
} as const;

const base = {
  dataset_id: cleaningFixtureIds.dataset,
  version_id: cleaningFixtureIds.baseVersion,
  episode_id: cleaningFixtureIds.episode,
  revision_id: cleaningFixtureIds.baseRevision,
  schema_snapshot_id: 'schema_snapshot_fx_mc_01',
  robot_model_version_id: 'robot_model_version_fx_mc_01',
  calibration_set_id: 'calibration_set_fx_mc_01',
} as const;

const streams = [{ stream_id: cleaningFixtureIds.stream, channel_path: '/camera/front/image', kind: 'RGB_VIDEO', duration_ns: '10000000000' }] as const;

export function makeCleaningBootstrap(draftId: string = cleaningFixtureIds.draftIssue) {
  const successor = draftId === cleaningFixtureIds.draftSuccessor;
  const returned = draftId === 'draft_fx_mc_returned_01';
  const selectedDraft = successor ? cleaningDrafts.successor : returned ? cleaningDrafts.returned : cleaningDrafts.editing;
  const data = {
    draft: selectedDraft,
    origin: selectedDraft.origin,
    base: successor ? { ...base, version_id: cleaningFixtureIds.outputVersion, episode_id: cleaningFixtureIds.outputEpisode, revision_id: cleaningFixtureIds.outputRevision } : base,
    streams: successor ? [{ stream_id: cleaningFixtureIds.outputStream, channel_path: '/camera/front/image', kind: 'RGB_VIDEO', duration_ns: '5600000000' }] : streams,
    edl: successor ? emptyCleaningEdl : cleaningEdl,
    successor_composition: successor ? { schema_version: 1, source_version_id: cleaningFixtureIds.outputVersion, editable_base_revision_id: cleaningFixtureIds.outputRevision, members: [{ source_revision_id: cleaningFixtureIds.outputRevision, source_ordinal: 0, handling: 'EDITABLE_BASE' }], composition_hash: hashComposition } : null,
    active_preview: successor ? null : previewReady,
    active_commit: returned ? commitSucceeded : null,
    review_feedback: successor || returned ? reviewFeedback : null,
    lease: selectedDraft.status === 'EDITING' ? { session_id: `session_${selectedDraft.draft_id}`, draft_id: selectedDraft.draft_id, etag: `"lease_${selectedDraft.draft_id}:v1"`, lease_revision: '1', holder_summary: actor, expires_at: '2026-08-16T14:15:00Z', read_only: false } : null,
    allowed_actions: selectedDraft.status === 'EDITING' ? ['VIEW', 'ACQUIRE_LEASE', 'SAVE_EDL', 'CREATE_PREVIEW', 'COMMIT'] : ['VIEW', 'OPEN_REVIEW', 'OPEN_SUCCESSOR'],
  };
  return { data, scope: cleaningFixtureScope, request_id: `req_bootstrap_${selectedDraft.draft_id}`, contract_version: 'manual-cleaning.v1' } as const;
}

export function makeReturnedBootstrap() {
  const envelope = makeCleaningBootstrap('draft_fx_mc_returned_01');
  return { ...envelope, data: { ...envelope.data, draft: cleaningDrafts.returned, origin: issueOrigin } };
}

export function makeCleaningDraftDetail(draftId: string) {
  const selected = draftId === cleaningFixtureIds.draftSuccessor ? cleaningDrafts.successor : draftId === cleaningFixtureIds.draftIssue ? cleaningDrafts.editing : cleaningDrafts.returned;
  const returned = selected.output_version_status === 'RETURNED';
  return {
    data: {
      draft: selected,
      origin: selected.origin,
      ordered_operations: selected.status === 'EDITING' && selected.draft_id === cleaningFixtureIds.draftIssue ? cleaningEdl.operations : [],
      preview_summary: selected.draft_id === cleaningFixtureIds.draftIssue ? cleaningSummary : null,
      review_summary: returned ? reviewSummary : null,
      relationships: returned ? {
        commit_id: cleaningFixtureIds.commit, output_version_id: cleaningFixtureIds.outputVersion, output_revision_ids: [cleaningFixtureIds.outputRevision], review_decision_id: cleaningFixtureIds.decision, review_finding_ids: [cleaningFixtureIds.findingOne, cleaningFixtureIds.findingTwo], successor_draft_id: cleaningFixtureIds.draftSuccessor, supersedes_draft_id: null, returned_from_version_id: cleaningFixtureIds.outputVersion, returned_from_review_decision_id: cleaningFixtureIds.decision,
      } : selected.origin.origin_type === 'REVIEW_RETURN' ? {
        commit_id: null, output_version_id: null, output_revision_ids: [], review_decision_id: null, review_finding_ids: [], successor_draft_id: null, supersedes_draft_id: cleaningFixtureIds.draftIssue, returned_from_version_id: cleaningFixtureIds.outputVersion, returned_from_review_decision_id: cleaningFixtureIds.decision,
      } : {
        commit_id: null, output_version_id: null, output_revision_ids: [], review_decision_id: null, review_finding_ids: [], successor_draft_id: null, supersedes_draft_id: null, returned_from_version_id: null, returned_from_review_decision_id: null,
      },
    },
    scope: cleaningFixtureScope,
    request_id: `req_detail_${draftId}`,
    contract_version: 'manual-cleaning.v1',
  } as const;
}

export const cleaningEvents = [
  { event_id: 'event_fx_mc_draft_created_01', event_type: 'cleaning.draft.created', occurred_at: at, result: 'SUCCESS', safe_summary: 'P09 从 ManualIssue 创建草稿。', request_id: 'req_fx_mc_issue_draft_01' },
  { event_id: 'event_fx_mc_edl_saved_01', event_type: 'cleaning.draft.updated', occurred_at: '2026-08-06T13:30:00Z', result: 'SUCCESS', safe_summary: '服务端保存 EDL revision 1。', request_id: 'req_fx_mc_edl_save_01' },
] as const;

export function makeCleaningEventsEnvelope() {
  return { items: cleaningEvents, page_info: { after: null, before: null, has_next: false, has_previous: false }, snapshot_at: at, scope: cleaningFixtureScope, request_id: 'req_fx_mc_events_01', contract_version: 'manual-cleaning.v1' } as const;
}

export function makeCreateDraftEnvelope() {
  return { data: { disposition: 'ALREADY_LINKED', draft_id: cleaningFixtureIds.draftIssue, context: issueOrigin.manual_issue_context, selection_token: null, expires_at: null, candidates: [] }, scope: cleaningFixtureScope, request_id: 'req_fx_mc_issue_draft_01', contract_version: 'manual-cleaning.v1' } as const;
}

export function makeReviewFindingsEnvelope() {
  return { data: { feedback: reviewFeedback }, scope: cleaningFixtureScope, request_id: 'req_fx_mc_findings_01', contract_version: 'manual-cleaning.v1' } as const;
}

export function makeAsyncJob(kind: 'CLEANING_PREVIEW' | 'CLEANING_COMMIT', draftId: string) {
  return {
    job_id: kind === 'CLEANING_PREVIEW' ? cleaningFixtureIds.previewJob : cleaningFixtureIds.commitJob,
    kind, status: 'QUEUED', stage: 'accepted', progress: null, result_ref: null, error: null,
    scope: { organization_id: cleaningFixtureScope.organization_id, project_id: cleaningFixtureScope.project_id, region_code: cleaningFixtureScope.region_code },
    resource_ref: { resource_type: 'cleaning_draft', resource_id: draftId, version: '1', etag: null },
    created_at: at, started_at: null, finished_at: null, updated_at: at, expires_at: null,
    etag: `"job_${kind}:v1"`, cancellable: false, retry_of_job_id: null,
  } as const;
}

export { hash0 as cleaningEmptyHash, hash1 as cleaningOperationHash, hashComposition as cleaningCompositionHash };
