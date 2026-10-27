import { useEffect, useRef, useState } from 'react';
import type { JSX } from 'react';
import type { PlaybackClock } from './PlaybackClock';
import { ViewerResourceRegistry } from './runtime/ViewerResourceRegistry';
import './RobotSceneCore.css';

export interface RobotSceneCoreProps {
  modelRef: { modelId: string; modelVersion: string };
  jointMapping: Readonly<Record<string, string>>;
  calibrationRef?: { setId: string; version: string };
  frameGraphRef?: string;
  clock?: PlaybackClock;
  /** Returns source-joint radians for the immutable frame nearest to `ns`. */
  jointFrameSource?: RobotJointFrameSource;
  runtimeLoader?: RobotSceneRuntimeLoader;
  onIncompatible?: (reason: 'JOINT_MAPPING'|'MODEL_VERSION'|'CALIBRATION_VERSION'|'FRAME_GRAPH') => void;
  onContextLost?: (recovered: boolean) => void;
  onLoadProgress?: (loaded: number, total: number, bytes?: number, totalBytes?: number) => void;
}

export interface RobotJointFrameSource {
  sampleAt(
    ns: string,
    signal: AbortSignal,
  ):
    | Readonly<Record<string, number>>
    | Promise<Readonly<Record<string, number>>>;
}

export interface RobotSceneManifest {
  readonly modelId: string;
  readonly modelVersion: string;
  readonly calibrationRef?: { readonly setId: string; readonly version: string };
  readonly frameGraphRef?: string;
  readonly requiredJoints: readonly string[];
}

export interface RobotSceneRuntime {
  readonly canvas: HTMLCanvasElement;
  applyTime(ns: string): void;
  restoreContext(): Promise<boolean>;
  dispose(): void;
}

export interface RobotSceneRuntimeLoad {
  readonly manifest: RobotSceneManifest;
  /**
   * Preferred provider shape. Keeping runtime construction deferred guarantees
   * incompatible model facts are rejected before URDF/WebGL resources load.
   */
  readonly createRuntime?: () => Promise<RobotSceneRuntime>;
  /** Compatibility escape hatch for lightweight/test providers. */
  readonly runtime?: RobotSceneRuntime;
}

export type RobotSceneRuntimeLoader = (
  host: HTMLElement,
  props: RobotSceneCoreProps,
  signal: AbortSignal,
) => Promise<RobotSceneRuntimeLoad>;

let runtimeLoader: RobotSceneRuntimeLoader | null = null;

// This provider setter is intentionally colocated with the component's narrow runtime boundary.
// eslint-disable-next-line react-refresh/only-export-components
export function configureRobotSceneRuntime(loader: RobotSceneRuntimeLoader): () => void {
  runtimeLoader = loader;
  return () => { if (runtimeLoader === loader) runtimeLoader = null; };
}

function incompatible(manifest: RobotSceneManifest, props: RobotSceneCoreProps): Parameters<NonNullable<RobotSceneCoreProps['onIncompatible']>>[0] | null {
  if (manifest.modelId !== props.modelRef.modelId || manifest.modelVersion !== props.modelRef.modelVersion) return 'MODEL_VERSION';
  if (manifest.calibrationRef?.setId !== props.calibrationRef?.setId || manifest.calibrationRef?.version !== props.calibrationRef?.version) return 'CALIBRATION_VERSION';
  if (manifest.frameGraphRef !== props.frameGraphRef) return 'FRAME_GRAPH';
  if (manifest.requiredJoints.some((joint) => !props.jointMapping[joint])) return 'JOINT_MAPPING';
  return null;
}

export function RobotSceneCore(p: RobotSceneCoreProps): JSX.Element {
  const { calibrationRef, clock, frameGraphRef, jointFrameSource, jointMapping, modelRef, onContextLost, onIncompatible, runtimeLoader: providedRuntimeLoader } = p;
  const hostRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'unavailable' | 'incompatible' | 'context-lost'>('loading');
  const [reason, setReason] = useState<string>('');
  const [retry, setRetry] = useState(0);
  const [slow, setSlow] = useState(false);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const controller = new AbortController();
    const resources = new ViewerResourceRegistry();
    let recoveryAttempted = false;
    setStatus('loading');
    setReason('');
    setSlow(false);
    const slowTimer = setTimeout(() => setSlow(true), 20_000);
    resources.add(() => clearTimeout(slowTimer));

    const activeRuntimeLoader = providedRuntimeLoader ?? runtimeLoader;
    if (!activeRuntimeLoader) {
      setStatus('unavailable');
      setReason('3D runtime provider 未安装；视频与曲线仍可使用。');
      return () => { controller.abort(); resources.dispose(); };
    }

    const runtimeProps: RobotSceneCoreProps = { modelRef, jointMapping, calibrationRef, frameGraphRef, clock, jointFrameSource, onContextLost, onIncompatible,
      onLoadProgress: (loaded, total, bytes, totalBytes) => { if (!controller.signal.aborted) setReason(
        totalBytes ? `模型 ${Math.min(100, Math.round((bytes ?? 0) / totalBytes * 100))}%` : `正在加载模型（${loaded}/${total}）…`
      ); } };
    activeRuntimeLoader(host, runtimeProps, controller.signal).then(async ({ manifest, createRuntime, runtime: eagerRuntime }) => {
      if (controller.signal.aborted) { eagerRuntime?.dispose(); return; }
      const mismatch = incompatible(manifest, runtimeProps);
      if (mismatch) {
        eagerRuntime?.dispose();
        setStatus('incompatible');
        setReason(mismatch);
        onIncompatible?.(mismatch);
        return;
      }
      const runtime = eagerRuntime ?? await createRuntime?.();
      if (!runtime) throw new Error('Robot scene provider did not return a runtime');
      if (controller.signal.aborted) { runtime.dispose(); return; }
      resources.add(runtime);
      host.replaceChildren(runtime.canvas);
      if (clock) resources.add(clock.subscribe((ns) => runtime.applyTime(ns)));
      const contextLost = (event: Event) => {
        event.preventDefault();
        setStatus('context-lost');
        if (recoveryAttempted) { onContextLost?.(false); return; }
        recoveryAttempted = true;
        runtime.restoreContext().then((recovered) => {
          if (controller.signal.aborted) return;
          setStatus(recovered ? 'ready' : 'unavailable');
          setReason(recovered ? '' : 'WebGL 恢复失败，已仅降级 3D。');
          onContextLost?.(recovered);
        }).catch(() => {
          if (controller.signal.aborted) return;
          setStatus('unavailable');
          setReason('WebGL 恢复失败，已仅降级 3D。');
          onContextLost?.(false);
        });
      };
      runtime.canvas.addEventListener('webglcontextlost', contextLost);
      resources.add(() => runtime.canvas.removeEventListener('webglcontextlost', contextLost));
      setStatus('ready');
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) {
        setStatus('unavailable');
        setReason(error instanceof Error && error.message === 'WEBGL_UNAVAILABLE'
          ? '浏览器无法创建 WebGL 画布，请检查 Chrome 图形加速设置后重试。'
          : error instanceof Error && error.name === 'ModelAssetLoadError' ? error.message
          : '模型加载失败，请检查网络后重试。视频与曲线仍可使用。');
      }
    });

    return () => {
      controller.abort();
      resources.dispose();
      host.replaceChildren();
    };
  }, [modelRef, jointMapping, calibrationRef, frameGraphRef, clock, jointFrameSource, onContextLost, onIncompatible, providedRuntimeLoader, retry]);

  return (
    <section className="robot-scene-core" data-status={status} aria-label="机器人 3D 场景">
      <div ref={hostRef} className="robot-scene-viewport" />
      {status !== 'ready' ? <div role={status === 'incompatible' ? 'alert' : 'status'}>
        <span>{reason || '正在加载 3D 场景…'}</span>
        {status === 'unavailable' || status === 'context-lost' || (status === 'loading' && slow)
          ? <button type="button" onClick={() => setRetry((value) => value + 1)}>重新加载模型</button> : null}
      </div> : null}
    </section>
  );
}
