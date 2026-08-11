const scope = { organization_id: 'org_fx_01', project_id: 'prj_fx_01', region_code: 'cn-shanghai' };
const envelope = <T>(data: T, requestId: string) => ({ data, scope, request_id: requestId, contract_version: 'v1' as const });

export const dashboardActivityFixture = envelope({
  from: '2026-08-04T08:00:00Z', to: '2026-08-05T08:00:00Z', timezone: 'Asia/Shanghai', as_of: '2026-08-05T08:00:00Z',
  uploads: {
    accepted_unique_bytes: '1099511627776', succeeded_count: '18', terminal_count: '20',
    buckets: [{ start: '2026-08-04T08:00:00Z', end: '2026-08-05T08:00:00Z', accepted_unique_bytes: '1099511627776', failed_count: '2' }],
  },
}, 'req_fx_p01_activity');

export const dashboardSnapshotFixture = envelope({
  timezone: 'Asia/Shanghai', as_of: '2026-08-05T08:00:00Z',
  storage: {
    data_physical_bytes: '1500',
    by_role: [{ role: 'RAW', bytes: '800' }, { role: 'REVISION', bytes: '400' }, { role: 'PREVIEW', bytes: '200' }, { role: 'EXPORT', bytes: '100' }],
    history: [{ month: '2026-08', standard_bytes: '1000', ia_bytes: '300', archive_bytes: '200', data_physical_bytes: '1500' }],
  },
  episodes: { uploaded_count: '100', validated_count: '90', viewable_count: '80' },
  work: { open_manual_issue_count: '11', pending_review_version_count: '7', returned_actionable_draft_count: '2', returned_scope: 'ACTIONABLE_SUCCESSOR' as const, active_cleaning_draft_count: '4', draft_scope: 'ACTIONABLE' as const },
}, 'req_fx_p01_snapshot');

export const dashboardCoverageFixture = envelope({
  timezone: 'Asia/Shanghai', as_of: '2026-08-05T08:00:00Z',
  robot_groups: [{ id: 'robot_group_fx_01', name: '双臂机器人', order: 10 }],
  tasks: [{ id: 'task_fx_pick', name: '抓取', order: 10 }, { id: 'task_fx_empty', name: '装配', order: 20 }],
  cells: [
    { robot_group_id: 'robot_group_fx_01', task_id: 'task_fx_pick', ratio: 0.8, numerator: '8', denominator: '10' },
    { robot_group_id: 'robot_group_fx_01', task_id: 'task_fx_empty', ratio: null, numerator: '0', denominator: '0' },
  ],
}, 'req_fx_p01_coverage');

export const dashboardPendingFixture = envelope({
  as_of: '2026-08-05T08:00:00Z', total_count: '6',
  items: [
    { item_id: 'pending_fx_upload_01', type: 'UPLOAD_FAILED', title: '上传任务失败', summary: '部分分片上传失败', status: 'FAILED', priority: 'HIGH', updated_at: '2026-08-05T07:59:00Z', upload_id: 'upload_fx_01', failure_code: 'PART_UPLOAD_FAILED' },
    { item_id: 'pending_fx_issue_01', type: 'MANUAL_ISSUE', title: '人工问题待分诊', summary: '高严重度问题尚未分派', status: 'OPEN', priority: 'HIGH', updated_at: '2026-08-05T07:57:30Z', issue_id: 'manual_issue_fx_01', version_id: 'version_fx_01', episode_id: 'episode_fx_01' },
    { item_id: 'pending_fx_draft_01', type: 'CLEANING_DRAFT_ACTIONABLE', title: '清洗草稿待预览', summary: '尚未生成预览', status: 'EDITING', priority: 'MEDIUM', updated_at: '2026-08-05T07:56:00Z', draft_id: 'cleaning_draft_fx_01', preview_status: 'NONE' },
  ],
  page_info: { has_next_page: false, has_previous_page: false, start_cursor: 'cursor_fx_p01_start', end_cursor: 'cursor_fx_p01_end' },
  snapshot_at: '2026-08-05T08:00:00Z',
}, 'req_fx_p01_pending');

export const dashboardEmptyFixtures = {
  activity: envelope({ from: '2026-08-04T08:00:00Z', to: '2026-08-05T08:00:00Z', timezone: 'Asia/Shanghai', as_of: '2026-08-05T08:00:00Z', uploads: { accepted_unique_bytes: '0', succeeded_count: '0', terminal_count: '0', buckets: [] } }, 'req_fx_p01_activity_empty'),
  snapshot: envelope({ timezone: 'Asia/Shanghai', as_of: '2026-08-05T08:00:00Z', storage: { data_physical_bytes: '0', by_role: [{ role: 'RAW', bytes: '0' }, { role: 'REVISION', bytes: '0' }, { role: 'PREVIEW', bytes: '0' }, { role: 'EXPORT', bytes: '0' }], history: [] }, episodes: { uploaded_count: '0', validated_count: '0', viewable_count: '0' }, work: { open_manual_issue_count: '0', pending_review_version_count: '0', returned_actionable_draft_count: '0', returned_scope: 'ACTIONABLE_SUCCESSOR' as const, active_cleaning_draft_count: '0', draft_scope: 'ACTIONABLE' as const } }, 'req_fx_p01_snapshot_empty'),
  coverage: envelope({ timezone: 'Asia/Shanghai', as_of: '2026-08-05T08:00:00Z', robot_groups: [], tasks: [], cells: [] }, 'req_fx_p01_coverage_empty'),
  pending: envelope({ as_of: '2026-08-05T08:00:00Z', total_count: '0', items: [], page_info: { has_next_page: false, has_previous_page: false, start_cursor: null, end_cursor: null }, snapshot_at: '2026-08-05T08:00:00Z' }, 'req_fx_p01_pending_empty'),
};

export const dashboardUnknownFixtures = {
  snapshot: { ...dashboardSnapshotFixture, data: { ...dashboardSnapshotFixture.data, storage: { ...dashboardSnapshotFixture.data.storage, data_physical_bytes: '1501', by_role: [...dashboardSnapshotFixture.data.storage.by_role, { role: 'FUTURE_ROLE', bytes: '1' }] } } },
  pending: { ...dashboardPendingFixture, data: { ...dashboardPendingFixture.data, total_count: '4', items: [...dashboardPendingFixture.data.items, { item_id: 'pending_fx_future', type: 'FUTURE_PENDING', title: '未来待办', summary: null, status: 'FUTURE', priority: 'FUTURE', updated_at: '2026-08-05T07:55:00Z' }] } },
};
