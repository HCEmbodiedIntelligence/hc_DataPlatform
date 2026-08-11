import { useEffect, useMemo, useRef, useState } from 'react';
import type { JSX, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent, RefObject } from 'react';
import type { PlaybackClock } from './PlaybackClock';
import { useClockText } from './PlaybackClock';
import { resolveViewerComposition } from './ViewerCompositionResolver';
import { ViewerResourceRegistry } from './runtime/ViewerResourceRegistry';
import { retrySignedResourceOnce } from './runtime/signed-resource';
import { createDomainError, isDomainError } from '../../shared/api/domain-error';
import type { DomainError, OverlayRenderer, StreamDescriptor, ViewerPanelSpec, ViewerResourceScope } from './types';

export interface EpisodeWorkbenchCoreProps {
  episodeId: string;
  datasetId: string;
  versionId: string;
  clock: PlaybackClock;
  mode: 'readonly' | 'annotate' | 'cleaning';
  streams: readonly StreamDescriptor[];
  overlays?: readonly OverlayRenderer[];
  onTimeRangeSelect?: (startNs: string, endNs: string) => void;
  onResourceError?: (e: DomainError, scope: 'video'|'curve'|'pointcloud'|'scene3d') => void;
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
  return <output aria-live="off" data-testid="viewer-clock-text">{current} ns</output>;
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
  return (
    <article ref={hostRef} className="viewer-panel" data-panel-kind={panel.kind} aria-labelledby={`${panel.panelId}-title`}>
      <header><h3 id={`${panel.panelId}-title`}>{panel.title}</h3><span>{panel.state}</span></header>
      {error ? <div role="alert">资源加载失败：{error.message}</div> : null}
      {pending ? <div role="status">Preview 生成中，其他面板可继续使用。</div> : null}
      {unavailable ? <div role="note">此 Stream 当前不支持：{stream.schema.id}@{stream.schema.version}</div> : null}
      {(panel.kind === 'video' || panel.kind === 'depth') && !unavailable
        ? <video ref={videoRef} muted playsInline preload="metadata" aria-label={`${panel.title} 媒体`} />
        : null}
      {panel.kind !== 'video' && panel.kind !== 'depth' && !unavailable
        ? <canvas ref={canvasRef} aria-label={`${panel.title} 可视化`} />
        : null}
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

function Timeline({ clock, onRangeSelect }: { clock: PlaybackClock; onRangeSelect?: (start: string, end: string) => void }): JSX.Element {
  const startRef = useRef<string | null>(null);
  const timelineRef = useRef<HTMLDivElement>(null);
  useEffect(() => clock.subscribe((ns) => {
    const node = timelineRef.current;
    if (!node) return;
    const start = BigInt(clock.startNs);
    const duration = BigInt(clock.endNs) - start;
    const percent = Number(((BigInt(ns) - start) * 10_000n) / duration) / 100;
    node.setAttribute('aria-valuenow', String(Math.max(0, Math.min(100, percent))));
    node.setAttribute('aria-valuetext', `${ns} 纳秒`);
  }), [clock]);
  const seekFromPointer = (event: ReactPointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const scaled = BigInt(Math.max(0, Math.min(1_000_000, Math.round(((event.clientX - rect.left) / rect.width) * 1_000_000))));
    const start = BigInt(clock.startNs);
    const ns = start + ((BigInt(clock.endNs) - start) * scaled) / 1_000_000n;
    return ns >= BigInt(clock.endNs) ? BigInt(clock.endNs) - 1n : ns;
  };
  return (
    <div
      ref={timelineRef}
      className="viewer-timeline"
      role="slider"
      tabIndex={0}
      aria-label="Episode 时间轴"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={0}
      aria-valuetext={`${clock.currentNs()} 纳秒`}
      onKeyDown={(event) => {
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
          event.preventDefault();
          directSeek(clock, event.key === 'ArrowLeft' ? -100_000_000n : 100_000_000n);
        } else if (event.key === 'PageDown' || event.key === 'PageUp') {
          event.preventDefault();
          directSeek(clock, event.key === 'PageDown' ? -1_000_000_000n : 1_000_000_000n);
        } else if (event.key === 'Home' || event.key === 'End') {
          event.preventDefault();
          clock.seek(event.key === 'Home' ? clock.startNs : (BigInt(clock.endNs) - 1n).toString());
        }
      }}
      onPointerDown={(event) => { startRef.current = seekFromPointer(event).toString(); }}
      onPointerUp={(event) => {
        const end = seekFromPointer(event);
        const start = BigInt(startRef.current ?? end.toString());
        const low = start < end ? start : end;
        const high = start < end ? end : start;
        clock.seek(end.toString());
        if (onRangeSelect && low < high) onRangeSelect(low.toString(), (high + 1n).toString());
        startRef.current = null;
      }}
    />
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
      </div>
      {composition.jointGroups.map((group) => (
        <section key={group.streamId} className="viewer-axis-group" aria-label={group.title}>
          {group.axes.map((axis) => <div key={axis.axisId} data-axis-id={axis.axisId}>{axis.displayName} <span>{axis.unit}</span></div>)}
        </section>
      ))}
      {composition.diagnostics.map((diagnostic) => <div role="alert" key={`${diagnostic.streamId}:${diagnostic.code}`}>{diagnostic.message}</div>)}
      {p.overlays?.map((overlay) => <OverlayHost key={overlay.id} renderer={overlay} clock={p.clock} />)}
      <Timeline clock={p.clock} onRangeSelect={p.onTimeRangeSelect} />
      <PlaybackControls clock={p.clock} />
    </section>
  );
}
