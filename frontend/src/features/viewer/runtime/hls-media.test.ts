// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { attachAuthorizedMedia } from "./hls-media";

type ErrorHandler = (event: string, data: { readonly fatal: boolean }) => void;

const instances: FakeHls[] = [];

class FakeHls {
  static readonly Events = { ERROR: "error" };
  static readonly isSupported = vi.fn(() => true);
  readonly loadSource = vi.fn();
  readonly attachMedia = vi.fn();
  readonly destroy = vi.fn();
  private errorHandler: ErrorHandler | null = null;

  constructor() {
    instances.push(this);
  }

  on(_event: string, handler: ErrorHandler): void {
    this.errorHandler = handler;
  }

  fatal(): void {
    this.errorHandler?.("error", { fatal: true });
  }
}

vi.mock("hls.js", () => ({ default: FakeHls }));

beforeEach(() => {
  instances.length = 0;
  FakeHls.isSupported.mockReset().mockReturnValue(true);
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(
    () => undefined,
  );
});

describe("attachAuthorizedMedia", () => {
  it("lazy-attaches HLS playlists and destroys the decoder on disposal", async () => {
    const video = document.createElement("video");
    const fatal = vi.fn();
    const dispose = await attachAuthorizedMedia(
      video,
      "/api/v1/previews/a/media/index.m3u8?sig=x",
      fatal,
    );

    expect(instances).toHaveLength(1);
    expect(instances[0]?.loadSource).toHaveBeenCalledWith(
      "/api/v1/previews/a/media/index.m3u8?sig=x",
    );
    expect(instances[0]?.attachMedia).toHaveBeenCalledWith(video);
    instances[0]?.fatal();
    expect(fatal).toHaveBeenCalledTimes(1);

    dispose();
    expect(instances[0]?.destroy).toHaveBeenCalledTimes(1);
    instances[0]?.fatal();
    expect(fatal).toHaveBeenCalledTimes(1);
  });

  it("keeps ordinary media on the native element without loading HLS.js", async () => {
    const video = document.createElement("video");
    const fatal = vi.fn();
    const dispose = await attachAuthorizedMedia(
      video,
      "/media/preview.mp4",
      fatal,
    );

    expect(instances).toHaveLength(0);
    expect(video.src).toContain("/media/preview.mp4");
    video.dispatchEvent(new Event("error"));
    expect(fatal).toHaveBeenCalledTimes(1);

    dispose();
    video.dispatchEvent(new Event("error"));
    expect(fatal).toHaveBeenCalledTimes(1);
  });
});
