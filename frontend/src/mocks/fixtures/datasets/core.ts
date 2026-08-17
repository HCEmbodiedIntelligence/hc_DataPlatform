export const datasetContractVersion = 'dataset-version-review.v1alpha1' as const;
export const datasetFixtureScope = {
  organization_id: 'org_fx_01',
  project_id: 'prj_fx_01',
  region_code: 'cn-shanghai',
} as const;
export const datasetIds = {
  dataset: 'dataset_fx_01',
  empty: 'dataset_fx_empty',
  reviewing: 'version_fx_review_01',
  returned: 'version_fx_returned_01',
  ready: 'version_fx_ready_01',
  episode: 'episode_fx_01',
  revision: 'revision_fx_01',
  stream: 'stream_fx_cam_01',
} as const;
const allowed = (action: string) => ({ action, allowed: true, blocked_reasons: [] });
export const responseMeta = (requestId: string) => ({
  request_id: requestId,
  trace_id: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  correlation_id: 'corr_fx_dataset',
  generated_at: '2026-08-06T12:00:05Z',
  as_of: '2026-08-06T12:00:00Z',
  projection_version: 'datasets-fixture-1',
  event_cursor: null,
});

export const datasetWire = {
  scope: datasetFixtureScope,
  dataset_id: datasetIds.dataset,
  name: 'Assembly dataset',
  description: 'Fixed versions for assembly tasks',
  labels: ['assembly', 'line-a'],
  availability: 'ACTIVE',
  owner: { id: 'user_fx_admin', display_name: 'Data Admin' },
  created_at: '2026-08-01T08:00:00Z',
  updated_at: '2026-08-06T12:00:00Z',
  etag: '"dataset-rv-7"',
  allowed_actions: [allowed('OPEN_VERSION'), allowed('START_INGEST')],
} as const;

export const currentReadyWire = {
  version_id: datasetIds.ready,
  display_version: 'v3',
  kind: 'CLEANED',
  status: 'READY',
  published_at: '2026-08-06T11:55:00Z',
  manifest_sha256: 'd'.repeat(64),
} as const;
export const datasetListFixture = {
  items: [
    {
      scope: datasetFixtureScope,
      dataset_id: datasetIds.dataset,
      name: datasetWire.name,
      dataset_created_at: datasetWire.created_at,
      dataset_activity_at: datasetWire.updated_at,
      current_version: currentReadyWire,
      episode_count: '1',
      pending_review_version_count: '1',
      returned_version_count: '1',
      actionable_draft_count: '1',
      allowed_actions: [allowed('OPEN_DATASET'), allowed('OPEN_EPISODE'), allowed('START_INGEST')],
    },
    {
      scope: datasetFixtureScope,
      dataset_id: datasetIds.empty,
      name: 'Waiting for ingest',
      dataset_created_at: '2026-08-06T10:00:00Z',
      dataset_activity_at: '2026-08-06T10:00:00Z',
      current_version: null,
      episode_count: '0',
      pending_review_version_count: '0',
      returned_version_count: '0',
      actionable_draft_count: '0',
      allowed_actions: [allowed('OPEN_DATASET'), allowed('START_INGEST')],
    },
  ],
  page_info: {
    after: null,
    before: null,
    has_next: false,
    has_previous: false,
    limit: 20,
    total_count: '2',
  },
  snapshot_at: '2026-08-06T12:00:00Z',
  snapshot_id: 'cursor_dataset_snapshot_fx_01',
  scope: datasetFixtureScope,
  request_id: 'req_fx_p05_list',
  contract_version: datasetContractVersion,
} as const;

export const datasetsPageCapabilitiesFixture = {
  data: {
    scope: datasetFixtureScope,
    authorization_revision: 'role_fx_dataset_01',
    allowed_actions: ['CREATE_DATASET', 'START_INGEST', 'EXPORT_MANIFEST_LIST'],
    blocked_reasons: [],
  },
  meta: responseMeta('req_fx_p05_capabilities'),
} as const;

export const datasetSummaryFixture = {
  data: {
    scope: datasetFixtureScope,
    dataset_count: '2',
    episode_count: '1',
    pending_review_version_count: '1',
    returned_version_count: '1',
    actionable_draft_count: '1',
    normalized_filters: {},
  },
  meta: responseMeta('req_fx_p05_summary'),
} as const;

export const datasetFacetsFixture = {
  data: {
    scope: datasetFixtureScope,
    normalized_filters: {},
    robots: [{ value: 'robot_fx_01', count: '1' }],
    robot_models: [{ value: 'robot_model_fx_01', count: '1' }],
    tasks: [{ value: 'assembly', count: '1' }],
    scenes: [{ value: 'line-a', count: '1' }],
    asset_states: [
      { value: 'ready', count: '1' },
      { value: 'pending_ingest', count: '1' },
    ],
    storage_classes: [{ value: 'standard', count: '1' }],
    channels: [{ value: '/camera/front', count: '1' }],
  },
  meta: responseMeta('req_fx_p05_facets'),
} as const;

export const datasetBootstrapFixture = {
  data: {
    scope: datasetFixtureScope,
    dataset: datasetWire,
    current_ready_version: currentReadyWire,
    suggested_version_id: datasetIds.reviewing,
    summary: {
      episode_count: '1',
      effective_duration_ns: '10000000000',
      source_bytes: '1024',
      required_physical_bytes: '2048',
      actual_oss_bytes: '2048',
      pending_review_version_count: '1',
      returned_version_count: '1',
      actionable_draft_count: '1',
      calculated_at: '2026-08-06T12:00:00Z',
      calculation_state: 'SETTLED',
    },
  },
  meta: responseMeta('req_fx_p06_bootstrap'),
} as const;

export const reviewingVersionWire = {
  scope: datasetFixtureScope,
  dataset_id: datasetIds.dataset,
  version_id: datasetIds.reviewing,
  display_version: 'v4',
  kind: 'CLEANED',
  status: 'REVIEWING',
  created_at: '2026-08-06T11:00:00Z',
  etag: '"version-review-rv-4"',
  version_token: 'version-token-review-00000001',
  source_draft_id: 'draft_fx_source_01',
  delivery_status: 'NOT_STARTED',
  approved_review_decision_id: null,
  allowed_actions: [allowed('REVIEW_VERSION'), allowed('CREATE_ISSUE')],
} as const;
export const returnedVersionWire = {
  scope: datasetFixtureScope,
  dataset_id: datasetIds.dataset,
  version_id: datasetIds.returned,
  display_version: 'v2',
  kind: 'CLEANED',
  status: 'RETURNED',
  created_at: '2026-08-05T10:00:00Z',
  etag: '"version-returned-rv-5"',
  version_token: 'version-token-returned-0001',
  source_draft_id: 'draft_fx_source_01',
  review_decision_id: 'review_decision_fx_01',
  review_finding_ids: ['review_finding_fx_01'],
  successor_draft_id: 'draft_fx_successor_01',
  supersedes_draft_id: 'draft_fx_source_01',
  returned_from_version_id: datasetIds.returned,
  returned_from_review_decision_id: 'review_decision_fx_01',
  allowed_actions: [allowed('OPEN_VERSION')],
} as const;
export const versionPageFixture = {
  items: [reviewingVersionWire, returnedVersionWire],
  page_info: {
    after: null,
    before: null,
    has_next: false,
    has_previous: false,
    limit: 20,
    total_count: '2',
  },
  snapshot_at: '2026-08-06T12:00:00Z',
  snapshot_id: 'cursor_versions_snapshot_fx_01',
  scope: datasetFixtureScope,
  request_id: 'req_fx_p06_versions',
  contract_version: datasetContractVersion,
} as const;

export const episodePageFixture = {
  items: [
    {
      scope: datasetFixtureScope,
      dataset_id: datasetIds.dataset,
      version_id: datasetIds.reviewing,
      episode_id: datasetIds.episode,
      selected_revision: {
        episode_id: datasetIds.episode,
        revision_id: datasetIds.revision,
        ordinal: 0,
        content_sha256: 'a'.repeat(64),
      },
      included: true,
      success_state: 'SUCCEEDED',
      task: 'assembly',
      robot_id: 'robot_fx_01',
      review_status: 'HAS_FINDING',
      review_finding_count: '1',
    },
  ],
  page_info: {
    after: null,
    before: null,
    has_next: false,
    has_previous: false,
    limit: 20,
    total_count: '1',
  },
  snapshot_at: '2026-08-06T12:00:00Z',
  snapshot_id: 'snapshot-token-version-review-0001',
  scope: datasetFixtureScope,
  request_id: 'req_fx_p06_episodes',
  contract_version: datasetContractVersion,
} as const;

export const datasetVersionSchemaFixture = {
  data: {
    scope: datasetFixtureScope,
    dataset_id: datasetIds.dataset,
    version_id: datasetIds.reviewing,
    schema_snapshot: {
      reference_type: 'DATASET_SCHEMA',
      reference_id: 'schema_snapshot_fx_01',
      reference_version: 'schema-v7',
      sha256: 'c'.repeat(64),
    },
    channel_count: '4',
  },
  meta: responseMeta('req_fx_p06_schema'),
} as const;
export const sourceProvenanceFixture = {
  items: [
    {
      scope: datasetFixtureScope,
      dataset_id: datasetIds.dataset,
      version_id: datasetIds.reviewing,
      provenance_id: 'provenance_fx_01',
      upload_id: 'upload_fx_01',
      source_id: 'source_fx_01',
      source_display_name: 'Line A ingest',
      source_manifest_id: 'source_manifest_fx_01',
      source_manifest_sha256: '3'.repeat(64),
      verified_object_set_hash: '4'.repeat(64),
      registered_at: '2026-08-06T09:00:00Z',
    },
  ],
  page_info: {
    after: null,
    before: null,
    has_next: false,
    has_previous: false,
    limit: 20,
    total_count: '1',
  },
  snapshot_at: '2026-08-06T12:00:00Z',
  snapshot_id: 'cursor_source_snapshot_fx_01',
  scope: datasetFixtureScope,
  request_id: 'req_fx_p06_sources',
  contract_version: datasetContractVersion,
} as const;
export const datasetVersionCapacityFixture = {
  data: {
    scope: datasetFixtureScope,
    dataset_id: datasetIds.dataset,
    version_id: datasetIds.reviewing,
    state: 'SETTLED',
    source_bytes: '1024',
    required_physical_bytes: '2048',
    actual_oss_bytes: '2048',
    calculated_at: '2026-08-06T12:00:00Z',
    basis_revision: 'capacity-rev-5',
  },
  meta: responseMeta('req_fx_p06_capacity'),
} as const;

export const versionBootstrapFixture = {
  data: {
    scope: datasetFixtureScope,
    dataset_id: datasetIds.dataset,
    version_id: datasetIds.reviewing,
    snapshot_token: 'snapshot-token-version-review-0001',
    operational_revision: 'operational-revision-11',
    version: reviewingVersionWire,
  },
  meta: responseMeta('req_fx_p07_reviewing'),
} as const;
export const episodeRevisionFixture = {
  data: {
    scope: datasetFixtureScope,
    dataset_id: datasetIds.dataset,
    version_id: datasetIds.reviewing,
    episode_id: datasetIds.episode,
    revision_id: datasetIds.revision,
    ordinal: 0,
    content_sha256: 'a'.repeat(64),
    started_at_ns: '0',
    duration_ns: '10000000000',
    streams: [
      {
        episode_stream_id: datasetIds.stream,
        channel_path: '/camera/front',
        kind: 'VIDEO',
        t_start_ns: '0',
        t_end_ns: '10000000000',
      },
    ],
  },
  meta: responseMeta('req_fx_revision'),
} as const;

export const reviewChecksFixture = {
  data: {
    scope: datasetFixtureScope,
    dataset_id: datasetIds.dataset,
    output_version_id: datasetIds.reviewing,
    source_draft_id: 'draft_fx_source_01',
    expected_status: 'REVIEWING',
    review_token: 'review-token-0000000000000000000000000001',
    review_token_expires_at: '2099-08-11T12:10:00Z',
    version_token: 'version-token-review-00000001',
    blockers: [],
    finding_catalog: {
      version: 'review-catalog-v3',
      finding_types: [
        {
          code: 'RANGE_QUALITY',
          label: 'Range quality',
          allowed_severities: ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'],
        },
      ],
      severities: [
        { code: 'LOW', label: 'Low', rank: 1 },
        { code: 'MEDIUM', label: 'Medium', rank: 2 },
        { code: 'HIGH', label: 'High', rank: 3 },
        { code: 'CRITICAL', label: 'Critical', rank: 4 },
      ],
      note_min_length: 1,
      note_max_length: 8192,
    },
    eligible_targets: [
      {
        output_revision_id: datasetIds.revision,
        episode_id: datasetIds.episode,
        streams: [
          {
            episode_stream_id: datasetIds.stream,
            channel_path: '/camera/front',
            kind: 'VIDEO',
            t_start_ns: '0',
            t_end_ns: '10000000000',
          },
        ],
      },
    ],
  },
  meta: responseMeta('req_fx_p07_checks'),
} as const;
export const returnResultFixture = {
  data: {
    scope: datasetFixtureScope,
    review_decision: {
      id: 'review_decision_fx_01',
      output_version_id: datasetIds.reviewing,
      decision: 'RETURNED',
      immutable: true,
      created_at: '2026-08-06T12:00:05Z',
    },
    findings: [
      {
        id: 'review_finding_fx_01',
        output_revision_id: datasetIds.revision,
        episode_stream_id: datasetIds.stream,
        start_ns: '1000000000',
        end_ns: '2000000000',
        finding_type: 'RANGE_QUALITY',
        severity: 'HIGH',
        note: 'Camera occlusion requires another cleaning pass.',
        immutable: true,
        created_at: '2026-08-06T12:00:05Z',
      },
    ],
    review_finding_ids: ['review_finding_fx_01'],
    output_version: {
      id: datasetIds.reviewing,
      status: 'RETURNED',
      version_token: 'version-token-returned-0002',
    },
    successor_draft_id: 'draft_fx_successor_01',
    supersedes_draft_id: 'draft_fx_source_01',
    returned_from_version_id: datasetIds.reviewing,
    returned_from_review_decision_id: 'review_decision_fx_01',
  },
  meta: responseMeta('req_fx_p07_return'),
} as const;
export const approveResultFixture = {
  review_decision: {
    id: 'review_decision_fx_approved_01',
    output_version_id: datasetIds.reviewing,
    decision: 'APPROVED',
    immutable: true,
    created_at: '2026-08-06T12:00:05Z',
  },
  output_version: {
    id: datasetIds.reviewing,
    status: 'REVIEWING',
    version_token: 'version-token-approved-0002',
  },
  job: {
    job_id: 'job_fx_manifest_materialization',
    kind: 'MANIFEST_MATERIALIZATION',
    status: 'QUEUED',
  },
  scope: datasetFixtureScope,
  request_id: 'req_fx_p07_approve',
  contract_version: datasetContractVersion,
} as const;

export const manifestFixture = {
  items: [
    {
      entry_id: 'entry_fx_manifest_01',
      episode_id: datasetIds.episode,
      revision_id: datasetIds.revision,
      role: 'REVISION',
      size_bytes: '1024',
      sha256: 'a'.repeat(64),
      safe_locator: null,
    },
  ],
  page_info: {
    after: null,
    before: null,
    has_next: false,
    has_previous: false,
    limit: 20,
    total_count: '1',
  },
  snapshot_at: '2026-08-06T12:00:05Z',
  snapshot_id: 'snapshot-token-version-ready-000001',
  scope: datasetFixtureScope,
  request_id: 'req_fx_p07_manifest',
  contract_version: datasetContractVersion,
  dataset_id: datasetIds.dataset,
  version_id: datasetIds.ready,
  content_snapshot_id: 'content_snapshot_fx_01',
  manifest: {
    manifest_id: 'manifest_fx_ready_01',
    format_version: '1',
    canonicalization: 'RFC8785',
    sha256: 'd'.repeat(64),
    entry_count: '1',
  },
} as const;
export const versionSchemaFixture = {
  data: {
    scope: datasetFixtureScope,
    dataset_id: datasetIds.dataset,
    version_id: datasetIds.reviewing,
    snapshot_token: 'snapshot-token-version-review-0001',
    schema_snapshot: {
      reference_type: 'DATASET_SCHEMA',
      reference_id: 'schema_snapshot_fx_01',
      reference_version: 'schema-v7',
      sha256: 'c'.repeat(64),
    },
    channel_count: '2',
    channels: [
      { channel_id: 'channel_fx_rgb', name: 'camera.front', data_type: 'video/rgb', unit: null },
      {
        channel_id: 'channel_fx_joint',
        name: 'joint.position',
        data_type: 'float64[7]',
        unit: 'rad',
      },
    ],
  },
  meta: responseMeta('req_fx_p07_schema'),
} as const;
export const requiredStorageFixture = {
  items: [
    {
      scope: datasetFixtureScope,
      dataset_id: datasetIds.dataset,
      version_id: datasetIds.reviewing,
      object_id: 'object_fx_required_01',
      role: 'REVISION',
      size_bytes: '2048',
      reuse: 'NEW',
      protection: 'IMMUTABLE',
      safe_locator: null,
    },
  ],
  page_info: {
    after: null,
    before: null,
    has_next: false,
    has_previous: false,
    limit: 20,
    total_count: '1',
  },
  snapshot_at: '2026-08-06T12:00:05Z',
  snapshot_id: 'snapshot-token-version-review-0001',
  scope: datasetFixtureScope,
  request_id: 'req_fx_p07_required_storage',
  contract_version: datasetContractVersion,
} as const;
export const operationalInventoryFixture = {
  items: [
    {
      scope: datasetFixtureScope,
      dataset_id: datasetIds.dataset,
      version_id: datasetIds.reviewing,
      inventory_id: 'inventory_fx_01',
      kind: 'MATERIALIZATION',
      operational_revision: 'operational-revision-11',
      status: 'SUCCEEDED',
      size_bytes: '2048',
      job_id: null,
      created_at: '2026-08-06T12:00:00Z',
      completed_at: '2026-08-06T12:00:04Z',
    },
  ],
  page_info: {
    after: null,
    before: null,
    has_next: false,
    has_previous: false,
    limit: 20,
    total_count: '1',
  },
  snapshot_at: '2026-08-06T12:00:05Z',
  snapshot_id: 'operational-revision-11',
  scope: datasetFixtureScope,
  request_id: 'req_fx_p07_operational_inventory',
  contract_version: datasetContractVersion,
} as const;
export const deletionPreflightFixture = {
  scope: datasetFixtureScope,
  resource_type: 'DATASET_VERSION',
  resource_id: datasetIds.reviewing,
  capability_status: 'RESERVED_CONDITIONAL',
  executable: false,
  domain_clear: false,
  preflight_token: 'preflight-token-000000000000000000000001',
  expires_at: '2026-08-06T12:10:00Z',
  checks: [
    'ACTIVE_REFERENCES',
    'RETENTION',
    'LEGAL_HOLD',
    'PERMISSION',
    'CONCURRENT_JOBS',
    'CURRENT_READY',
    'AUDIT_PROTECTION',
  ].map((check_type) => ({
    check_type,
    passed: check_type !== 'AUDIT_PROTECTION',
    blocked_reasons:
      check_type === 'AUDIT_PROTECTION'
        ? [{ code: 'AUDIT_PROTECTED', message: '审计保留期未结束' }]
        : [],
    observed_policy_version: null,
    retained_until: null,
  })),
  async_impact: {
    object_count: '3',
    estimated_bytes: '2048',
    dependent_projection_count: '2',
    requires_async_job: true,
  },
  blocked_reasons: [{ code: 'AUDIT_PROTECTED', message: '审计保留期未结束' }],
} as const;

export const asyncJobAcceptedFixture = {
  job: {
    job_id: 'job_fx_version_diff',
    kind: 'VERSION_DIFF',
    status: 'QUEUED',
    stage: 'QUEUED',
    progress: null,
    result_ref: null,
    error: null,
    scope: datasetFixtureScope,
    resource_ref: { resource_type: 'DATASET_VERSION', resource_id: datasetIds.reviewing },
    created_at: '2026-08-06T12:00:05Z',
    updated_at: '2026-08-06T12:00:05Z',
    etag: '"job-rv-1"',
    cancellable: true,
  },
  scope: datasetFixtureScope,
  request_id: 'req_fx_diff_job',
  contract_version: datasetContractVersion,
} as const;
export const sharedJobFixture = {
  job_id: 'job_fx_version_diff',
  job_type: 'VERSION_DIFF',
  status: 'SUCCEEDED',
  resource_type: 'DATASET_VERSION',
  resource_id: datasetIds.reviewing,
  progress: null,
  result_ref: { export_id: 'export_fx_01' },
  error: null,
  created_at: '2026-08-06T12:00:05Z',
  updated_at: '2026-08-06T12:00:07Z',
  resource_version: '3',
} as const;
