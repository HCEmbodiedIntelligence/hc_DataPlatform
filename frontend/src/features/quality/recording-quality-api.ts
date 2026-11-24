import type { QueryClient } from "@tanstack/react-query";
import type { IngestScope } from "../../entities/data-source";
import type { components } from "../../shared/api/generated/platform";
import { request } from "../../shared/api/http-client";

type Envelope = components["schemas"]["ContinuousRecordingEnvelope"];

export function recordingReportDigest(reportId?: string) {
  return reportId?.match(/^recording_qc_([0-9a-f]{64})$/)?.[1];
}

function recordingPath(scope: IngestScope, id: string) {
  return `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/continuous-recordings/${encodeURIComponent(id)}`;
}

export function readQualityRecording(
  scope: IngestScope,
  id: string,
  signal: AbortSignal,
) {
  return request<Envelope>({
    method: "GET",
    scope,
    path: recordingPath(scope, id),
    signal,
    cache: "no-store",
  });
}

export function discardQualityRecording(
  scope: IngestScope,
  id: string,
  digest: string,
) {
  return request<Envelope>({
    method: "POST",
    scope,
    path: recordingPath(scope, id) + "/quality:discard",
    body: { report_sha256: digest },
  });
}

export function retryQualityRecording(scope: IngestScope, id: string) {
  return request<Envelope>({
    method: "POST",
    scope,
    path: recordingPath(scope, id) + "/quality:retry",
    body: { request_id: crypto.randomUUID() },
  });
}

export function refreshRecordingQuality(client: QueryClient) {
  for (const key of [
    "continuous-recording",
    "continuous-recordings",
    "qc-recording-source",
    "dashboard",
    "failed-mcap",
    "quality-problems",
    "capture-progress",
    "collection-task-progress",
    "auto-quality-processing",
  ])
    void client.invalidateQueries({ queryKey: [key] });
}
