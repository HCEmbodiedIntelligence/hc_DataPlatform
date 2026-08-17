import { useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, JSX, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent, RefObject } from 'react';
import type { PlaybackClock } from './PlaybackClock';
import { useClockText } from './PlaybackClock';
import { resolveViewerComposition } from './ViewerCompositionResolver';
import { RobotSceneCore } from './RobotSceneCore';
import type { RobotSceneCoreProps } from './RobotSceneCore';
import { ViewerResourceRegistry } from './runtime/ViewerResourceRegistry';
import { retrySignedResourceOnce } from './runtime/signed-resource';
import { createDomainError, isDomainError } from '../../shared/api/domain-error';
import type { DomainError, OverlayRenderer, StreamDescriptor, ViewerPanelSpec, ViewerResourceScope } from './types';
import './EpisodeWorkbenchCore.css';

export interface EpisodeWorkbenchCoreProps {
  episodeId: string;
  datasetId: string;
  versionId: string;
  clock: PlaybackClock;
  mode: 'readonly' | 'annotate' | 'cleaning';
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
  onResourceError?: (e: DomainError, scope: 'video'|'curve'|'pointcloud'|'scene3d') => void;
}

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
  readonly tone?: 'phase' | 'action' | 'object' | 'event' | 'issue';
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
    code: 'SERVER_ERROR',
    message: error instanceof Error ? error.message : 'Viewer resource failed',
    fieldErrors: [],
    operationErrors: [{ code: fallback, message: 'Viewer resource failed' }],
    blockedReasons: [],
    requestId: null,
    retryable: true,
    httpStatus: null,
  });
}

function ClockReadout({ clock }: { clock: PlaybackClock }): JSX.Element {
  const current = useClockText(clock);
  return (
    <output aria-live="off" data-testid="viewer-clock-text" title={`${current} ns`}>
      {formatElapsedNs(BigInt(current), BigInt(clock.startNs))}
      <span aria-hidden="true"> / {formatElapsedNs(BigInt(clock.endNs) - BigInt(clock.startNs), 0n)}</span>
    </output>
  );
}

function directSeek(clock: PlaybackClock, deltaNs: bigint): void {
  clock.seek((BigInt(clock.currentNs()) + deltaNs).toString());
}

function PlaybackControls({ clock }: { clock: PlaybackClock }): JSX.Element {
  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
    const commands: Readonly<Record<string, () => void>> = {
      ' ': () => clock.play(),
      k: () => clock.pause(),
      j: () => directSeek(clock, -1_000_000_000n),
      l: () => directSeek(clock, 1_000_000_000n),
      ArrowLeft: () => directSeek(clock, -100_000_000n),
      ArrowRight: () => directSeek(clock, 100_000_000n),
      Home: () => clock.seek(clock.startNs),
      End: () => clock.seek((BigInt(clock.endNs) - 1n).toString()),
    };
    const command = commands[event.key];
    if (command) { event.preventDefault(); command(); }
  };
  return (
    <div className="viewer-playback-controls" aria-label="播放控制" onKeyDown={onKeyDown}>
      <button type="button" onClick={() => clock.play()} aria-label="播放">▶</button>
      <button type="button" onClick={() => clock.pause()} aria-label="暂停">Ⅱ</button>
      <button type="button" onClick={() => directSeek(clock, -100_000_000n)} aria-label="后退 100 毫秒">−</button>
      <button type="button" onClick={() => directSeek(clock, 100_000_000n)} aria-label="前进 100 毫秒">＋</button>
      <ClockReadout clock={clock} />
      {[0.5, 1, 2].map((rate) => <button type="button" key={rate} onClick={() => clock.setRate(rate)}>{rate}x</button>)}
    </div>
  );
}

function usePanelVisibility(ref: RefObject<HTMLElement | null>): boolean {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry?.isIntersecting ?? false));
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref]);
  return visible;
}

function mediaScope(kind: ViewerPanelSpec['kind']): ViewerResourceScope {
  if (kind === 'video' || kind === 'depth') return 'video';
  if (kind === 'pointcloud-preview') return 'pointcloud';
  return 'curve';
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
  onResourceError?: EpisodeWorkbenchCoreProps['onResourceError'];
}): JSX.Element {
  const hostRef = useRef<HTMLElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const visible = usePanelVisibility(hostRef);
  const [error, setError] = useState<DomainError | null>(null);

  useEffect(() => {
    if (!visible || panel.state === 'missing' || panel.state === 'unsupported') return;
    const controller = new AbortController();
    const resources = new ViewerResourceRegistry();
    let generation = 0;
    let latestPayloadDispose: (() => void) | undefined;
    const fail = (cause: unknown) => {
      const domainError = asDomainError(cause, 'VIEWER_RESOURCE_ERROR');
      setError(domainError);
      onResourceError?.(domainError, mediaScope(panel.kind));
    };

    if (videoRef.current && stream.mediaSource) {
      const video = videoRef.current;
      resources.trackMedia(video);
      retrySignedResourceOnce(
        () => stream.mediaSource!.authorize(controller.signal),
        () => stream.mediaSource!.refresh(controller.signal),
      ).then((descriptor) => {
        if (controller.signal.aborted) { descriptor.revoke?.(); return; }
        resources.add(descriptor.revoke);
        video.src = resources.trackObjectUrl(descriptor.url);
        video.load();
      }).catch(fail);
      resources.add(clock.subscribe((ns) => {
        const seconds = Number((BigInt(ns) - BigInt(clock.startNs)) / 1_000_000n) / 1000;
        if (Number.isFinite(seconds) && Math.abs(video.currentTime - seconds) > 0.08) video.currentTime = seconds;
      }));
    }

    if (canvasRef.current) {
      const canvas = canvasRef.current;
      const context = canvas.getContext('2d');
      resources.add(clock.subscribe((ns) => {
        if (!context) return;
        const width = canvas.clientWidth || 320;
        const height = canvas.clientHeight || 180;
        if (canvas.width !== width) canvas.width = width;
        if (canvas.height !== height) canvas.height = height;
        const start = BigInt(clock.startNs);
        const duration = BigInt(clock.endNs) - start;
        const position = Number(((BigInt(ns) - start) * 10_000n) / duration) / 10_000;
        context.clearRect(0, 0, width, height);
        context.strokeStyle = '#0f8f8b';
        context.beginPath();
        context.moveTo(position * width, 0);
        context.lineTo(position * width, height);
        context.stroke();
      }));
    }

    if (stream.windowSource) {
      const fiveSeconds = 5_000_000_000n;
      const guardBand = 2_000_000_000n;
      let loadedStart: bigint | null = null;
      let loadedEnd: bigint | null = null;
      let inFlight: AbortController | null = null;
      const loadAround = (ns: string) => {
        const current = BigInt(ns);
        if (loadedStart !== null && loadedEnd !== null) {
          const safeStart = loadedStart === BigInt(clock.startNs) ? loadedStart : loadedStart + guardBand;
          const safeEnd = loadedEnd === BigInt(clock.endNs) ? loadedEnd : loadedEnd - guardBand;
          if (current >= safeStart && current < safeEnd) return;
        }
        const start = current > BigInt(clock.startNs) + fiveSeconds ? current - fiveSeconds : BigInt(clock.startNs);
        const end = current + fiveSeconds < BigInt(clock.endNs) ? current + fiveSeconds : BigInt(clock.endNs);
        inFlight?.abort();
        const windowController = new AbortController();
        inFlight = windowController;
        const stop = () => windowController.abort();
        controller.signal.addEventListener('abort', stop, { once: true });
        const requestGeneration = ++generation;
        loadedStart = start;
        loadedEnd = end;
        stream.windowSource!.loadWindow({ startNs: start.toString(), endNs: end.toString(), lod: 1 }, windowController.signal)
          .then((payload) => {
            if (controller.signal.aborted || requestGeneration !== generation) { payload.dispose?.(); return; }
            latestPayloadDispose?.();
            latestPayloadDispose = payload.dispose;
          })
          .catch((cause) => {
            if (requestGeneration === generation) { loadedStart = null; loadedEnd = null; }
            if (!controller.signal.aborted && !windowController.signal.aborted) fail(cause);
          })
          .finally(() => controller.signal.removeEventListener('abort', stop));
      };
      resources.add(clock.subscribe(loadAround));
      resources.add(() => inFlight?.abort());
    }

    return () => {
      generation += 1;
      controller.abort();
      latestPayloadDispose?.();
      resources.dispose();
    };
  }, [clock, onResourceError, panel.kind, panel.state, stream, visible]);

  const pending = panel.state === 'pending';
  const unavailable = panel.state === 'missing' || panel.state === 'unsupported';
  const mediaUnauthorized = (panel.kind === 'video' || panel.kind === 'depth') && !stream.mediaSource;
  return (
    <article ref={hostRef} className="viewer-panel" data-panel-kind={panel.kind} aria-labelledby={`${panel.panelId}-title`}>
      <header><h3 id={`${panel.panelId}-title`}>{panel.title}</h3><span>{panel.state}</span></header>
      {error ? <div role="alert">资源加载失败：{error.message}</div> : null}
      {pending ? <div role="status">Preview 生成中，其他面板可继续使用。</div> : null}
      {unavailable ? <div role="note">此 Stream 当前不支持：{stream.schema.id}@{stream.schema.version}</div> : null}
      {mediaUnauthorized && !unavailable ? (
        <div className="viewer-resource-unavailable" role="status">
          <span aria-hidden="true">!</span>
          <strong>媒体资源不可用</strong>
          <small>未返回授权媒体 descriptor</small>
        </div>
      ) : null}
      {(panel.kind === 'video' || panel.kind === 'depth') && !unavailable && !mediaUnauthorized
        ? <video ref={videoRef} muted playsInline preload="metadata" aria-label={`${panel.title} 媒体`} />
        : null}
      {panel.kind !== 'video' && panel.kind !== 'depth' && !unavailable
        ? <canvas ref={canvasRef} aria-label={`${panel.title} 可视化`} />
        : null}
    </article>
  );
}

function RobotScenePanel({ scene }: { scene: NonNullable<EpisodeWorkbenchCoreProps['robotScene']> }): JSX.Element {
  const { title = '机器人 URDF', canonicalPath = 'robot/model/urdf', ...sceneProps } = scene;
  return (
    <article className="viewer-panel viewer-robot-panel" data-panel-kind="robot-scene" aria-labelledby="robot-scene-panel-title">
      <header>
        <h3 id="robot-scene-panel-title">{title}</h3>
        <span>{canonicalPath}</span>
      </header>
      <RobotSceneCore {...sceneProps} />
    </article>
  );
}

function OverlayHost({ renderer, clock }: { renderer: OverlayRenderer; clock: PlaybackClock }): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const controller = new AbortController();
    const mounted = renderer.mount({ container: ref.current, clock, signal: controller.signal });
    return () => {
      controller.abort();
      if (typeof mounted === 'function') mounted();
      else mounted?.dispose();
    };
  }, [clock, renderer]);
  return <div ref={ref} className="viewer-overlay" data-overlay-id={renderer.id} aria-label={renderer.label} />;
}

type TimelineDragMode = 'create' | 'start' | 'end' | 'move';

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

function formatElapsedNs(value: bigint, origin: bigint): string {
  const elapsed = value > origin ? value - origin : 0n;
  const totalMs = elapsed / 1_000_000n;
  const minutes = totalMs / 60_000n;
  const seconds = (totalMs % 60_000n) / 1_000n;
  const milliseconds = totalMs % 1_000n;
  return `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}.${milliseconds.toString().padStart(3, '0')}`;
}

function ClipTimeline({
  clock,
  disabled = false,
  onRangeSelect,
  selection,
  tracks = [],
}: {
  clock: PlaybackClock;
  disabled?: boolean;
  onRangeSelect?: (start: string, end: string) => void;
  selection?: ViewerTimelineSelection;
  tracks?: readonly ViewerTimelineTrack[];
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
  const normalizeSelection = (next?: ViewerTimelineSelection): ViewerTimelineSelection | null => {
    if (!next) return null;
    const start = clampNs(BigInt(next.startNs), boundsStart, boundsEnd - 1n);
    const end = clampNs(BigInt(next.endNs), start + 1n, boundsEnd);
    return { startNs: start.toString(), endNs: end.toString(), ...(next.label ? { label: next.label } : {}) };
  };
  const [preview, setPreviewState] = useState<ViewerTimelineSelection | null>(() => normalizeSelection(selection));
  const previewRef = useRef<ViewerTimelineSelection | null>(preview);
  const rangeEditing = Boolean(onRangeSelect) && !disabled;

  const setPreview = (next: ViewerTimelineSelection | null) => {
    previewRef.current = next;
    setPreviewState(next);
  };

  useEffect(() => {
    if (dragRef.current) return;
    const next = normalizeSelection(selection);
    previewRef.current = next;
    setPreviewState(next);
  }, [selection?.endNs, selection?.label, selection?.startNs]);

  const nsFromClientX = (clientX: number): bigint => {
    const rect = filmstripRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return boundsStart;
    const scaled = BigInt(Math.max(0, Math.min(1_000_000, Math.round(((clientX - rect.left) / rect.width) * 1_000_000))));
    return boundsStart + (duration * scaled) / 1_000_000n;
  };

  const commitRange = (next: ViewerTimelineSelection | null) => {
    if (!next || !onRangeSelect || disabled) return;
    onRangeSelect(next.startNs, next.endNs);
  };

  const beginDrag = (mode: TimelineDragMode, event: ReactPointerEvent<HTMLElement>) => {
    if (!rangeEditing || event.button !== 0) return;
    const point = nsFromClientX(event.clientX);
    const active = previewRef.current;
    if (mode !== 'create' && !active) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      mode,
      originClientX: event.clientX,
      originNs: point,
      initialStart: BigInt(active?.startNs ?? point),
      initialEnd: BigInt(active?.endNs ?? clampNs(point + 1n, boundsStart + 1n, boundsEnd)),
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
    if (drag.mode === 'create') {
      start = point < drag.originNs ? point : drag.originNs;
      end = point < drag.originNs ? drag.originNs : point;
      if (end <= start) end = clampNs(start + 1n, start + 1n, boundsEnd);
    } else if (drag.mode === 'start') {
      start = clampNs(point, boundsStart, drag.initialEnd - 1n);
    } else if (drag.mode === 'end') {
      end = clampNs(point, drag.initialStart + 1n, boundsEnd);
    } else {
      const width = drag.initialEnd - drag.initialStart;
      const delta = point - drag.originNs;
      start = drag.initialStart + delta;
      end = drag.initialEnd + delta;
      if (start < boundsStart) { start = boundsStart; end = boundsStart + width; }
      if (end > boundsEnd) { end = boundsEnd; start = boundsEnd - width; }
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
    if (drag.mode === 'create' && !drag.moved) {
      const point = clampNs(nsFromClientX(event.clientX), boundsStart, boundsEnd - 1n);
      clock.seek(point.toString());
      setPreview(normalizeSelection(selection));
    } else {
      const next = previewRef.current;
      commitRange(next);
      if (next) clock.seek(drag.mode === 'start' ? next.startNs : (BigInt(next.endNs) - 1n).toString());
    }
    dragRef.current = null;
    setDragging(null);
  };

  const cancelDrag = () => {
    dragRef.current = null;
    setDragging(null);
    setPreview(normalizeSelection(selection));
  };

  const adjustBoundary = (boundary: 'start' | 'end', event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (!preview || !rangeEditing || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const baseStep = duration / 1000n > 1n ? duration / 1000n : 1n;
    const step = (event.shiftKey ? 10n : 1n) * baseStep;
    const direction = event.key === 'ArrowLeft' ? -step : step;
    const start = BigInt(preview.startNs);
    const end = BigInt(preview.endNs);
    const next = boundary === 'start'
      ? { ...preview, startNs: clampNs(start + direction, boundsStart, end - 1n).toString() }
      : { ...preview, endNs: clampNs(end + direction, start + 1n, boundsEnd).toString() };
    setPreview(next);
    commitRange(next);
  };

  const setBoundaryAtPlayhead = (boundary: 'start' | 'end') => {
    if (!rangeEditing) return;
    const fallbackWidth = duration / 20n > 0n ? duration / 20n : 1n;
    const existingStart = BigInt(preview?.startNs ?? currentText);
    const existingEnd = BigInt(preview?.endNs ?? clampNs(current + fallbackWidth, boundsStart + 1n, boundsEnd));
    const next = boundary === 'start'
      ? {
          startNs: clampNs(current, boundsStart, existingEnd - 1n).toString(),
          endNs: existingEnd.toString(),
          ...(selection?.label ? { label: selection.label } : {}),
        }
      : {
          startNs: existingStart.toString(),
          endNs: clampNs(current + 1n, existingStart + 1n, boundsEnd).toString(),
          ...(selection?.label ? { label: selection.label } : {}),
        };
    setPreview(next);
    commitRange(next);
  };

  const selectionStyle: CSSProperties | undefined = preview ? {
    left: `${timelinePercent(BigInt(preview.startNs), boundsStart, boundsEnd)}%`,
    width: `${Math.max(0.15, timelinePercent(BigInt(preview.endNs), boundsStart, boundsEnd) - timelinePercent(BigInt(preview.startNs), boundsStart, boundsEnd))}%`,
  } : undefined;
  const playheadStyle: CSSProperties = { left: `${timelinePercent(current, boundsStart, boundsEnd)}%` };

  return (
    <section className="viewer-timeline" aria-label="视频剪辑时间轴" data-dragging={dragging ?? undefined}>
      <header className="viewer-timeline__toolbar">
        <div className="viewer-timeline__readout">
          <strong>{formatElapsedNs(current, boundsStart)}</strong>
          <span>{preview ? `选区 ${formatElapsedNs(BigInt(preview.endNs) - BigInt(preview.startNs), 0n)}` : '拖拽画面条创建标注区间'}</span>
        </div>
        <div className="viewer-timeline__tools" aria-label="时间轴工具">
          <button type="button" disabled={!rangeEditing} onClick={() => setBoundaryAtPlayhead('start')}>设为入点</button>
          <button type="button" disabled={!rangeEditing} onClick={() => setBoundaryAtPlayhead('end')}>设为出点</button>
          <span className="viewer-timeline__divider" aria-hidden="true" />
          <button type="button" aria-label="缩小时间轴" disabled={zoom === 1} onClick={() => setZoom((value) => Math.max(1, value / 2))}>−</button>
          <output aria-label="时间轴缩放">{zoom}x</output>
          <button type="button" aria-label="放大时间轴" disabled={zoom === 8} onClick={() => setZoom((value) => Math.min(8, value * 2))}>＋</button>
        </div>
      </header>
      <div className="viewer-timeline__viewport">
        <div className="viewer-timeline__surface" style={{ width: `${zoom * 100}%` }}>
          <div className="viewer-timeline__ruler" aria-hidden="true">
            {Array.from({ length: 11 }, (_, index) => {
              const at = boundsStart + (duration * BigInt(index)) / 10n;
              return <span key={index} style={{ left: `${index * 10}%` }}>{formatElapsedNs(at, boundsStart)}</span>;
            })}
          </div>
          <div
            ref={filmstripRef}
            className="viewer-timeline__filmstrip"
            role="slider"
            tabIndex={0}
            aria-label="播放位置；拖拽可创建标注区间"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={timelinePercent(current, boundsStart, boundsEnd)}
            aria-valuetext={formatElapsedNs(current, boundsStart)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                event.preventDefault();
                directSeek(clock, event.key === 'ArrowLeft' ? -100_000_000n : 100_000_000n);
              } else if (event.key === 'Home' || event.key === 'End') {
                event.preventDefault();
                clock.seek(event.key === 'Home' ? clock.startNs : (boundsEnd - 1n).toString());
              }
            }}
            onPointerDown={(event) => {
              if (rangeEditing) beginDrag('create', event);
              else clock.seek(clampNs(nsFromClientX(event.clientX), boundsStart, boundsEnd - 1n).toString());
            }}
            onPointerMove={updateDrag}
            onPointerUp={finishDrag}
            onPointerCancel={cancelDrag}
          >
            <div className="viewer-timeline__frames" aria-hidden="true">
              {Array.from({ length: 16 }, (_, index) => <span key={index} />)}
            </div>
            {preview && selectionStyle ? (
              <div className="viewer-timeline__selection" style={selectionStyle}>
                <button
                  type="button"
                  className="viewer-timeline__handle viewer-timeline__handle--start"
                  aria-label={`标注开始 ${formatElapsedNs(BigInt(preview.startNs), boundsStart)}`}
                  aria-valuetext={formatElapsedNs(BigInt(preview.startNs), boundsStart)}
                  disabled={!rangeEditing}
                  onKeyDown={(event) => adjustBoundary('start', event)}
                  onPointerDown={(event) => beginDrag('start', event)}
                ><span aria-hidden="true">‹</span></button>
                <button
                  type="button"
                  className="viewer-timeline__selection-body"
                  disabled={!rangeEditing}
                  aria-label="拖动整个标注区间"
                  onPointerDown={(event) => beginDrag('move', event)}
                >{preview.label || '当前标注'}</button>
                <button
                  type="button"
                  className="viewer-timeline__handle viewer-timeline__handle--end"
                  aria-label={`标注结束 ${formatElapsedNs(BigInt(preview.endNs), boundsStart)}`}
                  aria-valuetext={formatElapsedNs(BigInt(preview.endNs), boundsStart)}
                  disabled={!rangeEditing}
                  onKeyDown={(event) => adjustBoundary('end', event)}
                  onPointerDown={(event) => beginDrag('end', event)}
                ><span aria-hidden="true">›</span></button>
              </div>
            ) : null}
            <div className="viewer-timeline__playhead" style={playheadStyle} aria-hidden="true"><span /></div>
          </div>
          {tracks.length ? (
            <div className="viewer-timeline__tracks" aria-label="多级标注轨道">
              {tracks.map((track) => (
                <div className="viewer-timeline__track" key={track.id}>
                  <strong style={{ paddingLeft: `${10 + (track.level ?? 0) * 12}px` }}>{track.label}</strong>
                  <div>
                    {track.segments.length ? track.segments.map((segment) => {
                      const left = timelinePercent(BigInt(segment.startNs), boundsStart, boundsEnd);
                      const isPoint = !segment.endNs;
                      const width = isPoint ? 0 : Math.max(0.25, timelinePercent(BigInt(segment.endNs!), boundsStart, boundsEnd) - left);
                      return (
                        <span
                          key={segment.id}
                          className={isPoint ? 'viewer-timeline__segment viewer-timeline__segment--point' : 'viewer-timeline__segment'}
                          data-tone={segment.tone}
                          style={{ left: `${left}%`, ...(isPoint ? {} : { width: `${width}%` }) }}
                          title={segment.label}
                        >{isPoint ? <i aria-hidden="true" /> : null}{segment.label}</span>
                      );
                    }) : <em>暂无标注</em>}
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </div>
      <footer className="viewer-timeline__hint">拖拽空白处新建区间 · 拖动两侧手柄调整入点/出点 · 拖动选区中部整体移动</footer>
    </section>
  );
}

export function EpisodeWorkbenchCore(p: EpisodeWorkbenchCoreProps): JSX.Element {
  const composition = useMemo(() => resolveViewerComposition(p.streams), [p.streams]);
  const byId = useMemo(() => new Map(p.streams.map((stream) => [stream.id, stream])), [p.streams]);
  return (
    <section className="episode-workbench-core" data-mode={p.mode} data-episode-id={p.episodeId}>
      <h2 className="sr-only">Episode {p.episodeId} Viewer</h2>
      <div className="viewer-media-grid">
        {composition.panels.map((panel) => {
          const streamId = panel.streamIds[0];
          const stream = streamId ? byId.get(streamId) : undefined;
          return stream ? <StreamPanel key={panel.panelId} panel={panel} stream={stream} clock={p.clock} onResourceError={p.onResourceError} /> : null;
        })}
        {p.robotScene ? <RobotScenePanel scene={p.robotScene} /> : null}
      </div>
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
                <strong id={`${group.streamId}-axis-title`} className="viewer-axis-group__title">{group.title}</strong>
                {stream ? <code className="viewer-axis-group__path" title={stream.canonicalPath}>{stream.canonicalPath}</code> : null}
              </div>
              <span className="viewer-axis-group__count">{group.axes.length} DOF</span>
            </header>
            <div className="viewer-axis-group__grid" role="list" aria-label={`${group.title}关节轴`}>
              {group.axes.map((axis, index) => (
                <div
                  key={axis.axisId}
                  className="viewer-axis-card"
                  role="listitem"
                  data-axis-id={axis.axisId}
                  data-mapping-status={axis.mappingStatus}
                  aria-label={`${axis.displayName}，单位 ${axis.unit}`}
                  title={[axis.sourceName, axis.mappedJointName].filter(Boolean).join(' · ')}
                >
                  <span className="viewer-axis-card__index" aria-hidden="true">J{String(index + 1).padStart(2, '0')}</span>
                  <strong className="viewer-axis-card__name">{axis.displayName}</strong>
                  <span className="viewer-axis-card__unit">{axis.unit}</span>
                </div>
              ))}
            </div>
          </section>
        );
      })}
      {composition.diagnostics.map((diagnostic) => <div role="alert" key={`${diagnostic.streamId}:${diagnostic.code}`}>{diagnostic.message}</div>)}
      {p.overlays?.map((overlay) => <OverlayHost key={overlay.id} renderer={overlay} clock={p.clock} />)}
      <PlaybackControls clock={p.clock} />
      <ClipTimeline
        clock={p.clock}
        disabled={p.timelineDisabled}
        selection={p.timelineSelection}
        tracks={p.timelineTracks}
        onRangeSelect={p.onTimeRangeSelect}
      />
    </section>
  );
}
