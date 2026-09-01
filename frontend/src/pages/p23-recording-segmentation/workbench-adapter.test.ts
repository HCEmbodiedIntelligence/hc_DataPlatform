import { describe, expect, it, vi } from "vitest";
import type { RecordingVideoSource } from "./api";
import {
  buildEpisodeTimelineTracks,
  buildRecordingCameraStreams,
} from "./workbench-adapter";

const source: RecordingVideoSource = {
  schema_version: "recording-video-source/v1",
  recording_id: "recording-1",
  asset_id: "11111111-1111-4111-8111-111111111111",
  camera_id: "front",
  media_type: "video/mp4",
  source_url: "https://objects.example.test/front.mp4?signature=first",
  duration_ns: "60000000000",
  fps: 30,
  codec: "h264",
  expires_at: "2026-09-01T08:00:00Z",
  byte_range_supported: true,
  materialization: "ORIGINAL_RECORDING",
};

describe("recording segmentation workbench adapter", () => {
  it("adapts original recording videos to the shared viewer contract", async () => {
    const refreshSource = vi.fn(async () => ({
      ...source,
      source_url: "https://objects.example.test/front.mp4?signature=refreshed",
    }));
    const [stream] = buildRecordingCameraStreams({
      sources: [source],
      refreshSource,
    });

    expect(stream).toMatchObject({
      id: source.asset_id,
      displayName: "front",
      modality: "rgb",
      startNs: "0",
      endNs: source.duration_ns,
      availability: "ready",
    });
    await expect(
      stream?.mediaSource?.refresh(new AbortController().signal),
    ).resolves.toMatchObject({ url: expect.stringContaining("refreshed") });
    expect(refreshSource).toHaveBeenCalledWith(
      source.asset_id,
      expect.any(AbortSignal),
    );
  });

  it("renders Episode slices as shared timeline segments", () => {
    expect(
      buildEpisodeTimelineTracks([
        {
          episodeId: "episode_0001",
          startNs: 1_000_000_000,
          endNs: 3_000_000_000,
          title: "抓取",
          taskLabel: "pick",
          notes: "",
        },
      ]),
    ).toEqual([
      {
        id: "episodes",
        label: "Episode",
        segments: [
          {
            id: "episode_0001",
            label: "抓取",
            startNs: "1000000000",
            endNs: "3000000000",
            activatePlayback: true,
            tone: "action",
          },
        ],
      },
    ]);
  });
});
