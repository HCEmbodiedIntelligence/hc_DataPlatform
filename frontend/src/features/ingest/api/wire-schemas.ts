import { z } from 'zod';

export const decimalStringWireSchema = z.string().regex(/^(0|[1-9]\d*)$/);
export const sha256WireSchema = z.string().regex(/^[a-f0-9]{64}$/);
export const isoDateTimeWireSchema = z.string().datetime({ offset: true });
export const idWireSchema = z.string().min(1).max(256);
export const etagWireSchema = z.string().min(1);

export const scopeWireSchema = z
  .object({
    organization_id: idWireSchema,
    project_id: idWireSchema,
    region_code: z.string().min(1),
  })
  .strict();

export const blockedReasonWireSchema = z.object({ code: z.string().min(1), message: z.string().min(1) }).strict();
export const safeErrorWireSchema = z
  .object({
    code: z.string().min(1),
    message: z.string().min(1),
    retryable: z.boolean(),
    request_id: idWireSchema,
  })
  .strict();

export const pageInfoWireSchema = z
  .object({
    has_next_page: z.boolean(),
    has_previous_page: z.boolean(),
    start_cursor: z.string().nullable(),
    end_cursor: z.string().nullable(),
  })
  .strict();

const actorWireSchema = z.object({ id: idWireSchema, display_name: z.string().min(1) }).strict();

export const robotConnectorConfigurationWireSchema = z
  .object({
    kind: z.literal('ROBOT'),
    transport: z.enum(['HTTPS', 'MQTTS']),
    endpoint_ref: idWireSchema,
    safe_endpoint_hint: z.string().max(300).nullable(),
    tls_profile_id: z.string().nullable(),
  })
  .strict();

export const edgeConnectorConfigurationWireSchema = z
  .object({
    kind: z.literal('EDGE_AGENT'),
    agent_id: idWireSchema,
    transport: z.enum(['OUTBOUND_HTTPS', 'MQTTS']),
    heartbeat_policy_id: idWireSchema,
  })
  .strict();

export const ossConnectorConfigurationWireSchema = z
  .object({
    kind: z.literal('OSS_IMPORT'),
    oss_account_alias: z.string().min(1).max(100),
    bucket_alias: z.string().min(1).max(100),
    prefix_hint: z.string().min(1).max(300),
    role_ref: idWireSchema,
    source_region_code: z.string().min(1),
  })
  .strict();

export const unknownConnectorConfigurationWireSchema = z
  .object({
    kind: z.literal('UNKNOWN'),
    raw_source_type: z.string().min(1),
    safe_projection: z
      .object({
        display_name: z.string().max(200).nullable(),
        connector_family: z.string().max(100).nullable(),
        migration_hint: z.string().max(500).nullable(),
      })
      .strict(),
  })
  .strict();

export const connectorConfigurationWireSchema = z.discriminatedUnion('kind', [
  robotConnectorConfigurationWireSchema,
  edgeConnectorConfigurationWireSchema,
  ossConnectorConfigurationWireSchema,
  unknownConnectorConfigurationWireSchema,
]);

const robotBindingWireSchema = z
  .object({ kind: z.literal('ROBOT'), robot_id: idWireSchema, display_name: z.string().min(1).max(200).nullable() })
  .strict();
const edgeBindingWireSchema = z
  .object({ kind: z.literal('EDGE_AGENT'), agent_id: idWireSchema, display_name: z.string().min(1).max(200).nullable() })
  .strict();
const ossBindingWireSchema = z
  .object({ kind: z.literal('OSS_IMPORT'), source_alias: z.string().min(1).max(200), display_name: z.string().min(1).max(200).nullable() })
  .strict();
const unknownBindingWireSchema = z
  .object({
    kind: z.literal('UNKNOWN'),
    raw: z.string().min(1).max(100),
    display_name: z.string().min(1).max(200).nullable(),
  })
  .strict();
export const dataSourceBindingWireSchema = z.discriminatedUnion('kind', [
  robotBindingWireSchema,
  edgeBindingWireSchema,
  ossBindingWireSchema,
  unknownBindingWireSchema,
]);

export const credentialSummaryWireSchema = z
  .object({
    kind: z.string().min(1),
    state: z.string().min(1),
    credential_ref: z.string().nullable(),
    masked_hint: z.string().nullable(),
    version: decimalStringWireSchema,
    updated_at: isoDateTimeWireSchema.nullable(),
    expires_at: isoDateTimeWireSchema.nullable(),
    rotation_due_at: isoDateTimeWireSchema.nullable(),
  })
  .strict();

export const connectivitySummaryWireSchema = z
  .object({
    state: z.string().min(1),
    last_check_state: z.string().min(1),
    observed_config_version: decimalStringWireSchema.nullable(),
    observed_credential_version: decimalStringWireSchema.nullable(),
    checked_at: isoDateTimeWireSchema.nullable(),
    safe_error: z.object({ code: z.string().min(1), message: z.string().min(1) }).strict().nullable(),
  })
  .strict();

const dataSourceShape = {
  id: idWireSchema,
  scope: scopeWireSchema,
  name: z.string().min(1).max(128),
  source_type: z.string().min(1),
  source_format: z.string().min(1),
  source_format_version: z.string().nullable(),
  adapter_version: z.string().nullable(),
  binding: dataSourceBindingWireSchema,
  administrative_state: z.string().min(1),
  credential: credentialSummaryWireSchema,
  connectivity: connectivitySummaryWireSchema,
  heartbeat: z.object({ state: z.string().min(1), last_seen_at: isoDateTimeWireSchema.nullable() }).strict().nullable(),
  upload_policy: z
    .object({ code: z.string().min(1), label: z.string().min(1), max_object_size_bytes: decimalStringWireSchema })
    .strict(),
  last_upload: z
    .object({
      upload_id: idWireSchema,
      completed_at: isoDateTimeWireSchema,
      verified_bytes: decimalStringWireSchema,
      lifecycle_status: z.string().min(1),
    })
    .strict()
    .nullable(),
  config_version: decimalStringWireSchema,
  credential_version: decimalStringWireSchema,
  etag: etagWireSchema,
  allowed_actions: z.array(z.string().min(1)),
  blocked_reasons: z.array(blockedReasonWireSchema),
  created_at: isoDateTimeWireSchema,
  updated_at: isoDateTimeWireSchema,
} as const;

export const dataSourceSummaryWireSchema = z.object(dataSourceShape).strict();
export const dataSourceWireSchema = z.object({ ...dataSourceShape, configuration: connectorConfigurationWireSchema }).strict();

export const dataSourceEnvelopeWireSchema = z
  .object({
    data: dataSourceWireSchema,
    scope: scopeWireSchema,
    request_id: idWireSchema,
    contract_version: z.string().min(1),
  })
  .strict();

const dataSourceMetricsWireSchema = z
  .object({
    total_count: decimalStringWireSchema,
    online_count: decimalStringWireSchema,
    verified_bytes_today: decimalStringWireSchema,
    abnormal_count: decimalStringWireSchema,
    as_of: isoDateTimeWireSchema,
    timezone: z.string().min(1),
    definition_version: z.string().min(1),
  })
  .strict();

const facetWireSchema = z.object({ value: z.string().min(1), label: z.string().min(1), count: decimalStringWireSchema }).strict();
export const dataSourcePageWireSchema = z
  .object({
    summary: dataSourceMetricsWireSchema,
    facets: z
      .object({
        source_types: z.array(facetWireSchema),
        source_formats: z.array(facetWireSchema),
        robots: z.array(z.object({ id: idWireSchema, name: z.string().min(1), count: decimalStringWireSchema }).strict()),
        upload_policies: z.array(facetWireSchema),
        administrative_states: z.array(facetWireSchema),
        connectivity_states: z.array(facetWireSchema),
        credential_states: z.array(facetWireSchema),
        heartbeat_states: z.array(facetWireSchema),
      })
      .strict(),
    items: z.array(dataSourceSummaryWireSchema),
    page_info: pageInfoWireSchema,
    snapshot_at: isoDateTimeWireSchema,
    allowed_actions: z.array(z.string()),
    component_errors: z.array(z.object({ component: z.string(), code: z.string(), message: z.string(), request_id: idWireSchema }).strict()),
    scope: scopeWireSchema,
    request_id: idWireSchema,
    contract_version: z.string().min(1),
  })
  .strict();

export const uploadProgressWireSchema = z
  .object({
    expected_bytes: decimalStringWireSchema.nullable(),
    confirmed_received_bytes: decimalStringWireSchema,
    completed_parts: decimalStringWireSchema,
    total_parts: decimalStringWireSchema.nullable(),
    completed_objects: decimalStringWireSchema,
    total_objects: decimalStringWireSchema,
    throughput_bytes_per_second: decimalStringWireSchema.nullable(),
    estimated_remaining_seconds: decimalStringWireSchema.nullable(),
    verification_stage: z.string().nullable(),
  })
  .strict();

const sourceManifestSummaryWireSchema = z
  .object({
    manifest_id: idWireSchema,
    revision: decimalStringWireSchema,
    schema_version: z.string().min(1),
    canonicalization: z.literal('RFC8785'),
    sha256: sha256WireSchema,
    object_set_hash: sha256WireSchema,
    source_format: z.string().min(1),
    source_format_version: z.string().nullable(),
    adapter_version: z.string().min(1),
    declared_object_count: decimalStringWireSchema,
    declared_bytes: decimalStringWireSchema,
    submitted_by: actorWireSchema,
    submitted_at: isoDateTimeWireSchema,
    status: z.string().min(1),
    schema_issue_counts: z.object({ error: decimalStringWireSchema, warning: decimalStringWireSchema, info: decimalStringWireSchema }).strict(),
  })
  .strict();

export const uploadSessionWireSchema = z
  .object({
    upload_id: idWireSchema,
    scope: scopeWireSchema,
    data_source: z
      .object({
        id: idWireSchema,
        name: z.string().min(1),
        source_type: z.string().min(1),
        source_format: z.string().min(1),
        configuration_version: decimalStringWireSchema,
        credential_version: decimalStringWireSchema,
        upload_policy_version: decimalStringWireSchema,
      })
      .strict(),
    target_dataset: z.object({ id: idWireSchema, name: z.string().min(1) }).strict().nullable(),
    result: z
      .object({
        dataset_id: idWireSchema,
        dataset_version_id: idWireSchema,
        dataset_version_status: z.string().min(1),
        registration_receipt_id: idWireSchema,
        verified_object_set_hash: sha256WireSchema,
        source_manifest_id: idWireSchema,
        source_manifest_sha256: sha256WireSchema,
        adapter_version: z.string().min(1),
        schema_ref: idWireSchema,
        registered_at: isoDateTimeWireSchema,
      })
      .strict()
      .nullable(),
    supersedes_upload_id: idWireSchema.nullable(),
    source_format: z.string().min(1),
    source_format_version: z.string().nullable(),
    adapter_version: z.string().min(1),
    lifecycle_status: z.string().min(1),
    verification_status: z.string().min(1),
    progress: uploadProgressWireSchema,
    source_manifest: sourceManifestSummaryWireSchema.nullable(),
    latest_verification_run_id: idWireSchema.nullable(),
    active_job_ids: z.array(idWireSchema),
    created_by: actorWireSchema,
    created_at: isoDateTimeWireSchema,
    updated_at: isoDateTimeWireSchema,
    etag: etagWireSchema,
    resource_version: decimalStringWireSchema,
    allowed_actions: z.array(z.string()),
    blocked_reasons: z.array(blockedReasonWireSchema),
  })
  .strict();

export const uploadObjectWireSchema = z
  .object({
    object_id: idWireSchema,
    relative_path: z.string().min(1),
    source_role: z.string().min(1),
    media_type: z.string().nullable(),
    size_bytes: decimalStringWireSchema,
    multipart_status: z.string().min(1),
    completed_parts: decimalStringWireSchema,
    total_parts: decimalStringWireSchema.nullable(),
    etag: z.string().nullable(),
    declared_sha256: sha256WireSchema.nullable(),
    verified_sha256: sha256WireSchema.nullable(),
    checksum_status: z.string().min(1),
    verification_status: z.string().min(1),
    resource_version: decimalStringWireSchema,
    updated_at: isoDateTimeWireSchema,
    allowed_actions: z.array(z.string()),
    blocked_reasons: z.array(blockedReasonWireSchema),
  })
  .strict();

export const uploadPartWireSchema = z
  .object({
    part_id: idWireSchema,
    object_id: idWireSchema,
    part_number: decimalStringWireSchema,
    attempt_count: decimalStringWireSchema,
    offset_bytes: decimalStringWireSchema,
    size_bytes: decimalStringWireSchema,
    status: z.string().min(1),
    etag: z.string().nullable(),
    checksum: z.object({ algorithm: z.string(), value: z.string(), status: z.string() }).strict().nullable(),
    last_error: safeErrorWireSchema.nullable(),
    updated_at: isoDateTimeWireSchema,
    resource_version: decimalStringWireSchema,
  })
  .strict();

export const validationStageWireSchema = z
  .object({
    code: z.string().min(1),
    status: z.string().min(1),
    started_at: isoDateTimeWireSchema.nullable(),
    finished_at: isoDateTimeWireSchema.nullable(),
    job_id: idWireSchema.nullable(),
    finding_count: decimalStringWireSchema,
    retryable: z.boolean(),
    skip_reason: z.string().nullable(),
  })
  .strict();

export const verificationRunWireSchema = z
  .object({
    verification_run_id: idWireSchema,
    supersedes_run_id: idWireSchema.nullable(),
    object_set_hash: sha256WireSchema,
    manifest_sha256: sha256WireSchema,
    adapter_version: z.string().min(1),
    schema_ref: idWireSchema.optional(),
    status: z.string().min(1),
    stages: z.array(validationStageWireSchema).min(1),
    finding_counts: z.object({ info: decimalStringWireSchema, warning: decimalStringWireSchema, error: decimalStringWireSchema }).strict(),
    job_id: idWireSchema,
    started_at: isoDateTimeWireSchema.nullable(),
    finished_at: isoDateTimeWireSchema.nullable(),
    created_at: isoDateTimeWireSchema,
    resource_version: decimalStringWireSchema,
  })
  .strict();

const safeUploadEventPayloadWireSchema = z
  .object({
    previous_status: z.string().nullable().optional(),
    new_status: z.string().nullable().optional(),
    reason_code: z.string().nullable().optional(),
    stage: z.string().nullable().optional(),
    object_id: z.string().nullable().optional(),
    part_number: z.string().nullable().optional(),
    attempt: z.string().nullable().optional(),
    manifest_id: z.string().nullable().optional(),
    verification_run_id: z.string().nullable().optional(),
    quarantine_id: z.string().nullable().optional(),
    supersedes_upload_id: z.string().nullable().optional(),
    job_status: z.string().nullable().optional(),
    retryable: z.boolean().nullable().optional(),
  })
  .strict();

export const uploadEventWireSchema = z
  .object({
    event_id: idWireSchema,
    event_type: z.string().regex(/^[a-z][a-z0-9_]*(?:[.][a-z0-9_]+)+$/),
    event_level: z.enum(['INFO', 'WARNING', 'ERROR']),
    occurred_at: isoDateTimeWireSchema,
    actor: z.object({ kind: z.string().min(1).max(100), id: z.string().nullable(), display_name: z.string().min(1).max(200) }).strict(),
    from_state: z.string().nullable(),
    to_state: z.string().nullable(),
    resource_ref: z.object({ type: z.string().min(1), id: idWireSchema, version: decimalStringWireSchema }).strict(),
    job_id: z.string().nullable(),
    request_id: idWireSchema,
    safe_payload: safeUploadEventPayloadWireSchema,
  })
  .strict();

export const quarantineWireSchema = z
  .object({
    quarantine_id: idWireSchema,
    upload_id: idWireSchema,
    verification_run_id: idWireSchema,
    object_set_hash: sha256WireSchema,
    manifest_sha256: sha256WireSchema,
    reason_code: z.string().min(1),
    safe_summary: z.string().min(1).max(1000),
    disposition: z.string().min(1),
    retain_until: isoDateTimeWireSchema,
    created_at: isoDateTimeWireSchema,
    superseded_by_quarantine_id: idWireSchema.nullable(),
  })
  .strict();

export const uploadJobWireSchema = z
  .object({
    id: idWireSchema,
    type: z.string().min(1),
    status: z.string().min(1),
    stage: z.string().min(1),
    progress: z.object({ completed: decimalStringWireSchema, total: decimalStringWireSchema.nullable(), unit: z.string() }).strict(),
    resource_ref: z.object({ resource_type: z.string(), resource_id: idWireSchema }).strict(),
    observed_versions: z.object({ config_version: decimalStringWireSchema, credential_version: decimalStringWireSchema }).strict().optional(),
    result_ref: z.record(z.string(), z.string()).nullable(),
    safe_error: safeErrorWireSchema.nullable(),
    etag: etagWireSchema,
    allowed_actions: z.array(z.string()),
    created_at: isoDateTimeWireSchema,
    updated_at: isoDateTimeWireSchema,
  })
  .strict();

export const canonicalAsyncJobWireSchema = z
  .object({
    job_id: idWireSchema,
    job_type: z.string().min(1),
    status: z.string().min(1),
    resource_type: z.string().min(1),
    resource_id: idWireSchema,
    progress: z.unknown().nullable(),
    result_ref: z.unknown().nullable(),
    error: z.unknown().nullable(),
    created_at: isoDateTimeWireSchema,
    updated_at: isoDateTimeWireSchema,
    resource_version: decimalStringWireSchema,
  })
  .strict();

export const uploadPolicyWireSchema = z
  .object({
    schema_version: z.literal(1),
    policy_version: decimalStringWireSchema,
    effective_until: isoDateTimeWireSchema,
    part_size_bytes: z.object({ minimum: decimalStringWireSchema, preferred: decimalStringWireSchema, maximum: decimalStringWireSchema }).strict(),
    concurrency: z.object({ minimum: z.number().int().min(1), preferred: z.number().int().min(1), maximum: z.number().int().min(1) }).strict(),
    retry: z
      .object({
        max_attempts_per_part: z.number().int().min(1),
        base_delay_ms: z.number().int().min(0),
        maximum_delay_ms: z.number().int().min(0),
        jitter_ratio: z.number().min(0).max(1),
        retryable_http_statuses: z.array(z.number().int().min(100).max(599)),
      })
      .strict(),
    checksums: z
      .object({
        part_algorithm: z.string().min(1),
        object_algorithm: z.literal('SHA256'),
        require_object_sha256_before_manifest: z.literal(true),
      })
      .strict(),
    limits: z.object({ max_object_count: decimalStringWireSchema, max_total_bytes: decimalStringWireSchema, max_object_bytes: decimalStringWireSchema }).strict(),
  })
  .strict()
  .superRefine((value, ctx) => {
    if (BigInt(value.part_size_bytes.minimum) > BigInt(value.part_size_bytes.preferred) || BigInt(value.part_size_bytes.preferred) > BigInt(value.part_size_bytes.maximum)) {
      ctx.addIssue({ code: 'custom', message: 'part size policy ordering is invalid' });
    }
    if (value.concurrency.minimum > value.concurrency.preferred || value.concurrency.preferred > value.concurrency.maximum) {
      ctx.addIssue({ code: 'custom', message: 'concurrency policy ordering is invalid' });
    }
  });

export const shortUploadAuthorizationWireSchema = z
  .object({
    authorization_id: idWireSchema,
    issued_at: isoDateTimeWireSchema,
    expires_at: isoDateTimeWireSchema,
    refresh_after: isoDateTimeWireSchema,
    oss_region: z.string().min(1),
    endpoint: z.url(),
    bucket: z.string().min(1),
    object_prefix: z.string().min(1),
    credentials: z
      .object({ access_key_id: z.string().min(1), access_key_secret: z.string().min(1), security_token: z.string().min(1) })
      .strict(),
  })
  .strict();

const manifestPartWireSchema = z
  .object({ part_number: decimalStringWireSchema, etag: z.string().min(1), checksum_algorithm: z.string().min(1), checksum_value: z.string().min(1) })
  .strict();
export const uploadObjectPlanWireSchema = z
  .object({
    upload_object_id: idWireSchema,
    client_object_id: idWireSchema,
    relative_path: z.string().min(1),
    size_bytes: decimalStringWireSchema,
    multipart_upload_id: z.string().min(1),
    object_key: z.string().min(1),
    part_size_bytes: decimalStringWireSchema,
    confirmed_parts: z.array(manifestPartWireSchema),
  })
  .strict();

export const uploadHandoffWireSchema = z
  .object({
    authorization: shortUploadAuthorizationWireSchema,
    policy: uploadPolicyWireSchema.nullable(),
    object_plans: z.array(uploadObjectPlanWireSchema),
  })
  .strict();

export const uploadHandoffEnvelopeWireSchema = z
  .object({
    data: z.object({ upload_session: uploadSessionWireSchema, secret: uploadHandoffWireSchema }).strict(),
    scope: scopeWireSchema,
    request_id: idWireSchema,
    contract_version: z.string().min(1),
  })
  .strict();

export const uploadSessionEnvelopeWireSchema = z
  .object({ data: uploadSessionWireSchema, scope: scopeWireSchema, request_id: idWireSchema, contract_version: z.string().min(1) })
  .strict();

export const connectionTestJobEnvelopeWireSchema = z
  .object({
    data: uploadJobWireSchema,
    job: canonicalAsyncJobWireSchema,
    scope: scopeWireSchema,
    request_id: idWireSchema,
    contract_version: z.string().min(1),
  })
  .strict();

export const verificationRunJobEnvelopeWireSchema = z
  .object({
    data: z.object({ verification_run: verificationRunWireSchema }).strict(),
    job: canonicalAsyncJobWireSchema,
    scope: scopeWireSchema,
    request_id: idWireSchema,
    contract_version: z.string().min(1),
  })
  .strict();

export const uploadJobEnvelopeWireSchema = z
  .object({
    data: uploadJobWireSchema,
    job: canonicalAsyncJobWireSchema,
    scope: scopeWireSchema,
    request_id: idWireSchema,
    contract_version: z.string().min(1),
  })
  .strict();

export const uploadSessionBootstrapWireSchema = z
  .object({
    data: z
      .object({
        session: uploadSessionWireSchema,
        objects: z.array(uploadObjectWireSchema),
        latest_verification_run: verificationRunWireSchema.nullable(),
        latest_quarantine: quarantineWireSchema.nullable(),
        active_jobs: z.array(uploadJobWireSchema),
      })
      .strict(),
    scope: scopeWireSchema,
    request_id: idWireSchema,
    contract_version: z.string().min(1),
  })
  .strict();

function cursorEnvelope<T extends z.ZodType>(item: T) {
  return z
    .object({
      items: z.array(item),
      page_info: pageInfoWireSchema,
      snapshot_at: isoDateTimeWireSchema,
      scope: scopeWireSchema,
      request_id: idWireSchema,
      contract_version: z.string().min(1),
    })
    .strict();
}

export const uploadSessionPageWireSchema = cursorEnvelope(uploadSessionWireSchema);
export const uploadObjectPageWireSchema = cursorEnvelope(uploadObjectWireSchema);
export const uploadPartPageWireSchema = cursorEnvelope(uploadPartWireSchema);
export const verificationRunPageWireSchema = cursorEnvelope(verificationRunWireSchema);
export const uploadEventPageWireSchema = cursorEnvelope(uploadEventWireSchema);

export const uploadCreationOptionsEnvelopeWireSchema = z
  .object({
    data: z.object({
      data_sources: z.array(z.object({
        id: idWireSchema,
        name: z.string().min(1).max(128),
        source_type: z.enum(['ROBOT', 'EDGE_AGENT', 'OSS_IMPORT']),
        source_format: z.string().min(1),
        configuration_version: decimalStringWireSchema,
        credential_version: decimalStringWireSchema,
        upload_policy_version: decimalStringWireSchema,
        allowed: z.boolean(),
        blocked_reasons: z.array(blockedReasonWireSchema),
      }).strict()),
      datasets: z.array(z.object({ id: idWireSchema, name: z.string().min(1).max(200), allowed: z.boolean(), blocked_reasons: z.array(blockedReasonWireSchema) }).strict()),
      formats: z.array(z.object({ code: z.string().min(1), version: z.string().nullable(), adapter_version: z.string().min(1), allowed_extensions: z.array(z.string().regex(/^[a-z0-9][a-z0-9._-]*$/)) }).strict()),
      policy_summaries: z.array(z.object({ policy_version: decimalStringWireSchema, label: z.string().min(1).max(200), max_object_count: decimalStringWireSchema, max_total_bytes: decimalStringWireSchema, default_part_size_bytes: decimalStringWireSchema, max_concurrency: z.number().int().min(1).max(64) }).strict()),
      allowed_actions: z.array(z.string()),
      blocked_reasons: z.array(blockedReasonWireSchema),
    }).strict(),
    scope: scopeWireSchema,
    request_id: idWireSchema,
    contract_version: z.literal('ingest.v1alpha1'),
  })
  .strict();

export type DataSourceWire = z.infer<typeof dataSourceWireSchema>;
export type DataSourceSummaryWire = z.infer<typeof dataSourceSummaryWireSchema>;
export type DataSourcePageWire = z.infer<typeof dataSourcePageWireSchema>;
export type DataSourceEnvelopeWire = z.infer<typeof dataSourceEnvelopeWireSchema>;
export type ConnectionTestJobEnvelopeWire = z.infer<typeof connectionTestJobEnvelopeWireSchema>;
export type UploadSessionWire = z.infer<typeof uploadSessionWireSchema>;
export type UploadSessionBootstrapWire = z.infer<typeof uploadSessionBootstrapWireSchema>;
export type UploadHandoffEnvelopeWire = z.infer<typeof uploadHandoffEnvelopeWireSchema>;
export type UploadSessionEnvelopeWire = z.infer<typeof uploadSessionEnvelopeWireSchema>;
export type VerificationRunJobEnvelopeWire = z.infer<typeof verificationRunJobEnvelopeWireSchema>;
export type UploadJobEnvelopeWire = z.infer<typeof uploadJobEnvelopeWireSchema>;
export type UploadObjectWire = z.infer<typeof uploadObjectWireSchema>;
export type VerificationRunWire = z.infer<typeof verificationRunWireSchema>;
export type UploadEventWire = z.infer<typeof uploadEventWireSchema>;
export type UploadCreationOptionsEnvelopeWire = z.infer<typeof uploadCreationOptionsEnvelopeWireSchema>;
