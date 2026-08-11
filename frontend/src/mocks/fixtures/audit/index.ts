const scope = { organization_id: 'org_fx_01', project_id: 'prj_fx_01', region_code: 'cn-shanghai' };
const parentRefs: Array<{ type: string; id: string }> = [];

function auditEvent(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1 as const,
    event_id: 'audit_event_fx_02',
    event_name: 'access.membership.role_changed',
    occurred_at: '2026-08-05T08:00:00Z',
    recorded_at: '2026-08-05T08:00:01Z',
    actor: { type: 'USER', principal_id: 'usr_fx_admin', display_name: '管理员 A', role_ids: ['PROJECT_ADMIN'] },
    scope,
    resource: { type: 'PROJECT_MEMBERSHIP', id: 'membership_fx_02', display_name: '成员 U2', parent_refs: parentRefs },
    request: { request_id: 'req_fx_access_02', job_id: null, client_type: 'WEB', ip_address: '101.0.0.x', device_summary: 'Chrome / Windows' },
    outcome: { status: 'SUCCEEDED', reason_code: null, http_status: 200 },
    risk: { level: 'HIGH', signal_codes: ['PRIVILEGE_CHANGE'] },
    change: { summary_code: 'ROLE_CHANGED', changed_fields: ['role_id'], before: { role_id: 'PROJECT_DEVELOPER' }, after: { role_id: 'PROJECT_ADMIN' }, omitted_field_classes: [] },
    relationships: { parent_event_id: null, related_event_ids: [], resource_refs: [] },
    retention: { class: 'SECURITY', policy_version: 'retention_v1', retain_until: '2027-08-05T08:00:00Z', legal_hold: false },
    integrity: { status: 'PASSED', version: 'integrity_v1', record_digest: 'sha256:fixture-record-digest-00000001', checkpoint_id: 'checkpoint_fx_01' },
    producer: { service: 'access-service', producer_event_id: 'producer_event_fx_02', contract_version: 'audit-event-v1' },
    allowed_actions: ['VIEW', 'VIEW_RESOURCE'],
    blocked_reasons: [],
    ...overrides,
  };
}

export const auditEventFixture = auditEvent();
export const auditEventSecondFixture = auditEvent({
  event_id: 'audit_event_fx_01',
  event_name: 'storage.inventory_refresh.completed',
  occurred_at: '2026-08-05T07:59:00Z',
  recorded_at: '2026-08-05T07:59:01Z',
  actor: { type: 'SYSTEM', principal_id: null, display_name: '存储服务', role_ids: [] },
  resource: { type: 'STORAGE_OBJECT', id: 'storage_object_fx_01', display_name: '对象快照', parent_refs: [] },
  request: { request_id: 'req_fx_storage_01', job_id: 'job_fx_inventory_01', client_type: 'WORKER', ip_address: null, device_summary: null },
  outcome: { status: 'PARTIAL', reason_code: 'HAS_GAP', http_status: null },
  risk: { level: 'MEDIUM', signal_codes: ['RECONCILIATION_GAP'] },
  change: null,
});

export const auditBootstrapFixture = {
  data: {
    scope,
    metrics: { today: '142', high_risk: '3', failed: '2', active_actors: '12' },
    as_of: '2026-08-05T08:01:00Z', catalog_version: 'p19-v1-142', policy_version: 'audit-view-v1',
    integrity: 'PASSED' as const, allowed_actions: ['VIEW'], blocked_reasons: [],
  },
  scope, request_id: 'req_fx_p19_bootstrap', contract_version: 'v1' as const,
};

export const auditFacetsFixture = {
  data: {
    event_names: ['access.membership.role_changed', 'storage.inventory_refresh.completed'],
    actor_ids: ['usr_fx_admin'],
    resource_types: ['PROJECT_MEMBERSHIP', 'STORAGE_OBJECT'],
    outcomes: ['SUCCEEDED', 'PARTIAL'] as const,
    risk_levels: ['HIGH', 'MEDIUM'] as const,
  },
  scope, request_id: 'req_fx_p19_facets', contract_version: 'v1' as const,
};

export const auditEventsFixture = {
  items: [auditEventFixture, auditEventSecondFixture],
  page_info: { has_next_page: false, has_previous_page: false, start_cursor: 'cursor_fx_audit_start', end_cursor: 'cursor_fx_audit_end' },
  snapshot_at: '2026-08-05T08:01:00Z',
  redaction: { policy_version: 'audit-view-v1', omitted_field_classes: ['precise_ip'] },
  scope, request_id: 'req_fx_p19_list', contract_version: 'v1' as const,
};

export const auditEventEnvelopeFixture = { data: auditEventFixture, scope, request_id: 'req_fx_p19_detail', contract_version: 'v1' as const };

export const auditEmptyEventsFixture = {
  ...auditEventsFixture,
  items: [],
  page_info: { has_next_page: false, has_previous_page: false, start_cursor: null, end_cursor: null },
  request_id: 'req_fx_p19_empty',
};

export const auditUnknownEventFixture = auditEvent({
  event_id: 'audit_event_fx_future',
  event_name: 'future.domain.changed',
  actor: { type: 'FUTURE_ACTOR', principal_id: 'usr_fx_future', display_name: '未来主体', role_ids: ['FUTURE_ROLE'] },
  outcome: { status: 'FUTURE_OUTCOME', reason_code: null, http_status: null },
  risk: { level: 'FUTURE_RISK', signal_codes: [] },
  allowed_actions: ['VIEW_RESOURCE', 'EXPORT_EVENT'],
});

export const auditUnknownEventsFixture = {
  ...auditEventsFixture,
  items: [auditUnknownEventFixture],
  page_info: { has_next_page: false, has_previous_page: false, start_cursor: 'cursor_fx_future', end_cursor: 'cursor_fx_future' },
  request_id: 'req_fx_p19_unknown',
};

export const auditCapabilityProjectionFixtures = {
  fullAccess: auditEventEnvelopeFixture,
  reviewContext: { ...auditEventEnvelopeFixture, data: auditEvent({ request: { request_id: 'req_fx_review_context', job_id: null, client_type: 'WEB', ip_address: 'REDACTED', device_summary: null }, allowed_actions: ['VIEW'] }) },
  readOnly: { ...auditEventEnvelopeFixture, data: auditEvent({ request: { request_id: 'req_fx_read_only', job_id: null, client_type: 'WEB', ip_address: null, device_summary: null }, change: null, integrity: { status: 'PASSED', version: 'integrity_v1', record_digest: null, checkpoint_id: null }, allowed_actions: ['VIEW'] }) },
} as const;
