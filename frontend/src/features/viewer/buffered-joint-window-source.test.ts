import { describe, expect, it, vi } from "vitest";
import { bufferJointWindows } from "./buffered-joint-window-source";
import { buildJointFrameSource } from "./joint-frame-source";
import type { StreamDescriptor, ViewerWindowPayload } from "./types";

function payload(start: number): ViewerWindowPayload {
  return {
    generation: 0,
    timestampsNs: Array.from({ length: 4 }, (_, i) => `${(start + i) * 1e9}`),
    values: Array.from({ length: 4 }, (_, i) => [start + i]),
    series: [{ id: "elbow", displayName: "elbow", unit: "rad" }],
  };
}

describe("buffered joint playback", () => {
  it("shares in-flight reads across the curve and robot, even when one consumer aborts", async () => {
    let resolve!: (value: ViewerWindowPayload) => void;
    const loadWindow = vi.fn(
      () =>
        new Promise<ViewerWindowPayload>((done) => {
          resolve = done;
        }),
    );
    const source = bufferJointWindows({ loadWindow }, "0", "20000000000");
    const first = new AbortController();
    const cancelled = source.loadWindow(
      { startNs: "0", endNs: "4000000000", lod: 1 },
      first.signal,
    );
    const rejected = expect(cancelled).rejects.toMatchObject({
      name: "AbortError",
    });
    first.abort();
    const robot = source.loadWindow(
      { startNs: "0", endNs: "4000000000", lod: 0 },
      new AbortController().signal,
    );
    expect(loadWindow).toHaveBeenCalledTimes(1);
    resolve(payload(0));
    await rejected;
    await expect(robot).resolves.toMatchObject({
      values: [[0], [1], [2], [3]],
    });
    const subset = await source.loadWindow(
      { startNs: "1000000000", endNs: "3000000000", lod: 1 },
      new AbortController().signal,
    );
    expect(subset.values).toEqual([[1], [2]]);
    expect(loadWindow).toHaveBeenCalledTimes(1);
  });

  it("prefetches the next robot chunk and reuses it for the curve and boundary crossing", async () => {
    const loadWindow = vi.fn(async (window: { startNs: string }) =>
      payload(Number(BigInt(window.startNs) / 1_000_000_000n)),
    );
    const windowSource = bufferJointWindows({ loadWindow }, "0", "20000000000");
    const stream: StreamDescriptor = {
      id: "joint",
      canonicalPath: "/joint",
      displayName: "joint",
      modality: "joint_state",
      schema: { id: "joint", version: "1" },
      startNs: "0",
      endNs: "20000000000",
      availability: "ready",
      windowSource,
    };
    const robot = buildJointFrameSource(stream)!;
    const signal = new AbortController().signal;
    await expect(robot.sampleAt("0", signal)).resolves.toEqual({ elbow: 0 });
    expect(loadWindow).toHaveBeenCalledTimes(1);
    await expect(robot.sampleAt("2500000000", signal)).resolves.toEqual({
      elbow: 2,
    });
    expect(loadWindow).toHaveBeenCalledTimes(2);
    const curve = await windowSource.loadWindow(
      { startNs: "1000000000", endNs: "7000000000", lod: 1 },
      signal,
    );
    expect(curve.values).toEqual([[1], [2], [3], [4], [5], [6]]);
    await expect(robot.sampleAt("4000000000", signal)).resolves.toEqual({
      elbow: 4,
    });
    expect(loadWindow).toHaveBeenCalledTimes(2);
    await expect(robot.sampleAt("1000000000", signal)).resolves.toEqual({
      elbow: 1,
    });
    expect(loadWindow).toHaveBeenCalledTimes(2);
  });

  it("retries failed reads without caching the rejection", async () => {
    const loadWindow = vi
      .fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(payload(0));
    const source = bufferJointWindows({ loadWindow }, "0", "4000000000");
    const window = { startNs: "0", endNs: "4000000000", lod: 0 };
    await expect(
      source.loadWindow(window, new AbortController().signal),
    ).rejects.toThrow("offline");
    await expect(
      source.loadWindow(window, new AbortController().signal),
    ).resolves.toMatchObject({ values: [[0], [1], [2], [3]] });
    expect(loadWindow).toHaveBeenCalledTimes(2);
  });
});
