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
import { afterEach, expect, it, vi } from "vitest";
import { OriginalEpisodePlayer } from "./OriginalSourceBrowser";

vi.mock("../../../shared/api/http-client", () => ({
  request: vi.fn(async ({ query }: { query: { path: string } }) => ({
    url: `https://original.invalid/${query.path}`,
    expires_at: "2099-01-01T00:00:00Z",
  })),
}));
afterEach(() => {
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
