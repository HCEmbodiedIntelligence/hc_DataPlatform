import { afterEach, describe, expect, it, vi } from "vitest";
import type { Scope } from "../../entities/scope";
import { request } from "../../shared/api/http-client";
import { authorizeAlignedMedia } from "./authorize-aligned-media";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));

const requestMock = vi.mocked(request);
const scope: Scope = {
  organizationId: "organization-1",
  projectId: "project-1",
  regionCode: "cn-test",
};
const selector = {
  project_id: "project-1",
  dataset_id: "dataset-1",
  rollout_id: "rollout-1",
  dataset_version: 7,
  camera_id: "front",
};

function authorization() {
  return {
    schema_version: "aligned-media-authorization/v1",
    artifact_id: "artifact-1",
    artifact_key: "a".repeat(64),
    ...selector,
    media_url: "https://media.invalid/camera.mp4?signature=opaque",
    content_type: "video/mp4",
    expires_at: "2026-08-31T10:30:00Z",
    fps: 30,
    frame_count: 1800,
    duration_seconds: 60,
    width: 1920,
    height: 1080,
    timeline: {
      fps: 30,
      frame_count: 1800,
      first_step: 0,
      pts_time_base_numerator: 1,
      pts_time_base_denominator: 30,
      start_timestamp_ns: "0",
    },
    alignment_version: "causal-30hz-v1",
    profile_id: "canonical-h264-crf20-v1",
    profile_version: "1",
  };
}

afterEach(() => vi.clearAllMocks());

describe("aligned media authorization", () => {
  it("performs exactly one stateless authorization request and never polls", async () => {
    requestMock.mockResolvedValue(authorization() as never);
    const statuses: string[] = [];

    await expect(
      authorizeAlignedMedia(
        scope,
        selector,
        new AbortController().signal,
        (status) => statuses.push(status),
      ),
    ).resolves.toMatchObject({ media_url: expect.stringContaining(".mp4") });

    expect(statuses).toEqual(["ready"]);
    expect(requestMock).toHaveBeenCalledTimes(1);
    expect(requestMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "POST",
        path: "/aligned-media/authorize",
        body: selector,
        cache: "no-store",
      }),
    );
  });
});
