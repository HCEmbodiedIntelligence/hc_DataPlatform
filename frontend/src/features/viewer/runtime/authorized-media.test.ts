// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { attachAuthorizedMedia } from "./authorized-media";

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(
    () => undefined,
  );
});

describe("attachAuthorizedMedia", () => {
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
