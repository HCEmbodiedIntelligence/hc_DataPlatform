export const ingestFixtureScope = {
  organization_id: 'org_fx_01', project_id: 'prj_fx_01', region_code: 'cn-shanghai',
} as const;
export const fixturePageInfo = { has_next_page: false, has_previous_page: false, start_cursor: null, end_cursor: null } as const;

export const dataSourceFixture = {
  id: 'source_fx_01', scope: ingestFixtureScope, name: '上海采集站 A', source_type: 'ROBOT', source_format: 'LEROBOT_V2',
  source_format_version: '2.1', adapter_version: 'adapter-2026-08',
  binding: { kind: 'ROBOT', robot_id: 'robot_fx_01', display_name: '装配机器人 01' },
  configuration: { kind: 'ROBOT', transport: 'HTTPS', endpoint_ref: 'endpoint-ref-fx-01', safe_endpoint_hint: 'https://edge.example.test:443', tls_profile_id: 'tls_fx_01' },
  administrative_state: 'ENABLED',
  credential: { kind: 'DEVICE_CERTIFICATE', state: 'CONFIGURED', credential_ref: 'cred_ref_fx_01', masked_hint: 'cert …A91F', version: '3', updated_at: '2026-08-05T07:00:00Z', expires_at: '2027-08-05T07:00:00Z', rotation_due_at: '2027-07-05T07:00:00Z' },
  connectivity: { state: 'ONLINE', last_check_state: 'SUCCEEDED', observed_config_version: '7', observed_credential_version: '3', checked_at: '2026-08-05T08:00:00Z', safe_error: null },
  heartbeat: { state: 'ONLINE', last_seen_at: '2026-08-05T08:09:30Z' },
  upload_policy: { code: 'STANDARD', label: '标准浏览器上传', max_object_size_bytes: '1099511627776' },
  last_upload: { upload_id: 'upload_fx_available', completed_at: '2026-08-05T06:00:00Z', verified_bytes: '4294967296', lifecycle_status: 'AVAILABLE' },
  config_version: '7', credential_version: '3', etag: 'source-rv-9',
  allowed_actions: ['VIEW', 'EDIT_CONFIGURATION', 'ROTATE_CREDENTIAL', 'TEST_CONNECTION', 'DISABLE', 'OPEN_UPLOADS'], blocked_reasons: [],
  created_at: '2026-08-01T08:00:00Z', updated_at: '2026-08-05T08:00:00Z',
} as const;

const { configuration: _configuration, ...dataSourceSummaryFixture } = dataSourceFixture;
void _configuration;
export { dataSourceSummaryFixture };

export const dataSourcePageFixture = {
  summary: { total_count: '1', online_count: '1', verified_bytes_today: '4294967296', abnormal_count: '0', as_of: '2026-08-05T08:10:00Z', timezone: 'Asia/Shanghai', definition_version: 'source-summary-v1' },
  facets: {
    source_types: [{ value: 'ROBOT', label: '机器人', count: '1' }], source_formats: [{ value: 'LEROBOT_V2', label: 'LeRobot v2', count: '1' }],
    robots: [{ id: 'robot_fx_01', name: '装配机器人 01', count: '1' }], upload_policies: [{ value: 'STANDARD', label: '标准', count: '1' }],
    administrative_states: [{ value: 'ENABLED', label: '已启用', count: '1' }], connectivity_states: [{ value: 'ONLINE', label: '在线', count: '1' }],
    credential_states: [{ value: 'CONFIGURED', label: '已配置', count: '1' }], heartbeat_states: [{ value: 'ONLINE', label: '在线', count: '1' }],
  },
  items: [dataSourceSummaryFixture], page_info: fixturePageInfo, snapshot_at: '2026-08-05T08:10:00Z', allowed_actions: ['CREATE'], component_errors: [],
  scope: ingestFixtureScope, request_id: 'req_fx_p02_page', contract_version: 'ingest.v1alpha1',
} as const;

export const uploadSessionFixture = {
  upload_id: 'upload_fx_quarantined', scope: ingestFixtureScope,
  data_source: { id: 'source_fx_01', name: '上海采集站 A', source_type: 'ROBOT', source_format: 'LEROBOT_V2', configuration_version: '7', credential_version: '3', upload_policy_version: '4' },
  target_dataset: { id: 'dataset_fx_01', name: '装配观测数据集' }, result: null, supersedes_upload_id: null,
  source_format: 'LEROBOT_V2', source_format_version: '2.1', adapter_version: 'adapter-2026-08', lifecycle_status: 'QUARANTINED', verification_status: 'FAILED',
  progress: { expected_bytes: '1099511627776', confirmed_received_bytes: '1099511627776', completed_parts: '16384', total_parts: '16384', completed_objects: '1', total_objects: '1', throughput_bytes_per_second: null, estimated_remaining_seconds: null, verification_stage: 'OBJECT_SHA256' },
  source_manifest: { manifest_id: 'manifest_fx_01', revision: '1', schema_version: 'source-upload-manifest/v1', canonicalization: 'RFC8785', sha256: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', object_set_hash: 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc', source_format: 'LEROBOT_V2', source_format_version: '2.1', adapter_version: 'adapter-2026-08', declared_object_count: '1', declared_bytes: '1099511627776', submitted_by: { id: 'user_fx_01', display_name: 'Fixture Operator' }, submitted_at: '2026-08-05T08:20:00Z', status: 'PARSED', schema_issue_counts: { error: '0', warning: '0', info: '0' } },
  latest_verification_run_id: 'verification_run_fx_failed', active_job_ids: [], created_by: { id: 'user_fx_01', display_name: 'Fixture Operator' },
  created_at: '2026-08-05T08:10:00Z', updated_at: '2026-08-05T08:22:00Z', etag: 'upload-rv-8', resource_version: '8',
  allowed_actions: ['VIEW', 'RETRY_VERIFY', 'CREATE_REPLACEMENT'], blocked_reasons: [],
} as const;

export const uploadingSessionFixture = {
  ...uploadSessionFixture,
  upload_id: 'upload_fx_uploading', lifecycle_status: 'UPLOADING', verification_status: 'NOT_STARTED',
  source_manifest: null, latest_verification_run_id: null, active_job_ids: [], result: null,
  progress: { ...uploadSessionFixture.progress, confirmed_received_bytes: '536870912000', completed_parts: '8000', completed_objects: '0', verification_stage: null },
  allowed_actions: ['VIEW', 'PAUSE', 'CANCEL'], blocked_reasons: [], etag: 'upload-rv-3', resource_version: '3',
} as const;

export const rawObjectFixture = {
  object_id: 'object_fx_failed', relative_path: 'data/chunk-000.parquet', source_role: 'DATA', media_type: 'application/vnd.apache.parquet',
  size_bytes: '1099511627776', multipart_status: 'QUARANTINED', completed_parts: '16384', total_parts: '16384', etag: 'multipart-etag-fx-1',
  declared_sha256: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', verified_sha256: 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
  checksum_status: 'MISMATCH', verification_status: 'FAILED', resource_version: '17', updated_at: '2026-08-05T08:25:00Z', allowed_actions: ['VIEW_PARTS'],
  blocked_reasons: [{ code: 'QUARANTINED', message: '普通 Dataset 访问已阻断。' }],
} as const;

export const verificationRunFixture = {
  verification_run_id: 'verification_run_fx_failed', supersedes_run_id: null,
  object_set_hash: 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc', manifest_sha256: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
  adapter_version: 'adapter-2026-08', status: 'FAILED',
  stages: [
    { code: 'MANIFEST_SCHEMA', status: 'PASSED', started_at: '2026-08-05T08:21:00Z', finished_at: '2026-08-05T08:21:01Z', job_id: 'job_verify_failed', finding_count: '0', retryable: false, skip_reason: null },
    { code: 'OBJECT_EXISTENCE_SIZE', status: 'PASSED', started_at: '2026-08-05T08:21:01Z', finished_at: '2026-08-05T08:21:02Z', job_id: 'job_verify_failed', finding_count: '0', retryable: false, skip_reason: null },
    { code: 'OBJECT_SHA256', status: 'FAILED', started_at: '2026-08-05T08:21:02Z', finished_at: '2026-08-05T08:22:00Z', job_id: 'job_verify_failed', finding_count: '1', retryable: false, skip_reason: null },
    { code: 'ADAPTER_PARSE', status: 'SKIPPED', started_at: null, finished_at: null, job_id: 'job_verify_failed', finding_count: '0', retryable: false, skip_reason: 'blocked by OBJECT_SHA256' },
  ],
  finding_counts: { info: '0', warning: '0', error: '1' }, job_id: 'job_verify_failed', started_at: '2026-08-05T08:21:00Z', finished_at: '2026-08-05T08:22:00Z',
  created_at: '2026-08-05T08:20:00Z', resource_version: '5',
} as const;

export const quarantineFixture = {
  quarantine_id: 'quarantine_fx_01', upload_id: 'upload_fx_quarantined', verification_run_id: 'verification_run_fx_failed',
  object_set_hash: 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc', manifest_sha256: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
  reason_code: 'OBJECT_SHA256_MISMATCH', safe_summary: '对象摘要不匹配；普通 Dataset 访问已阻断。', disposition: 'OPEN',
  retain_until: '2026-09-05T08:22:00Z', created_at: '2026-08-05T08:22:00Z', superseded_by_quarantine_id: null,
} as const;

export const uploadListFixture = { items: [uploadingSessionFixture, uploadSessionFixture], page_info: fixturePageInfo, snapshot_at: '2026-08-05T08:22:00Z', scope: ingestFixtureScope, request_id: 'req_fx_p03_list', contract_version: 'ingest.v1alpha1' } as const;
export const uploadCreationOptionsFixture = {
  data: {
    data_sources: [{ id: 'source_fx_01', name: '上海采集站 A', source_type: 'ROBOT', source_format: 'LEROBOT_V2', configuration_version: '7', credential_version: '3', upload_policy_version: '4', allowed: true, blocked_reasons: [] }],
    datasets: [{ id: 'dataset_fx_01', name: '装配观测数据集', allowed: true, blocked_reasons: [] }],
    formats: [{ code: 'LEROBOT_V2', version: '2.1', adapter_version: 'adapter-2026-08', allowed_extensions: ['parquet', 'json'] }],
    policy_summaries: [{ policy_version: '4', label: '标准浏览器上传', max_object_count: '10000', max_total_bytes: '10995116277760', default_part_size_bytes: '67108864', max_concurrency: 4 }],
    allowed_actions: ['CREATE'], blocked_reasons: [],
  }, scope: ingestFixtureScope, request_id: 'req_fx_p03_options', contract_version: 'ingest.v1alpha1',
} as const;
export const uploadBootstrapFixture = { data: { session: uploadSessionFixture, objects: [rawObjectFixture], latest_verification_run: verificationRunFixture, latest_quarantine: quarantineFixture, active_jobs: [] }, scope: ingestFixtureScope, request_id: 'req_fx_p04_bootstrap', contract_version: 'ingest.v1alpha1' } as const;
export const uploadObjectPageFixture = { items: [rawObjectFixture], page_info: fixturePageInfo, snapshot_at: '2026-08-05T08:25:00Z', scope: ingestFixtureScope, request_id: 'req_fx_objects', contract_version: 'ingest.v1alpha1' } as const;
export const verificationRunPageFixture = { items: [verificationRunFixture], page_info: fixturePageInfo, snapshot_at: '2026-08-05T08:22:00Z', scope: ingestFixtureScope, request_id: 'req_fx_runs', contract_version: 'ingest.v1alpha1' } as const;

export const uploadEventFixture = {
  event_id: 'upload_event_fx_01', event_type: 'upload.verification.completed', event_level: 'ERROR', occurred_at: '2026-08-05T08:22:00Z',
  actor: { kind: 'SYSTEM', id: null, display_name: 'Ingest Verifier' }, from_state: 'VERIFYING', to_state: 'QUARANTINED',
  resource_ref: { type: 'UPLOAD_SESSION', id: 'upload_fx_quarantined', version: '8' }, job_id: 'job_verify_failed', request_id: 'req_fx_verify_failed',
  safe_payload: { previous_status: 'VERIFYING', new_status: 'QUARANTINED', reason_code: 'OBJECT_SHA256_MISMATCH', verification_run_id: 'verification_run_fx_failed', quarantine_id: 'quarantine_fx_01', retryable: false },
} as const;
export const uploadEventPageFixture = { items: [uploadEventFixture], page_info: fixturePageInfo, snapshot_at: '2026-08-05T08:22:00Z', scope: ingestFixtureScope, request_id: 'req_fx_events', contract_version: 'ingest.v1alpha1' } as const;

export const asyncJobFixture = {
  job_id: 'job_verify_retry', job_type: 'UPLOAD_VERIFICATION', status: 'QUEUED', resource_type: 'VERIFICATION_RUN', resource_id: 'verification_run_fx_retry',
  progress: { completed: '0', total: '6', unit: 'STAGE' }, result_ref: null, error: null, created_at: '2026-08-05T08:45:00Z', updated_at: '2026-08-05T08:45:00Z', resource_version: '8',
} as const;

export const internalUploadJobFixture = {
  id: 'job_verify_retry', type: 'UPLOAD_VERIFICATION', status: 'QUEUED', stage: 'MANIFEST_SCHEMA', progress: { completed: '0', total: '6', unit: 'STAGE' },
  resource_ref: { resource_type: 'VERIFICATION_RUN', resource_id: 'verification_run_fx_retry' }, result_ref: null, safe_error: null, etag: 'job-rv-8', allowed_actions: ['VIEW'],
  created_at: '2026-08-05T08:45:00Z', updated_at: '2026-08-05T08:45:00Z',
} as const;
