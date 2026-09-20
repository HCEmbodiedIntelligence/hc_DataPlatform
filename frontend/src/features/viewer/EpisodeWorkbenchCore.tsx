import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  CSSProperties,
  JSX,
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
  ReactNode,
  RefObject,
} from "react";
import {
  Activity,
  Bot,
  Pause,
  Play,
  RotateCcw,
  StepBack,
  StepForward,
  VideoOff,
} from "lucide-react";
import type { PlaybackClock, PlaybackClockUpdate } from "./PlaybackClock";
import { useClockText } from "./PlaybackClock";
import { resolveViewerComposition } from "./ViewerCompositionResolver";
import { RobotSceneCore } from "./RobotSceneCore";
import type { RobotSceneCoreProps } from "./RobotSceneCore";
import { ViewerResourceRegistry } from "./runtime/ViewerResourceRegistry";
import { attachAuthorizedMedia } from "./runtime/authorized-media";
import { isSignedResourceExpired } from "./runtime/signed-resource";
import {
  createDomainError,
  isDomainError,
} from "../../shared/api/domain-error";
import type {
  DomainError,
  OverlayRenderer,
  StreamDescriptor,
  ViewerPanelSpec,
  ViewerResourceScope,
  ViewerWindowPayload,
} from "./types";
import {
  drawViewerWindowCanvas,
  viewerWindowSummary,
} from "./viewer-window-canvas";
import "./EpisodeWorkbenchCore.css";

export interface EpisodeWorkbenchCoreProps {
  episodeId: string;
  datasetId: string;
  versionId: string;
  clock: PlaybackClock;
  mode: "readonly" | "annotate" | "cleaning";
  streams: readonly StreamDescriptor[];
  robotScene?: RobotSceneCoreProps & {
    readonly title?: string;
    readonly canonicalPath?: string;
  };
  overlays?: readonly OverlayRenderer[];
  onTimeRangeSelect?: (startNs: string, endNs: string) => void;
  timelineSelection?: ViewerTimelineSelection;
  timelineTracks?: readonly ViewerTimelineTrack[];
  timelineDisabled?: boolean;
  timelineVariant?: ViewerTimelineVariant;
  timelineLabel?: string;
  renderPanel?: ViewerPanelRenderer;
  onResourceError?: (
    e: DomainError,
    scope: "video" | "curve" | "pointcloud" | "scene3d",
  ) => void;
}

export type ViewerTimelineVariant = "filmstrip" | "signals";

export interface ViewerPanelRenderContext {
  readonly panel: ViewerPanelSpec;
  readonly stream: StreamDescriptor;
  readonly clock: PlaybackClock;
  readonly defaultPanel: ReactNode;
}

export type ViewerPanelRenderer = (
  context: ViewerPanelRenderContext,
) => ReactNode;

export interface ViewerTimelineSelection {
  readonly startNs: string;
  readonly endNs: string;
  readonly label?: string;
}

export interface ViewerTimelineSegment {
  readonly id: string;
  readonly label: string;
  readonly startNs: string;
  readonly endNs?: string;
  /** Makes this segment start every shared-clock consumer from its first frame. */
  readonly activatePlayback?: boolean;
  readonly tone?:
    | "phase"
    | "action"
    | "object"
    | "event"
    | "issue"
    | "signal"
    | "quality-pass"
    | "tag-level-1"
    | "tag-level-2"
    | "tag-level-3"
    | "tag-level-4";
}

export interface ViewerTimelineTrack {
  readonly id: string;
  readonly label: string;
  readonly level?: number;
  readonly segments: readonly ViewerTimelineSegment[];
}

function asDomainError(error: unknown, fallback: string): DomainError {
  if (isDomainError(error)) return error;
  return createDomainError({
    code: "SERVER_ERROR",
    message: error instanceof Error ? error.message : "Viewer resource failed",
    fieldErrors: [],
    operationErrors: [{ code: fallback, message: "Viewer resource failed" }],
    blockedReasons: [],
    requestId: null,
    retryable: true,
    httpStatus: null,
  });
}

function ClockReadout({ clock }: { clock: PlaybackClock }): JSX.Element {
  const current = useClockText(clock);
  return (
    <output
      aria-live="off"
      data-testid="viewer-clock-text"
      title={formatElapsedNs(BigInt(current), BigInt(clock.startNs))}
    >
      {formatElapsedNs(BigInt(current), BigInt(clock.startNs))}
      <span aria-hidden="true">
        {" "}
        / {formatElapsedNs(BigInt(clock.endNs) - BigInt(clock.startNs), 0n)}
      </span>
    </output>
  );
}

function directSeek(clock: PlaybackClock, deltaNs: bigint): void {
  clock.seek((BigInt(clock.currentNs()) + deltaNs).toString());
}

export function ViewerPlaybackControls({
  clock,
}: {
  clock: PlaybackClock;
}): JSX.Element {
  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (
      event.target instanceof HTMLInputElement ||
      event.target instanceof HTMLTextAreaElement
    )
      return;
    const commands: Readonly<Record<string, () => void>> = {
      " ": () => (clock.isPlaying() ? clock.pause() : clock.play()),
      k: () => clock.pause(),
      j: () => directSeek(clock, -1_000_000_000n),
      l: () => directSeek(clock, 1_000_000_000n),
      ArrowLeft: () => directSeek(clock, -100_000_000n),
      ArrowRight: () => directSeek(clock, 100_000_000n),
      Home: () => clock.seek(clock.startNs),
      End: () => clock.seek((BigInt(clock.endNs) - 1n).toString()),
    };
    const command = commands[event.key];
    if (command) {
      event.preventDefault();
      command();
    }
  };
  return (
    <div
      className="viewer-playback-controls"
      aria-label="播放控制；空格播放或暂停，J/L 前后跳转 1.00s"
      onKeyDown={onKeyDown}
      tabIndex={0}
    >
      <button type="button" onClick={() => clock.play()} aria-label="播放">
        <Play aria-hidden="true" size={15} />
      </button>
      <button type="button" onClick={() => clock.pause()} aria-label="暂停">
        <Pause aria-hidden="true" size={15} />
      </button>
      <button
        type="button"
        onClick={() => directSeek(clock, -100_000_000n)}
        aria-label="后退 0.10s"
      >
        <StepBack aria-hidden="true" size={15} />
      </button>
      <button
        type="button"
        onClick={() => directSeek(clock, 100_000_000n)}
        aria-label="前进 0.10s"
      >
        <StepForward aria-hidden="true" size={15} />
      </button>
      <ClockReadout clock={clock} />
      {[0.5, 1, 2].map((rate) => (
        <button type="button" key={rate} onClick={() => clock.setRate(rate)}>
          {rate}x
        </button>
      ))}
    </div>
  );
}

function usePanelVisibility(ref: RefObject<HTMLElement | null>): boolean {
  const [intersecting, setIntersecting] = useState(
    () => typeof IntersectionObserver === "undefined",
  );
  const [documentVisible, setDocumentVisible] = useState(
    () =>
      typeof document === "undefined" || document.visibilityState !== "hidden",
  );
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      ([entry]) => setIntersecting(entry?.isIntersecting ?? false),
      { rootMargin: "120px 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref]);
  useEffect(() => {
    if (typeof document === "undefined") return;
    const update = () =>
      setDocumentVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return intersecting && documentVisible;
}

function mediaScope(kind: ViewerPanelSpec["kind"]): ViewerResourceScope {
  if (kind === "video" || kind === "depth") return "video";
  if (kind === "pointcloud-preview") return "pointcloud";
  return "curve";
}

function StreamPanel({
  panel,
  stream,
  clock,
  onResourceError,
}: {
  panel: ViewerPanelSpec;
  stream: StreamDescriptor;
  clock: PlaybackClock;
  onResourceError?: EpisodeWorkbenchCoreProps["onResourceError"];
}): JSX.Element {
  const hostRef = useRef<HTMLElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const visible = usePanelVisibility(hostRef);
  const [error, setError] = useState<DomainError | null>(null);
  const [windowSummary, setWindowSummary] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const onResourceErrorRef = useRef(onResourceError);

  useEffect(() => {
    onResourceErrorRef.current = onResourceError;
  }, [onResourceError]);

  useEffect(() => {
    if (!visible || panel.state === "missing" || panel.state === "unsupported")
      return;
    setError(null);
    setWindowSummary(null);
    const controller = new AbortController();
    const resources = new ViewerResourceRegistry();
    let generation = 0;
    let latestPayloadDispose: (() => void) | undefined;
    let latestPayload: ViewerWindowPayload | null = null;
    let drawCanvas: ((ns: string) => void) | undefined;
    const fail = (cause: unknown) => {
      if (controller.signal.aborted) return;
      const domainError = asDomainError(cause, "VIEWER_RESOURCE_ERROR");
      setError(domainError);
      onResourceErrorRef.current?.(domainError, mediaScope(panel.kind));
    };

    if (videoRef.current && stream.mediaSource) {
      const video = videoRef.current;
      resources.trackMedia(video);
      let mediaErrorRefreshUsed = false;
      let mediaAttached = false;
      let playRequested = false;
      let latestClockNs = clock.currentNs();
      let detachMedia: (() => void) | undefined;
      let revokeDescriptor: (() => void) | undefined;
      let expiryRefreshTimer: number | undefined;
      let initialPositionPending = true;
      let lastHardSeekAt = Number.NEGATIVE_INFINITY;
      let lastPlayAttemptAt = Number.NEGATIVE_INFINITY;
      let waiting = false;
      let sourceStartSeconds = stream.mediaStartSeconds ?? 0;
      let sourceEndSeconds = stream.mediaEndSeconds ?? Number.POSITIVE_INFINITY;
      const detachClock = clock.attachMediaClock?.(() => {
        if (!mediaAttached || video.readyState < 2 || video.seeking || waiting)
          return null;
        const elapsed = Math.max(0, video.currentTime - sourceStartSeconds);
        return (
          BigInt(clock.startNs) + BigInt(Math.round(elapsed * 1_000_000_000))
        ).toString();
      });
      if (detachClock) resources.add(detachClock);
      const onWaiting = () => {
        waiting = true;
      };
      const onPlaying = () => {
        waiting = false;
      };
      video.addEventListener("waiting", onWaiting);
      video.addEventListener("stalled", onWaiting);
      video.addEventListener("playing", onPlaying);
      resources.add(() => {
        video.removeEventListener("waiting", onWaiting);
        video.removeEventListener("stalled", onWaiting);
        video.removeEventListener("playing", onPlaying);
      });
      const streamRateHz = Math.max(stream.rateHz ?? 30, 1);
      const playbackDriftToleranceSeconds = Math.max(0.25, 4 / streamRateHz);
      const pausedDriftToleranceSeconds = 0.5 / streamRateHz;
      const clockUpdate = () => ({
        reason: "tick" as const,
        playing: clock.isPlaying(),
        rate: clock.playbackRate(),
      });
      const requestPlayback = () => {
        if (
          playRequested ||
          !video.paused ||
          performance.now() - lastPlayAttemptAt < 500
        )
          return;
        lastPlayAttemptAt = performance.now();
        playRequested = true;
        try {
          void video.play().catch(() => {
            playRequested = false;
            // Refreshing an expiring direct MP4 URL can interrupt play(). The
            // next media-ready event retries without surfacing a false failure.
          });
        } catch {
          playRequested = false;
          // Media implementations may throw synchronously. A later
          // loadedmetadata/canplay event is the safe retry boundary.
        }
      };
      const synchronizeVideo = (
        ns: string,
        update: PlaybackClockUpdate,
        forceSeek = false,
      ) => {
        const restarted =
          update.reason === "play" && BigInt(ns) < BigInt(latestClockNs);
        latestClockNs = ns;
        if (!mediaAttached) return;
        const clockSeconds =
          sourceStartSeconds +
          Number(BigInt(ns) - BigInt(clock.startNs)) / 1_000_000_000;
        if (!Number.isFinite(clockSeconds)) return;
        const duration = Math.min(video.duration, sourceEndSeconds);
        const seconds =
          Number.isFinite(duration) &&
          clockSeconds >= duration - 0.5 / streamRateHz
            ? Math.max(0, duration - 1 / streamRateHz)
            : clockSeconds;

        if (Math.abs(video.playbackRate - update.rate) > 0.001)
          video.playbackRate = update.rate;

        const currentTime = video.currentTime;
        const drift = Number.isFinite(currentTime)
          ? Math.abs(currentTime - seconds)
          : Number.POSITIVE_INFINITY;
        const directClockMove =
          forceSeek ||
          restarted ||
          update.reason === "subscribe" ||
          update.reason === "seek";
        const driftTolerance = update.playing
          ? playbackDriftToleranceSeconds
          : pausedDriftToleranceSeconds;
        const mayCorrect =
          directClockMove ||
          (!video.seeking && !waiting && video.readyState >= 2);
        const hardDrift = update.playing
          ? Math.max(0.75, driftTolerance)
          : driftTolerance;
        if (
          mayCorrect &&
          drift > (directClockMove ? 0.001 : hardDrift) &&
          (directClockMove || performance.now() - lastHardSeekAt >= 1_000)
        ) {
          try {
            video.currentTime = seconds;
            lastHardSeekAt = performance.now();
          } catch {
            // Metadata may not be available yet; the media-ready event retries.
          }
        } else if (
          mayCorrect &&
          update.playing &&
          drift > driftTolerance &&
          !directClockMove
        ) {
          video.playbackRate = Math.max(
            0.1,
            update.rate * (currentTime < seconds ? 1.05 : 0.95),
          );
        }

        if (update.playing) requestPlayback();
        else {
          playRequested = false;
          lastPlayAttemptAt = Number.NEGATIVE_INFINITY;
          if (!video.paused) video.pause();
        }
      };
      const synchronizeWhenReady = () => {
        playRequested = false;
        waiting = false;
        synchronizeVideo(latestClockNs, clockUpdate(), initialPositionPending);
        if (mediaAttached) initialPositionPending = false;
      };
      video.addEventListener("loadedmetadata", synchronizeWhenReady);
      video.addEventListener("canplay", synchronizeWhenReady);
      resources.add(() => {
        video.removeEventListener("loadedmetadata", synchronizeWhenReady);
        video.removeEventListener("canplay", synchronizeWhenReady);
      });
      const installMedia = async (
        refresh: boolean,
        reason: "initial" | "expiry" | "error" = "initial",
      ): Promise<void> => {
        if (expiryRefreshTimer !== undefined) {
          window.clearTimeout(expiryRefreshTimer);
          expiryRefreshTimer = undefined;
        }
        let descriptor;
        try {
          descriptor = await (refresh
            ? stream.mediaSource!.refresh(controller.signal)
            : stream.mediaSource!.authorize(controller.signal));
        } catch (cause) {
          if (
            !refresh &&
            !mediaErrorRefreshUsed &&
            isSignedResourceExpired(cause)
          ) {
            mediaErrorRefreshUsed = true;
            return installMedia(true, "error");
          }
          throw cause;
        }
        if (controller.signal.aborted) {
          descriptor.revoke?.();
          return;
        }
        mediaAttached = false;
        sourceStartSeconds =
          descriptor.mediaStartSeconds ?? stream.mediaStartSeconds ?? 0;
        sourceEndSeconds =
          descriptor.mediaEndSeconds ??
          stream.mediaEndSeconds ??
          Number.POSITIVE_INFINITY;
        initialPositionPending = true;
        playRequested = false;
        detachMedia?.();
        revokeDescriptor?.();
        revokeDescriptor = descriptor.revoke;
        detachMedia = await attachAuthorizedMedia(
          video,
          resources.trackObjectUrl(descriptor.url),
          (cause) => {
            if (controller.signal.aborted) return;
            if (!mediaErrorRefreshUsed) {
              mediaErrorRefreshUsed = true;
              void installMedia(true, "error").catch(fail);
              return;
            }
            fail(cause);
          },
        );
        if (controller.signal.aborted) {
          detachMedia();
          detachMedia = undefined;
          revokeDescriptor?.();
          revokeDescriptor = undefined;
          return;
        }
        mediaAttached = true;
        if (reason !== "error") mediaErrorRefreshUsed = false;
        synchronizeVideo(latestClockNs, clockUpdate(), true);
        const expiresAt = Date.parse(descriptor.expiresAt);
        if (Number.isFinite(expiresAt)) {
          const refreshDelay = Math.max(0, expiresAt - Date.now() - 30_000);
          expiryRefreshTimer = window.setTimeout(
            () => {
              expiryRefreshTimer = undefined;
              if (!controller.signal.aborted)
                void installMedia(true, "expiry").catch(fail);
            },
            Math.min(refreshDelay, 2_147_483_647),
          );
        }
      };
      // Defer the first authorization past React StrictMode's synchronous
      // mount/cleanup probe. The discarded effect is aborted before it can
      // launch a duplicate authorization request.
      queueMicrotask(() => {
        if (!controller.signal.aborted) void installMedia(false).catch(fail);
      });
      resources.add(() => {
        if (expiryRefreshTimer !== undefined)
          window.clearTimeout(expiryRefreshTimer);
        detachMedia?.();
        revokeDescriptor?.();
      });
      resources.add(
        clock.subscribe((ns, update) => synchronizeVideo(ns, update)),
      );
    }

    if (canvasRef.current) {
      const canvas = canvasRef.current;
      const context = canvas.getContext("2d");
      drawCanvas = (ns) => {
        if (!context) return;
        drawViewerWindowCanvas({
          canvas,
          context,
          payload: latestPayload,
          currentNs: ns,
          startNs: clock.startNs,
          endNs: clock.endNs,
        });
      };
      resources.add(
        clock.subscribe((ns) => {
          drawCanvas?.(ns);
        }),
      );
    }

    if (stream.windowSource) {
      const fiveSeconds = 5_000_000_000n;
      const guardBand = 2_000_000_000n;
      let loadedStart: bigint | null = null;
      let loadedEnd: bigint | null = null;
      let retryAfter = 0;
      let failures = 0;
      let inFlight: AbortController | null = null;
      const loadAround = (ns: string) => {
        if (performance.now() < retryAfter) return;
        const current = BigInt(ns);
        if (loadedStart !== null && loadedEnd !== null) {
          const safeStart =
            loadedStart === BigInt(clock.startNs)
              ? loadedStart
              : loadedStart + guardBand;
          const safeEnd =
            loadedEnd === BigInt(clock.endNs)
              ? loadedEnd
              : loadedEnd - guardBand;
          if (current >= safeStart && current < safeEnd) return;
        }
        const start =
          current > BigInt(clock.startNs) + fiveSeconds
            ? current - fiveSeconds
            : BigInt(clock.startNs);
        const end =
          current + fiveSeconds < BigInt(clock.endNs)
            ? current + fiveSeconds
            : BigInt(clock.endNs);
        inFlight?.abort();
        const windowController = new AbortController();
        inFlight = windowController;
        const stop = () => windowController.abort();
        controller.signal.addEventListener("abort", stop, { once: true });
        const requestGeneration = ++generation;
        loadedStart = start;
        loadedEnd = end;
        stream
          .windowSource!.loadWindow(
            { startNs: start.toString(), endNs: end.toString(), lod: 1 },
            windowController.signal,
          )
          .then((payload) => {
            if (controller.signal.aborted || requestGeneration !== generation) {
              payload.dispose?.();
              return;
            }
            failures = 0;
            retryAfter = 0;
            latestPayloadDispose?.();
            latestPayloadDispose = payload.dispose;
            latestPayload = payload;
            setWindowSummary(viewerWindowSummary(payload));
            drawCanvas?.(clock.currentNs());
          })
          .catch((cause) => {
            if (requestGeneration === generation) {
              loadedStart = null;
              loadedEnd = null;
              failures += 1;
              retryAfter =
                performance.now() +
                Math.min(30_000, 500 * 2 ** Math.min(failures - 1, 6));
            }
            if (!controller.signal.aborted && !windowController.signal.aborted)
              fail(cause);
          })
          .finally(() => controller.signal.removeEventListener("abort", stop));
      };
      resources.add(clock.subscribe(loadAround));
      resources.add(() => inFlight?.abort());
    }

    return () => {
      generation += 1;
      controller.abort();
      latestPayload = null;
      latestPayloadDispose?.();
      resources.dispose();
    };
  }, [clock, panel.kind, panel.state, retryKey, stream, visible]);

  const pending = panel.state === "pending";
  const unavailable =
    panel.state === "missing" || panel.state === "unsupported";
  const cameraSlotPlaceholder =
    stream.semanticRole === "camera-slot-placeholder";
  const mediaUnauthorized =
    (panel.kind === "video" || panel.kind === "depth") && !stream.mediaSource;
  return (
    <article
      ref={hostRef}
      className="viewer-panel"
      data-panel-kind={panel.kind}
      data-panel-state={panel.state}
      data-resource-error={error ? true : undefined}
      data-panel-visible={visible || undefined}
      aria-labelledby={`${panel.panelId}-title`}
    >
      <header>
        <h3 id={`${panel.panelId}-title`}>{panel.title}</h3>
        <span>
          {cameraSlotPlaceholder ? "未接入" : panelStateLabel(panel.state)}
        </span>
      </header>
      {error ? (
        <div className="viewer-panel__error" role="alert">
          <span>资源加载失败：{error.message}。其他面板仍可使用。</span>
          {error.retryable ? (
            <button
              type="button"
              onClick={() => setRetryKey((current) => current + 1)}
            >
              <RotateCcw aria-hidden="true" size={14} />
              重试此面板
            </button>
          ) : null}
        </div>
      ) : null}
      {pending ? (
        <div role="status">Preview 生成中，其他面板可继续使用。</div>
      ) : null}
      {unavailable ? (
        cameraSlotPlaceholder ? (
          <div className="viewer-camera-slot-placeholder" role="note">
            <VideoOff aria-hidden="true" size={22} />
            <strong>等待摄像头接入</strong>
            <small>接入后将在此处同步显示</small>
          </div>
        ) : (
          <div className="viewer-panel__unavailable" role="note">
            {panel.state === "missing"
              ? (stream.accessibleSummary ??
                "数据清单已声明此相机，但当前媒体流缺失。")
              : "此 Stream 当前不支持。"}
            <code>
              {stream.schema.id}@{stream.schema.version}
            </code>
          </div>
        )
      ) : null}
      {mediaUnauthorized && !unavailable ? (
        <div className="viewer-resource-unavailable" role="status">
          <span aria-hidden="true">!</span>
          <strong>媒体资源不可用</strong>
          <small>未返回授权媒体 descriptor</small>
        </div>
      ) : null}
      {(panel.kind === "video" || panel.kind === "depth") &&
      !unavailable &&
      !mediaUnauthorized ? (
        <video
          ref={videoRef}
          muted
          playsInline
          preload="auto"
          aria-label={`${panel.title} 媒体`}
        />
      ) : null}
      {panel.kind !== "video" && panel.kind !== "depth" && !unavailable ? (
        <>
          <canvas
            ref={canvasRef}
            aria-describedby={`${panel.panelId}-summary`}
            aria-label={`${panel.title} 可视化`}
          />
          <p className="sr-only" id={`${panel.panelId}-summary`}>
            {windowSummary ??
              stream.accessibleSummary ??
              `${panel.title} 与共享时间光标同步；精确值请查看时间轴文字摘要。`}
          </p>
        </>
      ) : null}
    </article>
  );
}

function panelStateLabel(state: ViewerPanelSpec["state"]): string {
  const labels: Readonly<Record<ViewerPanelSpec["state"], string>> = {
    ready: "流已就绪",
    pending: "慢流加载中",
    partial: "存在缺帧",
    unsupported: "格式不支持",
    missing: "流缺失",
  };
  return labels[state];
}

export function ViewerRobotPosePanel({
  scene,
  unavailableReason,
}: {
  readonly scene?: EpisodeWorkbenchCoreProps["robotScene"];
  readonly unavailableReason?: string;
}): JSX.Element {
  if (!scene)
    return (
      <article
        className="viewer-panel viewer-robot-panel"
        data-panel-kind="robot-scene"
        data-panel-state="unavailable"
        aria-labelledby="robot-scene-panel-title"
      >
        <header>
          <h3 id="robot-scene-panel-title">机器人姿态</h3>
          <span>共享时间轴</span>
        </header>
        <div className="viewer-robot-placeholder" role="status">
          <span aria-hidden="true">
            <Bot size={26} />
          </span>
          <strong>3D 姿态暂不可用</strong>
          <small>
            {unavailableReason ?? "当前任务没有可用的机器人姿态数据。"}
          </small>
        </div>
      </article>
    );
  const {
    title = "机器人 URDF",
    canonicalPath = "robot/model/urdf",
    ...sceneProps
  } = scene;
  return (
    <article
      className="viewer-panel viewer-robot-panel"
      data-panel-kind="robot-scene"
      aria-labelledby="robot-scene-panel-title"
    >
      <header>
        <h3 id="robot-scene-panel-title">{title}</h3>
        <span>{canonicalPath}</span>
      </header>
      <RobotSceneCore {...sceneProps} />
    </article>
  );
}

const jointCurveColors = [
  "#2f6fed",
  "#8b5cf6",
  "#d97706",
  "#0891b2",
  "#db2777",
  "#4f46e5",
  "#0f766e",
  "#b45309",
  "#9333ea",
  "#0369a1",
  "#c2417b",
  "#475569",
  "#6d5bd0",
  "#0e7490",
] as const;

interface JointCurveWindowState {
  readonly status: "idle" | "loading" | "ready" | "error";
  readonly payload: ViewerWindowPayload | null;
  readonly startNs: string;
  readonly endNs: string;
  readonly error: DomainError | null;
}

function clampTimelinePosition(
  valueNs: string,
  startNs: string,
  endNs: string,
): number {
  const start = BigInt(startNs);
  const end = BigInt(endNs);
  if (end <= start) return 0;
  const value = BigInt(valueNs);
  if (value <= start) return 0;
  if (value >= end) return 1;
  return Number(((value - start) * 100_000n) / (end - start)) / 100_000;
}

function smoothJointPath(points: readonly { x: number; y: number }[]): string {
  if (!points.length) return "";
  if (points.length === 1) return `M ${points[0]!.x} ${points[0]!.y}`;
  let path = `M ${points[0]!.x} ${points[0]!.y}`;
  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1]!;
    const point = points[index]!;
    const middleX = (previous.x + point.x) / 2;
    path += ` C ${middleX} ${previous.y}, ${middleX} ${point.y}, ${point.x} ${point.y}`;
  }
  return path;
}

function secondsLabel(ns: string): string {
  return `${(Number(BigInt(ns)) / 1_000_000_000).toFixed(1)}s`;
}

export function ViewerJointAngleCurvePanel({
  clock,
  stream,
  unavailableReason,
  onResourceError,
}: {
  readonly clock: PlaybackClock;
  readonly stream?: StreamDescriptor | null;
  readonly unavailableReason?: string;
  readonly onResourceError?: (error: DomainError) => void;
}): JSX.Element {
  const [retryKey, setRetryKey] = useState(0);
  const cursorNs = useClockText(clock, 10);
  const [windowState, setWindowState] = useState<JointCurveWindowState>({
    status: "idle",
    payload: null,
    startNs: clock.startNs,
    endNs: clock.endNs,
    error: null,
  });

  useEffect(() => {
    const source = stream?.windowSource;
    if (!source) {
      setWindowState({
        status: "idle",
        payload: null,
        startNs: clock.startNs,
        endNs: clock.endNs,
        error: null,
      });
      return;
    }
    const controller = new AbortController();
    const fourSeconds = 4_000_000_000n;
    const guardBand = 1_000_000_000n;
    let loadedStart: bigint | null = null;
    let loadedEnd: bigint | null = null;
    let loadGeneration = 0;
    let payloadDispose: (() => void) | undefined;
    let inFlight: AbortController | null = null;
    let pendingStart: bigint | null = null;
    let pendingEnd: bigint | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const insideSafeWindow = (current: bigint, start: bigint, end: bigint) => {
      const safeStart =
        start === BigInt(clock.startNs) ? start : start + guardBand;
      const safeEnd = end === BigInt(clock.endNs) ? end : end - guardBand;
      return current >= safeStart && current < safeEnd;
    };

    const clearRetryTimer = () => {
      if (retryTimer === null) return;
      clearTimeout(retryTimer);
      retryTimer = null;
    };

    const loadWindow = (start: bigint, end: bigint, attempt: number) => {
      clearRetryTimer();
      inFlight?.abort();
      const loadController = new AbortController();
      inFlight = loadController;
      pendingStart = start;
      pendingEnd = end;
      const stop = () => loadController.abort();
      controller.signal.addEventListener("abort", stop, { once: true });
      const generation = ++loadGeneration;
      setWindowState((currentState) => ({
        ...currentState,
        status: currentState.payload ? "ready" : "loading",
        error: null,
      }));
      source
        .loadWindow(
          { startNs: start.toString(), endNs: end.toString(), lod: 1 },
          loadController.signal,
        )
        .then((payload) => {
          if (controller.signal.aborted || generation !== loadGeneration) {
            payload.dispose?.();
            return;
          }
          payloadDispose?.();
          payloadDispose = payload.dispose;
          loadedStart = start;
          loadedEnd = end;
          pendingStart = null;
          pendingEnd = null;
          setWindowState({
            status: "ready",
            payload,
            startNs: start.toString(),
            endNs: end.toString(),
            error: null,
          });
        })
        .catch((cause) => {
          if (
            controller.signal.aborted ||
            loadController.signal.aborted ||
            generation !== loadGeneration
          )
            return;
          pendingStart = null;
          pendingEnd = null;
          const error = isDomainError(cause)
            ? cause
            : createDomainError({
                code: "SERVER_ERROR",
                message:
                  cause instanceof Error
                    ? cause.message
                    : "关节角窗口读取失败。",
                fieldErrors: [],
                operationErrors: [],
                blockedReasons: [],
                requestId: null,
                retryable: true,
                httpStatus: null,
              });
          if (error.retryable && attempt < 2) {
            const delayMs = attempt === 0 ? 400 : 1_200;
            pendingStart = start;
            pendingEnd = end;
            setWindowState((currentState) => ({
              ...currentState,
              status: currentState.payload ? "ready" : "loading",
              error,
            }));
            retryTimer = setTimeout(() => {
              retryTimer = null;
              if (!controller.signal.aborted)
                loadWindow(start, end, attempt + 1);
            }, delayMs);
            return;
          }
          // Block clock ticks in the failed window from creating a request
          // storm. A manual retry or a seek outside this window can try again.
          pendingStart = start;
          pendingEnd = end;
          setWindowState((currentState) =>
            currentState.payload
              ? { ...currentState, status: "ready", error }
              : {
                  status: "error",
                  payload: null,
                  startNs: start.toString(),
                  endNs: end.toString(),
                  error,
                },
          );
          onResourceError?.(error);
        })
        .finally(() => {
          controller.signal.removeEventListener("abort", stop);
          if (inFlight === loadController) inFlight = null;
        });
    };

    const loadAround = (ns: string) => {
      const current = BigInt(ns);
      if (
        loadedStart !== null &&
        loadedEnd !== null &&
        insideSafeWindow(current, loadedStart, loadedEnd)
      )
        return;
      if (
        pendingStart !== null &&
        pendingEnd !== null &&
        insideSafeWindow(current, pendingStart, pendingEnd)
      )
        return;
      const start =
        current > BigInt(clock.startNs) + fourSeconds
          ? current - fourSeconds
          : BigInt(clock.startNs);
      const end =
        current + fourSeconds < BigInt(clock.endNs)
          ? current + fourSeconds
          : BigInt(clock.endNs);
      if (end <= start) return;
      loadWindow(start, end, 0);
    };
    const unsubscribe = clock.subscribe(loadAround);
    return () => {
      loadGeneration += 1;
      clearRetryTimer();
      controller.abort();
      inFlight?.abort();
      unsubscribe();
      payloadDispose?.();
    };
  }, [clock, onResourceError, retryKey, stream]);

  const chart = useMemo(() => {
    const payload = windowState.payload;
    const values = payload?.values ?? [];
    if (!values.length) return null;
    const dimension = Math.min(
      64,
      values.reduce((largest, sample) => Math.max(largest, sample.length), 0),
    );
    const finiteValues = values.flatMap((sample) =>
      sample
        .slice(0, dimension)
        .filter((value) => typeof value === "number" && Number.isFinite(value)),
    );
    if (!dimension || !finiteValues.length) return null;
    const rawLow = finiteValues.reduce(
      (low, value) => Math.min(low, value),
      Infinity,
    );
    const rawHigh = finiteValues.reduce(
      (high, value) => Math.max(high, value),
      -Infinity,
    );
    const padding = Math.max(0.08, (rawHigh - rawLow) * 0.08);
    const low = rawLow - padding;
    const high = rawHigh + padding;
    const span = high - low || 1;
    const plot = { left: 52, right: 592, top: 14, bottom: 188 };
    const stride = Math.max(1, Math.ceil(values.length / 260));
    const paths = Array.from({ length: dimension }, (_, seriesIndex) => {
      const points = values.flatMap((sample, sampleIndex) => {
        if (sampleIndex % stride !== 0 && sampleIndex !== values.length - 1)
          return [];
        const value = sample[seriesIndex];
        if (typeof value !== "number" || !Number.isFinite(value)) return [];
        const timestamp =
          payload?.timestampsNs[sampleIndex] ?? windowState.startNs;
        const position = clampTimelinePosition(
          timestamp,
          windowState.startNs,
          windowState.endNs,
        );
        return [
          {
            x: plot.left + position * (plot.right - plot.left),
            y: plot.bottom - ((value - low) / span) * (plot.bottom - plot.top),
          },
        ];
      });
      return smoothJointPath(points);
    });
    return { dimension, high, low, paths, plot };
  }, [windowState.endNs, windowState.payload, windowState.startNs]);

  const sampleTimes = useMemo(
    () =>
      (windowState.payload?.timestampsNs ?? []).map((value) => BigInt(value)),
    [windowState.payload],
  );
  const nearestSampleIndex = useMemo(() => {
    if (!sampleTimes.length) return -1;
    const current = BigInt(cursorNs);
    let low = 0;
    let high = sampleTimes.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (sampleTimes[middle]! < current) low = middle + 1;
      else high = middle;
    }
    if (low === 0) return 0;
    if (low === sampleTimes.length) return low - 1;
    return current - sampleTimes[low - 1]! <= sampleTimes[low]! - current
      ? low - 1
      : low;
  }, [cursorNs, sampleTimes]);

  const series = windowState.payload?.series ?? [];
  const currentValues =
    nearestSampleIndex >= 0
      ? (windowState.payload?.values?.[nearestSampleIndex] ?? [])
      : [];
  const cursorX = chart
    ? chart.plot.left +
      clampTimelinePosition(cursorNs, windowState.startNs, windowState.endNs) *
        (chart.plot.right - chart.plot.left)
    : 0;
  const unavailable =
    !stream || stream.availability === "missing" || !stream.windowSource;

  return (
    <article
      className="viewer-joint-curves"
      data-state={unavailable ? "unavailable" : windowState.status}
      aria-labelledby="joint-angle-curves-title"
    >
      <header>
        <span>
          <Activity aria-hidden="true" size={15} />
          <strong id="joint-angle-curves-title">关节角变化</strong>
        </span>
        <small>{stream?.canonicalPath ?? "共享时间轴"}</small>
      </header>
      {unavailable ? (
        <div className="viewer-joint-curves__empty" role="status">
          <Activity aria-hidden="true" size={24} />
          <strong>关节曲线暂不可用</strong>
          <span>
            {unavailableReason ??
              stream?.accessibleSummary ??
              "当前任务未发现可读取的关节角 Topic。"}
          </span>
        </div>
      ) : windowState.status === "loading" ? (
        <div className="viewer-joint-curves__empty" role="status">
          <span className="viewer-joint-curves__loading" aria-hidden="true" />
          <strong>正在读取关节角</strong>
          <span>从固定 Lance 版本加载当前光标附近的真实样本。</span>
        </div>
      ) : windowState.status === "error" ? (
        <div className="viewer-joint-curves__empty" role="alert">
          <Activity aria-hidden="true" size={24} />
          <strong>关节角读取失败</strong>
          <span>{windowState.error?.message ?? "请稍后重试。"}</span>
          {windowState.error?.retryable ? (
            <button type="button" onClick={() => setRetryKey((key) => key + 1)}>
              <RotateCcw aria-hidden="true" size={13} />
              重试
            </button>
          ) : null}
        </div>
      ) : chart ? (
        <div className="viewer-joint-curves__body">
          <svg
            aria-label={`关节角时间序列，共 ${chart.dimension} 个关节，单位弧度，当前时间 ${secondsLabel(cursorNs)}`}
            className="viewer-joint-curves__chart"
            role="img"
            viewBox="0 0 606 216"
          >
            <g className="viewer-joint-curves__grid">
              {Array.from({ length: 5 }, (_, index) => {
                const x =
                  chart.plot.left +
                  (index / 4) * (chart.plot.right - chart.plot.left);
                return (
                  <line
                    key={`x-${index}`}
                    x1={x}
                    x2={x}
                    y1={chart.plot.top}
                    y2={chart.plot.bottom}
                  />
                );
              })}
              {Array.from({ length: 5 }, (_, index) => {
                const y =
                  chart.plot.top +
                  (index / 4) * (chart.plot.bottom - chart.plot.top);
                return (
                  <line
                    key={`y-${index}`}
                    x1={chart.plot.left}
                    x2={chart.plot.right}
                    y1={y}
                    y2={y}
                  />
                );
              })}
            </g>
            <text x="6" y={chart.plot.top + 4}>
              {chart.high.toFixed(2)}
            </text>
            <text x="6" y={(chart.plot.top + chart.plot.bottom) / 2 + 4}>
              rad
            </text>
            <text x="6" y={chart.plot.bottom + 4}>
              {chart.low.toFixed(2)}
            </text>
            <text x={chart.plot.left} y="207">
              {secondsLabel(windowState.startNs)}
            </text>
            <text x={chart.plot.right} y="207" textAnchor="end">
              {secondsLabel(windowState.endNs)}
            </text>
            {chart.paths.map((path, index) => (
              <path
                d={path}
                fill="none"
                key={`joint-path-${index}`}
                stroke={jointCurveColors[index % jointCurveColors.length]}
                strokeDasharray={
                  index % 4 === 1
                    ? "5 3"
                    : index % 4 === 2
                      ? "2 3"
                      : index % 4 === 3
                        ? "7 2 2 2"
                        : undefined
                }
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="1.6"
                vectorEffect="non-scaling-stroke"
              />
            ))}
            <line
              className="viewer-joint-curves__cursor"
              x1={cursorX}
              x2={cursorX}
              y1={chart.plot.top}
              y2={chart.plot.bottom}
            />
          </svg>
          <div
            className="viewer-joint-curves__legend"
            aria-label="关节角图例和当前值"
          >
            {Array.from({ length: chart.dimension }, (_, index) => (
              <span key={series[index]?.id ?? `joint-${index + 1}`}>
                <i
                  aria-hidden="true"
                  data-line-style={index % 4}
                  style={{
                    backgroundColor:
                      jointCurveColors[index % jointCurveColors.length],
                  }}
                />
                <b title={series[index]?.displayName ?? `J${index + 1}`}>
                  {series[index]?.displayName ?? `J${index + 1}`}
                </b>
                <code>
                  {typeof currentValues[index] === "number"
                    ? `${currentValues[index]!.toFixed(2)} ${series[index]?.unit ?? "rad"}`
                    : "—"}
                </code>
              </span>
            ))}
          </div>
          <p className="viewer-joint-curves__summary">
            {windowState.payload?.values?.length.toLocaleString("zh-CN")}{" "}
            个真实样本 · 8 秒滑动窗口 · 橙色竖线为共享光标
            {windowState.error ? " · 最近一次刷新失败，已保留上一窗口" : ""}
          </p>
        </div>
      ) : (
        <div className="viewer-joint-curves__empty" role="status">
          <Activity aria-hidden="true" size={24} />
          <strong>当前窗口没有数值样本</strong>
          <span>拖动共享光标后将重新读取附近的关节角窗口。</span>
        </div>
      )}
    </article>
  );
}

function OverlayHost({
  renderer,
  clock,
}: {
  renderer: OverlayRenderer;
  clock: PlaybackClock;
}): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const controller = new AbortController();
    const mounted = renderer.mount({
      container: ref.current,
      clock,
      signal: controller.signal,
    });
    return () => {
      controller.abort();
      if (typeof mounted === "function") mounted();
      else mounted?.dispose();
    };
  }, [clock, renderer]);
  return (
    <div
      ref={ref}
      className="viewer-overlay"
      data-overlay-id={renderer.id}
      aria-label={renderer.label}
    />
  );
}

type TimelineDragMode = "create" | "start" | "end" | "move";

interface TimelineDragState {
  pointerId: number;
  mode: TimelineDragMode;
  originClientX: number;
  originNs: bigint;
  initialStart: bigint;
  initialEnd: bigint;
  moved: boolean;
}

function clampNs(value: bigint, low: bigint, high: bigint): bigint {
  return value < low ? low : value > high ? high : value;
}

function timelinePercent(value: bigint, start: bigint, end: bigint): number {
  const clamped = clampNs(value, start, end);
  return Number(((clamped - start) * 100_000n) / (end - start)) / 1000;
}

export function formatElapsedNs(value: bigint, origin: bigint): string {
  const elapsed = value > origin ? value - origin : 0n;
  const centiseconds = (elapsed + 5_000_000n) / 10_000_000n;
  const seconds = centiseconds / 100n;
  const fraction = centiseconds % 100n;
  return `${seconds}.${fraction.toString().padStart(2, "0")}s`;
}

export function SharedSignalTimeline({
  clock,
  disabled = false,
  label = "共享视频时间轴与信号轨道",
  onRangeSelect,
  selection,
  tracks = [],
  variant = "filmstrip",
}: {
  clock: PlaybackClock;
  disabled?: boolean;
  label?: string;
  onRangeSelect?: (start: string, end: string) => void;
  selection?: ViewerTimelineSelection;
  tracks?: readonly ViewerTimelineTrack[];
  variant?: ViewerTimelineVariant;
}): JSX.Element {
  const boundsStart = BigInt(clock.startNs);
  const boundsEnd = BigInt(clock.endNs);
  const duration = boundsEnd - boundsStart;
  const currentText = useClockText(clock, 10);
  const current = BigInt(currentText);
  const filmstripRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<TimelineDragState | null>(null);
  const [dragging, setDragging] = useState<TimelineDragMode | null>(null);
  const [zoom, setZoom] = useState(1);
  const normalizeSelection = useCallback(
    (next?: ViewerTimelineSelection): ViewerTimelineSelection | null => {
      if (!next) return null;
      const start = clampNs(BigInt(next.startNs), boundsStart, boundsEnd - 1n);
      const end = clampNs(BigInt(next.endNs), start + 1n, boundsEnd);
      return {
        startNs: start.toString(),
        endNs: end.toString(),
        ...(next.label ? { label: next.label } : {}),
      };
    },
    [boundsEnd, boundsStart],
  );
  const [preview, setPreviewState] = useState<ViewerTimelineSelection | null>(
    () => normalizeSelection(selection),
  );
  const previewRef = useRef<ViewerTimelineSelection | null>(preview);
  const rangeEditing = Boolean(onRangeSelect) && !disabled;
  const hasPlaybackSegments = tracks.some((track) =>
    track.segments.some((segment) => segment.activatePlayback),
  );

  const setPreview = (next: ViewerTimelineSelection | null) => {
    previewRef.current = next;
    setPreviewState(next);
  };

  useEffect(() => {
    if (dragRef.current) return;
    const next = normalizeSelection(selection);
    previewRef.current = next;
    setPreviewState(next);
  }, [normalizeSelection, selection]);

  const nsFromClientX = (clientX: number): bigint => {
    const rect = filmstripRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return boundsStart;
    const scaled = BigInt(
      Math.max(
        0,
        Math.min(
          1_000_000,
          Math.round(((clientX - rect.left) / rect.width) * 1_000_000),
        ),
      ),
    );
    return boundsStart + (duration * scaled) / 1_000_000n;
  };

  const commitRange = (next: ViewerTimelineSelection | null) => {
    if (!next || !onRangeSelect || disabled) return;
    onRangeSelect(next.startNs, next.endNs);
  };

  const beginDrag = (
    mode: TimelineDragMode,
    event: ReactPointerEvent<HTMLElement>,
  ) => {
    if (!rangeEditing || event.button !== 0) return;
    const point = nsFromClientX(event.clientX);
    const active = previewRef.current;
    if (mode !== "create" && !active) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      mode,
      originClientX: event.clientX,
      originNs: point,
      initialStart: BigInt(active?.startNs ?? point),
      initialEnd: BigInt(
        active?.endNs ?? clampNs(point + 1n, boundsStart + 1n, boundsEnd),
      ),
      moved: false,
    };
    setDragging(mode);
  };

  const updateDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const point = nsFromClientX(event.clientX);
    if (Math.abs(event.clientX - drag.originClientX) >= 3) drag.moved = true;
    let start = drag.initialStart;
    let end = drag.initialEnd;
    if (drag.mode === "create") {
      start = point < drag.originNs ? point : drag.originNs;
      end = point < drag.originNs ? drag.originNs : point;
      if (end <= start) end = clampNs(start + 1n, start + 1n, boundsEnd);
    } else if (drag.mode === "start") {
      start = clampNs(point, boundsStart, drag.initialEnd - 1n);
    } else if (drag.mode === "end") {
      end = clampNs(point, drag.initialStart + 1n, boundsEnd);
    } else {
      const width = drag.initialEnd - drag.initialStart;
      const delta = point - drag.originNs;
      start = drag.initialStart + delta;
      end = drag.initialEnd + delta;
      if (start < boundsStart) {
        start = boundsStart;
        end = boundsStart + width;
      }
      if (end > boundsEnd) {
        end = boundsEnd;
        start = boundsEnd - width;
      }
    }
    setPreview({
      startNs: start.toString(),
      endNs: end.toString(),
      ...(selection?.label ? { label: selection.label } : {}),
    });
  };

  const finishDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (drag.mode === "create" && !drag.moved) {
      const point = clampNs(
        nsFromClientX(event.clientX),
        boundsStart,
        boundsEnd - 1n,
      );
      clock.seek(point.toString());
      setPreview(normalizeSelection(selection));
    } else {
      const next = previewRef.current;
      commitRange(next);
      if (next)
        clock.seek(
          drag.mode === "start"
            ? next.startNs
            : (BigInt(next.endNs) - 1n).toString(),
        );
    }
    dragRef.current = null;
    setDragging(null);
  };

  const cancelDrag = () => {
    dragRef.current = null;
    setDragging(null);
    setPreview(normalizeSelection(selection));
  };

  const adjustBoundary = (
    boundary: "start" | "end",
    event: ReactKeyboardEvent<HTMLButtonElement>,
  ) => {
    if (
      !preview ||
      !rangeEditing ||
      !["ArrowLeft", "ArrowRight"].includes(event.key)
    )
      return;
    event.preventDefault();
    const baseStep = duration / 1000n > 1n ? duration / 1000n : 1n;
    const step = (event.shiftKey ? 10n : 1n) * baseStep;
    const direction = event.key === "ArrowLeft" ? -step : step;
    const start = BigInt(preview.startNs);
    const end = BigInt(preview.endNs);
    const next =
      boundary === "start"
        ? {
            ...preview,
            startNs: clampNs(
              start + direction,
              boundsStart,
              end - 1n,
            ).toString(),
          }
        : {
            ...preview,
            endNs: clampNs(end + direction, start + 1n, boundsEnd).toString(),
          };
    setPreview(next);
    commitRange(next);
  };

  const setBoundaryAtPlayhead = (boundary: "start" | "end") => {
    if (!rangeEditing) return;
    const fallbackWidth = duration / 20n > 0n ? duration / 20n : 1n;
    const existingStart = BigInt(preview?.startNs ?? currentText);
    const existingEnd = BigInt(
      preview?.endNs ??
        clampNs(current + fallbackWidth, boundsStart + 1n, boundsEnd),
    );
    const next =
      boundary === "start"
        ? {
            startNs: clampNs(current, boundsStart, existingEnd - 1n).toString(),
            endNs: existingEnd.toString(),
            ...(selection?.label ? { label: selection.label } : {}),
          }
        : {
            startNs: existingStart.toString(),
            endNs: clampNs(
              current + 1n,
              existingStart + 1n,
              boundsEnd,
            ).toString(),
            ...(selection?.label ? { label: selection.label } : {}),
          };
    setPreview(next);
    commitRange(next);
  };

  const selectionStyle: CSSProperties | undefined = preview
    ? {
        left: `${timelinePercent(BigInt(preview.startNs), boundsStart, boundsEnd)}%`,
        width: `${Math.max(0.15, timelinePercent(BigInt(preview.endNs), boundsStart, boundsEnd) - timelinePercent(BigInt(preview.startNs), boundsStart, boundsEnd))}%`,
      }
    : undefined;
  const playheadStyle: CSSProperties = {
    left: `${timelinePercent(current, boundsStart, boundsEnd)}%`,
  };

  return (
    <section
      className="viewer-timeline"
      aria-label={label}
      data-dragging={dragging ?? undefined}
      data-variant={variant}
    >
      <header className="viewer-timeline__toolbar">
        <div className="viewer-timeline__readout">
          <strong>{formatElapsedNs(current, boundsStart)}</strong>
          <span>
            {preview
              ? `选区 ${formatElapsedNs(BigInt(preview.endNs) - BigInt(preview.startNs), 0n)}`
              : variant === "signals"
                ? "全部相机、关节、动作与质检共享此光标"
                : "拖拽画面条创建标注区间"}
          </span>
        </div>
        <div className="viewer-timeline__tools" aria-label="时间轴工具">
          <button
            type="button"
            disabled={!rangeEditing}
            onClick={() => setBoundaryAtPlayhead("start")}
          >
            设为入点
          </button>
          <button
            type="button"
            disabled={!rangeEditing}
            onClick={() => setBoundaryAtPlayhead("end")}
          >
            设为出点
          </button>
          <span className="viewer-timeline__divider" aria-hidden="true" />
          <button
            type="button"
            aria-label="缩小时间轴"
            disabled={zoom === 1}
            onClick={() => setZoom((value) => Math.max(1, value / 2))}
          >
            −
          </button>
          <output aria-label="时间轴缩放">{zoom}x</output>
          <button
            type="button"
            aria-label="放大时间轴"
            disabled={zoom === 8}
            onClick={() => setZoom((value) => Math.min(8, value * 2))}
          >
            ＋
          </button>
        </div>
      </header>
      <div className="viewer-timeline__viewport">
        <div
          className="viewer-timeline__surface"
          style={{ width: `${zoom * 100}%` }}
        >
          <div className="viewer-timeline__ruler" aria-hidden="true">
            {Array.from({ length: 11 }, (_, index) => {
              const at = boundsStart + (duration * BigInt(index)) / 10n;
              return (
                <span key={index} style={{ left: `${index * 10}%` }}>
                  {formatElapsedNs(at, boundsStart)}
                </span>
              );
            })}
          </div>
          <div
            ref={filmstripRef}
            className={
              variant === "signals"
                ? "viewer-timeline__filmstrip viewer-timeline__filmstrip--signals"
                : "viewer-timeline__filmstrip"
            }
            role="slider"
            tabIndex={0}
            aria-label={
              variant === "signals"
                ? "共享播放位置"
                : "播放位置；拖拽可创建标注区间"
            }
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={timelinePercent(current, boundsStart, boundsEnd)}
            aria-valuetext={formatElapsedNs(current, boundsStart)}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                event.preventDefault();
                directSeek(
                  clock,
                  event.key === "ArrowLeft" ? -100_000_000n : 100_000_000n,
                );
              } else if (event.key === "Home" || event.key === "End") {
                event.preventDefault();
                clock.seek(
                  event.key === "Home"
                    ? clock.startNs
                    : (boundsEnd - 1n).toString(),
                );
              }
            }}
            onPointerDown={(event) => {
              if (rangeEditing) beginDrag("create", event);
              else
                clock.seek(
                  clampNs(
                    nsFromClientX(event.clientX),
                    boundsStart,
                    boundsEnd - 1n,
                  ).toString(),
                );
            }}
            onPointerMove={updateDrag}
            onPointerUp={finishDrag}
            onPointerCancel={cancelDrag}
          >
            {variant === "filmstrip" ? (
              <div className="viewer-timeline__frames" aria-hidden="true">
                {Array.from({ length: 16 }, (_, index) => (
                  <span key={index} />
                ))}
              </div>
            ) : (
              <span
                className="viewer-timeline__scrub-line"
                aria-hidden="true"
              />
            )}
            {preview && selectionStyle ? (
              <div
                className="viewer-timeline__selection"
                style={selectionStyle}
              >
                <button
                  type="button"
                  className="viewer-timeline__handle viewer-timeline__handle--start"
                  aria-label={`标注开始 ${formatElapsedNs(BigInt(preview.startNs), boundsStart)}`}
                  aria-valuetext={formatElapsedNs(
                    BigInt(preview.startNs),
                    boundsStart,
                  )}
                  disabled={!rangeEditing}
                  onKeyDown={(event) => adjustBoundary("start", event)}
                  onPointerDown={(event) => beginDrag("start", event)}
                >
                  <span aria-hidden="true" />
                </button>
                <button
                  type="button"
                  className="viewer-timeline__selection-body"
                  disabled={!rangeEditing}
                  aria-label="拖动整个标注区间"
                  onPointerDown={(event) => beginDrag("move", event)}
                >
                  {preview.label || "当前标注"}
                </button>
                <button
                  type="button"
                  className="viewer-timeline__handle viewer-timeline__handle--end"
                  aria-label={`标注结束 ${formatElapsedNs(BigInt(preview.endNs), boundsStart)}`}
                  aria-valuetext={formatElapsedNs(
                    BigInt(preview.endNs),
                    boundsStart,
                  )}
                  disabled={!rangeEditing}
                  onKeyDown={(event) => adjustBoundary("end", event)}
                  onPointerDown={(event) => beginDrag("end", event)}
                >
                  <span aria-hidden="true" />
                </button>
              </div>
            ) : null}
            <div
              className="viewer-timeline__playhead"
              style={playheadStyle}
              aria-hidden="true"
            >
              <span />
            </div>
          </div>
          {tracks.length ? (
            <div className="viewer-timeline__tracks" aria-label="同步信号轨道">
              {tracks.map((track) => (
                <div
                  className="viewer-timeline__track"
                  data-level={track.level}
                  key={track.id}
                >
                  <strong
                    style={{ paddingLeft: `${10 + (track.level ?? 0) * 12}px` }}
                  >
                    {track.label}
                  </strong>
                  <div>
                    {track.segments.length ? (
                      track.segments.map((segment) => {
                        const segmentStart = BigInt(segment.startNs);
                        const segmentEnd = segment.endNs
                          ? BigInt(segment.endNs)
                          : segmentStart + 1n;
                        const left = timelinePercent(
                          segmentStart,
                          boundsStart,
                          boundsEnd,
                        );
                        const isPoint = !segment.endNs;
                        const isCurrent =
                          current >= segmentStart && current < segmentEnd;
                        const width = isPoint
                          ? 0
                          : Math.max(
                              0.25,
                              timelinePercent(
                                BigInt(segment.endNs!),
                                boundsStart,
                                boundsEnd,
                              ) - left,
                            );
                        const className = isPoint
                          ? "viewer-timeline__segment viewer-timeline__segment--point"
                          : "viewer-timeline__segment";
                        const style = {
                          left: `${left}%`,
                          ...(isPoint ? {} : { width: `${width}%` }),
                        };
                        const content = (
                          <>
                            {isPoint ? <i aria-hidden="true" /> : null}
                            {segment.label}
                          </>
                        );
                        return segment.activatePlayback ? (
                          <button
                            aria-current={isCurrent ? "time" : undefined}
                            aria-label={`从“${segment.label}”起点同步播放全部视频和关节数据`}
                            key={segment.id}
                            className={className}
                            data-tone={segment.tone}
                            style={style}
                            title={`从“${segment.label}”起点同步播放全部视频和关节数据`}
                            type="button"
                            onClick={() => {
                              clock.seek(segment.startNs);
                              clock.play();
                            }}
                          >
                            {content}
                          </button>
                        ) : (
                          <span
                            key={segment.id}
                            className={className}
                            data-tone={segment.tone}
                            style={style}
                            title={segment.label}
                          >
                            {content}
                          </span>
                        );
                      })
                    ) : (
                      <em>当前时间范围无可用信号</em>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : null}
          {variant === "signals" ? (
            <div className="viewer-timeline__cursor-layer" aria-hidden="true">
              <div
                className="viewer-timeline__shared-playhead"
                style={playheadStyle}
              >
                <span />
              </div>
            </div>
          ) : null}
          <table className="sr-only">
            <caption>{label}文字摘要</caption>
            <thead>
              <tr>
                <th scope="col">轨道</th>
                <th scope="col">区间与事件</th>
              </tr>
            </thead>
            <tbody>
              {tracks.map((track) => (
                <tr key={track.id}>
                  <th scope="row">{track.label}</th>
                  <td>
                    {track.segments.length
                      ? track.segments
                          .map(
                            (segment) =>
                              `${segment.label} ${formatElapsedNs(BigInt(segment.startNs), boundsStart)}${segment.endNs ? `–${formatElapsedNs(BigInt(segment.endNs), boundsStart)}` : ""}`,
                          )
                          .join("；")
                      : "当前时间范围无可用信号"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <footer className="viewer-timeline__hint">
        {variant === "signals"
          ? `${hasPlaybackSegments ? "点击 Tag 从起点同步播放 · " : ""}方向键微调 0.10s · Home / End 跳转边界 · 只有一条视频播放时间轴`
          : "拖拽空白处新建区间 · 拖动两侧手柄调整入点/出点 · 拖动选区中部整体移动"}
      </footer>
    </section>
  );
}

export interface ViewerMediaSurfaceProps {
  readonly clock: PlaybackClock;
  readonly streams: readonly StreamDescriptor[];
  readonly robotScene?: EpisodeWorkbenchCoreProps["robotScene"];
  readonly robotSceneUnavailableReason?: string;
  readonly overlays?: readonly OverlayRenderer[];
  readonly renderPanel?: ViewerPanelRenderer;
  readonly onResourceError?: EpisodeWorkbenchCoreProps["onResourceError"];
  readonly emptyMessage?: string;
}

export function ViewerMediaSurface({
  clock,
  emptyMessage = "数据清单中未发现相机。关节、动作与质检轨道仍可独立诊断。",
  onResourceError,
  overlays,
  renderPanel,
  robotScene,
  robotSceneUnavailableReason,
  streams,
}: ViewerMediaSurfaceProps): JSX.Element {
  const composition = useMemo(
    () => resolveViewerComposition(streams),
    [streams],
  );
  const byId = useMemo(
    () => new Map(streams.map((stream) => [stream.id, stream])),
    [streams],
  );
  const hasRobotPanel = Boolean(robotScene || robotSceneUnavailableReason);
  const panelCount =
    composition.panels.length +
    (hasRobotPanel ? 1 : 0) +
    (composition.panels.length === 0 ? 1 : 0);
  const cameraCount = composition.panels.length;
  const cameraLayout =
    cameraCount === 0
      ? "empty"
      : cameraCount === 1
        ? "single"
        : cameraCount === 2
          ? "pair"
          : cameraCount <= 4
            ? "quad"
            : "many";

  return (
    <section
      className="viewer-media-surface"
      aria-label="数据清单相机视图"
      data-panel-count={panelCount}
    >
      <div
        className="viewer-media-grid"
        data-camera-count={composition.panels.length}
        data-camera-layout={cameraLayout}
        tabIndex={0}
      >
        {composition.panels.map((panel) => {
          const streamId = panel.streamIds[0];
          const stream = streamId ? byId.get(streamId) : undefined;
          if (!stream) return null;
          const defaultPanel = (
            <StreamPanel
              clock={clock}
              onResourceError={onResourceError}
              panel={panel}
              stream={stream}
            />
          );
          return (
            <div className="viewer-panel-slot" key={panel.panelId}>
              {renderPanel
                ? renderPanel({ panel, stream, clock, defaultPanel })
                : defaultPanel}
            </div>
          );
        })}
        {hasRobotPanel ? (
          <ViewerRobotPosePanel
            scene={robotScene}
            unavailableReason={robotSceneUnavailableReason}
          />
        ) : null}
        {composition.panels.length === 0 ? (
          <div className="viewer-media-empty" role="status">
            {emptyMessage}
          </div>
        ) : null}
      </div>
      {composition.diagnostics.map((diagnostic) => (
        <div role="alert" key={`${diagnostic.streamId}:${diagnostic.code}`}>
          {diagnostic.message}
        </div>
      ))}
      {overlays?.map((overlay) => (
        <OverlayHost key={overlay.id} renderer={overlay} clock={clock} />
      ))}
    </section>
  );
}

export function EpisodeWorkbenchCore(
  p: EpisodeWorkbenchCoreProps,
): JSX.Element {
  const composition = useMemo(
    () => resolveViewerComposition(p.streams),
    [p.streams],
  );
  const byId = useMemo(
    () => new Map(p.streams.map((stream) => [stream.id, stream])),
    [p.streams],
  );
  return (
    <section
      className="episode-workbench-core"
      data-mode={p.mode}
      data-episode-id={p.episodeId}
    >
      <h2 className="sr-only">Episode {p.episodeId} Viewer</h2>
      <ViewerMediaSurface
        clock={p.clock}
        onResourceError={p.onResourceError}
        overlays={p.overlays}
        renderPanel={p.renderPanel}
        robotScene={p.robotScene}
        streams={p.streams}
      />
      {composition.jointGroups.map((group) => {
        const stream = byId.get(group.streamId);
        return (
          <section
            key={group.streamId}
            className="viewer-axis-group"
            aria-labelledby={`${group.streamId}-axis-title`}
            data-axis-count={group.axes.length}
          >
            <header className="viewer-axis-group__header">
              <div className="viewer-axis-group__identity">
                <strong
                  id={`${group.streamId}-axis-title`}
                  className="viewer-axis-group__title"
                >
                  {group.title}
                </strong>
                {stream ? (
                  <code
                    className="viewer-axis-group__path"
                    title={stream.canonicalPath}
                  >
                    {stream.canonicalPath}
                  </code>
                ) : null}
              </div>
              <span className="viewer-axis-group__count">
                {group.axes.length} DOF
              </span>
            </header>
            <div
              className="viewer-axis-group__grid"
              role="list"
              aria-label={`${group.title}关节轴`}
            >
              {group.axes.map((axis, index) => (
                <div
                  key={axis.axisId}
                  className="viewer-axis-card"
                  role="listitem"
                  data-axis-id={axis.axisId}
                  data-mapping-status={axis.mappingStatus}
                  aria-label={`${axis.displayName}，单位 ${axis.unit}`}
                  title={[axis.sourceName, axis.mappedJointName]
                    .filter(Boolean)
                    .join(" · ")}
                >
                  <span className="viewer-axis-card__index" aria-hidden="true">
                    J{String(index + 1).padStart(2, "0")}
                  </span>
                  <strong className="viewer-axis-card__name">
                    {axis.displayName}
                  </strong>
                  <span className="viewer-axis-card__unit">{axis.unit}</span>
                </div>
              ))}
            </div>
          </section>
        );
      })}
      <ViewerPlaybackControls clock={p.clock} />
      <SharedSignalTimeline
        clock={p.clock}
        disabled={p.timelineDisabled}
        label={p.timelineLabel}
        selection={p.timelineSelection}
        tracks={p.timelineTracks}
        variant={p.timelineVariant}
        onRangeSelect={p.onTimeRangeSelect}
      />
    </section>
  );
}
