import type { z } from "zod";
import type { DatasetId } from "../../../entities/dataset";
import type { DatasetVersionId } from "../../../entities/dataset-version";
import type { EpisodeId, EpisodeRevisionId } from "../../../entities/episode";
import { createDomainError } from "../../../shared/api/domain-error";
import { request, type QueryValue } from "../../../shared/api/http-client";
import { parseWire } from "../../../shared/api/validate";
import { getShellState } from "../../../shared/scope/shell-store";
import {
  adaptApproveReviewResult,
  adaptDatasetBootstrap,
  adaptDatasetFacets,
  adaptDatasetListEnvelope,
  adaptDatasetSummary,
  adaptDatasetsPageCapabilities,
  adaptDatasetVersionCapacity,
  adaptDatasetVersionSchemaSummary,
  adaptEpisodeRevisionHistoryPage,
  adaptEpisodePage,
  adaptOperationalInventoryPage,
  adaptRequiredStoragePage,
  adaptReturnReviewResult,
  adaptSourceProvenancePage,
  adaptVersionBootstrap,
  adaptVersionPage,
  adaptVersionSchema,
} from "./adapters";
import {
  CONTRACT_VERSION,
  approveReviewCommandWireSchema,
  approveReviewResultWireSchema,
  createDatasetRequestWireSchema,
  datasetBootstrapWireSchema,
  datasetFacetsEnvelopeWireSchema,
  datasetEnvelopeWireSchema,
  datasetListEnvelopeWireSchema,
  datasetSummaryEnvelopeWireSchema,
  datasetsPageCapabilitiesEnvelopeWireSchema,
  datasetVersionCapacityWireSchema,
  datasetVersionSchemaSummaryWireSchema,
  deletionPreflightWireSchema,
  episodePageEnvelopeWireSchema,
  episodeRevisionHistoryWireSchema,
  episodeRevisionWireSchema,
  jobAcceptedWireSchema,
  operationalInventoryPageWireSchema,
  publishedDatasetManifestWireSchema,
  requiredStoragePageWireSchema,
  returnReviewCommandWireSchema,
  returnReviewResultWireSchema,
  reviewChecksWireSchema,
  sourceProvenancePageWireSchema,
  versionBootstrapWireSchema,
  versionManifestWireSchema,
  versionPageEnvelopeWireSchema,
  versionSchemaWireSchema,
  type ApproveReviewCommandWire,
  type CreateDatasetRequestWire,
  type ReturnReviewCommandWire,
} from "./wire-schemas";

export type DatasetListApiFilters = Readonly<{
  collectionTaskId?: string;
  q?: string;
  robotModelId?: string;
  robotId?: string;
  task?: string;
  tag?: string;
  scene?: string;
  assetState?: string;
  workflowState?: "pendingReview" | "returned" | "actionableDraft";
  storageClass?: string;
  channels?: readonly string[];
  channelMatch?: "all" | "any";
  datasetCreatedFrom?: string;
  datasetCreatedTo?: string;
  sort?: "activityDesc" | "createdDesc" | "nameAsc";
  after?: string;
  before?: string;
  limit?: 20 | 50 | 100;
}>;

export type VersionListApiFilters = Readonly<{
  q?: string;
  versionKind?: "raw" | "cleaned";
  versionStatus?: "reviewing" | "returned" | "ready";
  sort?: string;
  after?: string;
  before?: string;
  limit?: 10 | 20 | 50;
}>;

export type EpisodeListApiFilters = Readonly<{
  snapshotToken?: string;
  collectionTaskId?: string;
  q?: string;
  task?: string;
  robotId?: string;
  successState?: string;
  startedFrom?: string;
  startedTo?: string;
  included?: boolean;
  reviewStatus?: readonly string[];
  hasFinding?: boolean;
  changeType?: readonly string[];
  sort?: string;
  after?: string;
  before?: string;
  limit?: 10 | 20 | 50 | 100;
}>;

export type EpisodeRevisionHistoryApiFilters = Readonly<{
  after?: string;
  before?: string;
  limit?: 10 | 20 | 50;
}>;

export type PublishDatasetVersionCommand = Readonly<{
  datasetId: DatasetId;
  datasetVersion: string;
  baseLanceVersion: string;
}>;

const datasetSort = {
  activityDesc: "activity_at:desc,dataset_id:desc",
  createdDesc: "created_at:desc,dataset_id:desc",
  nameAsc: "name:asc,dataset_id:asc",
} as const;

const versionSort: Readonly<Record<string, string>> = {
  "created-desc": "created_at:desc,version_id:desc",
  "created-asc": "created_at:asc,version_id:asc",
  "version-desc": "display_version:desc,version_id:desc",
  "version-asc": "display_version:asc,version_id:asc",
};

const episodeSort: Readonly<Record<string, string>> = {
  "ordinal-asc": "ordinal:asc,episode_id:asc",
  "started-desc": "started_at_ns:desc,episode_id:desc",
  "started-asc": "started_at_ns:asc,episode_id:asc",
};

function projectId(): string {
  const value = getShellState().scope?.projectId;
  if (value) return value;
  throw createDomainError({
    code: "FORBIDDEN",
    message: "请先选择项目范围",
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId: null,
    retryable: false,
    httpStatus: 403,
  });
}

function projectPath(suffix: string): string {
  return `/projects/${encodeURIComponent(projectId())}${suffix}`;
}

function requestId(raw: unknown): string | null {
  if (typeof raw !== "object" || raw === null) return null;
  if ("request_id" in raw && typeof raw.request_id === "string")
    return raw.request_id;
  if (
    "meta" in raw &&
    typeof raw.meta === "object" &&
    raw.meta !== null &&
    "request_id" in raw.meta
  ) {
    const value = raw.meta.request_id;
    return typeof value === "string" ? value : null;
  }
  return null;
}

function parse<T>(schema: z.ZodType<T>, raw: unknown, endpoint: string): T {
  const parsed = parseWire(schema, raw, {
    endpoint,
    schemaVersion: CONTRACT_VERSION,
    requestId: requestId(raw),
  });
  const record =
    typeof parsed === "object" && parsed !== null
      ? (parsed as Record<string, unknown>)
      : null;
  const data =
    record && typeof record.data === "object" && record.data !== null
      ? (record.data as Record<string, unknown>)
      : null;
  const scope = (record?.scope ?? data?.scope) as
    | Record<string, unknown>
    | undefined;
  const expected = getShellState().scope;
  if (
    expected &&
    scope &&
    (scope.organization_id !== expected.organizationId ||
      scope.project_id !== expected.projectId ||
      scope.region_code !== expected.regionCode)
  ) {
    throw createDomainError({
      code: "CONTRACT_MISMATCH",
      message: `响应 Scope 与当前授权范围不一致: ${endpoint}`,
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: requestId(raw),
      retryable: false,
      httpStatus: null,
    });
  }
  return parsed;
}

function contractMismatch(
  message: string,
  requestIdValue: string | null = null,
): never {
  throw createDomainError({
    code: "CONTRACT_MISMATCH",
    message,
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId: requestIdValue,
    retryable: false,
    httpStatus: null,
  });
}

function assertPathIdentity(
  actualDatasetId: string,
  actualVersionId: string,
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  endpoint: string,
): void {
  if (actualDatasetId === datasetId && actualVersionId === versionId) return;
  throw createDomainError({
    code: "CONTRACT_MISMATCH",
    message: `响应资源身份与固定 Path 不一致: ${endpoint}`,
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId: null,
    retryable: false,
    httpStatus: null,
  });
}

function aggregateQuery(
  filters: DatasetListApiFilters,
): Record<string, QueryValue | readonly QueryValue[]> {
  return {
    q: filters.q,
    robotModelId: filters.robotModelId,
    robotId: filters.robotId,
    collectionTaskId: filters.collectionTaskId,
    task: filters.task,
    tag: filters.tag,
    scene: filters.scene,
    assetState: filters.assetState,
    workflowState: filters.workflowState,
    storageClass: filters.storageClass,
    channels: filters.channels,
    channelMatch: filters.channelMatch,
    createdFrom: filters.datasetCreatedFrom,
    createdTo: filters.datasetCreatedTo,
  };
}

function listQuery(
  filters: DatasetListApiFilters,
): Record<string, QueryValue | readonly QueryValue[]> {
  return {
    ...aggregateQuery(filters),
    sort: datasetSort[filters.sort ?? "activityDesc"],
    after: filters.after,
    before: filters.before,
    limit: filters.limit ?? 20,
  };
}

export async function fetchDatasets(
  filters: DatasetListApiFilters,
  signal?: AbortSignal,
) {
  const endpoint = projectPath("/datasets");
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    query: listQuery(filters),
    signal,
  });
  return adaptDatasetListEnvelope(
    parse(datasetListEnvelopeWireSchema, raw, endpoint),
  );
}

export async function fetchDatasetsPageCapabilities(signal?: AbortSignal) {
  const endpoint = projectPath("/datasets:page-capabilities");
  const raw = await request<unknown>({ method: "GET", path: endpoint, signal });
  return adaptDatasetsPageCapabilities(
    parse(datasetsPageCapabilitiesEnvelopeWireSchema, raw, endpoint).data,
  );
}

export async function fetchDatasetSummary(
  filters: DatasetListApiFilters,
  signal?: AbortSignal,
) {
  const endpoint = projectPath("/datasets:summary");
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    query: aggregateQuery(filters),
    signal,
  });
  return adaptDatasetSummary(
    parse(datasetSummaryEnvelopeWireSchema, raw, endpoint).data,
  );
}

export async function fetchDatasetFacets(
  filters: DatasetListApiFilters,
  signal?: AbortSignal,
) {
  const endpoint = projectPath("/datasets:facets");
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    query: aggregateQuery(filters),
    signal,
  });
  return adaptDatasetFacets(
    parse(datasetFacetsEnvelopeWireSchema, raw, endpoint).data,
  );
}

export async function fetchDatasetBootstrap(
  datasetId: DatasetId,
  signal?: AbortSignal,
) {
  const endpoint = projectPath(
    `/datasets/${encodeURIComponent(datasetId)}/bootstrap`,
  );
  const raw = await request<unknown>({ method: "GET", path: endpoint, signal });
  const parsed = parse(datasetBootstrapWireSchema, raw, endpoint).data;
  if (parsed.dataset.dataset_id !== datasetId) {
    contractMismatch("Dataset Bootstrap 身份与固定 Path 不一致");
  }
  return adaptDatasetBootstrap(parsed);
}

export async function fetchDatasetVersions(
  datasetId: DatasetId,
  filters: VersionListApiFilters,
  signal?: AbortSignal,
) {
  const endpoint = projectPath(
    `/datasets/${encodeURIComponent(datasetId)}/versions`,
  );
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    signal,
    query: {
      q: filters.q,
      versionKind: filters.versionKind?.toUpperCase(),
      versionStatus: filters.versionStatus?.toUpperCase(),
      sort: versionSort[filters.sort ?? "created-desc"],
      after: filters.after,
      before: filters.before,
      limit: filters.limit ?? 20,
    },
  });
  const parsed = parse(versionPageEnvelopeWireSchema, raw, endpoint);
  parsed.items.forEach((item) => {
    if (item.dataset_id !== datasetId) {
      contractMismatch(
        "Version 列表包含其他 Dataset 的资源",
        parsed.request_id,
      );
    }
  });
  return adaptVersionPage(parsed);
}

export async function publishDatasetVersion(
  command: PublishDatasetVersionCommand,
) {
  const endpoint = "/datasets/publications";
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    body: {
      project_id: projectId(),
      dataset_id: command.datasetId,
      dataset_version: command.datasetVersion,
      base_lance_version: command.baseLanceVersion,
    },
  });
  const manifest = parse(publishedDatasetManifestWireSchema, raw, endpoint);
  if (
    manifest.dataset_id !== command.datasetId ||
    manifest.dataset_version !== command.datasetVersion ||
    manifest.base_lance_version !== command.baseLanceVersion
  ) {
    contractMismatch("发布结果与请求的数据集版本身份不一致");
  }
  return manifest;
}

export async function fetchDatasetVersionSchemaSummary(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  signal?: AbortSignal,
) {
  const endpoint = projectPath(
    `/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(versionId)}/schema-summary`,
  );
  const parsed = parse(
    datasetVersionSchemaSummaryWireSchema,
    await request<unknown>({ method: "GET", path: endpoint, signal }),
    endpoint,
  ).data;
  assertPathIdentity(
    parsed.dataset_id,
    parsed.version_id,
    datasetId,
    versionId,
    endpoint,
  );
  return adaptDatasetVersionSchemaSummary(parsed);
}

export async function fetchDatasetVersionSourceProvenance(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  filters: Readonly<{
    q?: string;
    sourceId?: string;
    sort?: string;
    after?: string;
    before?: string;
    limit?: 10 | 20 | 50;
  }>,
  signal?: AbortSignal,
) {
  const endpoint = projectPath(
    `/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(versionId)}/source-provenance`,
  );
  const sort: Readonly<Record<string, string>> = {
    "registered-desc": "registered_at:desc,provenance_id:desc",
    "registered-asc": "registered_at:asc,provenance_id:asc",
    "source-name-asc": "source_display_name:asc,provenance_id:asc",
  };
  const parsed = parse(
    sourceProvenancePageWireSchema,
    await request<unknown>({
      method: "GET",
      path: endpoint,
      query: {
        ...filters,
        sort: sort[filters.sort ?? "registered-desc"],
        limit: filters.limit ?? 20,
      },
      signal,
    }),
    endpoint,
  );
  parsed.items.forEach((item) =>
    assertPathIdentity(
      item.dataset_id,
      item.version_id,
      datasetId,
      versionId,
      endpoint,
    ),
  );
  return adaptSourceProvenancePage(parsed);
}

export async function fetchDatasetVersionCapacity(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  signal?: AbortSignal,
) {
  const endpoint = projectPath(
    `/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(versionId)}/capacity-facts`,
  );
  const parsed = parse(
    datasetVersionCapacityWireSchema,
    await request<unknown>({ method: "GET", path: endpoint, signal }),
    endpoint,
  ).data;
  assertPathIdentity(
    parsed.dataset_id,
    parsed.version_id,
    datasetId,
    versionId,
    endpoint,
  );
  return adaptDatasetVersionCapacity(parsed);
}

export async function fetchVersionEpisodes(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  filters: EpisodeListApiFilters,
  signal?: AbortSignal,
) {
  const endpoint = projectPath(
    `/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(versionId)}/episodes`,
  );
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    signal,
    query: {
      snapshotToken: filters.snapshotToken,
      q: filters.q,
      task: filters.collectionTaskId ?? filters.task,
      robotId: filters.robotId,
      successState: filters.successState?.toUpperCase(),
      startedFrom: filters.startedFrom,
      startedTo: filters.startedTo,
      included: filters.included,
      reviewStatus: filters.reviewStatus,
      hasFinding: filters.hasFinding,
      changeType: filters.changeType,
      sort: episodeSort[filters.sort ?? "ordinal-asc"] ?? filters.sort,
      after: filters.after,
      before: filters.before,
      limit: filters.limit ?? 20,
    },
  });
  const parsed = parse(episodePageEnvelopeWireSchema, raw, endpoint);
  parsed.items.forEach((item) => {
    assertPathIdentity(
      item.dataset_id,
      item.version_id,
      datasetId,
      versionId,
      endpoint,
    );
  });
  return adaptEpisodePage(parsed);
}

export async function fetchVersionBootstrap(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  signal?: AbortSignal,
) {
  const endpoint = projectPath(
    `/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(versionId)}/bootstrap`,
  );
  const raw = await request<unknown>({ method: "GET", path: endpoint, signal });
  const parsed = parse(versionBootstrapWireSchema, raw, endpoint).data;
  assertPathIdentity(
    parsed.dataset_id,
    parsed.version_id,
    datasetId,
    versionId,
    endpoint,
  );
  return adaptVersionBootstrap(parsed);
}

export async function fetchEpisodeRevision(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  revisionId: EpisodeRevisionId,
  snapshotToken: string,
  signal?: AbortSignal,
) {
  const endpoint = projectPath(
    `/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(versionId)}/episode-revisions/${encodeURIComponent(revisionId)}`,
  );
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    query: { snapshotToken },
    signal,
  });
  const parsed = parse(episodeRevisionWireSchema, raw, endpoint).data;
  assertPathIdentity(
    parsed.dataset_id,
    parsed.version_id,
    datasetId,
    versionId,
    endpoint,
  );
  if (parsed.revision_id !== revisionId) {
    contractMismatch("Episode Revision 身份与固定 Path 不一致");
  }
  return parsed;
}

export async function fetchEpisodeRevisionHistory(
  datasetId: DatasetId,
  episodeId: EpisodeId,
  filters: EpisodeRevisionHistoryApiFilters,
  signal?: AbortSignal,
) {
  const endpoint = projectPath(
    `/datasets/${encodeURIComponent(datasetId)}/episodes/${encodeURIComponent(episodeId)}/revision-history`,
  );
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    query: {
      after: filters.after,
      before: filters.before,
      limit: filters.limit ?? 20,
    },
    signal,
  });
  const parsed = parse(episodeRevisionHistoryWireSchema, raw, endpoint);
  parsed.items.forEach((item) => {
    if (item.dataset_id !== datasetId || item.episode_id !== episodeId) {
      contractMismatch(
        "Episode 历史包含其他 Dataset 或 Episode 的修订",
        parsed.request_id,
      );
    }
  });
  return adaptEpisodeRevisionHistoryPage(parsed);
}

export async function fetchVersionManifest(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  cursors: { after?: string; before?: string; limit?: number },
  signal?: AbortSignal,
) {
  const endpoint = projectPath(
    `/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(versionId)}/manifest`,
  );
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    query: cursors,
    signal,
  });
  const parsed = parse(versionManifestWireSchema, raw, endpoint);
  assertPathIdentity(
    parsed.dataset_id,
    parsed.version_id,
    datasetId,
    versionId,
    endpoint,
  );
  return parsed;
}

export async function fetchVersionSchema(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  snapshotToken: string,
  signal?: AbortSignal,
) {
  const endpoint = projectPath(
    `/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(versionId)}/schema`,
  );
  const parsed = parse(
    versionSchemaWireSchema,
    await request<unknown>({
      method: "GET",
      path: endpoint,
      query: { snapshotToken },
      signal,
    }),
    endpoint,
  ).data;
  assertPathIdentity(
    parsed.dataset_id,
    parsed.version_id,
    datasetId,
    versionId,
    endpoint,
  );
  if (parsed.snapshot_token !== snapshotToken)
    contractMismatch("数据版本的数据结构固定标识不一致");
  return adaptVersionSchema(parsed);
}

export async function fetchRequiredStorage(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  snapshotToken: string,
  cursors: Readonly<{ after?: string; before?: string; limit?: 20 | 50 | 100 }>,
  signal?: AbortSignal,
) {
  const endpoint = projectPath(
    `/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(versionId)}/required-storage`,
  );
  const parsed = parse(
    requiredStoragePageWireSchema,
    await request<unknown>({
      method: "GET",
      path: endpoint,
      query: { snapshotToken, ...cursors, sort: "role:asc,object_id:asc" },
      signal,
    }),
    endpoint,
  );
  parsed.items.forEach((item) =>
    assertPathIdentity(
      item.dataset_id,
      item.version_id,
      datasetId,
      versionId,
      endpoint,
    ),
  );
  return adaptRequiredStoragePage(parsed);
}

export async function fetchOperationalInventory(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  operationalRevision: string,
  cursors: Readonly<{ after?: string; before?: string; limit?: 20 | 50 | 100 }>,
  signal?: AbortSignal,
) {
  const endpoint = projectPath(
    `/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(versionId)}/operational-inventory`,
  );
  const parsed = parse(
    operationalInventoryPageWireSchema,
    await request<unknown>({
      method: "GET",
      path: endpoint,
      query: {
        operationalRevision,
        ...cursors,
        sort: "created_at:desc,inventory_id:desc",
      },
      signal,
    }),
    endpoint,
  );
  parsed.items.forEach((item) => {
    assertPathIdentity(
      item.dataset_id,
      item.version_id,
      datasetId,
      versionId,
      endpoint,
    );
    if (item.operational_revision !== operationalRevision)
      contractMismatch("运营库存 revision 不一致", parsed.request_id);
  });
  return adaptOperationalInventoryPage(parsed);
}

export async function createDataset(
  input: CreateDatasetRequestWire,
  idempotencyKey: string,
) {
  const body = createDatasetRequestWireSchema.parse(input);
  const endpoint = projectPath("/datasets");
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    body,
    idempotencyKey,
  });
  return parse(datasetEnvelopeWireSchema, raw, endpoint).data;
}

export async function runReviewChecks(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  etag: string,
) {
  const endpoint = projectPath(
    `/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(versionId)}/review-checks`,
  );
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    ifMatch: etag,
    body: { expected_status: "REVIEWING" },
  });
  const parsed = parse(reviewChecksWireSchema, raw, endpoint).data;
  if (
    parsed.dataset_id !== datasetId ||
    parsed.output_version_id !== versionId
  ) {
    contractMismatch("Review Checks 身份与固定 Path 不一致");
  }
  return parsed;
}

export async function approveVersionReview(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  command: ApproveReviewCommandWire,
  etag: string,
  idempotencyKey: string,
) {
  const body = approveReviewCommandWireSchema.parse(command);
  const endpoint = projectPath(
    `/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(versionId)}:approve`,
  );
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    body,
    ifMatch: etag,
    idempotencyKey,
  });
  const parsed = parse(approveReviewResultWireSchema, raw, endpoint);
  if (parsed.output_version.id !== versionId) {
    contractMismatch("Approve 响应指向了其他 Version", parsed.request_id);
  }
  return adaptApproveReviewResult(parsed);
}

export async function returnVersionReview(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  command: ReturnReviewCommandWire,
  expectedSourceDraftId: string,
  etag: string,
  idempotencyKey: string,
) {
  const body = returnReviewCommandWireSchema.parse(command);
  const endpoint = projectPath(
    `/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(versionId)}:return`,
  );
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    body,
    ifMatch: etag,
    idempotencyKey,
  });
  const parsed = parse(returnReviewResultWireSchema, raw, endpoint).data;
  if (
    parsed.output_version.id !== versionId ||
    parsed.supersedes_draft_id !== expectedSourceDraftId
  ) {
    contractMismatch("Return 响应的 Version 或前序 Draft lineage 不一致");
  }
  const matchesRequest =
    parsed.findings.length === body.findings.length &&
    parsed.findings.every((finding, index) => {
      const requested = body.findings[index];
      return (
        requested !== undefined &&
        finding.output_revision_id === requested.output_revision_id &&
        finding.episode_stream_id === requested.episode_stream_id &&
        finding.start_ns === requested.start_ns &&
        finding.end_ns === requested.end_ns &&
        finding.finding_type === requested.finding_type &&
        finding.severity === requested.severity &&
        finding.note === requested.note
      );
    });
  if (!matchesRequest)
    contractMismatch("Return 响应 Findings 与已确认命令不一致");
  return adaptReturnReviewResult(parsed);
}

export async function preflightDeletion(input: {
  datasetId: DatasetId;
  versionId?: DatasetVersionId;
  etag: string;
  reason: string;
  idempotencyKey: string;
}) {
  const suffix = input.versionId
    ? `/datasets/${encodeURIComponent(input.datasetId)}/versions/${encodeURIComponent(input.versionId)}/deletion-checks`
    : `/datasets/${encodeURIComponent(input.datasetId)}/deletion-checks`;
  const endpoint = projectPath(suffix);
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    ifMatch: input.etag,
    idempotencyKey: input.idempotencyKey,
    body: { intent: "DELETE", reason: input.reason, expected_etag: input.etag },
  });
  return parse(deletionPreflightWireSchema, raw, endpoint);
}

export async function createVersionDiffJob(input: {
  datasetId: DatasetId;
  versionId: DatasetVersionId;
  compareTo: DatasetVersionId;
  snapshotToken: string;
  etag: string;
  idempotencyKey: string;
}) {
  const endpoint = projectPath(
    `/datasets/${encodeURIComponent(input.datasetId)}/versions/${encodeURIComponent(input.versionId)}/diff-jobs`,
  );
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    ifMatch: input.etag,
    idempotencyKey: input.idempotencyKey,
    body: { compare_to: input.compareTo, snapshot_token: input.snapshotToken },
  });
  return parse(jobAcceptedWireSchema, raw, endpoint).job.job_id;
}

export async function resolveViewerEpisode(
  datasetId: DatasetId,
  versionId: DatasetVersionId,
  episodeId: EpisodeId,
  signal?: AbortSignal,
) {
  const bootstrap = await fetchVersionBootstrap(datasetId, versionId, signal);
  let episodes = await fetchVersionEpisodes(
    datasetId,
    versionId,
    { snapshotToken: bootstrap.snapshotToken, limit: 100 },
    signal,
  );
  let episode = episodes.items.find((item) => item.episodeId === episodeId);
  const visitedCursors = new Set<string>();
  while (!episode && episodes.pageInfo.hasNextPage) {
    const after = episodes.pageInfo.after;
    if (!after || visitedCursors.has(after)) {
      contractMismatch("Episode 分页未返回可继续读取的游标");
    }
    visitedCursors.add(after);
    episodes = await fetchVersionEpisodes(
      datasetId,
      versionId,
      { snapshotToken: bootstrap.snapshotToken, limit: 100, after },
      signal,
    );
    episode = episodes.items.find((item) => item.episodeId === episodeId);
  }
  if (!episode)
    throw createDomainError({
      code: "NOT_FOUND",
      message: "Episode 不在这个固定版本中",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: episodes.requestId,
      retryable: false,
      httpStatus: 404,
    });
  const revision = await fetchEpisodeRevision(
    datasetId,
    versionId,
    episode.selectedRevisionId,
    bootstrap.snapshotToken,
    signal,
  );
  if (revision.episode_id !== episodeId)
    throw createDomainError({
      code: "CONTRACT_MISMATCH",
      message: "Episode Revision 身份不一致",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: null,
      retryable: false,
      httpStatus: null,
    });
  return { bootstrap, episode, revision };
}
