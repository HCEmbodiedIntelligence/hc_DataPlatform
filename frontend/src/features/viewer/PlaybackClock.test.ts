import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPlaybackClock } from "./PlaybackClock";

let frame: FrameRequestCallback;
beforeEach(() => {
  vi.spyOn(performance, "now").mockReturnValue(0);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frame = callback;
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("video-driven playback clock", () => {
  it("holds time while the master buffers and resumes from the decoded frame", () => {
    const clock = createPlaybackClock({ startNs: "0", endNs: "10000000000" });
    let decoded: string | null = "500000000";
    clock.attachMediaClock!(() => decoded);
    clock.play();
    frame(16);
    expect(clock.currentNs()).toBe("500000000");
    decoded = null;
    frame(5016);
    expect(clock.currentNs()).toBe("500000000");
    decoded = "600000000";
    frame(5032);
    expect(clock.currentNs()).toBe("600000000");
    clock.dispose();
  });

  it("switches to the next visible video when the master is detached", () => {
    const clock = createPlaybackClock({ startNs: "0", endNs: "2000000000" });
    const detach = clock.attachMediaClock!(() => "500000000");
    clock.attachMediaClock!(() => "700000000");
    clock.play();
    frame(16);
    detach();
    frame(32);
    expect(clock.currentNs()).toBe("700000000");
    clock.dispose();
  });

  it("ends at the episode boundary and retains wall-clock playback without videos", () => {
    const clock = createPlaybackClock({ startNs: "0", endNs: "20000000" });
    clock.play();
    frame(16);
    expect(clock.currentNs()).toBe("16000000");
    frame(32);
    expect(clock.currentNs()).toBe("19999999");
    expect(clock.isPlaying()).toBe(false);
    clock.dispose();
  });
});
