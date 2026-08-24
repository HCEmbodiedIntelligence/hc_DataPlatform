import { afterEach, describe, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { createDatasetPreviewMediaSource } from "./preview-media-source";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));

const requestMock = vi.mocked(request);
const scope = {
  organizationId: "org-p06",
  projectId: "project-p06",
  regionCode: "region-p06",
};
const binding = {
  rollout_id: "rollout-p06",
  lance_version: 7,
  annotation_revision: 2,
  camera_id: "front-rgb",
  frequency_hz: 30,
  start_step: 0,
  end_step: 90,
} as const;

function descriptor(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    session_id: "preview-session-p06",
    cache_key: "cache-p06",
    project_id: scope.projectId,
    dataset_id: "dataset_p06fixture",
    rollout_id: binding.rollout_id,
    lance_version: String(binding.lance_version),
    annotation_revision: binding.annotation_revision,
    camera_id: binding.camera_id,
    view_mode: "original",
    encoding_profile: {
      name: "h264-cmaf-preview-v1",
      width: 1280,
      height: 720,
      video_codec: "h264",
      pixel_format: "yuv420p",
      video_bitrate_kbps: 2000,
      segment_duration_seconds: 2,
      preset: "veryfast",
    },
    playlist_url:
      "/api/v1/previews/sessions/preview-session-p06/media/index.m3u8?expires=1&sig=a",
    media_type: "application/vnd.apple.mpegurl",
    frame_count: 90,
    placeholder_count: 0,
    placeholders: [],
    duration_seconds: 3,
    timeline: { frequency_hz: 30, segments: [] },
    cache_expires_at: "2026-08-20T09:15:00Z",
    signed_url_expires_at: "2026-08-20T09:00:00Z",
    ...overrides,
  };
}

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  resetRuntimeConfigForTests();
});

describe("P06 scoped preview media source", () => {
  it("lazily asks the real preview API for a fixed camera binding and resolves its signed HLS URL", async () => {
    configureRuntime({
      apiBaseUrl: "/api/v1",
      sseBaseUrl: "/api/v1/events",
      buildVersion: "test",
      releaseEnv: "test",
    });
    requestMock.mockResolvedValue(descriptor() as never);
    const source = createDatasetPreviewMediaSource({
      scope,
      datasetId: "dataset_p06fixture",
      binding,
      modality: "rgb",
    });

    await expect(
      source.authorize(new AbortController().signal),
    ).resolves.toEqual({
      url: "http://localhost/api/v1/previews/sessions/preview-session-p06/media/index.m3u8?expires=1&sig=a",
      expiresAt: "2026-08-20T09:00:00Z",
      kind: "rgb-video",
    });
    expect(requestMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "POST",
        path: "/previews/sessions",
        scope,
        cache: "no-store",
        body: {
          project_id: scope.projectId,
          dataset_id: "dataset_p06fixture",
          rollout_id: binding.rollout_id,
          lance_version: "7",
          annotation_revision: binding.annotation_revision,
          camera_id: binding.camera_id,
          view_mode: "original",
          frequency_hz: 30,
          start_step: 0,
          end_step: 90,
        },
      }),
    );
  });

  it("fails closed when the descriptor is malformed or crosses its immutable binding", async () => {
    configureRuntime({
      apiBaseUrl: "/api/v1",
      sseBaseUrl: "/api/v1/events",
      buildVersion: "test",
      releaseEnv: "test",
    });
    const source = createDatasetPreviewMediaSource({
      scope,
      datasetId: "dataset_p06fixture",
      binding,
      modality: "depth",
    });
    requestMock.mockResolvedValue(
      descriptor({ rollout_id: "other-rollout" }) as never,
    );
    await expect(
      source.authorize(new AbortController().signal),
    ).rejects.toThrow("P06 预览授权与当前固定采集条目不一致。");
    requestMock.mockResolvedValue(
      descriptor({ cache_key: undefined }) as never,
    );
    await expect(
      source.refresh(new AbortController().signal),
    ).rejects.toMatchObject({
      code: "CONTRACT_MISMATCH",
    });
  });

  it("requests VP9 CMAF when this browser cannot decode H.264 through MediaSource", async () => {
    configureRuntime({
      apiBaseUrl: "/api/v1",
      sseBaseUrl: "/api/v1/events",
      buildVersion: "test",
      releaseEnv: "test",
    });
    vi.stubGlobal("MediaSource", {
      isTypeSupported: vi.fn((value: string) => value.includes("vp09")),
    });
    requestMock.mockResolvedValue(
      descriptor({
        encoding_profile: {
          name: "vp9-cmaf-preview-v1",
          width: 1280,
          height: 720,
          video_codec: "vp9",
          pixel_format: "yuv420p",
          video_bitrate_kbps: 2000,
          segment_duration_seconds: 2,
          preset: "veryfast",
        },
      }) as never,
    );
    const source = createDatasetPreviewMediaSource({
      scope,
      datasetId: "dataset_p06fixture",
      binding,
      modality: "rgb",
    });

    await source.authorize(new AbortController().signal);

    expect(requestMock).toHaveBeenCalledWith(
      expect.objectContaining({
        body: expect.objectContaining({
          encoding_profile: {
            name: "vp9-cmaf-preview-v1",
            video_codec: "vp9",
          },
        }),
      }),
    );
  });
});
