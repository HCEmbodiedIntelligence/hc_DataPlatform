import type { IngestScope } from "../../entities/data-source";
import { request } from "../../shared/api/http-client";
import type {
  components,
  operations,
} from "../../shared/api/generated/platform";

type RuntimeUploadManifest = components["schemas"]["RolloutManifestV1"];
export type UploadManifest = Omit<RuntimeUploadManifest, "processing_mode"> & {
  /** Omitted by legacy direct-episode recorders; the API defaults it server-side. */
  processing_mode?: RuntimeUploadManifest["processing_mode"];
};
type RuntimeManifestPreflight =
  components["schemas"]["ManifestPreflightResultV1"];
export type ManifestPreflight = Omit<RuntimeManifestPreflight, "manifest"> & {
  manifest: UploadManifest;
};
export type FormalUploadSession = components["schemas"]["UploadSession"];
export type FormalUploadSessionList =
  components["schemas"]["UploadSessionListV1"];
export type UploadSessionGrant = components["schemas"]["UploadSessionGrant"];
export type UploadPart = components["schemas"]["UploadPart"];
export type PartAuthorization = components["schemas"]["PartAuthorization"];
export type UploadStatus = components["schemas"]["UploadStatus"];
export type RawObjectCommitted = components["schemas"]["RawObjectCommittedV1"];

type CreateUploadBody =
  operations["createUploadSession"]["requestBody"]["content"]["application/json"];
type CompleteUploadBody =
  operations["completeUploadSession"]["requestBody"]["content"]["application/json"];
type ResumeUploadBody =
  operations["resumeUploadSession"]["requestBody"]["content"]["application/json"];
type RetryUploadBody =
  operations["retryFailedUploadParts"]["requestBody"]["content"]["application/json"];

function resourceRoot(scope: IngestScope): string {
  return `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}`;
}

function sessionPath(scope: IngestScope, sessionId: string): string {
  return `${resourceRoot(scope)}/upload-sessions/${encodeURIComponent(sessionId)}`;
}

/** Thin transport bindings. All request and response shapes come from runtime-generated platform.ts. */
export function preflightUploadManifest(
  scope: IngestScope,
  manifest: UploadManifest,
  signal?: AbortSignal,
): Promise<ManifestPreflight> {
  return request<ManifestPreflight>({
    method: "POST",
    path: `${resourceRoot(scope)}/upload-manifests:preflight`,
    scope,
    body: manifest,
    signal,
  });
}

export function listFormalUploadSessions(
  scope: IngestScope,
  query: operations["listUploadSessions"]["parameters"]["query"] = {},
  signal?: AbortSignal,
): Promise<FormalUploadSessionList> {
  return request<FormalUploadSessionList>({
    method: "GET",
    path: `${resourceRoot(scope)}/upload-sessions`,
    scope,
    query,
    signal,
  });
}

export function getFormalUploadSession(
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

export function getFormalUploadManifest(
  scope: IngestScope,
  sessionId: string,
  signal?: AbortSignal,
): Promise<ManifestPreflight> {
  return request<ManifestPreflight>({
    method: "GET",
    path: `${sessionPath(scope, sessionId)}/manifest`,
    scope,
    signal,
  });
}

export function listFormalUploadParts(
  scope: IngestScope,
  sessionId: string,
  signal?: AbortSignal,
): Promise<UploadPart[]> {
  return request<UploadPart[]>({
    method: "GET",
    path: `${sessionPath(scope, sessionId)}/parts`,
    scope,
    signal,
  });
}

export function createFormalUploadSession(
  scope: IngestScope,
  body: CreateUploadBody,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<UploadSessionGrant> {
  return request<UploadSessionGrant>({
    method: "POST",
    path: `${resourceRoot(scope)}/upload-sessions`,
    scope,
    body,
    idempotencyKey,
    cache: "no-store",
    signal,
  });
}

export function renewFormalUploadParts(
  scope: IngestScope,
  sessionId: string,
  partNumbers: readonly number[],
  signal?: AbortSignal,
): Promise<PartAuthorization[]> {
  return request<PartAuthorization[]>({
    method: "POST",
    path: `${sessionPath(scope, sessionId)}:renew`,
    scope,
    body: { part_numbers: [...partNumbers] },
    cache: "no-store",
    signal,
  });
}

export function pauseFormalUpload(
  scope: IngestScope,
  sessionId: string,
): Promise<FormalUploadSession> {
  return request<FormalUploadSession>({
    method: "POST",
    path: `${sessionPath(scope, sessionId)}:pause`,
    scope,
  });
}

export function resumeFormalUpload(
  scope: IngestScope,
  sessionId: string,
  body: ResumeUploadBody,
): Promise<UploadSessionGrant> {
  return request<UploadSessionGrant>({
    method: "POST",
    path: `${sessionPath(scope, sessionId)}:resume`,
    scope,
    body,
    cache: "no-store",
  });
}

export function retryFormalUploadParts(
  scope: IngestScope,
  sessionId: string,
  body: RetryUploadBody,
): Promise<PartAuthorization[]> {
  return request<PartAuthorization[]>({
    method: "POST",
    path: `${sessionPath(scope, sessionId)}:retry-parts`,
    scope,
    body,
    cache: "no-store",
  });
}

export function completeFormalUpload(
  scope: IngestScope,
  sessionId: string,
  body: CompleteUploadBody,
): Promise<FormalUploadSession> {
  return request<FormalUploadSession>({
    method: "POST",
    path: `${sessionPath(scope, sessionId)}:complete`,
    scope,
    body,
  });
}

export function commitFormalManifest(
  scope: IngestScope,
  sessionId: string,
  manifest: UploadManifest,
): Promise<RawObjectCommitted> {
  return request<RawObjectCommitted>({
    method: "POST",
    path: `${sessionPath(scope, sessionId)}:commit-manifest`,
    scope,
    body: manifest,
  });
}

export function cancelFormalUpload(
  scope: IngestScope,
  sessionId: string,
): Promise<FormalUploadSession> {
  return request<FormalUploadSession>({
    method: "POST",
    path: `${sessionPath(scope, sessionId)}:cancel`,
    scope,
  });
}
