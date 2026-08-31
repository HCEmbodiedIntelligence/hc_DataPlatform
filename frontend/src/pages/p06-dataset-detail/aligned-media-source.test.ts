import { afterEach, describe, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { createDatasetAlignedMediaSource } from "./aligned-media-source";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));

const requestMock = vi.mocked(request);
const scope = {
  organizationId: "org-p06",
  projectId: "project-p06",
  regionCode: "region-p06",
};
const binding = {
  rollout_id: "rollout-p06",
  dataset_version: 7,
  artifact_id: "artifact-p06",
  camera_id: "front-rgb",
  fps: 30,
  start_step: 0,
  end_step: 90,
} as const;

function authorization(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: "aligned-media-authorization/v1",
    artifact_id: binding.artifact_id,
    artifact_key: "a".repeat(64),
    project_id: scope.projectId,
    dataset_id: "dataset_p06fixture",
    rollout_id: binding.rollout_id,
    dataset_version: binding.dataset_version,
    camera_id: binding.camera_id,
    media_url: "/objects/front.mp4?signature=opaque",
    content_type: "video/mp4",
    expires_at: "2026-08-31T09:00:00Z",
    fps: 30,
    frame_count: 90,
    duration_seconds: 3,
    width: 1280,
    height: 720,
    timeline: {
      fps: 30,
      frame_count: 90,
      first_step: 0,
      pts_time_base_numerator: 1,
      pts_time_base_denominator: 30,
      start_timestamp_ns: "0",
    },
    alignment_version: "causal-30hz-v1",
    profile_id: "canonical-h264-crf20-v1",
    profile_version: "1",
    ...overrides,
  };
}

afterEach(() => {
  vi.clearAllMocks();
  resetRuntimeConfigForTests();
});

describe("P06 aligned media source", () => {
  it("authorizes a fixed camera once and returns its direct MP4 URL", async () => {
    configureRuntime({
      apiBaseUrl: "/api/v1",
      sseBaseUrl: "/api/v1/events",
      buildVersion: "test",
      releaseEnv: "test",
    });
    requestMock.mockResolvedValue(authorization() as never);
    const source = createDatasetAlignedMediaSource({
      scope,
      datasetId: "dataset_p06fixture",
      binding,
      modality: "rgb",
    });

    await expect(
      source.authorize(new AbortController().signal),
    ).resolves.toEqual({
      url: "http://localhost/objects/front.mp4?signature=opaque",
      expiresAt: "2026-08-31T09:00:00Z",
      kind: "rgb-video",
    });
    expect(requestMock).toHaveBeenCalledTimes(1);
  });
});
