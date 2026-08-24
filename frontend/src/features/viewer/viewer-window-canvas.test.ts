import { describe, expect, it, vi } from "vitest";
import {
  drawViewerWindowCanvas,
  viewerWindowSummary,
} from "./viewer-window-canvas";
import type { ViewerWindowPayload } from "./types";

function canvasFixture() {
  const setTransform = vi.fn();
  const clearRect = vi.fn();
  const beginPath = vi.fn();
  const moveTo = vi.fn();
  const lineTo = vi.fn();
  const stroke = vi.fn();
  const fillRect = vi.fn();
  const context = {
    setTransform,
    clearRect,
    beginPath,
    moveTo,
    lineTo,
    stroke,
    fillRect,
  } as unknown as CanvasRenderingContext2D;
  const canvas = {
    clientWidth: 320,
    clientHeight: 180,
    width: 0,
    height: 0,
  } as unknown as HTMLCanvasElement;
  return { canvas, context, setTransform, lineTo, fillRect };
}

function draw(payload: ViewerWindowPayload) {
  const { canvas, context, setTransform, lineTo, fillRect } = canvasFixture();
  drawViewerWindowCanvas({
    canvas,
    context,
    payload,
    currentNs: "50",
    startNs: "0",
    endNs: "100",
  });
  return { canvas, context, setTransform, lineTo, fillRect };
}

describe("viewer window canvas", () => {
  it("draws real numeric samples rather than only the shared playhead", () => {
    const { setTransform, lineTo } = draw({
      generation: 0,
      timestampsNs: ["10", "50", "90"],
      values: [[0], [10], [2]],
    });

    expect(setTransform).toHaveBeenCalled();
    expect(lineTo.mock.calls.length).toBeGreaterThan(3);
    expect(
      viewerWindowSummary({
        generation: 0,
        timestampsNs: ["10"],
        values: [[1]],
      }),
    ).toContain("真实数值样本");
  });

  it("selects and draws the nearest real pointcloud sample for the current clock", () => {
    const { fillRect } = draw({
      generation: 0,
      timestampsNs: ["40", "60"],
      pointFrames: [
        { timestampNs: "40", points: Float32Array.from([0, 0, 0]) },
        {
          timestampNs: "60",
          points: Float32Array.from([1, 1, 1, 2, 2, 2]),
        },
      ],
    });

    expect(fillRect.mock.calls.length).toBeGreaterThan(0);
  });

  it("draws event timestamps and gives each supported payload an accessible summary", () => {
    const { lineTo } = draw({
      generation: 0,
      timestampsNs: ["30"],
      events: [{ timestampNs: "30", label: "抓取" }],
    });

    expect(lineTo.mock.calls.length).toBeGreaterThan(5);
    expect(
      viewerWindowSummary({
        generation: 0,
        timestampsNs: ["30"],
        events: [{ timestampNs: "30", label: "抓取" }],
      }),
    ).toContain("真实事件");
  });
});
