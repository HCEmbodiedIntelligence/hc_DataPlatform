import type { IngestScope } from "../../entities/data-source";
import type { components } from "../../shared/api/generated/platform";
import { createDomainError } from "../../shared/api/domain-error";
import { request } from "../../shared/api/http-client";

export type FormalUploadSession = components["schemas"]["UploadSession"];
export type FormalManifestPreflight =
  components["schemas"]["ManifestPreflightResultV1"];
export type FormalQcReport = components["schemas"]["QcReportV1"];

export interface FormalUploadDetail {
  readonly session: FormalUploadSession;
  readonly manifest: FormalManifestPreflight;
  readonly quality: FormalQcReport;
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
