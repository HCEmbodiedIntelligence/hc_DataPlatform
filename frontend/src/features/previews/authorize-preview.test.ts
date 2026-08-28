import { afterEach, describe, expect, it, vi } from "vitest";
import type { Scope } from "../../entities/scope";
import { request } from "../../shared/api/http-client";
import { authorizePreview, type PreviewRequest } from "./authorize-preview";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));

const requestMock = vi.mocked(request);
const scope: Scope = {
  organizationId: "organization-1",
  projectId: "project-1",
  regionCode: "cn-test",
};
const body: PreviewRequest = {
  project_id: "project-1",
  dataset_id: "dataset-1",
  rollout_id: "rollout-1",
  lance_version: "1",
  camera_id: "front",
  profile_id: "annotation-h264-720p-v1",
  annotation_revision: 0,
  frequency_hz: 30,
  view_mode: "original",
};
const artifactKey = "a".repeat(64);

function pending() {
  return {
    schema_version: 1,
    status: "QUEUED",
    artifact_key: artifactKey,
    job_id: "job-1",
    status_url: "/previews/jobs/job-1?project_id=project-1",
    retry_after_seconds: 1,
  };
}

function descriptor() {
  return {
    schema_version: 1,
    session_id: "session-1",
    artifact_key: artifactKey,
    project_id: "project-1",
    dataset_id: "dataset-1",
    rollout_id: "rollout-1",
    lance_version: "1",
    annotation_revision: 0,
    camera_id: "front",
    view_mode: "original",
    profile_id: "annotation-h264-720p-v1",
    playlist_url: "/api/v1/previews/sessions/session-1/media/index.m3u8",
    signed_url_expires_at: "2026-08-28T10:30:00Z",
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("durable preview authorization", () => {
  it("shows PREPARING, polls sequentially, and reauthorizes after SUCCEEDED", async () => {
    vi.useFakeTimers();
    requestMock
      .mockResolvedValueOnce(pending() as never)
      .mockResolvedValueOnce({
        schema_version: 1,
        status: "SUCCEEDED",
        artifact_key: artifactKey,
        job_id: "job-1",
        progress: 100,
      } as never)
      .mockResolvedValueOnce(descriptor() as never);
    const statuses: string[] = [];

    const result = authorizePreview(
      scope,
      body,
      new AbortController().signal,
      (status) => statuses.push(status),
    );
    await vi.advanceTimersByTimeAsync(1_000);

    await expect(result).resolves.toMatchObject({ session_id: "session-1" });
    expect(statuses).toEqual(["preparing", "ready"]);
    expect(requestMock.mock.calls.map(([options]) => options.method)).toEqual([
      "POST",
      "GET",
      "POST",
    ]);
    expect(requestMock.mock.calls[1]?.[0].path).toBe(
      "/previews/jobs/job-1?project_id=project-1",
    );
  });

  it("cancels its timer and never polls after the viewer unmounts", async () => {
    vi.useFakeTimers();
    requestMock.mockResolvedValueOnce(pending() as never);
    const controller = new AbortController();
    const result = authorizePreview(scope, body, controller.signal);
    await Promise.resolve();

    controller.abort();

    await expect(result).rejects.toMatchObject({ name: "AbortError" });
    expect(requestMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("surfaces a durable failed job and marks the panel failed", async () => {
    vi.useFakeTimers();
    requestMock
      .mockResolvedValueOnce(pending() as never)
      .mockResolvedValueOnce({
        schema_version: 1,
        status: "FAILED",
        artifact_key: artifactKey,
        job_id: "job-1",
        progress: 40,
        error_code: "FFMPEG_EXIT_1",
      } as never);
    const statuses: string[] = [];
    const result = authorizePreview(
      scope,
      body,
      new AbortController().signal,
      (status) => statuses.push(status),
    );
    const rejection = expect(result).rejects.toThrow("FFMPEG_EXIT_1");
    await vi.advanceTimersByTimeAsync(1_000);

    await rejection;
    expect(statuses).toEqual(["preparing", "failed"]);
  });
});
