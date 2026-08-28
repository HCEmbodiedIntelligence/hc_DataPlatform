import type {
  Dataset,
  DatasetAvailability,
  DatasetId,
} from "../../../entities/dataset";
import type {
  DatasetVersion,
  DatasetVersionDeliveryStatus,
  DatasetVersionId,
  DatasetVersionKind,
  DatasetVersionStatus,
} from "../../../entities/dataset-version";
import type { EpisodeId, EpisodeRevisionId } from "../../../entities/episode";
import type {
  ReviewDecision,
  ReviewDecisionId,
  ReviewFinding,
  ReviewFindingId,
} from "../../../entities/review-finding";
import type {
  ApproveReviewResultWire,
  DatasetBootstrapWire,
  DatasetFacetsWire,
  DatasetListEnvelopeWire,
  DatasetSummaryWire,
  DatasetsPageCapabilitiesWire,
  DatasetVersionCapacityWire,
  DatasetVersionSchemaSummaryWire,
  EpisodeRevisionHistoryWire,
  OperationalInventoryPageWire,
  RequiredStoragePageWire,
  EpisodePageEnvelopeWire,
  ReturnReviewResultWire,
  SourceProvenancePageWire,
  VersionSchemaWire,
  VersionBootstrapWire,
  VersionPageEnvelopeWire,
} from "./wire-schemas";

export type ScopeVm = Readonly<{
  organizationId: string;
  projectId: string;
  regionCode: string;
}>;

export type BlockedReasonVm = Readonly<{ code: string; message: string }>;

export const KNOWN_DATASET_ACTIONS = [
  "OPEN_DATASET",
  "OPEN_VERSION",
  "OPEN_EPISODE",
  "CREATE_ISSUE",
  "START_INGEST",
  "CREATE_DATASET",
  "EXPORT_MANIFEST_LIST",
  "REVIEW_VERSION",
  "DOWNLOAD_MANIFEST",
  "CREATE_EXPORT",
] as const;
export type KnownDatasetAction = (typeof KNOWN_DATASET_ACTIONS)[number];

export type ResourceActionVm = Readonly<{
  action: KnownDatasetAction;
  allowed: boolean;
  blockedReasons: readonly BlockedReasonVm[];
}>;

export type DatasetListItemVm = Readonly<{
  datasetId: DatasetId;
  folderPath: readonly string[];
  collectionTaskId: string | null;
  name: string;
  availability: DatasetAvailability;
  datasetCreatedAt: string;
  datasetActivityAt: string;
  currentVersion: null | Readonly<{
    versionId: DatasetVersionId;
    displayVersion: string;
    kind: DatasetVersionKind;
    publishedAt: string;
    manifestSha256: string;
  }>;
  episodeCount: string;
  pendingReviewVersionCount: string;
  returnedVersionCount: string;
  actionableDraftCount: string;
  allowedActions: readonly ResourceActionVm[];
}>;

export type CursorPageVm<T> = Readonly<{
  items: readonly T[];
  pageInfo: Readonly<{
    after: string | null;
    before: string | null;
    hasNextPage: boolean;
    hasPreviousPage: boolean;
  }>;
  snapshotAt: string;
  snapshotId: string;
  scope: ScopeVm;
  requestId: string;
}>;

export type DatasetBootstrapVm = Readonly<{
  scope: ScopeVm;
  dataset: Dataset;
  currentReadyVersion: DatasetListItemVm["currentVersion"];
  workingVersionId: DatasetVersionId | null;
  suggestedVersionId: DatasetVersionId | null;
  summary: Readonly<{
    episodeCount: string;
    effectiveDurationNs: string;
    sourceBytes: string;
    requiredPhysicalBytes: string;
    actualOssBytes: string | null;
    pendingReviewVersionCount: string;
    returnedVersionCount: string;
    actionableDraftCount: string;
    calculatedAt: string;
    calculationState:
      | "CALCULATING"
      | "PARTIAL"
      | "SETTLED"
      | "FAILED"
      | "UNKNOWN";
  }>;
  allowedActions: readonly ResourceActionVm[];
}>;

export type DatasetSummaryVm = Readonly<{
  scope: ScopeVm;
  datasetCount: string;
  episodeCount: string;
  pendingReviewVersionCount: string;
  returnedVersionCount: string;
  actionableDraftCount: string;
  normalizedFilters: Readonly<Record<string, unknown>>;
}>;

export type DatasetFacetsVm = Readonly<{
  scope: ScopeVm;
  normalizedFilters: Readonly<Record<string, unknown>>;
  robots: DatasetFacetsWire["robots"];
  robotModels: DatasetFacetsWire["robot_models"];
  tasks: DatasetFacetsWire["tasks"];
  tags: DatasetFacetsWire["tags"];
  scenes: DatasetFacetsWire["scenes"];
  assetStates: DatasetFacetsWire["asset_states"];
  storageClasses: DatasetFacetsWire["storage_classes"];
  channels: DatasetFacetsWire["channels"];
}>;

export type DatasetsPageCapabilitiesVm = Readonly<{
  scope: ScopeVm;
  authorizationRevision: string;
  allowedActions: DatasetsPageCapabilitiesWire["allowed_actions"];
  blockedReasons: readonly BlockedReasonVm[];
}>;

export type DatasetVersionSchemaSummaryVm = Readonly<{
  datasetId: DatasetId;
  versionId: DatasetVersionId;
  snapshot: Readonly<{
    type: string;
    id: string;
    version: string;
    sha256: string;
  }>;
  channelCount: string | null;
}>;

export type SourceProvenanceVm = Readonly<{
  datasetId: DatasetId;
  versionId: DatasetVersionId;
  storageRegionCode: string | null;
  provenanceId: string;
  uploadId: string;
  sourceId: string | null;
  sourceDisplayName: string | null;
  sourceManifestId: string;
  sourceManifestSha256: string;
  verifiedObjectSetHash: string;
  registeredAt: string;
}>;

export type DatasetVersionCapacityVm = Readonly<{
  datasetId: DatasetId;
  versionId: DatasetVersionId;
  state: DatasetVersionCapacityWire["state"];
  sourceBytes: string | null;
  requiredPhysicalBytes: string | null;
  actualOssBytes: string | null;
  calculatedAt: string;
  basisRevision: string;
}>;

export type VersionSchemaVm = Readonly<{
  datasetId: DatasetId;
  versionId: DatasetVersionId;
  snapshotToken: string;
  snapshot: Readonly<{
    type: string;
    id: string;
    version: string;
    sha256: string;
  }>;
  channelCount: string;
  channels: readonly Readonly<{
    id: string;
    name: string;
    dataType: string;
    unit: string | null;
  }>[];
}>;

export type RequiredStorageItemVm = Readonly<{
  objectId: string;
  role: RequiredStoragePageWire["items"][number]["role"];
  sizeBytes: string;
  reuse: RequiredStoragePageWire["items"][number]["reuse"];
  protection: RequiredStoragePageWire["items"][number]["protection"];
  safeLocator: string | null;
}>;

export type OperationalInventoryItemVm = Readonly<{
  inventoryId: string;
  kind: OperationalInventoryPageWire["items"][number]["kind"];
  operationalRevision: string;
  status: OperationalInventoryPageWire["items"][number]["status"];
  sizeBytes: string;
  jobId: string | null;
  createdAt: string;
  completedAt: string | null;
}>;

export type EpisodeListItemVm = Readonly<{
  datasetId: DatasetId;
  versionId: DatasetVersionId;
  episodeId: EpisodeId;
  storageRegionCode: string | null;
  selectedRevisionId: EpisodeRevisionId;
  ordinal: number;
  included: boolean;
  successState: "SUCCEEDED" | "FAILED" | "UNKNOWN";
  reviewStatus: "UNREVIEWED" | "ACCEPTED" | "HAS_FINDING" | "UNKNOWN";
  reviewFindingCount: string | null;
  task: string | null;
  robotId: string | null;
}>;

export type EpisodeRevisionHistoryItemVm = Readonly<{
  datasetId: DatasetId;
  episodeId: EpisodeId;
  versionId: DatasetVersionId;
  displayVersion: string;
  versionKind: DatasetVersionKind;
  versionStatus: DatasetVersionStatus;
  versionCreatedAt: string;
  versionPublishedAt: string | null;
  selectedRevisionId: EpisodeRevisionId;
  ordinal: number;
  contentSha256: string;
}>;

export type VersionBootstrapVm = Readonly<{
  scope: ScopeVm;
  datasetId: DatasetId;
  versionId: DatasetVersionId;
  snapshotToken: string;
  operationalRevision: string;
  version: DatasetVersion;
  allowedActions: readonly ResourceActionVm[];
  returnLineage: null | Readonly<{
    reviewDecisionId: ReviewDecisionId;
    reviewFindingIds: readonly ReviewFindingId[];
    successorDraftId: string;
    supersedesDraftId: string;
    returnedFromVersionId: DatasetVersionId;
    returnedFromReviewDecisionId: ReviewDecisionId;
  }>;
}>;

export type ApproveReviewResultVm = Readonly<{
  scope: ScopeVm;
  reviewDecision: ReviewDecision;
  outputVersionId: DatasetVersionId;
  versionToken: string;
  jobId: string;
  requestId: string;
}>;

export type ReturnReviewResultVm = Readonly<{
  scope: ScopeVm;
  reviewDecision: ReviewDecision;
  findings: readonly ReviewFinding[];
  reviewFindingIds: readonly ReviewFindingId[];
  outputVersionId: DatasetVersionId;
  versionToken: string;
  successorDraftId: string;
  supersedesDraftId: string;
  returnedFromVersionId: DatasetVersionId;
  returnedFromReviewDecisionId: ReviewDecisionId;
}>;

type ActionWire = {
  action: string;
  allowed: boolean;
  blocked_reasons: readonly BlockedReasonVm[];
};

function adaptScope(scope: {
  organization_id: string;
  project_id: string;
  region_code: string;
}): ScopeVm {
  return {
    organizationId: scope.organization_id,
    projectId: scope.project_id,
    regionCode: scope.region_code,
  };
}

function isKnownDatasetAction(value: string): value is KnownDatasetAction {
  return KNOWN_DATASET_ACTIONS.includes(value as KnownDatasetAction);
}

export function adaptAllowedActions(
  actions: readonly ActionWire[],
): readonly ResourceActionVm[] {
  return actions
    .filter((value) => isKnownDatasetAction(value.action))
    .map((value) => ({
      action: value.action as KnownDatasetAction,
      allowed: value.allowed,
      blockedReasons: value.blocked_reasons,
    }));
}

function adaptDatasetKind(value: string): DatasetVersionKind {
  return value === "RAW" || value === "CLEANED" ? value : "UNKNOWN";
}

function adaptDatasetStatus(value: string): DatasetVersionStatus {
  return value === "REVIEWING" || value === "RETURNED" || value === "READY"
    ? value
    : "UNKNOWN";
}

function adaptDeliveryStatus(
  value: string | undefined,
): DatasetVersionDeliveryStatus | undefined {
  if (
    value === "NOT_STARTED" ||
    value === "GENERATING" ||
    value === "FAILED" ||
    value === "CANDIDATE_READY"
  ) {
    return value;
  }
  return value === undefined ? undefined : "UNKNOWN";
}

function adaptDatasetAvailability(value: string): DatasetAvailability {
  return value === "ACTIVE" || value === "FROZEN" ? value : "UNKNOWN";
}

function adaptCalculationState(
  value: string,
): DatasetBootstrapVm["summary"]["calculationState"] {
  return value === "CALCULATING" ||
    value === "PARTIAL" ||
    value === "SETTLED" ||
    value === "FAILED"
    ? value
    : "UNKNOWN";
}

function adaptVersion(value: VersionBootstrapWire["version"]): DatasetVersion {
  const rawDeliveryStatus =
    "delivery_status" in value && typeof value.delivery_status === "string"
      ? value.delivery_status
      : undefined;
  const deliveryStatus = adaptDeliveryStatus(rawDeliveryStatus);
  const publishedAt =
    "published_at" in value && typeof value.published_at === "string"
      ? value.published_at
      : null;
  return {
    id: value.version_id as DatasetVersionId,
    datasetId: value.dataset_id as DatasetId,
    displayVersion: value.display_version,
    kind: adaptDatasetKind(value.kind),
    status: adaptDatasetStatus(value.status),
    ...(deliveryStatus ? { deliveryStatus } : {}),
    createdAt: value.created_at,
    publishedAt,
    etag: value.etag,
    versionToken: value.version_token,
  };
}

function adaptCurrentReadyVersion(
  value: DatasetListEnvelopeWire["items"][number]["current_version"],
): DatasetListItemVm["currentVersion"] {
  return value
    ? {
        versionId: value.version_id as DatasetVersionId,
        displayVersion: value.display_version,
        kind: adaptDatasetKind(value.kind),
        publishedAt: value.published_at,
        manifestSha256: value.manifest_sha256,
      }
    : null;
}

export function adaptDatasetListEnvelope(
  wire: DatasetListEnvelopeWire,
): CursorPageVm<DatasetListItemVm> {
  return {
    items: wire.items.map((item) => ({
      datasetId: item.dataset_id as DatasetId,
      folderPath: item.folder_path,
      collectionTaskId: item.collection_task_id ?? null,
      name: item.name,
      availability: adaptDatasetAvailability(item.availability),
      datasetCreatedAt: item.dataset_created_at,
      datasetActivityAt: item.dataset_activity_at,
      currentVersion: adaptCurrentReadyVersion(item.current_version),
      episodeCount: item.episode_count,
      pendingReviewVersionCount: item.pending_review_version_count,
      returnedVersionCount: item.returned_version_count,
      actionableDraftCount: item.actionable_draft_count,
      allowedActions: adaptAllowedActions(item.allowed_actions),
    })),
    pageInfo: {
      after: wire.page_info.after,
      before: wire.page_info.before,
      hasNextPage: wire.page_info.has_next,
      hasPreviousPage: wire.page_info.has_previous,
    },
    snapshotAt: wire.snapshot_at,
    snapshotId: wire.snapshot_id,
    scope: adaptScope(wire.scope),
    requestId: wire.request_id,
  };
}

export function adaptDatasetSummary(
  wire: DatasetSummaryWire,
): DatasetSummaryVm {
  return {
    scope: adaptScope(wire.scope),
    datasetCount: wire.dataset_count,
    episodeCount: wire.episode_count,
    pendingReviewVersionCount: wire.pending_review_version_count,
    returnedVersionCount: wire.returned_version_count,
    actionableDraftCount: wire.actionable_draft_count,
    normalizedFilters: wire.normalized_filters,
  };
}

export function adaptDatasetFacets(wire: DatasetFacetsWire): DatasetFacetsVm {
  return {
    scope: adaptScope(wire.scope),
    normalizedFilters: wire.normalized_filters,
    robots: wire.robots,
    robotModels: wire.robot_models,
    tasks: wire.tasks,
    tags: wire.tags,
    scenes: wire.scenes,
    assetStates: wire.asset_states,
    storageClasses: wire.storage_classes,
    channels: wire.channels,
  };
}

export function adaptDatasetsPageCapabilities(
  wire: DatasetsPageCapabilitiesWire,
): DatasetsPageCapabilitiesVm {
  return {
    scope: adaptScope(wire.scope),
    authorizationRevision: wire.authorization_revision,
    allowedActions: wire.allowed_actions,
    blockedReasons: wire.blocked_reasons,
  };
}

export function adaptDatasetVersionSchemaSummary(
  wire: DatasetVersionSchemaSummaryWire,
): DatasetVersionSchemaSummaryVm {
  return {
    datasetId: wire.dataset_id as DatasetId,
    versionId: wire.version_id as DatasetVersionId,
    snapshot: {
      type: wire.schema_snapshot.reference_type,
      id: wire.schema_snapshot.reference_id,
      version: wire.schema_snapshot.reference_version,
      sha256: wire.schema_snapshot.sha256,
    },
    channelCount: wire.channel_count ?? null,
  };
}

export function adaptSourceProvenancePage(
  wire: SourceProvenancePageWire,
): CursorPageVm<SourceProvenanceVm> {
  return {
    items: wire.items.map((item) => ({
      datasetId: item.dataset_id as DatasetId,
      versionId: item.version_id as DatasetVersionId,
      storageRegionCode: item.storage_region_code ?? null,
      provenanceId: item.provenance_id,
      uploadId: item.upload_id,
      sourceId: item.source_id ?? null,
      sourceDisplayName: item.source_display_name ?? null,
      sourceManifestId: item.source_manifest_id,
      sourceManifestSha256: item.source_manifest_sha256,
      verifiedObjectSetHash: item.verified_object_set_hash,
      registeredAt: item.registered_at,
    })),
    pageInfo: {
      after: wire.page_info.after,
      before: wire.page_info.before,
      hasNextPage: wire.page_info.has_next,
      hasPreviousPage: wire.page_info.has_previous,
    },
    snapshotAt: wire.snapshot_at,
    snapshotId: wire.snapshot_id,
    scope: adaptScope(wire.scope),
    requestId: wire.request_id,
  };
}

export function adaptDatasetVersionCapacity(
  wire: DatasetVersionCapacityWire,
): DatasetVersionCapacityVm {
  return {
    datasetId: wire.dataset_id as DatasetId,
    versionId: wire.version_id as DatasetVersionId,
    state: wire.state,
    sourceBytes: wire.source_bytes,
    requiredPhysicalBytes: wire.required_physical_bytes,
    actualOssBytes: wire.actual_oss_bytes,
    calculatedAt: wire.calculated_at,
    basisRevision: wire.basis_revision,
  };
}

export function adaptEpisodeRevisionHistoryPage(
  wire: EpisodeRevisionHistoryWire,
): CursorPageVm<EpisodeRevisionHistoryItemVm> {
  return {
    items: wire.items.map((item) => ({
      datasetId: item.dataset_id as DatasetId,
      episodeId: item.episode_id as EpisodeId,
      versionId: item.version_id as DatasetVersionId,
      displayVersion: item.display_version,
      versionKind: adaptDatasetKind(item.version_kind),
      versionStatus: adaptDatasetStatus(item.version_status),
      versionCreatedAt: item.version_created_at,
      versionPublishedAt: item.version_published_at,
      selectedRevisionId: item.selected_revision
        .revision_id as EpisodeRevisionId,
      ordinal: item.selected_revision.ordinal,
      contentSha256: item.selected_revision.content_sha256,
    })),
    pageInfo: {
      after: wire.page_info.after,
      before: wire.page_info.before,
      hasNextPage: wire.page_info.has_next,
      hasPreviousPage: wire.page_info.has_previous,
    },
    snapshotAt: wire.snapshot_at,
    snapshotId: wire.snapshot_id,
    scope: adaptScope(wire.scope),
    requestId: wire.request_id,
  };
}

export function adaptVersionSchema(wire: VersionSchemaWire): VersionSchemaVm {
  return {
    datasetId: wire.dataset_id as DatasetId,
    versionId: wire.version_id as DatasetVersionId,
    snapshotToken: wire.snapshot_token,
    snapshot: {
      type: wire.schema_snapshot.reference_type,
      id: wire.schema_snapshot.reference_id,
      version: wire.schema_snapshot.reference_version,
      sha256: wire.schema_snapshot.sha256,
    },
    channelCount: wire.channel_count,
    channels: wire.channels.map((channel) => ({
      id: channel.channel_id,
      name: channel.name,
      dataType: channel.data_type,
      unit: channel.unit,
    })),
  };
}

export function adaptRequiredStoragePage(
  wire: RequiredStoragePageWire,
): CursorPageVm<RequiredStorageItemVm> {
  return {
    items: wire.items.map((item) => ({
      objectId: item.object_id,
      role: item.role,
      sizeBytes: item.size_bytes,
      reuse: item.reuse,
      protection: item.protection,
      safeLocator: item.safe_locator ?? null,
    })),
    pageInfo: {
      after: wire.page_info.after,
      before: wire.page_info.before,
      hasNextPage: wire.page_info.has_next,
      hasPreviousPage: wire.page_info.has_previous,
    },
    snapshotAt: wire.snapshot_at,
    snapshotId: wire.snapshot_id,
    scope: adaptScope(wire.scope),
    requestId: wire.request_id,
  };
}

export function adaptOperationalInventoryPage(
  wire: OperationalInventoryPageWire,
): CursorPageVm<OperationalInventoryItemVm> {
  return {
    items: wire.items.map((item) => ({
      inventoryId: item.inventory_id,
      kind: item.kind,
      operationalRevision: item.operational_revision,
      status: item.status,
      sizeBytes: item.size_bytes,
      jobId: item.job_id,
      createdAt: item.created_at,
      completedAt: item.completed_at,
    })),
    pageInfo: {
      after: wire.page_info.after,
      before: wire.page_info.before,
      hasNextPage: wire.page_info.has_next,
      hasPreviousPage: wire.page_info.has_previous,
    },
    snapshotAt: wire.snapshot_at,
    snapshotId: wire.snapshot_id,
    scope: adaptScope(wire.scope),
    requestId: wire.request_id,
  };
}

export function adaptDatasetBootstrap(
  wire: DatasetBootstrapWire,
): DatasetBootstrapVm {
  return {
    scope: adaptScope(wire.scope),
    dataset: {
      id: wire.dataset.dataset_id as DatasetId,
      folderPath: wire.dataset.folder_path,
      name: wire.dataset.name,
      description: wire.dataset.description,
      labels: wire.dataset.labels.map((label) => ({
        key: label,
        value: label,
      })),
      availability: adaptDatasetAvailability(wire.dataset.availability),
      owner: {
        id: wire.dataset.owner.id,
        displayName: wire.dataset.owner.display_name,
      },
      createdAt: wire.dataset.created_at,
      updatedAt: wire.dataset.updated_at,
      etag: wire.dataset.etag,
    },
    currentReadyVersion: adaptCurrentReadyVersion(wire.current_ready_version),
    workingVersionId: (wire.working_version_id ?? null) as DatasetVersionId | null,
    suggestedVersionId: wire.suggested_version_id as DatasetVersionId | null,
    summary: {
      episodeCount: wire.summary.episode_count,
      effectiveDurationNs: wire.summary.effective_duration_ns,
      sourceBytes: wire.summary.source_bytes,
      requiredPhysicalBytes: wire.summary.required_physical_bytes,
      actualOssBytes: wire.summary.actual_oss_bytes,
      pendingReviewVersionCount: wire.summary.pending_review_version_count,
      returnedVersionCount: wire.summary.returned_version_count,
      actionableDraftCount: wire.summary.actionable_draft_count,
      calculatedAt: wire.summary.calculated_at,
      calculationState: adaptCalculationState(wire.summary.calculation_state),
    },
    allowedActions: adaptAllowedActions(wire.dataset.allowed_actions),
  };
}

export function adaptVersionPage(
  wire: VersionPageEnvelopeWire,
): CursorPageVm<DatasetVersion> {
  return {
    items: wire.items.map((item) => adaptVersion(item)),
    pageInfo: {
      after: wire.page_info.after,
      before: wire.page_info.before,
      hasNextPage: wire.page_info.has_next,
      hasPreviousPage: wire.page_info.has_previous,
    },
    snapshotAt: wire.snapshot_at,
    snapshotId: wire.snapshot_id,
    scope: adaptScope(wire.scope),
    requestId: wire.request_id,
  };
}

export function adaptEpisodePage(
  wire: EpisodePageEnvelopeWire,
): CursorPageVm<EpisodeListItemVm> {
  return {
    items: wire.items.map((item) => ({
      datasetId: item.dataset_id as DatasetId,
      versionId: item.version_id as DatasetVersionId,
      episodeId: item.episode_id as EpisodeId,
      storageRegionCode: item.storage_region_code ?? null,
      selectedRevisionId: item.selected_revision
        .revision_id as EpisodeRevisionId,
      ordinal: item.selected_revision.ordinal,
      included: item.included,
      successState:
        item.success_state === "SUCCEEDED" || item.success_state === "FAILED"
          ? item.success_state
          : "UNKNOWN",
      reviewStatus:
        item.review_status === "UNREVIEWED" ||
        item.review_status === "ACCEPTED" ||
        item.review_status === "HAS_FINDING"
          ? item.review_status
          : "UNKNOWN",
      reviewFindingCount: item.review_finding_count ?? null,
      task: item.task,
      robotId: item.robot_id,
    })),
    pageInfo: {
      after: wire.page_info.after,
      before: wire.page_info.before,
      hasNextPage: wire.page_info.has_next,
      hasPreviousPage: wire.page_info.has_previous,
    },
    snapshotAt: wire.snapshot_at,
    snapshotId: wire.snapshot_id,
    scope: adaptScope(wire.scope),
    requestId: wire.request_id,
  };
}

export function adaptVersionBootstrap(
  wire: VersionBootstrapWire,
): VersionBootstrapVm {
  const rawVersion = wire.version as Record<string, unknown>;
  const returnLineage =
    wire.version.status === "RETURNED" &&
    typeof rawVersion.review_decision_id === "string" &&
    Array.isArray(rawVersion.review_finding_ids) &&
    rawVersion.review_finding_ids.every((id) => typeof id === "string") &&
    typeof rawVersion.successor_draft_id === "string" &&
    typeof rawVersion.supersedes_draft_id === "string" &&
    typeof rawVersion.returned_from_version_id === "string" &&
    typeof rawVersion.returned_from_review_decision_id === "string"
      ? {
          reviewDecisionId: rawVersion.review_decision_id as ReviewDecisionId,
          reviewFindingIds:
            rawVersion.review_finding_ids as unknown as readonly ReviewFindingId[],
          successorDraftId: rawVersion.successor_draft_id,
          supersedesDraftId: rawVersion.supersedes_draft_id,
          returnedFromVersionId:
            rawVersion.returned_from_version_id as DatasetVersionId,
          returnedFromReviewDecisionId:
            rawVersion.returned_from_review_decision_id as ReviewDecisionId,
        }
      : null;
  return {
    scope: adaptScope(wire.scope),
    datasetId: wire.dataset_id as DatasetId,
    versionId: wire.version_id as DatasetVersionId,
    snapshotToken: wire.snapshot_token,
    operationalRevision: wire.operational_revision,
    version: adaptVersion(wire.version),
    allowedActions: adaptAllowedActions(wire.version.allowed_actions),
    returnLineage,
  };
}

export function adaptApproveReviewResult(
  wire: ApproveReviewResultWire,
): ApproveReviewResultVm {
  return {
    scope: adaptScope(wire.scope),
    reviewDecision: {
      id: wire.review_decision.id as ReviewDecisionId,
      outputVersionId: wire.review_decision
        .output_version_id as DatasetVersionId,
      decision: wire.review_decision.decision,
      immutable: true,
      createdAt: wire.review_decision.created_at,
    },
    outputVersionId: wire.output_version.id as DatasetVersionId,
    versionToken: wire.output_version.version_token,
    jobId: wire.job.job_id,
    requestId: wire.request_id,
  };
}

export function adaptReturnReviewResult(
  wire: ReturnReviewResultWire,
): ReturnReviewResultVm {
  return {
    scope: adaptScope(wire.scope),
    reviewDecision: {
      id: wire.review_decision.id as ReviewDecisionId,
      outputVersionId: wire.review_decision
        .output_version_id as DatasetVersionId,
      decision: wire.review_decision.decision,
      immutable: true,
      createdAt: wire.review_decision.created_at,
    },
    findings: wire.findings.map((finding) => ({
      id: finding.id as ReviewFindingId,
      outputRevisionId: finding.output_revision_id as EpisodeRevisionId,
      episodeStreamId:
        finding.episode_stream_id as ReviewFinding["episodeStreamId"],
      startNs: finding.start_ns,
      endNs: finding.end_ns,
      findingType: finding.finding_type,
      severity: finding.severity,
      note: finding.note,
      immutable: true,
      createdAt: finding.created_at,
    })),
    reviewFindingIds:
      wire.review_finding_ids as unknown as readonly ReviewFindingId[],
    outputVersionId: wire.output_version.id as DatasetVersionId,
    versionToken: wire.output_version.version_token,
    successorDraftId: wire.successor_draft_id,
    supersedesDraftId: wire.supersedes_draft_id,
    returnedFromVersionId: wire.returned_from_version_id as DatasetVersionId,
    returnedFromReviewDecisionId:
      wire.returned_from_review_decision_id as ReviewDecisionId,
  };
}
