import { describe, expect, it } from "vitest";
import {
  clampBoundary,
  formatTimecode,
  nextEpisodeId,
  toSliceInputs,
  validateSlices,
  type EditableSlice,
} from "./model";

const second = 1_000_000_000;

function slice(
  episodeId: string,
  startNs: number,
  endNs: number,
): EditableSlice {
  return { episodeId, startNs, endNs, title: "", taskLabel: "", notes: "" };
}

describe("recording segmentation model", () => {
  it("formats three-hour source positions without rolling hours over", () => {
    expect(formatTimecode(10_799_123_000_000)).toBe("02:59:59.123");
    expect(formatTimecode(10_800_000_000_000)).toBe("03:00:00.000");
  });

  it("detects overlap and recording-boundary violations", () => {
    const issues = validateSlices(
      [
        slice("episode_0001", 0, 5 * second),
        slice("episode_0002", 4 * second, 11 * second),
      ],
      10 * second,
      second / 30,
    );
    expect(issues.map((issue) => issue.code)).toEqual([
      "OUT_OF_RANGE",
      "OVERLAP",
    ]);
  });

  it("clamps dragged boundaries against adjacent episodes and one-frame minimum", () => {
    const slices = [
      slice("episode_0001", 0, 3 * second),
      slice("episode_0002", 4 * second, 8 * second),
      slice("episode_0003", 9 * second, 10 * second),
    ];
    const movedStart = clampBoundary(
      slices,
      "episode_0002",
      "start",
      2 * second,
      10 * second,
      second,
    );
    const movedEnd = clampBoundary(
      movedStart,
      "episode_0002",
      "end",
      10 * second,
      10 * second,
      second,
    );
    expect(movedStart[1]?.startNs).toBe(3 * second);
    expect(movedEnd[1]?.endNs).toBe(9 * second);
  });

  it("creates stable unique ids and sends sorted canonical nanosecond strings", () => {
    const slices = [
      { ...slice("episode_0002", 2 * second, 3 * second), title: " 第二条 " },
      slice("episode_0001", 0, second),
    ];
    expect(nextEpisodeId(slices)).toBe("episode_0003");
    expect(toSliceInputs(slices)).toEqual([
      {
        episode_id: "episode_0001",
        start_offset_ns: "0",
        end_offset_ns: "1000000000",
      },
      {
        episode_id: "episode_0002",
        start_offset_ns: "2000000000",
        end_offset_ns: "3000000000",
        title: "第二条",
      },
    ]);
  });
});
