// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "../../../shared/api/http-client";
import { useShellStore } from "../../../shared/scope/shell-store";
import { originalEpisodeWire } from "../../../features/ingest/browser/original-source-api";
import { OriginalEpisodePlayer } from "../../../features/ingest/browser/OriginalSourceBrowser";

vi.mock("../../../shared/api/http-client", () => ({
  request: vi.fn(async ({ query }: { query: { path: string } }) => ({
    path: query.path,
    url: `https://original.invalid/${query.path}`,
    expires_at: "2099-01-01T00:00:00Z",
  })),
}));
const scope = {
  organizationId: "org",
  projectId: "project",
  regionCode: "local",
};
beforeEach(() => {
  useShellStore
    .getState()
    .setSession(
      { actorId: "reader", displayName: "Reader", roleIds: [] },
      "session",
    );
  useShellStore.getState().setScope(scope);
  useShellStore
    .getState()
    .setSessionScopes(
      [
        {
          ...scope,
          regionCodes: ["local"],
          projectWide: false,
          capabilities: [],
        },
      ],
      1,
    );
});
afterEach(() => {
  useShellStore.getState().clearSensitiveState();
  vi.clearAllMocks();
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("plays four original shard segments at their offsets through StrictMode without repeated seeks", async () => {
  let frame: FrameRequestCallback | undefined;
  vi.stubGlobal("IntersectionObserver", undefined);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frame = callback;
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  const playing = new WeakSet<HTMLMediaElement>();
  vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(function (
    this: HTMLMediaElement,
  ) {
    playing.add(this);
    return Promise.resolve();
  });
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(function (
    this: HTMLMediaElement,
  ) {
    playing.delete(this);
  });
  vi.spyOn(HTMLMediaElement.prototype, "paused", "get").mockImplementation(
    function (this: HTMLMediaElement) {
      return !playing.has(this);
    },
  );
  vi.spyOn(HTMLMediaElement.prototype, "readyState", "get").mockReturnValue(4);
  vi.spyOn(HTMLMediaElement.prototype, "duration", "get").mockReturnValue(120);
  const seek = vi.spyOn(HTMLMediaElement.prototype, "currentTime", "set");
  const { container } = render(
    <StrictMode>
      <OriginalEpisodePlayer
        scope={{
          organizationId: "org",
          projectId: "project",
          regionCode: "local",
        }}
        importId="raw"
        episode={{
          episode_index: 1,
          frame_count: 60,
          fps: 30,
          duration_ns: "2000000000",
          videos: Array.from({ length: 4 }, (_, i) => ({
            camera_id: `camera-${i}`,
            media_kind: "RGB",
            preview_kind: "ORIGINAL_VIDEO",
            preview_reason: null,
            path: `camera-${i}.mp4`,
            start_seconds: 31.933,
            end_seconds: 33.933,
          })),
        }}
      />
    </StrictMode>,
  );
  const videos = Array.from(container.querySelectorAll("video"));
  expect(videos).toHaveLength(4);
  await waitFor(() =>
    expect(
      videos.every((video) => video.src.includes("original.invalid")),
    ).toBe(true),
  );
  videos.forEach((video) => fireEvent.loadedMetadata(video));
  expect(videos.map((video) => video.currentTime)).toEqual([
    31.933, 31.933, 31.933, 31.933,
  ]);
  fireEvent.click(screen.getByRole("button", { name: "播放" }));
  await waitFor(() =>
    expect(videos.every((video) => !video.paused)).toBe(true),
  );
  videos.forEach((video) => {
    video.currentTime = 32.133;
  });
  seek.mockClear();
  act(() => frame?.(16));
  videos.forEach((video) => fireEvent.canPlay(video));
  act(() => frame?.(32));
  expect(seek).not.toHaveBeenCalled();
});

it("keeps raw depth and undeclared media downloadable without attempting playback authorization", () => {
  const episode = {
    episode_index: 0,
    frame_count: 6,
    fps: 25,
    duration_ns: "240000000",
    videos: [
      {
        camera_id: "camera-color-named",
        path: "looks-like-rgb.mp4",
        start_seconds: 0,
        end_seconds: 0.24,
        media_kind: "DEPTH" as const,
        preview_kind: "DOWNLOAD_ONLY" as const,
        preview_reason: "RAW_DEPTH_REQUIRES_COLORIZATION" as const,
      },
      {
        camera_id: "camera-depth-named",
        path: "looks-like-depth.mp4",
        start_seconds: 0,
        end_seconds: 0.24,
        media_kind: "UNKNOWN" as const,
        preview_kind: "DOWNLOAD_ONLY" as const,
        preview_reason: "MEDIA_KIND_UNDECLARED" as const,
      },
    ],
  };
  const { container } = render(
    <OriginalEpisodePlayer scope={scope} importId="raw" episode={episode} />,
  );
  expect(container.querySelectorAll("video")).toHaveLength(0);
  expect(screen.getByText(/原始深度字节完整保留/u)).toBeVisible();
  expect(screen.getByText(/源文件未声明可播放的媒体类型/u)).toBeVisible();
  expect(request).not.toHaveBeenCalled();
  expect(() =>
    originalEpisodeWire.parse({
      ...episode,
      videos: [{ ...episode.videos[0], preview_kind: "ORIGINAL_VIDEO" }],
    }),
  ).toThrow();
});
