// @vitest-environment jsdom

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { createPlaybackClock } from "./PlaybackClock";
import {
  EpisodeWorkbenchCore,
  formatElapsedNs,
  ViewerJointAngleCurvePanel,
} from "./EpisodeWorkbenchCore";
import type { ViewerTimelineTrack } from "./EpisodeWorkbenchCore";

beforeAll(() => {
  if (!window.PointerEvent) {
    class TestPointerEvent extends MouseEvent {
      readonly pointerId: number;

      constructor(type: string, init: PointerEventInit = {}) {
        super(type, init);
        this.pointerId = init.pointerId ?? 0;
      }
    }
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      value: TestPointerEvent,
    });
  }

  Object.defineProperty(HTMLElement.prototype, "setPointerCapture", {
    configurable: true,
    value: () => undefined,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const tracks: readonly ViewerTimelineTrack[] = [
  {
    id: "phase",
    label: "阶段",
    level: 0,
    segments: [
      {
        id: "phase-1",
        label: "抓取零件",
        startNs: "2000000000",
        endNs: "6000000000",
        activatePlayback: true,
        tone: "phase",
      },
    ],
  },
  { id: "action", label: "动作", level: 1, segments: [] },
];

function renderTimeline() {
  const clock = createPlaybackClock({ startNs: "0", endNs: "10000000000" });
  const onRangeSelect = vi.fn();
  render(
    <EpisodeWorkbenchCore
      episodeId="episode-1"
      datasetId="dataset-1"
      versionId="version-1"
      clock={clock}
      mode="annotate"
      streams={[]}
      timelineSelection={{
        startNs: "2000000000",
        endNs: "6000000000",
        label: "抓取零件",
      }}
      timelineTracks={tracks}
      onTimeRangeSelect={onRangeSelect}
    />,
  );
  return { clock, onRangeSelect };
}

it("formats all viewer time readouts as seconds with two decimals", () => {
  expect(formatElapsedNs(12_030_000_000n, 0n)).toBe("12.03s");
  expect(formatElapsedNs(65_000_000_000n, 0n)).toBe("65.00s");
});

describe("ClipTimeline", () => {
  it("creates a precise range by dragging across the filmstrip", () => {
    const { clock, onRangeSelect } = renderTimeline();
    const filmstrip = screen.getByRole("slider", { name: /播放位置/ });
    vi.spyOn(filmstrip, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 100,
      bottom: 76,
      width: 100,
      height: 76,
      toJSON: () => ({}),
    });

    fireEvent.pointerDown(filmstrip, { pointerId: 7, button: 0, clientX: 20 });
    fireEvent.pointerMove(filmstrip, { pointerId: 7, clientX: 60 });
    fireEvent.pointerUp(filmstrip, { pointerId: 7, clientX: 60 });

    expect(onRangeSelect).toHaveBeenLastCalledWith("2000000000", "6000000000");
    clock.dispose();
  });

  it("supports keyboard trimming, zooming, and nested track labels", () => {
    const { clock, onRangeSelect } = renderTimeline();

    fireEvent.keyDown(screen.getByRole("button", { name: /标注开始/ }), {
      key: "ArrowRight",
    });
    expect(onRangeSelect).toHaveBeenLastCalledWith("2010000000", "6000000000");

    fireEvent.click(screen.getByRole("button", { name: "放大时间轴" }));
    expect(
      screen.getByRole("status", { name: "时间轴缩放" }),
    ).toHaveTextContent("2x");
    expect(screen.getAllByText("阶段")).toHaveLength(2);
    expect(screen.getAllByText("动作")).toHaveLength(2);
    clock.dispose();
  });

  it("starts the shared playback clock at an interactive segment boundary", async () => {
    const { clock } = renderTimeline();
    clock.seek("8000000000");

    const segment = screen.getByRole("button", {
      name: "从“抓取零件”起点同步播放全部视频和关节数据",
    });
    fireEvent.click(segment);

    expect(clock.currentNs()).toBe("2000000000");
    expect(clock.isPlaying()).toBe(true);
    await waitFor(() =>
      expect(segment).toHaveAttribute("aria-current", "time"),
    );
    clock.dispose();
  });
});

describe("EpisodeWorkbenchCore immutable window data", () => {
  it("loads a bound non-camera stream only when visible and exposes a real-data summary", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const clock = createPlaybackClock({ startNs: "0", endNs: "10000000000" });
    const loadWindow = vi.fn().mockResolvedValue({
      generation: 0,
      timestampsNs: ["1000000000", "2000000000"],
      values: [[0.2], [0.4]],
    });
    render(
      <EpisodeWorkbenchCore
        episodeId="episode-p06"
        datasetId="dataset-p06"
        versionId="version-p06"
        clock={clock}
        mode="readonly"
        streams={[
          {
            id: "force-p06",
            canonicalPath: "/force/wrench",
            displayName: "末端力",
            modality: "force",
            schema: { id: "hc.force", version: "contract-v1" },
            startNs: "0",
            endNs: "10000000000",
            availability: "ready",
            windowSource: { loadWindow },
          },
        ]}
      />,
    );

    await waitFor(() => expect(loadWindow).toHaveBeenCalledTimes(1));
    expect(screen.getByLabelText("末端力 可视化")).toBeInTheDocument();
    expect(screen.getByText(/已加载 2 个真实数值样本/)).toBeInTheDocument();
    clock.dispose();
  });
});

describe("ViewerJointAngleCurvePanel recovery", () => {
  it("keeps the last successful chart visible when a window refresh is transiently rejected", async () => {
    const clock = createPlaybackClock({ startNs: "0", endNs: "20000000000" });
    const loadWindow = vi
      .fn()
      .mockResolvedValueOnce({
        generation: 0,
        timestampsNs: ["0", "1000000000"],
        values: [
          [0.1, -0.2],
          [0.2, -0.1],
        ],
        series: [
          { id: "shoulder", displayName: "shoulder", unit: "rad" },
          { id: "elbow", displayName: "elbow", unit: "rad" },
        ],
      })
      .mockRejectedValueOnce(new Error("temporary object-store failure"));
    const rendered = render(
      <ViewerJointAngleCurvePanel
        clock={clock}
        stream={{
          id: "joint-stream",
          canonicalPath: "/joint_states",
          displayName: "关节角变化",
          modality: "joint_state",
          schema: { id: "joint-state", version: "1", unit: "rad" },
          rateHz: 30,
          startNs: "0",
          endNs: "20000000000",
          availability: "ready",
          windowSource: { loadWindow },
        }}
      />,
    );

    await waitFor(() => expect(loadWindow).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("img", { name: /关节角时间序列/ })).toBeVisible();

    clock.seek("8000000000");
    await waitFor(() => expect(loadWindow).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(
        screen.getByText(/最近一次刷新失败，已保留上一窗口/),
      ).toBeVisible(),
    );
    expect(screen.queryByText("关节角读取失败")).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: /关节角时间序列/ })).toBeVisible();

    rendered.unmount();
    clock.dispose();
  });
});
