import type {
  RobotSceneCoreProps,
  RobotSceneManifest,
  RobotSceneRuntimeLoader,
} from './RobotSceneCore';

export interface RobotSceneAssets {
  readonly manifest: RobotSceneManifest;
  readonly urdfUrl: string;
  readonly packages?: string | Readonly<Record<string, string>>;
  readonly background?: string;
}

export type RobotSceneAssetResolver = (
  props: RobotSceneCoreProps,
  signal: AbortSignal,
) => Promise<RobotSceneAssets>;

/**
 * This small factory is safe in common chunks. Three.js, URDFLoader and
 * OrbitControls are behind the dynamic import and enter only a caller's lazy
 * viewer/annotation/cleaning route chunk.
 */
export function createLazyThreeRobotSceneLoader(resolveAssets: RobotSceneAssetResolver): RobotSceneRuntimeLoader {
  return async (host, props, signal) => {
    const assets = await resolveAssets(props, signal);
    return {
      manifest: assets.manifest,
      createRuntime: async () => {
        const { createThreeRobotSceneRuntime } = await import('./runtime/threeRobotSceneRuntime');
        if (signal.aborted) throw new DOMException('Robot scene load aborted', 'AbortError');
        return createThreeRobotSceneRuntime(host, assets, signal);
      },
    };
  };
}
