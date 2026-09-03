const scope = { organization_id: 'org_fx_01', project_id: 'prj_fx_01', region_code: 'cn-shanghai', timezone: 'Asia/Shanghai' };
const known = (value: string) => ({ state: 'KNOWN' as const, value });
const watermarks = {
  provider_as_of: '2026-08-05T08:00:00Z', reference_as_of: '2026-08-05T08:00:00Z',
  protection_as_of: '2026-08-05T08:00:00Z', classification_as_of: '2026-08-05T08:00:00Z',
  billing_as_of: '2026-08-05T08:00:00Z', evaluated_at: '2026-08-05T08:00:00Z', stale_after_seconds: 600,
};

export const storageOverviewFixture = {
  data: {
    snapshot_id: 'snapshot_fx_storage_01', as_of: '2026-08-05T08:00:00Z', freshness: 'CURRENT' as const,
    formula_version: 'storage_formula_v1', data_completeness: 'COMPLETE' as const,
    inventory: { status: 'FRESH' as const, last_completed_at: '2026-08-05T08:00:00Z', running_job_id: null, staleness_seconds: 0 },
    watermarks,
    reconciliation: { status: 'MATCHED' as const, registered_physical_bytes: known('2147483648'), actual_oss_physical_bytes: known('2147483648'), unclassified_bytes: known('0'), multipart_bytes: known('0'), formula_version: 'reconcile_v1', note: null },
    totals: {
      logical_referenced_bytes: known('3221225472'), actual_oss_physical_bytes: known('2147483648'), billed_bytes: known('2147483648'), object_count: known('4'), reference_count: known('8'), reuse_rate: known('0.3333'),
      monthly_cost: { availability: 'KNOWN' as const, amount_minor: '128800', currency: 'CNY', kind: 'ACTUAL' as const, billing_period: '2026-08', source_revision: 'billing_rev_fx_01', formula_version: 'cost_v1', as_of: '2026-08-05T08:00:00Z' },
    },
    roles: [
      { role: 'SOURCE', physical_bytes: known('1073741824'), object_count: known('1'), average_object_bytes: known('1073741824'), primary_storage_class: 'STANDARD', growth_30d_bytes: known('1048576'), growth_30d_rate: known('0.01'), status: 'AVAILABLE' as const, allowed_actions: ['OPEN_OBJECTS'], display_scope: { storage_area_id: 'area_fx_01', area_label: '主存储区', masked: true as const } },
      { role: 'DERIVED', physical_bytes: known('536870912'), object_count: known('1'), average_object_bytes: known('536870912'), primary_storage_class: 'IA', growth_30d_bytes: known('0'), growth_30d_rate: known('0'), status: 'AVAILABLE' as const, allowed_actions: ['OPEN_OBJECTS'], display_scope: { storage_area_id: 'area_fx_02', area_label: '派生区', masked: true as const } },
      { role: 'PREVIEW', physical_bytes: known('268435456'), object_count: known('1'), average_object_bytes: known('268435456'), primary_storage_class: 'STANDARD', growth_30d_bytes: known('0'), growth_30d_rate: known('0'), status: 'AVAILABLE' as const, allowed_actions: ['OPEN_OBJECTS'], display_scope: { storage_area_id: 'area_fx_03', area_label: null, masked: true as const } },
      { role: 'EXPORT', physical_bytes: known('134217728'), object_count: known('1'), average_object_bytes: known('134217728'), primary_storage_class: 'ARCHIVE', growth_30d_bytes: known('0'), growth_30d_rate: known('0'), status: 'AVAILABLE' as const, allowed_actions: ['OPEN_OBJECTS'], display_scope: { storage_area_id: 'area_fx_04', area_label: null, masked: true as const } },
      { role: 'ROBOT_ASSET', physical_bytes: known('134217728'), object_count: known('1'), average_object_bytes: known('134217728'), primary_storage_class: 'STANDARD', growth_30d_bytes: known('0'), growth_30d_rate: known('0'), status: 'AVAILABLE' as const, allowed_actions: ['OPEN_OBJECTS'], display_scope: { storage_area_id: 'area_fx_05', area_label: null, masked: true as const } },
    ],
    classification_gaps: [],
    growth: [
      { month: '2026-07', role: 'SOURCE', physical_bytes: '1072693248', completeness: 'COMPLETE' as const },
      { month: '2026-08', role: 'SOURCE', physical_bytes: '1073741824', completeness: 'COMPLETE' as const },
    ],
    storage_classes: [
      { storage_class: 'STANDARD', physical_bytes: '1476395008', object_count: '3', availability: 'AVAILABLE' as const },
      { storage_class: 'IA', physical_bytes: '536870912', object_count: '1', availability: 'AVAILABLE' as const },
      { storage_class: 'ARCHIVE', physical_bytes: '134217728', object_count: '1', availability: 'AVAILABLE' as const },
    ],
    alerts: [{ type: 'SMALL_OBJECT', candidate_count: '1', candidate_bytes: '1024', threshold_bytes: '4096', as_of: '2026-08-05T08:00:00Z' }],
    allowed_actions: ['OPEN_OBJECTS', 'OPEN_COST'], partial_errors: [],
  },
  scope, request_id: 'req_fx_p12_overview', contract_version: 'v1' as const,
};

const objectBase = {
  snapshot_id: 'snapshot_fx_storage_01', classification_status: 'CLASSIFIED' as const, masked: true as const,
  media_type: 'application/octet-stream', status: 'AVAILABLE', reference_count: '2', business_references: [],
  protection_reasons: [], retention: { retain_until: null, source: 'APPLICATION' }, legal_hold: { active: false, count: '0', as_of: '2026-08-05T08:00:00Z' },
  lifecycle_rule_id: null, created_at: '2026-08-01T08:00:00Z', last_accessed_at: '2026-08-05T07:00:00Z', expires_at: null,
  allowed_actions: [{ action: 'VIEW_RESOURCE', allowed: true, reason_code: null }],
};

export const storageInventoryFixture = {
  items: [
    { ...objectBase, object_id: 'storage_object_fx_02', object_role: 'SOURCE', display_key: 'source/•••/02', physical_bytes: '1073741824', storage_class: 'STANDARD' },
    { ...objectBase, object_id: 'storage_object_fx_01', object_role: 'DERIVED', display_key: 'derived/•••/01', physical_bytes: '536870912', storage_class: 'IA' },
  ],
  page_info: { start_cursor: 'cursor_fx_storage_start', end_cursor: 'cursor_fx_storage_end', has_previous_page: false, has_next_page: false },
  snapshot_at: '2026-08-05T08:00:00Z', snapshot_id: 'snapshot_fx_storage_01', scope,
  request_id: 'req_fx_p12_objects', contract_version: 'v1' as const,
};

export const storageObjectDetailFixture = {
  data: storageInventoryFixture.items[0]!,
  scope,
  request_id: 'req_fx_p12_object_detail',
  contract_version: 'v1' as const,
};

export const storageEmptyInventoryFixture = {
  ...storageInventoryFixture,
  items: [],
  page_info: { start_cursor: null, end_cursor: null, has_previous_page: false, has_next_page: false },
  request_id: 'req_fx_p12_objects_empty',
};

export const storageCostFixture = {
  data: { billing_period: '2026-08', as_of: '2026-08-05T08:00:00Z', currency: 'CNY', storage: known('100000'), request: known('10000'), transfer: known('12000'), tax: known('6800'), total: known('128800'), formula_version: 'cost_v1' },
  scope, request_id: 'req_fx_p12_cost', contract_version: 'v1' as const,
};

const multipartBase = {
  upload_id: 'upload_fx_01', upload_version: 'upload_version_fx_01', upload_session_id: 'upload_session_fx_01',
  data_source_id: 'source_fx_01', source_format: 'ROS_BAG', status: 'PAUSED_RESUMABLE',
  expected_bytes: '2147483648', part_count: '8', started_at: '2026-08-04T06:00:00Z',
  last_activity_at: '2026-08-05T06:00:00Z', resumable_until: '2026-08-12T06:00:00Z',
  protection_reasons: [],
  allowed_actions: [{ action: 'VIEW_UPLOAD', allowed: true, reason_code: null }],
};

export const storageMultipartFixture = {
  items: [{ ...multipartBase, multipart_id: 'multipart_fx_01', received_bytes: '1073741824' }],
  page_info: { start_cursor: 'cursor_fx_multipart_start', end_cursor: 'cursor_fx_multipart_end', has_previous_page: false, has_next_page: false },
  snapshot_at: '2026-08-05T08:00:00Z', snapshot_id: 'snapshot_fx_storage_01', scope,
  request_id: 'req_fx_p12_multipart', contract_version: 'v1' as const,
};

export const storageEmptyMultipartFixture = {
  ...storageMultipartFixture,
  items: [],
  page_info: { start_cursor: null, end_cursor: null, has_previous_page: false, has_next_page: false },
  request_id: 'req_fx_p12_multipart_empty',
};

export const storageUnknownFixtures = {
  overview: { ...storageOverviewFixture, data: { ...storageOverviewFixture.data, roles: [...storageOverviewFixture.data.roles, { role: 'FUTURE_ROLE', physical_bytes: known('0'), object_count: known('0'), average_object_bytes: known('0'), primary_storage_class: 'FUTURE_CLASS', growth_30d_bytes: known('0'), growth_30d_rate: known('0'), status: 'UNAVAILABLE' as const, allowed_actions: [], display_scope: { storage_area_id: 'area_fx_future', area_label: null, masked: true as const } }] } },
  inventory: { ...storageInventoryFixture, items: [{ ...objectBase, object_id: 'storage_object_fx_future', object_role: 'FUTURE_ROLE', display_key: 'unknown/•••', physical_bytes: '0', storage_class: 'FUTURE_CLASS' }] },
  multipart: { ...storageMultipartFixture, items: [{ ...multipartBase, multipart_id: 'multipart_fx_future', received_bytes: '0', status: 'FUTURE_MULTIPART_STATE' }] },
};
