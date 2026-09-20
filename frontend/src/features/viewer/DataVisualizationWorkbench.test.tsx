// @vitest-environment jsdom

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StrictMode, useEffect, useRef } from "react";
import type { JSX } from "react";
import { createPlaybackClock } from "./PlaybackClock";
import { RawDiagnosticWorkbench } from "./RawDiagnosticWorkbench";
import type { ViewerPanelRenderContext } from "./EpisodeWorkbenchCore";
import type { RuntimeManifestDiscoveryProjection } from "./raw-diagnostic-adapter";
import type { StreamDescriptor } from "./types";

const clocks: ReturnType<typeof createPlaybackClock>[] = [];

afterEach(() => {
  cleanup();
  clocks.splice(0).forEach((clock) => clock.dispose());
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function clock() {
  const value = createPlaybackClock({ startNs: "0", endNs: "10000000000" });
  clocks.push(value);
  return value;
}

function manifest(cameraCount: number): RuntimeManifestDiscoveryProjection {
  return {
    source: "MANIFEST",
    read_only: true,
    cameras: Array.from({ length: cameraCount }, (_, index) => ({
      camera_id: `相机 ${index + 1}`,
      topic: `/camera/${index + 1}/image`,
      encoding: "h264",
      frame_id: `camera_${index + 1}`,
    })),
    topics: [],
    missing_expected_topics: [],
  };
}

function stream(
  index: number,
  availability: StreamDescriptor["availability"] = "ready",
): StreamDescriptor {
  return {
    id: `stream-${index}`,
    canonicalPath: `/camera/${index}/image`,
    displayName: `相机 ${index}`,
    modality: "rgb",
    schema: { id: "sensor_msgs/Image", version: "1", encoding: "h264" },
    rateHz: 30,
    startNs: "0",
    endNs: "10000000000",
    availability,
  };
}

function media(
  cameraCount: number,
  states: Readonly<Record<number, StreamDescriptor["availability"]>> = {},
) {
  return Object.fromEntries(
    Array.from({ length: cameraCount }, (_, index) => [
      `/camera/${index + 1}/image`,
      stream(index + 1, states[index + 1] ?? "ready"),
    ]),
  );
}

function baseProps(cameraCount: number) {
  return {
    id: "raw-diagnostic-test",
    clock: clock(),
    manifest: manifest(cameraCount),
    mediaStreamsByTopic: media(cameraCount),
    collectionItems: [
      {
        id: "package-1",
        label: "测试数据包",
        description: "确定性组件测试数据",
        status: "自动质检异常",
        statusTone: "error" as const,
        facts: [{ label: "帧数", value: "3256" }],
      },
    ],
    selectedCollectionItemId: "package-1",
    findings: [],
    signalTracks: [
      {
        id: "joint-state",
        label: "关节状态",
        segments: [
          {
            id: "joint-coverage",
            label: "7 关节",
            startNs: "0",
            endNs: "10000000000",
            tone: "signal" as const,
          },
        ],
      },
      {
        id: "action-command",
        label: "动作指令",
        segments: [
          {
            id: "action-segment",
            label: "抓取",
            startNs: "2000000000",
            endNs: "4000000000",
            tone: "action" as const,
          },
        ],
      },
      {
        id: "quality-check",
        label: "QC 轨道",
        segments: [
          {
            id: "quality-segment",
            label: "自动异常",
            startNs: "5000000000",
            endNs: "6000000000",
            tone: "issue" as const,
          },
        ],
      },
    ],
  };
}

function ClockProbe({
  id,
  sharedClock,
}: {
  readonly id: string;
  readonly sharedClock: ReturnType<typeof createPlaybackClock>;
}): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(
    () =>
      sharedClock.subscribe((ns) => {
        if (ref.current) ref.current.dataset.timeNs = ns;
      }),
    [sharedClock],
  );
  return <div ref={ref} data-testid={`probe-${id}`} />;
}

function renderProbe(context: ViewerPanelRenderContext): JSX.Element {
  return (
    <article
      className="viewer-panel"
      aria-label={`${context.stream.displayName} 测试画面`}
    >
      <header>
        <h3>{context.stream.displayName}</h3>
        <span>{context.panel.state}</span>
      </header>
      <ClockProbe
        id={context.stream.id}
        sharedClock={context.clock as ReturnType<typeof createPlaybackClock>}
      />
    </article>
  );
}

describe("DataVisualizationWorkbench camera composition", () => {
  it.each([
    [0, "empty"],
    [1, "single"],
    [2, "pair"],
    [4, "quad"],
    [8, "many"],
  ] as const)(
    "adapts the Manifest camera grid for %i cameras",
    (cameraCount, layout) => {
      render(<RawDiagnosticWorkbench {...baseProps(cameraCount)} />);
      const grid = screen
        .getByLabelText("数据清单相机视图")
        .querySelector(".viewer-media-grid");
      expect(grid).toHaveAttribute("data-camera-count", String(cameraCount));
      expect(grid).toHaveAttribute("data-camera-layout", layout);
      if (cameraCount === 0) {
        expect(screen.getByText(/数据清单中未发现相机/)).toBeInTheDocument();
      } else {
        expect(
          screen.getAllByRole("heading", { name: /相机 \d+/ }),
        ).toHaveLength(cameraCount);
      }
    },
  );

  it("isolates missing and slow camera streams without clearing healthy panels", () => {
    const props = baseProps(4);
    render(
      <RawDiagnosticWorkbench
        {...props}
        mediaStreamsByTopic={{
          ...media(4, { 2: "media-preparing", 3: "partial" }),
          "/camera/4/image": undefined,
        }}
      />,
    );
    expect(screen.getByText("慢流加载中")).toBeInTheDocument();
    expect(screen.getByText("存在缺帧")).toBeInTheDocument();
    expect(screen.getByText("流缺失")).toBeInTheDocument();
    expect(
      screen.getByText("局部缺流或缺帧不会清空其他相机与信号轨道。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "相机 1" })).toBeInTheDocument();
  });

  it("authorizes and decodes video only while its panel is near the viewport", async () => {
    let visibilityCallback: IntersectionObserverCallback | undefined;
    class TestIntersectionObserver {
      readonly root = null;
      readonly rootMargin = "120px 0px";
      readonly thresholds = [0];
      constructor(callback: IntersectionObserverCallback) {
        visibilityCallback = callback;
      }
      disconnect() {}
      observe() {}
      takeRecords() {
        return [];
      }
      unobserve() {}
    }
    vi.stubGlobal("IntersectionObserver", TestIntersectionObserver);
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(
      () => undefined,
    );
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(
      () => undefined,
    );
    const authorize = vi
      .fn()
      .mockResolvedValue({ url: "https://media.invalid/camera-1.mp4" });
    const props = baseProps(1);
    render(
      <RawDiagnosticWorkbench
        {...props}
        mediaStreamsByTopic={{
          "/camera/1/image": {
            ...stream(1),
            mediaSource: { authorize, refresh: authorize },
          },
        }}
      />,
    );
    expect(authorize).not.toHaveBeenCalled();
    act(() => {
      visibilityCallback?.(
        [{ isIntersecting: true } as IntersectionObserverEntry],
        {} as IntersectionObserver,
      );
    });
    await waitFor(() => expect(authorize).toHaveBeenCalledTimes(1));
    act(() => {
      visibilityCallback?.(
        [{ isIntersecting: false } as IntersectionObserverEntry],
        {} as IntersectionObserver,
      );
    });
    await waitFor(() =>
      expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled(),
    );
  });

  it("follows the video playback clock without seeking it on every animation frame", async () => {
    let animationFrame: FrameRequestCallback | undefined;
    let mediaPlaying = false;
    vi.stubGlobal("IntersectionObserver", undefined);
    vi.stubGlobal(
      "requestAnimationFrame",
      vi.fn((callback: FrameRequestCallback) => {
        animationFrame = callback;
        return 1;
      }),
    );
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    vi.spyOn(performance, "now").mockReturnValue(0);
    vi.spyOn(HTMLMediaElement.prototype, "paused", "get").mockImplementation(
      () => !mediaPlaying,
    );
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(
      () => undefined,
    );
    const play = vi
      .spyOn(HTMLMediaElement.prototype, "play")
      .mockImplementation(() => {
        mediaPlaying = true;
        return Promise.resolve();
      });
    const pause = vi
      .spyOn(HTMLMediaElement.prototype, "pause")
      .mockImplementation(() => {
        mediaPlaying = false;
      });
    const seek = vi.spyOn(HTMLMediaElement.prototype, "currentTime", "set");
    const authorize = vi.fn().mockResolvedValue({
      url: "https://media.invalid/camera-1.mp4",
      expiresAt: "2099-01-01T00:00:00Z",
      kind: "rgb-video" as const,
    });
    const props = baseProps(1);
    render(
      <RawDiagnosticWorkbench
        {...props}
        mediaStreamsByTopic={{
          "/camera/1/image": {
            ...stream(1),
            mediaSource: { authorize, refresh: authorize },
          },
        }}
      />,
    );

    const video = screen.getByLabelText("相机 1 媒体") as HTMLVideoElement;
    await waitFor(() =>
      expect(video).toHaveAttribute(
        "src",
        "https://media.invalid/camera-1.mp4",
      ),
    );
    seek.mockClear();

    act(() => props.clock.play());
    expect(play).toHaveBeenCalledTimes(1);

    act(() => props.clock.seek("1000000000"));
    expect(seek).toHaveBeenLastCalledWith(1);

    Object.defineProperty(video, "readyState", {
      configurable: true,
      value: 4,
    });
    video.currentTime = 0.916;
    seek.mockClear();
    act(() => animationFrame?.(16));
    expect(seek).not.toHaveBeenCalled();

    video.currentTime = 0.5;
    seek.mockClear();
    act(() => animationFrame?.(32));
    expect(seek).not.toHaveBeenCalled();
    expect(props.clock.currentNs()).toBe("500000000");

    act(() => props.clock.setRate(2));
    expect(video.playbackRate).toBe(2);
    act(() => props.clock.pause());
    expect(pause).toHaveBeenCalledTimes(1);

    Object.defineProperty(video, "duration", {
      configurable: true,
      value: 10,
    });
    seek.mockClear();
    act(() => props.clock.seek(props.clock.endNs));
    expect(seek).toHaveBeenLastCalledWith(10 - 1 / 30);

    seek.mockClear();
    act(() => props.clock.play());
    expect(seek).toHaveBeenLastCalledWith(0);
    expect(play).toHaveBeenCalledTimes(2);
  });

  it("does not authorize media for StrictMode's discarded effect", async () => {
    vi.stubGlobal("IntersectionObserver", undefined);
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(
      () => undefined,
    );
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(
      () => undefined,
    );
    const authorize = vi.fn().mockResolvedValue({
      url: "https://media.invalid/camera-1.mp4",
      expiresAt: "2099-01-01T00:00:00Z",
      kind: "rgb-video" as const,
    });
    const props = baseProps(1);

    render(
      <StrictMode>
        <RawDiagnosticWorkbench
          {...props}
          mediaStreamsByTopic={{
            "/camera/1/image": {
              ...stream(1),
              mediaSource: { authorize, refresh: authorize },
            },
          }}
        />
      </StrictMode>,
    );

    await waitFor(() => expect(authorize).toHaveBeenCalledTimes(1));
    const video = screen.getByLabelText("相机 1 媒体");
    await waitFor(() =>
      expect(video).toHaveAttribute(
        "src",
        "https://media.invalid/camera-1.mp4",
      ),
    );
    expect(screen.queryByText(/资源加载失败/u)).not.toBeInTheDocument();
  });

  it("refreshes a direct MP4 grant before its signed URL expires", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-31T10:00:00Z"));
    vi.stubGlobal("IntersectionObserver", undefined);
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(
      () => undefined,
    );
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(
      () => undefined,
    );
    const authorize = vi.fn().mockResolvedValue({
      url: "https://media.invalid/first.mp4",
      expiresAt: "2026-08-31T10:01:00Z",
      kind: "rgb-video" as const,
    });
    const refresh = vi.fn().mockResolvedValue({
      url: "https://media.invalid/second.mp4",
      expiresAt: "2026-08-31T10:16:00Z",
      kind: "rgb-video" as const,
    });
    const props = baseProps(1);

    render(
      <RawDiagnosticWorkbench
        {...props}
        mediaStreamsByTopic={{
          "/camera/1/image": {
            ...stream(1),
            mediaSource: { authorize, refresh },
          },
        }}
      />,
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(authorize).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("相机 1 媒体")).toHaveAttribute(
      "src",
      "https://media.invalid/second.mp4",
    );
  });

  it("refreshes a failed signed media descriptor once, then keeps the failure local to its panel", async () => {
    let visibilityCallback: IntersectionObserverCallback | undefined;
    class TestIntersectionObserver {
      readonly root = null;
      readonly rootMargin = "120px 0px";
      readonly thresholds = [0];
      constructor(callback: IntersectionObserverCallback) {
        visibilityCallback = callback;
      }
      disconnect() {}
      observe() {}
      takeRecords() {
        return [];
      }
      unobserve() {}
    }
    vi.stubGlobal("IntersectionObserver", TestIntersectionObserver);
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(
      () => undefined,
    );
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(
      () => undefined,
    );
    const authorize = vi
      .fn()
      .mockResolvedValue({ url: "https://media.invalid/first.mp4" });
    const refresh = vi
      .fn()
      .mockResolvedValue({ url: "https://media.invalid/second.mp4" });
    const props = baseProps(1);
    render(
      <RawDiagnosticWorkbench
        {...props}
        mediaStreamsByTopic={{
          "/camera/1/image": {
            ...stream(1),
            mediaSource: { authorize, refresh },
          },
        }}
      />,
    );
    act(() => {
      visibilityCallback?.(
        [{ isIntersecting: true } as IntersectionObserverEntry],
        {} as IntersectionObserver,
      );
    });
    await waitFor(() => expect(authorize).toHaveBeenCalledTimes(1));
    const video = screen.getByLabelText("相机 1 媒体");
    fireEvent.error(video);
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    fireEvent.error(video);
    await waitFor(() =>
      expect(screen.getByText(/资源加载失败/u)).toBeInTheDocument(),
    );
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});

describe("DataVisualizationWorkbench shared clock and boundaries", () => {
  it("drives every camera probe and the only video timeline from one clock", async () => {
    const props = baseProps(4);
    render(
      <RawDiagnosticWorkbench
        {...props}
        slots={{ renderPanel: renderProbe }}
      />,
    );
    expect(screen.getAllByRole("slider")).toHaveLength(1);
    expect(screen.getAllByText("关节状态").length).toBeGreaterThan(0);
    expect(screen.getAllByText("动作指令").length).toBeGreaterThan(0);
    expect(screen.getAllByText("QC 轨道").length).toBeGreaterThan(0);
    act(() => props.clock.seek("5000000000"));
    await waitFor(() => {
      expect(
        screen.getByRole("slider", { name: "共享播放位置" }),
      ).toHaveAttribute("aria-valuenow", "50");
    });
    for (let index = 1; index <= 4; index += 1) {
      expect(screen.getByTestId(`probe-stream-${index}`)).toHaveAttribute(
        "data-time-ns",
        "5000000000",
      );
    }
  });

  it("keeps static navigation and inspector slots outside clock updates", () => {
    const props = baseProps(8);
    const navigation = vi.fn(() => <div>静态采集导航</div>);
    const inspector = vi.fn(() => <div>静态 Tag 工具</div>);
    render(
      <RawDiagnosticWorkbench
        {...props}
        slots={{ navigation, inspector, renderPanel: renderProbe }}
      />,
    );
    act(() => {
      for (let index = 0; index < 40; index += 1)
        props.clock.seek(String(index * 100000000));
    });
    expect(navigation).toHaveBeenCalledTimes(1);
    expect(inspector).toHaveBeenCalledTimes(1);
  });

  it("makes Raw status read-only and exposes no artificial PASS action", () => {
    const props = baseProps(1);
    render(
      <RawDiagnosticWorkbench
        {...props}
        findings={[
          {
            id: "finding-1",
            title: "时间戳不连续",
            severity: "error",
            streamLabel: "相机 1",
            startNs: "2000000000",
            endNs: "2500000000",
            message: "检测到时间戳回退。",
          },
        ]}
        notes={{ value: "", onChange: vi.fn() }}
      />,
    );
    expect(screen.getByText("只读诊断")).toBeInTheDocument();
    expect(
      screen.getByText("异常数据保留在 Raw，不进入 Lance"),
    ).toBeInTheDocument();
    expect(screen.getByText(/备注只附加诊断上下文/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /PASS|通过|放行/u }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "请求重新采集" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "重新运行自动校验" }),
    ).toBeDisabled();
  });

  it("invokes only explicitly supplied capability-gated commands", async () => {
    const props = baseProps(1);
    const preserve = vi.fn();
    render(
      <RawDiagnosticWorkbench
        {...props}
        commands={{ preserveEvidence: { invoke: preserve } }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "复制证据链接" }));
    await waitFor(() => expect(preserve).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "请求重新采集" })).toBeDisabled();
  });
});
