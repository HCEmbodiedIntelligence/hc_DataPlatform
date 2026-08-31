import type { components } from "../../shared/api/generated/platform";
import { request } from "../../shared/api/http-client";
import type { Scope } from "../../entities/scope";

export type ContinuousRecording = components["schemas"]["ContinuousRecording"];
export type ContinuousRecordingEnvelope =
  components["schemas"]["ContinuousRecordingEnvelope"];
export type ContinuousRecordingPage =
  components["schemas"]["ContinuousRecordingPage"];
export type RecordingVideoSourceEnvelope =
  components["schemas"]["RecordingVideoSourceEnvelope"];
export type SaveSliceDraftCommand =
  components["schemas"]["SaveSliceDraftCommand"];
export type SliceRevisionEnvelope =
  components["schemas"]["SliceRevisionEnvelope"];
export type EpisodeProcessing = components["schemas"]["EpisodeProcessing"];
export type EpisodeProcessingPage =
  components["schemas"]["EpisodeProcessingPage"];

export interface RecordingScope extends Scope {
  readonly projectId: string;
  readonly regionCode: string;
}

export interface RecordingGateway {
  list(
    scope: RecordingScope,
    signal?: AbortSignal,
  ): Promise<ContinuousRecordingPage>;
  detail(
    scope: RecordingScope,
    recordingId: string,
    signal?: AbortSignal,
  ): Promise<ContinuousRecordingEnvelope>;
  videoSources(
    scope: RecordingScope,
    recordingId: string,
    signal?: AbortSignal,
  ): Promise<RecordingVideoSourceEnvelope>;
  processing(
    scope: RecordingScope,
    recordingId: string,
    signal?: AbortSignal,
  ): Promise<EpisodeProcessingPage>;
  saveDraft(
    scope: RecordingScope,
    recordingId: string,
    command: SaveSliceDraftCommand,
    etag: string,
  ): Promise<SliceRevisionEnvelope>;
  finalize(
    scope: RecordingScope,
    recordingId: string,
    expectedDraftRevision: number,
    etag: string,
  ): Promise<SliceRevisionEnvelope>;
}

function root(scope: RecordingScope): string {
  return `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/continuous-recordings`;
}

export const recordingGateway: RecordingGateway = {
  list: (scope, signal) =>
    request<ContinuousRecordingPage>({
      method: "GET",
      path: root(scope),
      scope,
      ...(signal ? { signal } : {}),
    }),

  detail: (scope, recordingId, signal) =>
    request<ContinuousRecordingEnvelope>({
      method: "GET",
      path: `${root(scope)}/${encodeURIComponent(recordingId)}`,
      scope,
      cache: "no-store",
      ...(signal ? { signal } : {}),
    }),

  videoSources: (scope, recordingId, signal) =>
    request<RecordingVideoSourceEnvelope>({
      method: "GET",
      path: `${root(scope)}/${encodeURIComponent(recordingId)}/video-sources`,
      scope,
      cache: "no-store",
      ...(signal ? { signal } : {}),
    }),

  processing: (scope, recordingId, signal) =>
    request<EpisodeProcessingPage>({
      method: "GET",
      path: `${root(scope)}/${encodeURIComponent(recordingId)}/episodes`,
      scope,
      cache: "no-store",
      ...(signal ? { signal } : {}),
    }),

  saveDraft: (scope, recordingId, command, etag) =>
    request<SliceRevisionEnvelope>({
      method: "PUT",
      path: `${root(scope)}/${encodeURIComponent(recordingId)}/slice-draft`,
      scope,
      ifMatch: etag,
      body: command,
    }),

  finalize: (scope, recordingId, expectedDraftRevision, etag) =>
    request<SliceRevisionEnvelope>({
      method: "POST",
      path: `${root(scope)}/${encodeURIComponent(recordingId)}/slice-draft:finalize`,
      scope,
      ifMatch: etag,
      body: { expected_draft_revision: expectedDraftRevision },
    }),
};
