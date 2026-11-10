// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { attachAuthorizedMedia } from "./authorized-media";

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(
    () => undefined,
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("attachAuthorizedMedia", () => {
  it.each([
    "episodes/capture/videos/head/file-000.mp4",
    "episode-previews/capture/depth/preview.mp4",
  ])("plays local signed media through the gateway: %s", async (key) => {
    vi.stubEnv("DEV", true);
    vi.stubEnv("VITE_ROBOT_PLATFORM_BASE_URL", "https://platform.example.test");
    vi.stubGlobal("window", {
      location: new URL("http://127.0.0.1:8088/annotations/tasks/task-1"),
    });
    const video = document.createElement("video");
    const query = "?response-content-type=video%2Fmp4&X-Amz-Signature=abcdef";
    const path = `/hc-data-local/${key}${query}`;
    const dispose = await attachAuthorizedMedia(
      video,
      `https://platform.example.test${path}`,
      vi.fn(),
    );

    expect(video.getAttribute("src")).toBe(
      `/_recording_media/platform.example.test${path}`,
    );
    expect(video.load).toHaveBeenCalledTimes(1);
    dispose();
  });

  it("attaches MP4 directly and detaches its failure listener on disposal", async () => {
    const video = document.createElement("video");
    const fatal = vi.fn();
    const dispose = await attachAuthorizedMedia(
      video,
      "/media/canonical.mp4",
      fatal,
    );

    expect(video.src).toContain("/media/canonical.mp4");
    video.dispatchEvent(new Event("error"));
    expect(fatal).toHaveBeenCalledTimes(1);

    dispose();
    video.dispatchEvent(new Event("error"));
    expect(fatal).toHaveBeenCalledTimes(1);
  });
});
