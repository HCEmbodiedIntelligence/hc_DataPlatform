// @vitest-environment jsdom

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import {
  act,
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
  SharedSignalTimeline,
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

function renderTimeline(onRangeCreate?: (start: string, end: string) => void) {
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
      onTimeRangeCreate={onRangeCreate}
    />,
  );
  return { clock, onRangeSelect };
}

it("formats all viewer time readouts as seconds with two decimals", () => {
  expect(formatElapsedNs(12_030_000_000n, 0n)).toBe("12.03s");
  expect(formatElapsedNs(65_000_000_000n, 0n)).toBe("65.00s");
});

describe("ClipTimeline", () => {
  it("distinguishes a new drag from resizing an existing selection", () => {
    const onRangeCreate = vi.fn();
    const { clock, onRangeSelect } = renderTimeline(onRangeCreate);
    const slider = screen.getByRole("slider", { name: /播放位置/ });
    vi.spyOn(slider, "getBoundingClientRect").mockReturnValue({
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
    fireEvent.pointerDown(slider, { pointerId: 7, button: 0, clientX: 70 });
    fireEvent.pointerMove(slider, { pointerId: 7, clientX: 90 });
    fireEvent.pointerUp(slider, { pointerId: 7, clientX: 90 });
    expect(onRangeCreate).toHaveBeenCalledExactlyOnceWith(
      "7000000000",
      "9000000000",
    );
    expect(onRangeSelect).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByRole("button", { name: /标注开始/ }), {
      key: "ArrowRight",
    });
    expect(onRangeSelect).toHaveBeenLastCalledWith("7010000000", "9000000000");
    expect(onRangeCreate).toHaveBeenCalledTimes(1);
    clock.dispose();
  });

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

describe("direct Tag manipulation", () => {
  function setup(disabled = false) {
    const clock = createPlaybackClock({ startNs: "0", endNs: "10000000000" });
    const editing = {
      selected: true,
      onSelect: vi.fn(),
      onChange: vi.fn(),
      onDelete: vi.fn(),
    };
    render(
      <SharedSignalTimeline
        clock={clock}
        disabled={disabled}
        tracks={[
          {
            ...tracks[0]!,
            segments: [{ ...tracks[0]!.segments[0]!, editing }],
          },
        ]}
      />,
    );
    const tag = screen.getByRole("button", { name: /从“抓取零件”起点/u });
    vi.spyOn(tag.parentElement!, "getBoundingClientRect").mockReturnValue({
      left: 0,
      width: 100,
      x: 0,
      y: 0,
      top: 0,
      bottom: 25,
      right: 100,
      height: 25,
      toJSON: () => ({}),
    });
    return { clock, editing, tag };
  }

  it("scrubs while moving a Tag, commits on release, and suppresses the drag's click", () => {
    const { clock, editing, tag } = setup();
    clock.play();
    fireEvent.pointerDown(tag, { button: 0, pointerId: 1, clientX: 40 });
    fireEvent.pointerMove(tag, { pointerId: 1, clientX: 50 });
    expect(clock.isPlaying()).toBe(false);
    expect(clock.currentNs()).toBe("5000000000");
    expect(editing.onChange).not.toHaveBeenCalled();
    fireEvent.pointerUp(tag, { pointerId: 1, clientX: 50 });
    expect(editing.onChange).toHaveBeenCalledExactlyOnceWith(
      "3000000000",
      "7000000000",
    );
    fireEvent.click(tag);
    expect(clock.isPlaying()).toBe(false);
    expect(clock.currentNs()).toBe("5000000000");
    clock.dispose();
  });

  it.each(["start", "end"] as const)(
    "drags the %s edge directly and follows that boundary",
    (edge) => {
      const { clock, editing, tag } = setup();
      const grip = tag.querySelector(`[data-tag-edge="${edge}"]`)!;
      fireEvent.pointerDown(grip, {
        button: 0,
        pointerId: 1,
        clientX: edge === "start" ? 20 : 60,
      });
      fireEvent.pointerMove(tag, {
        pointerId: 1,
        clientX: edge === "start" ? 10 : 80,
      });
      expect(clock.currentNs()).toBe(
        edge === "start" ? "1000000000" : "7999999999",
      );
      fireEvent.pointerUp(tag, {
        pointerId: 1,
        clientX: edge === "start" ? 10 : 80,
      });
      expect(editing.onChange).toHaveBeenCalledExactlyOnceWith(
        edge === "start" ? "1000000000" : "2000000000",
        edge === "start" ? "6000000000" : "8000000000",
      );
      clock.dispose();
    },
  );

  it("keeps the interval length inside the timeline bounds", () => {
    const { clock, editing, tag } = setup();
    fireEvent.pointerDown(tag, { button: 0, pointerId: 1, clientX: 40 });
    fireEvent.pointerMove(tag, { pointerId: 1, clientX: 120 });
    fireEvent.pointerUp(tag, { pointerId: 1, clientX: 120 });
    expect(editing.onChange).toHaveBeenCalledExactlyOnceWith(
      "6000000000",
      "10000000000",
    );
    clock.dispose();
  });

  it.each(["Escape", "pointercancel"])(
    "cancels without writing on %s",
    (cancel) => {
      const { clock, editing, tag } = setup();
      clock.seek("8000000000");
      fireEvent.pointerDown(tag, { button: 0, pointerId: 1, clientX: 40 });
      fireEvent.pointerMove(tag, { pointerId: 1, clientX: 50 });
      if (cancel === "Escape") fireEvent.keyDown(tag, { key: "Escape" });
      else fireEvent.pointerCancel(tag, { pointerId: 1 });
      fireEvent.pointerUp(tag, { pointerId: 1, clientX: 50 });
      expect(editing.onChange).not.toHaveBeenCalled();
      expect(clock.currentNs()).toBe("8000000000");
      clock.dispose();
    },
  );

  it("selects a right-clicked Tag and offers deletion at the pointer", () => {
    const { clock, editing, tag } = setup();
    fireEvent.contextMenu(tag, { clientX: 30, clientY: 40 });
    expect(editing.onSelect).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("menuitem", { name: /删除 Tag/u }));
    expect(editing.onDelete).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    clock.dispose();
  });

  it("does not expose direct editing or deletion when read-only", () => {
    const { clock, editing, tag } = setup(true);
    fireEvent.contextMenu(tag);
    fireEvent.pointerDown(tag, { button: 0, pointerId: 1, clientX: 40 });
    fireEvent.pointerMove(tag, { pointerId: 1, clientX: 60 });
    fireEvent.pointerUp(tag, { pointerId: 1, clientX: 60 });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(editing.onChange).not.toHaveBeenCalled();
    expect(editing.onDelete).not.toHaveBeenCalled();
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
  it("keeps a slow window request alive and updates the cursor and values when time changes", async () => {
    const clock = createPlaybackClock({ startNs: "0", endNs: "20000000000" });
    let resolveWindow!: (payload: unknown) => void;
    const loadWindow = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveWindow = resolve;
        }),
    );
    const { container, unmount } = render(
      <ViewerJointAngleCurvePanel
        clock={clock}
        stream={{
          id: "slow-joint",
          canonicalPath: "/joint",
          displayName: "joint",
          modality: "joint_state",
          schema: { id: "joint", version: "1" },
          startNs: "0",
          endNs: "20000000000",
          availability: "ready",
          windowSource: { loadWindow: loadWindow as never },
        }}
      />,
    );
    // Enter the prefetch guard while the initial request is still running.
    // Previously this cancelled and restarted the very data we were waiting for.
    act(() => clock.seek("3500000000"));
    expect(loadWindow).toHaveBeenCalledTimes(1);
    await act(async () =>
      resolveWindow({
        generation: 0,
        timestampsNs: ["0", "3500000000"],
        values: [[0.1], [0.8]],
        series: [{ id: "elbow", displayName: "elbow", unit: "rad" }],
      }),
    );
    await waitFor(() => expect(screen.getByText("0.80 rad")).toBeVisible());
    const cursor = container.querySelector(".viewer-joint-curves__cursor")!;
    const forwardX = Number(cursor.getAttribute("x1"));
    act(() => clock.seek("0"));
    await waitFor(() => expect(screen.getByText("0.10 rad")).toBeVisible());
    expect(Number(cursor.getAttribute("x1"))).toBeLessThan(forwardX);
    expect(loadWindow).toHaveBeenCalledTimes(1);
    unmount();
    clock.dispose();
  });

  it("renders all 29 G1 joints including the right wrist", async () => {
    const clock = createPlaybackClock({ startNs: "0", endNs: "3000000000" });
    const names = Array.from({ length: 29 }, (_, i) =>
      i === 28 ? "right_wrist_yaw_joint" : `joint_${i}`,
    );
    render(
      <ViewerJointAngleCurvePanel
        clock={clock}
        stream={{
          id: "g1-joints",
          canonicalPath: "/robot/joint_states",
          displayName: "G1",
          modality: "joint_state",
          schema: { id: "joint-state", version: "1", unit: "rad" },
          rateHz: 30,
          startNs: "0",
          endNs: "3000000000",
          availability: "ready",
          windowSource: {
            loadWindow: async () => ({
              generation: 0,
              timestampsNs: ["0"],
              values: [names.map((_, i) => i / 100)],
              series: names.map((name) => ({
                id: name,
                displayName: name,
                unit: "rad",
              })),
            }),
          },
        }}
      />,
    );
    expect(
      await screen.findByRole("img", { name: /共 29 个关节/ }),
    ).toBeVisible();
    expect(screen.getByText("right_wrist_yaw_joint")).toBeVisible();
    clock.dispose();
  });

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
