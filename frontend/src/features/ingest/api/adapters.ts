import type {
  AdministrativeState,
  ConnectivityState,
  CredentialState,
  DataSourceAllowedAction,
  DataSourceDetail,
  DataSourceSummary,
  DecimalString,
  IngestScope,
  UnknownEnum,
} from "../../../entities/data-source";
import type {
  RawObject,
  RawObjectId,
  Sha256,
} from "../../../entities/raw-object";
import type { UploadJob, UploadJobId } from "../../../entities/upload-job";
import type { ConnectorConfiguration } from "../connectors/registry";
import {
  parseValidationStageCode,
  type QuarantineDisposition,
  type ValidationStageStatus,
} from "../validation-pipeline";
import type {
  UploadBootstrap,
  UploadLifecycleStatus,
  UploadSession,
  VerificationRun,
  VerificationStatus,
} from "../upload/model";
import type {
  DataSourcePageWire,
  DataSourceSummaryWire,
  DataSourceWire,
  UploadObjectWire,
  UploadSessionBootstrapWire,
  UploadSessionWire,
  VerificationRunWire,
} from "./wire-schemas";

const DATA_SOURCE_ACTIONS = new Set([
  "VIEW",
  "EDIT_CONFIGURATION",
  "ROTATE_CREDENTIAL",
  "TEST_CONNECTION",
  "ENABLE",
  "DISABLE",
  "DELETE",
  "OPEN_UPLOADS",
]);
const UPLOAD_ACTIONS = new Set([
  "VIEW",
  "PAUSE",
  "RESUME",
  "RETRY_UPLOAD",
  "SUBMIT_MANIFEST",
  "RETRY_VERIFY",
  "CREATE_REPLACEMENT",
  "CANCEL",
]);
const ADMIN_STATES = new Set(["ENABLED", "DISABLED"]);
const CREDENTIAL_STATES = new Set([
  "NOT_REQUIRED",
  "MISSING",
  "CONFIGURED",
  "ROTATION_DUE",
  "EXPIRED",
  "REVOKED",
  "INVALID",
]);
const CONNECTIVITY_STATES = new Set([
  "UNKNOWN",
  "ONLINE",
  "DEGRADED",
  "OFFLINE",
  "AUTH_FAILED",
  "CONFIG_ERROR",
]);
const UPLOAD_STATES = new Set([
  "CREATED",
  "AUTHORIZING",
  "UPLOADING",
  "PAUSED",
  "FINALIZING",
  "PENDING_VERIFY",
  "VERIFYING",
  "AVAILABLE",
  "FAILED",
  "QUARANTINED",
  "CANCELLING",
  "CANCELLED",
  "EXPIRED",
]);
const VERIFICATION_STATES = new Set([
  "NOT_STARTED",
  "QUEUED",
  "RUNNING",
  "PASSED",
  "FAILED",
  "CANCELLED",
]);
const VALIDATION_STAGE_STATUSES = new Set([
  "PENDING",
  "RUNNING",
  "PASSED",
  "FAILED",
  "SKIPPED",
  "CANCELLED",
]);
const QUARANTINE_DISPOSITIONS = new Set([
  "OPEN",
  "REVERIFY_REQUESTED",
  "RELEASED",
  "REPLACED",
  "SUPERSEDED",
  "RETENTION_EXPIRED",
]);

function unknown(raw: string): UnknownEnum {
  return { kind: "UNKNOWN", raw };
}

function knownOrUnknown<T extends string>(
  value: string,
  known: ReadonlySet<string>,
): T | UnknownEnum {
  return known.has(value) ? (value as T) : unknown(value);
}

export function adaptScope(wire: {
  organization_id: string;
  project_id: string;
  region_code: string;
}): IngestScope {
  return {
    organizationId: wire.organization_id,
    projectId: wire.project_id,
    regionCode: wire.region_code,
  };
}

export function assertResponseScope(
  actual: IngestScope,
  expected: IngestScope,
): void {
  if (
    actual.organizationId !== expected.organizationId ||
    actual.projectId !== expected.projectId ||
    actual.regionCode !== expected.regionCode
  ) {
    throw new Error("SCOPE_MISMATCH");
  }
}

function adaptDataSourceBase(wire: DataSourceSummaryWire): DataSourceSummary {
  const sourceType = ["ROBOT", "EDGE_AGENT", "OSS_IMPORT"].includes(
    wire.source_type,
  )
    ? wire.source_type
    : unknown(wire.source_type);
  return {
    id: wire.id as DataSourceSummary["id"],
    scope: adaptScope(wire.scope),
    name: wire.name,
    sourceType,
    sourceFormat: wire.source_format,
    sourceFormatVersion: wire.source_format_version,
    adapterVersion: wire.adapter_version,
    binding:
      wire.binding.kind === "ROBOT"
        ? {
            kind: "ROBOT",
            robotId: wire.binding.robot_id,
            displayName: wire.binding.display_name,
          }
        : wire.binding.kind === "EDGE_AGENT"
          ? {
              kind: "EDGE_AGENT",
              agentId: wire.binding.agent_id,
              displayName: wire.binding.display_name,
            }
          : wire.binding.kind === "OSS_IMPORT"
            ? {
                kind: "OSS_IMPORT",
                sourceAlias: wire.binding.source_alias,
                displayName: wire.binding.display_name,
              }
            : {
                kind: "UNKNOWN",
                rawSourceType: wire.binding.raw,
                safeDisplayName: wire.binding.display_name,
              },
    administrativeState: knownOrUnknown<
      Exclude<AdministrativeState, UnknownEnum>
    >(wire.administrative_state, ADMIN_STATES),
    credential: {
      kind: wire.credential.kind,
      state: knownOrUnknown<Exclude<CredentialState, UnknownEnum>>(
        wire.credential.state,
        CREDENTIAL_STATES,
      ),
      configured:
        wire.credential.credential_ref !== null &&
        wire.credential.state !== "MISSING",
      maskedHint: wire.credential.masked_hint,
      version: wire.credential.version as DecimalString,
      updatedAt: wire.credential.updated_at,
      expiresAt: wire.credential.expires_at,
      rotationDueAt: wire.credential.rotation_due_at,
    },
    connectivity: {
      state: knownOrUnknown<Exclude<ConnectivityState, UnknownEnum>>(
        wire.connectivity.state,
        CONNECTIVITY_STATES,
      ),
      lastCheckState: wire.connectivity.last_check_state,
      observedConfigVersion: wire.connectivity
        .observed_config_version as DecimalString | null,
      observedCredentialVersion: wire.connectivity
        .observed_credential_version as DecimalString | null,
      checkedAt: wire.connectivity.checked_at,
      safeError: wire.connectivity.safe_error,
    },
    heartbeat: wire.heartbeat
      ? { state: wire.heartbeat.state, lastSeenAt: wire.heartbeat.last_seen_at }
      : null,
    uploadPolicy: {
      code: wire.upload_policy.code,
      label: wire.upload_policy.label,
      maxObjectSizeBytes: wire.upload_policy
        .max_object_size_bytes as DecimalString,
    },
    lastUpload: wire.last_upload
      ? {
          uploadId: wire.last_upload.upload_id,
          completedAt: wire.last_upload.completed_at,
          verifiedBytes: wire.last_upload.verified_bytes as DecimalString,
          lifecycleStatus: wire.last_upload.lifecycle_status,
        }
      : null,
    configVersion: wire.config_version as DecimalString,
    credentialVersion: wire.credential_version as DecimalString,
    etag: wire.etag as DataSourceSummary["etag"],
    allowedActions: wire.allowed_actions.filter(
      (action): action is DataSourceAllowedAction =>
        DATA_SOURCE_ACTIONS.has(action),
    ),
    blockedReasons: wire.blocked_reasons,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
  };
}

function adaptConnectorConfiguration(
  wire: DataSourceWire["configuration"],
): ConnectorConfiguration {
  switch (wire.kind) {
    case "ROBOT":
      return {
        kind: "ROBOT",
        transport: wire.transport,
        endpointRef: wire.endpoint_ref,
        safeEndpointHint: wire.safe_endpoint_hint,
        tlsProfileId: wire.tls_profile_id,
      };
    case "EDGE_AGENT":
      return {
        kind: "EDGE_AGENT",
        agentId: wire.agent_id,
        transport: wire.transport,
        heartbeatPolicyId: wire.heartbeat_policy_id,
      };
    case "OSS_IMPORT":
      return {
        kind: "OSS_IMPORT",
        ossAccountAlias: wire.oss_account_alias,
        bucketAlias: wire.bucket_alias,
        prefixHint: wire.prefix_hint,
        roleRef: wire.role_ref,
        sourceRegionCode: wire.source_region_code,
      };
    case "UNKNOWN":
      return {
        kind: "UNKNOWN",
        rawType: wire.raw_source_type,
        safeProjection: {
          displayName: wire.safe_projection.display_name,
          connectorFamily: wire.safe_projection.connector_family,
          migrationHint: wire.safe_projection.migration_hint,
        },
      };
  }
}

export function adaptDataSource(
  wire: DataSourceWire,
  expectedScope?: IngestScope,
): DataSourceDetail<ConnectorConfiguration> {
  const source = {
    ...adaptDataSourceBase(wire),
    configuration: adaptConnectorConfiguration(wire.configuration),
  };
  if (expectedScope) assertResponseScope(source.scope, expectedScope);
  if (
    typeof source.sourceType === "string" &&
    source.sourceType !== source.configuration.kind
  ) {
    throw new Error(
      "CONTRACT_MISMATCH: connector discriminator does not match source_type",
    );
  }
  return source;
}

export interface DataSourcePage {
  readonly summary: {
    readonly totalCount: DecimalString;
    readonly onlineCount: DecimalString;
    readonly verifiedBytesToday: DecimalString;
    readonly abnormalCount: DecimalString;
    readonly asOf: string;
  };
  readonly items: readonly DataSourceSummary[];
  readonly pageInfo: {
    readonly hasNextPage: boolean;
    readonly hasPreviousPage: boolean;
    readonly startCursor: string | null;
    readonly endCursor: string | null;
  };
  readonly snapshotAt: string;
  readonly allowedActions: readonly string[];
  readonly requestId: string;
  readonly componentErrors: readonly {
    readonly component: string;
    readonly code: string;
    readonly message: string;
    readonly requestId: string;
  }[];
}

export function adaptDataSourcePage(
  wire: DataSourcePageWire,
  expectedScope?: IngestScope,
): DataSourcePage {
  const scope = adaptScope(wire.scope);
  if (expectedScope) assertResponseScope(scope, expectedScope);
  return {
    summary: {
      totalCount: wire.summary.total_count as DecimalString,
      onlineCount: wire.summary.online_count as DecimalString,
      verifiedBytesToday: wire.summary.verified_bytes_today as DecimalString,
      abnormalCount: wire.summary.abnormal_count as DecimalString,
      asOf: wire.summary.as_of,
    },
    items: wire.items.map(adaptDataSourceBase),
    pageInfo: {
      hasNextPage: wire.page_info.has_next_page,
      hasPreviousPage: wire.page_info.has_previous_page,
      startCursor: wire.page_info.start_cursor,
      endCursor: wire.page_info.end_cursor,
    },
    snapshotAt: wire.snapshot_at,
    allowedActions: wire.allowed_actions,
    requestId: wire.request_id,
    componentErrors: wire.component_errors.map((error) => ({
      component: error.component,
      code: error.code,
      message: error.message,
      requestId: error.request_id,
    })),
  };
}

export function adaptUploadSession(
  wire: UploadSessionWire,
  expectedScope?: IngestScope,
): UploadSession {
  const scope = adaptScope(wire.scope);
  if (expectedScope) assertResponseScope(scope, expectedScope);
  return {
    uploadId: wire.upload_id as UploadSession["uploadId"],
    scope,
    dataSource: {
      id: wire.data_source.id,
      name: wire.data_source.name,
      sourceType: wire.data_source.source_type,
      sourceFormat: wire.data_source.source_format,
      configurationVersion: wire.data_source
        .configuration_version as DecimalString,
      credentialVersion: wire.data_source.credential_version as DecimalString,
      uploadPolicyVersion: wire.data_source
        .upload_policy_version as DecimalString,
    },
    targetDataset: wire.target_dataset,
    result: wire.result
      ? {
          datasetId: wire.result.dataset_id,
          datasetVersionId: wire.result.dataset_version_id,
          datasetVersionStatus: wire.result.dataset_version_status,
        }
      : null,
    supersedesUploadId:
      wire.supersedes_upload_id as UploadSession["supersedesUploadId"],
    sourceFormat: wire.source_format,
    sourceFormatVersion: wire.source_format_version,
    adapterVersion: wire.adapter_version,
    lifecycleStatus: knownOrUnknown<
      Exclude<UploadLifecycleStatus, UnknownEnum>
    >(wire.lifecycle_status, UPLOAD_STATES),
    verificationStatus: knownOrUnknown<
      Exclude<VerificationStatus, UnknownEnum>
    >(wire.verification_status, VERIFICATION_STATES),
    progress: {
      expectedBytes: wire.progress.expected_bytes as DecimalString | null,
      confirmedReceivedBytes: wire.progress
        .confirmed_received_bytes as DecimalString,
      completedParts: wire.progress.completed_parts as DecimalString,
      totalParts: wire.progress.total_parts as DecimalString | null,
      completedObjects: wire.progress.completed_objects as DecimalString,
      totalObjects: wire.progress.total_objects as DecimalString,
      throughputBytesPerSecond: wire.progress
        .throughput_bytes_per_second as DecimalString | null,
      estimatedRemainingSeconds: wire.progress
        .estimated_remaining_seconds as DecimalString | null,
      verificationStage: wire.progress.verification_stage,
    },
    sourceManifest: wire.source_manifest
      ? {
          manifestId: wire.source_manifest.manifest_id,
          revision: wire.source_manifest.revision as DecimalString,
          schemaVersion: wire.source_manifest.schema_version,
          canonicalization: wire.source_manifest.canonicalization,
          sha256: wire.source_manifest.sha256,
          objectSetHash: wire.source_manifest.object_set_hash,
          sourceFormat: wire.source_manifest.source_format,
          sourceFormatVersion: wire.source_manifest.source_format_version,
          adapterVersion: wire.source_manifest.adapter_version,
          declaredObjectCount: wire.source_manifest
            .declared_object_count as DecimalString,
          declaredBytes: wire.source_manifest.declared_bytes as DecimalString,
          submittedBy: {
            id: wire.source_manifest.submitted_by.id,
            displayName: wire.source_manifest.submitted_by.display_name,
          },
          submittedAt: wire.source_manifest.submitted_at,
          status: wire.source_manifest.status,
          schemaIssueCounts: {
            error: wire.source_manifest.schema_issue_counts
              .error as DecimalString,
            warning: wire.source_manifest.schema_issue_counts
              .warning as DecimalString,
            info: wire.source_manifest.schema_issue_counts
              .info as DecimalString,
          },
        }
      : null,
    latestVerificationRunId: wire.latest_verification_run_id,
    activeJobIds: wire.active_job_ids,
    createdBy: {
      id: wire.created_by.id,
      displayName: wire.created_by.display_name,
    },
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
    etag: wire.etag as UploadSession["etag"],
    resourceVersion: wire.resource_version as DecimalString,
    allowedActions: wire.allowed_actions.filter(
      (action): action is UploadSession["allowedActions"][number] =>
        UPLOAD_ACTIONS.has(action),
    ),
    blockedReasons: wire.blocked_reasons,
  };
}

export function adaptRawObject(wire: UploadObjectWire): RawObject {
  return {
    id: wire.object_id as RawObjectId,
    relativePath: wire.relative_path,
    sourceRole: wire.source_role,
    mediaType: wire.media_type,
    sizeBytes: wire.size_bytes as DecimalString,
    multipartStatus: wire.multipart_status,
    completedParts: wire.completed_parts as DecimalString,
    totalParts: wire.total_parts as DecimalString | null,
    multipartEtag: wire.etag,
    declaredSha256: wire.declared_sha256 as Sha256 | null,
    verifiedSha256: wire.verified_sha256 as Sha256 | null,
    checksumStatus: wire.checksum_status,
    verificationStatus: wire.verification_status,
    resourceVersion: wire.resource_version as DecimalString,
    updatedAt: wire.updated_at,
    allowedActions: wire.allowed_actions,
    blockedReasons: wire.blocked_reasons,
  };
}

export function adaptVerificationRun(
  wire: VerificationRunWire,
): VerificationRun {
  return {
    verificationRunId: wire.verification_run_id,
    supersedesRunId: wire.supersedes_run_id,
    objectSetHash: wire.object_set_hash,
    manifestSha256: wire.manifest_sha256,
    adapterVersion: wire.adapter_version,
    status: knownOrUnknown<Exclude<VerificationStatus, UnknownEnum>>(
      wire.status,
      VERIFICATION_STATES,
    ),
    stages: wire.stages.map((stage) => ({
      code: parseValidationStageCode(stage.code),
      status: knownOrUnknown<Exclude<ValidationStageStatus, UnknownEnum>>(
        stage.status,
        VALIDATION_STAGE_STATUSES,
      ),
      startedAt: stage.started_at,
      finishedAt: stage.finished_at,
      jobId: stage.job_id,
      findingCount: stage.finding_count,
      retryable: stage.retryable,
      skipReason: stage.skip_reason,
    })),
    jobId: wire.job_id,
    createdAt: wire.created_at,
    resourceVersion: wire.resource_version as DecimalString,
  };
}

function adaptUploadJob(
  wire: UploadSessionBootstrapWire["data"]["active_jobs"][number],
): UploadJob {
  return {
    id: wire.id as UploadJobId,
    type: wire.type,
    status: wire.status as UploadJob["status"],
    stage: wire.stage,
    progress: {
      completed: wire.progress.completed as DecimalString,
      total: wire.progress.total as DecimalString | null,
      unit: wire.progress.unit,
    },
    resourceRef: {
      resourceType: wire.resource_ref.resource_type,
      resourceId: wire.resource_ref.resource_id,
    },
    resultRef: wire.result_ref,
    safeError: wire.safe_error
      ? {
          code: wire.safe_error.code,
          message: wire.safe_error.message,
          retryable: wire.safe_error.retryable,
          requestId: wire.safe_error.request_id,
        }
      : null,
    etag: wire.etag,
    allowedActions: wire.allowed_actions,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
    resourceVersion: null,
  };
}

export function adaptUploadBootstrap(
  wire: UploadSessionBootstrapWire,
  expectedScope?: IngestScope,
): UploadBootstrap {
  const envelopeScope = adaptScope(wire.scope);
  if (expectedScope) assertResponseScope(envelopeScope, expectedScope);
  const session = adaptUploadSession(wire.data.session, envelopeScope);
  if (
    wire.data.latest_quarantine &&
    wire.data.latest_quarantine.upload_id !== session.uploadId
  ) {
    throw new Error("CONTRACT_MISMATCH: quarantine does not belong to upload");
  }
  return {
    session,
    objects: wire.data.objects.map(adaptRawObject),
    latestVerificationRun: wire.data.latest_verification_run
      ? adaptVerificationRun(wire.data.latest_verification_run)
      : null,
    latestQuarantine: wire.data.latest_quarantine
      ? {
          quarantineId: wire.data.latest_quarantine.quarantine_id,
          uploadId: wire.data.latest_quarantine
            .upload_id as UploadSession["uploadId"],
          verificationRunId: wire.data.latest_quarantine.verification_run_id,
          reasonCode: wire.data.latest_quarantine.reason_code,
          safeSummary: wire.data.latest_quarantine.safe_summary,
          disposition: knownOrUnknown<
            Exclude<QuarantineDisposition, UnknownEnum>
          >(wire.data.latest_quarantine.disposition, QUARANTINE_DISPOSITIONS),
          retainUntil: wire.data.latest_quarantine.retain_until,
          createdAt: wire.data.latest_quarantine.created_at,
        }
      : null,
    activeJobs: wire.data.active_jobs.map(adaptUploadJob),
    requestId: wire.request_id,
    contractVersion: wire.contract_version,
  };
}
