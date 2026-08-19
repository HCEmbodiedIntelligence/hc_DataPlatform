const at = '2026-08-05T08:00:00Z';
const pageInfo = {
  has_next_page: false,
  has_previous_page: false,
  start_cursor: null,
  end_cursor: null,
} as const;

export const lifecyclePoliciesFixture = [{
  policy_id: 'policy_fx_retain_raw', project_id: 'prj_fx_01', name: '原始数据长期保留',
  business_category: 'RAW', object_role: 'RAW', action: 'RETAIN', minimum_age_days: 0,
  priority: 10, state: 'ENABLED', version: 3, etag: '"v3"', created_at: at, updated_at: at,
}, {
  policy_id: 'policy_fx_review_pending', project_id: 'prj_fx_01', name: '待标注数据到期复核',
  business_category: 'PENDING_ANNOTATION', object_role: 'OTHER', action: 'REVIEW_EXPIRATION',
  minimum_age_days: 30, priority: 100, state: 'DRAFT', version: 1, etag: '"v1"',
  created_at: at, updated_at: at,
}, {
  policy_id: 'policy_fx_clean_cache', project_id: 'prj_fx_01', name: '可重建缓存清理',
  business_category: 'ANNOTATION_COMPLETE', object_role: 'REBUILDABLE_DERIVATIVE',
  action: 'CLEAN_REBUILDABLE_CACHE', minimum_age_days: 14, priority: 200,
  state: 'PAUSED', version: 2, etag: '"v2"', created_at: at, updated_at: at,
}] as const;

export const lifecycleAuditFixture = [{
  audit_id: 'audit_fx_001', project_id: 'prj_fx_01', policy_id: 'policy_fx_retain_raw',
  actor_id: 'usr_fx_admin', action: 'storage.lifecycle_policy.created', before_digest: null,
  after_digest: 'a'.repeat(64), request_id: 'req_fx_policy_create',
  details: { state: 'DRAFT', version: 1 }, occurred_at: at,
}, {
  audit_id: 'audit_fx_002', project_id: 'prj_fx_01', policy_id: 'policy_fx_retain_raw',
  actor_id: 'usr_fx_admin', action: 'storage.lifecycle_policy.enabled',
  before_digest: 'a'.repeat(64), after_digest: 'b'.repeat(64),
  request_id: 'req_fx_policy_enable', details: { state: 'ENABLED', version: 3 }, occurred_at: at,
}] as const;

export const robotModelsPageFixture = {
  items: [{
    id: 'robot_model_fx_01', manufacturer: 'HC Robotics', model_code: 'HC-A1',
    display_name: 'HC Assembly Arm', current_published_version_id: 'robot_model_version_fx_01',
  }],
  page_info: pageInfo, snapshot_at: at,
  scope: { organization_id: 'org_fx_01', project_id: 'prj_fx_01' },
  request_id: 'req_fx_robot_models', contract_version: 'robotics-calibration-schema.v1alpha1',
} as const;

export const robotsPageFixture = {
  items: [{
    id: 'robot_fx_01', display_name: 'Assembly Robot 01', serial_no: 'HC-ROBOT-0001',
    lifecycle_status: 'ACTIVE',
    connectivity: { state: 'ONLINE', observed_at: at, source: 'SERVER', reason_code: null },
  }],
  page_info: pageInfo, snapshot_at: at,
  scope: { project_id: 'prj_fx_01', region_code: 'cn-shanghai' },
  request_id: 'req_fx_robots', contract_version: 'robotics-calibration-schema.v1alpha1',
} as const;

export const calibrationSetsPageFixture = {
  items: [{
    id: 'calibration_set_fx_01', robot_instance_id: 'robot_fx_01',
    component_id: 'component_fx_camera_01', version: '4', snapshot_status: 'READY',
    availability: 'ACTIVE', content_hash: 'sha256:calibration-fixture',
    validation_context_hash: 'sha256:calibration-context-fixture',
    validation: {
      status: 'PASSED', content_hash: 'sha256:calibration-fixture',
      validation_context_hash: 'sha256:calibration-context-fixture',
      report_id: 'calibration_report_fx_01',
    },
    etag: '"calibration-4"', allowed_actions: ['VIEW'], blocked_reasons: [],
  }],
  page_info: pageInfo, snapshot_at: at,
  scope: { project_id: 'prj_fx_01', region_code: 'cn-shanghai' },
  request_id: 'req_fx_calibrations', contract_version: 'robotics-calibration-schema.v1alpha1',
} as const;

export const dataSchemasPageFixture = {
  items: [{
    schema_id: 'schema_fx_joint_state', family_id: 'schema_family_fx_robot',
    schema_version: '3', display_name: 'Robot Joint State', logical_type: 'JOINT_STATE',
    status: 'PUBLISHED', compatibility_mode: 'BACKWARD', compatibility_result: 'COMPATIBLE',
    schema_hash: {
      algorithm: 'SHA-256', canonicalization_version: 'canonical-json-v1',
      value: 'sha256:schema-fixture',
    },
    schema_definition: { fields: [{ name: 'position', type: 'float64[]' }] },
    etag: '"schema-3"', allowed_actions: ['VIEW'], blocked_reasons: [],
  }],
  page_info: pageInfo, snapshot_at: at,
  scope: { organization_id: 'org_fx_01' },
  request_id: 'req_fx_schemas', contract_version: 'robotics-calibration-schema.v1alpha1',
} as const;

export const accessBootstrapFixture = {
  data: {
    role_version: 'roles-v2-conditional', capability_catalog_version: 'capabilities-v2-conditional',
    policy_revision: 'policy_fx_07', project_policy_etag: '"policy-7"',
    summary: { active_members: '3', pending_invitations: '1', as_of: at },
    allowed_actions: ['INVITE_MEMBER', 'CHANGE_ROLE', 'ADD_SCOPE_GRANT'], blocked_reasons: [],
  },
  scope: { project_id: 'prj_fx_01' }, request_id: 'req_fx_access_bootstrap',
  contract_version: 'access-audit.v1',
} as const;

export const membersPageFixture = {
  items: [{
    id: 'membership_fx_admin',
    principal: {
      id: 'usr_fx_admin', display_name: 'Fixture 管理员',
      secondary_display: 'admin@example.invalid', identity_status: 'ACTIVE',
    },
    status: 'ACTIVE', role_id: 'PROJECT_ADMIN', role_version: 'roles-v2-conditional',
    joined_at: at, last_active_at: at, etag: '"member-1"',
    allowed_actions: ['CHANGE_ROLE'], blocked_reasons: [],
  }],
  page_info: pageInfo, snapshot_at: at, scope: { project_id: 'prj_fx_01' },
  request_id: 'req_fx_members', contract_version: 'access-audit.v1',
} as const;
