import { z } from "zod";
import type { DatasetId } from "../../entities/dataset";
import type { DatasetVersionId } from "../../entities/dataset-version";
import type { IngestScope } from "../../entities/data-source";
import type { EpisodeId, EpisodeRevisionId } from "../../entities/episode";
import type { components } from "../../shared/api/generated/platform";
import { createDomainError } from "../../shared/api/domain-error";
import { request } from "../../shared/api/http-client";
import { parseWire } from "../../shared/api/validate";

export type FormalUploadSession = components["schemas"]["UploadSession"];
export type FormalManifestPreflight =
  components["schemas"]["ManifestPreflightResultV1"];
export type FormalQcReport = components["schemas"]["QcReportV1"];
export type FormalRawMediaSource = components["schemas"]["RawMediaSourceV1"];
export type FormalUploadProcessingStatus =
  components["schemas"]["UploadProcessingStatusV1"];

export interface FormalUploadDetail {
  readonly session: FormalUploadSession;
  readonly manifest: FormalManifestPreflight;
  readonly quality: FormalQcReport;
}

const uploadPreviewTargetSchema = z
  .object({
    schema_version: z.literal("upload-preview-target/v1"),
    project_id: z.string().min(1),
    dataset_id: z.string().min(1),
    rollout_id: z.string().min(1),
    dataset_version: z.number().int().positive(),
    lance_version: z.number().int().positive(),
    annotation_task_id: z.string().min(1),
    frequency_hz: z.number().positive(),
    start_step: z.number().int().nonnegative(),
    end_step: z.number().int().positive(),
  })
  .strict()
  .refine((target) => target.end_step > target.start_step, {
    message: "preview step window must be non-empty",
  });

const uploadViewerTargetSchema = z
  .object({
    schema_version: z.literal("dataset-ingest-viewer-target/v1"),
    dataset_id: z.string().regex(/^dataset_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/u),
    version_id: z.string().regex(/^version_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/u),
    episode_id: z.string().regex(/^episode_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/u),
    revision_id: z.string().regex(/^revision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$/u),
  })
  .strict();

const uploadProcessingStatusSchema = z
  .object({
    schema_version: z.literal("upload-processing-status/v1"),
    session_id: z.string().uuid(),
    rollout_id: z.string().min(1),
    workflow_id: z.string().min(1),
    status: z.enum([
      "PENDING",
      "RUNNING",
      "SUCCEEDED",
      "TECHNICAL_FAILED",
      "QUALITY_RISK",
      "QUALITY_REJECTED",
      "CANCELLED",
    ]),
    stage: z.string().min(1).max(128),
    attempt: z.number().int().nonnegative(),
    preview: uploadPreviewTargetSchema.nullable(),
    viewer: uploadViewerTargetSchema.nullable(),
    error_code: z
      .string()
      .regex(/^[A-Z0-9_]+$/u)
      .max(128)
      .nullable(),
    updated_at: z.string().datetime({ offset: true }),
  })
  .strict()
  .refine(
    (processing) =>
      (processing.status === "SUCCEEDED") === (processing.preview !== null) &&
      (processing.status === "SUCCEEDED") === (processing.viewer !== null) &&
      (processing.preview === null ||
        processing.viewer === null ||
        (processing.preview.dataset_id === processing.viewer.dataset_id &&
          processing.viewer.version_id ===
            `version_lance_${processing.preview.dataset_version}`)),
    { message: "successful upload targets must share one Dataset version" },
  );

export interface FormalUploadPreviewTarget {
  readonly datasetId: string;
  readonly datasetVersion: number;
  readonly lanceVersion: number;
  readonly annotationTaskId: string;
  readonly frequencyHz: number;
  readonly startStep: number;
  readonly endStep: number;
}

export interface FormalUploadViewerTarget {
  readonly datasetId: DatasetId;
  readonly versionId: DatasetVersionId;
  readonly episodeId: EpisodeId;
  readonly revisionId: EpisodeRevisionId;
}

function resourceRoot(scope: IngestScope): string {
  return `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}`;
}

function sessionPath(scope: IngestScope, sessionId: string): string {
  return `${resourceRoot(scope)}/upload-sessions/${encodeURIComponent(sessionId)}`;
}

function contractMismatch(message: string): Error {
  return createDomainError({
    code: "CONTRACT_MISMATCH",
    message,
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId: null,
    retryable: false,
    httpStatus: null,
  });
}

function assertSessionIdentity(
  session: FormalUploadSession,
  scope: IngestScope,
  sessionId: string,
): void {
  if (
    !session.session_id ||
    session.project_id !== scope.projectId ||
    session.region_code !== scope.regionCode ||
    (session.session_id !== undefined && session.session_id !== sessionId)
  ) {
    throw contractMismatch("上传会话响应与当前项目、区域或会话不匹配。");
  }
}

function assertManifestIdentity(
  manifest: FormalManifestPreflight,
  session: FormalUploadSession,
): void {
  if (
    manifest.identifiers.data_package_id !== session.data_package_id ||
    manifest.manifest_fingerprint !== session.manifest_fingerprint ||
    manifest.manifest.project_id !== session.project_id ||
    manifest.manifest.rollout_id !== session.rollout_id
  ) {
    throw contractMismatch("Manifest 响应与当前上传会话不匹配。");
  }
}

function assertQualityIdentity(
  quality: FormalQcReport,
  session: FormalUploadSession,
): void {
  if (quality.rollout_id !== session.rollout_id) {
    throw contractMismatch("自动质检响应与当前 rollout 不匹配。");
  }
}

export function getFormalUploadSessionDetail(
  scope: IngestScope,
  sessionId: string,
  signal?: AbortSignal,
): Promise<FormalUploadSession> {
  return request<FormalUploadSession>({
    method: "GET",
    path: sessionPath(scope, sessionId),
    scope,
    signal,
  });
}

export function getFormalUploadManifestDetail(
  scope: IngestScope,
  sessionId: string,
  signal?: AbortSignal,
): Promise<FormalManifestPreflight> {
  return request<FormalManifestPreflight>({
    method: "GET",
    path: `${sessionPath(scope, sessionId)}/manifest`,
    scope,
    signal,
  });
}

export function getFormalRolloutQuality(
  scope: IngestScope,
  rolloutId: string,
  signal?: AbortSignal,
): Promise<FormalQcReport> {
  return request<FormalQcReport>({
    method: "GET",
    path: `${resourceRoot(scope)}/rollouts/${encodeURIComponent(rolloutId)}/quality`,
    scope,
    signal,
  });
}

export function getFormalUploadRawMedia(
  scope: IngestScope,
  sessionId: string,
  signal?: AbortSignal,
): Promise<FormalRawMediaSource> {
  return request<FormalRawMediaSource>({
    method: "GET",
    path: `${sessionPath(scope, sessionId)}/raw-media`,
    scope,
    signal,
  });
}

export async function getFormalUploadProcessingStatus(
  scope: IngestScope,
  session: FormalUploadSession,
  signal?: AbortSignal,
): Promise<FormalUploadProcessingStatus> {
  const locator = session.workflow;
  if (!locator) {
    throw contractMismatch("已提交上传缺少持久化处理工作流定位信息。");
  }
  if (!session.session_id) {
    throw contractMismatch("上传处理状态缺少持久化会话 ID。");
  }
  const endpoint = `${sessionPath(scope, session.session_id)}/processing`;
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scope,
    signal,
    cache: "no-store",
  });
  const processing = parseWire(uploadProcessingStatusSchema, raw, { endpoint });
  if (
    processing.session_id !== session.session_id ||
    processing.workflow_id !== locator.workflow_id ||
    processing.rollout_id !== session.rollout_id ||
    (processing.preview !== null &&
      (processing.preview.project_id !== session.project_id ||
        processing.preview.rollout_id !== session.rollout_id))
  ) {
    throw contractMismatch("上传处理工作流与当前上传会话不匹配。");
  }
  return processing;
}

export function resolveFormalUploadPreviewTarget(
  session: FormalUploadSession,
  processing: FormalUploadProcessingStatus,
): FormalUploadPreviewTarget | null {
  const preview = processing.preview;
  if (!preview) return null;
  return {
    datasetId: preview.dataset_id,
    datasetVersion: preview.dataset_version,
    lanceVersion: preview.lance_version,
    annotationTaskId: preview.annotation_task_id,
    frequencyHz: preview.frequency_hz,
    startStep: preview.start_step,
    endStep: preview.end_step,
  };
}

export function resolveFormalUploadViewerTarget(
  session: FormalUploadSession,
  processing: FormalUploadProcessingStatus,
): FormalUploadViewerTarget | null {
  const viewer = processing.viewer;
  const preview = processing.preview;
  if (!viewer || !preview) return null;
  if (
    preview.project_id !== session.project_id ||
    preview.rollout_id !== session.rollout_id ||
    viewer.dataset_id !== preview.dataset_id ||
    viewer.version_id !== `version_lance_${preview.dataset_version}`
  ) {
    throw contractMismatch("上传数据视图与当前上传会话不匹配。");
  }
  return {
    datasetId: viewer.dataset_id as DatasetId,
    versionId: viewer.version_id as DatasetVersionId,
    episodeId: viewer.episode_id as EpisodeId,
    revisionId: viewer.revision_id as EpisodeRevisionId,
  };
}

/**
 * Starts the independent session and Manifest reads together. The QC read can
 * only start after the server-provided rollout_id is known, then runs alongside
 * the still-pending Manifest read.
 */
export async function loadFormalUploadDetail(
  scope: IngestScope,
  sessionId: string,
  signal?: AbortSignal,
): Promise<FormalUploadDetail> {
  const manifestPromise = getFormalUploadManifestDetail(
    scope,
    sessionId,
    signal,
  );
  const session = await getFormalUploadSessionDetail(scope, sessionId, signal);
  assertSessionIdentity(session, scope, sessionId);

  const qualityPromise = getFormalRolloutQuality(
    scope,
    session.rollout_id,
    signal,
  );
  const [manifest, quality] = await Promise.all([
    manifestPromise,
    qualityPromise,
  ]);
  assertManifestIdentity(manifest, session);
  assertQualityIdentity(quality, session);
  return { session, manifest, quality };
}
