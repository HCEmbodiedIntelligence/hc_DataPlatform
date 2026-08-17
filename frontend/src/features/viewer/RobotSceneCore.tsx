import { useEffect, useRef, useState } from 'react';
import type { JSX } from 'react';
import type { PlaybackClock } from './PlaybackClock';
import { ViewerResourceRegistry } from './runtime/ViewerResourceRegistry';

export interface RobotSceneCoreProps {
  modelRef: { modelId: string; modelVersion: string };
  jointMapping: Readonly<Record<string, string>>;
  calibrationRef?: { setId: string; version: string };
  frameGraphRef?: string;
  clock?: PlaybackClock;
  runtimeLoader?: RobotSceneRuntimeLoader;
  onIncompatible?: (reason: 'JOINT_MAPPING'|'MODEL_VERSION'|'CALIBRATION_VERSION'|'FRAME_GRAPH') => void;
  onContextLost?: (recovered: boolean) => void;
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
  const { calibrationRef, clock, frameGraphRef, jointMapping, modelRef, onContextLost, onIncompatible, runtimeLoader: providedRuntimeLoader } = p;
  const hostRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'unavailable' | 'incompatible' | 'context-lost'>('loading');
  const [reason, setReason] = useState<string>('');

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const controller = new AbortController();
    const resources = new ViewerResourceRegistry();
    let recoveryAttempted = false;
    setStatus('loading');
    setReason('');

    const activeRuntimeLoader = providedRuntimeLoader ?? runtimeLoader;
    if (!activeRuntimeLoader) {
      setStatus('unavailable');
      setReason('3D runtime provider 未安装；视频与曲线仍可使用。');
      return () => controller.abort();
    }

    const runtimeProps: RobotSceneCoreProps = { modelRef, jointMapping, calibrationRef, frameGraphRef, clock, onContextLost, onIncompatible };
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
    }).catch(() => {
      if (!controller.signal.aborted) {
        setStatus('unavailable');
        setReason('3D 资源加载失败；视频与曲线仍可使用。');
      }
    });

    return () => {
      controller.abort();
      resources.dispose();
      host.replaceChildren();
    };
  }, [modelRef, jointMapping, calibrationRef, frameGraphRef, clock, onContextLost, onIncompatible, providedRuntimeLoader]);

  return (
    <section className="robot-scene-core" data-status={status} aria-label="机器人 3D 场景">
      <div ref={hostRef} className="robot-scene-viewport" />
      {status !== 'ready' ? <div role={status === 'incompatible' ? 'alert' : 'status'}>{reason || '正在加载 3D 场景…'}</div> : null}
    </section>
  );
}
