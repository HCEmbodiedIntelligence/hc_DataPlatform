import {
  AmbientLight,
  Box3,
  Color,
  DirectionalLight,
  PerspectiveCamera,
  Scene,
  Texture,
  Vector3,
  WebGLRenderer,
} from 'three';
import type { Material, Object3D } from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import URDFLoader from 'urdf-loader';
import type { RobotSceneRuntime } from '../RobotSceneCore';
import type { RobotSceneAssets } from '../lazy-three-loader';

function abortError(): DOMException {
  return new DOMException('Robot scene load aborted', 'AbortError');
}

function disposeMaterial(material: Material): void {
  for (const value of Object.values(material)) {
    if (value instanceof Texture) value.dispose();
  }
  material.dispose();
}

function disposeObject(root: Object3D): void {
  root.traverse((object) => {
    const disposable = object as Object3D & {
      geometry?: { dispose(): void };
      material?: Material | Material[];
    };
    disposable.geometry?.dispose();
    const materials = Array.isArray(disposable.material) ? disposable.material : disposable.material ? [disposable.material] : [];
    materials.forEach(disposeMaterial);
  });
}

export async function createThreeRobotSceneRuntime(
  host: HTMLElement,
  assets: RobotSceneAssets,
  signal: AbortSignal,
): Promise<RobotSceneRuntime> {
  if (signal.aborted) throw abortError();
  const renderer = new WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
  const canvas = renderer.domElement;
  const scene = new Scene();
  scene.background = new Color(assets.background ?? '#101820');
  const camera = new PerspectiveCamera(45, 1, 0.01, 1_000);
  camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  scene.add(new AmbientLight(0xffffff, 1.2));
  const keyLight = new DirectionalLight(0xffffff, 2.2);
  keyLight.position.set(3, 5, 4);
  scene.add(keyLight);

  const loader = new URDFLoader();
  loader.fetchOptions = { signal, credentials: 'same-origin' };
  if (assets.packages) loader.packages = assets.packages as string | Record<string, string>;
  let robot: Object3D;
  try {
    robot = await loader.loadAsync(assets.urdfUrl);
  } catch (error) {
    controls.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
    throw error;
  }
  if (signal.aborted) {
    disposeObject(robot);
    controls.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
    throw abortError();
  }
  scene.add(robot);

  const bounds = new Box3().setFromObject(robot);
  const center = bounds.getCenter(new Vector3());
  const size = bounds.getSize(new Vector3());
  const extent = Math.max(size.x, size.y, size.z, 0.75);
  const distance = extent * 1.65;
  camera.position.set(center.x + distance, center.y - distance, center.z + distance * 0.72);
  camera.lookAt(center);
  controls.target.copy(center);
  controls.update();

  let disposed = false;
  let frame = 0;
  const resize = () => {
    const width = Math.max(1, host.clientWidth);
    const height = Math.max(1, host.clientHeight);
    renderer.setPixelRatio(Math.min(globalThis.devicePixelRatio || 1, 2));
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  };
  const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(resize);
  observer?.observe(host);
  resize();

  const render = () => {
    if (disposed) return;
    controls.update();
    renderer.render(scene, camera);
    frame = requestAnimationFrame(render);
  };
  frame = requestAnimationFrame(render);

  return {
    canvas,
    applyTime(ns) {
      canvas.dataset.timeNs = ns;
      if (!disposed) renderer.render(scene, camera);
    },
    restoreContext() {
      if (disposed) return Promise.resolve(false);
      try {
        renderer.forceContextRestore();
        resize();
        renderer.render(scene, camera);
        return Promise.resolve(true);
      } catch {
        return Promise.resolve(false);
      }
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      cancelAnimationFrame(frame);
      observer?.disconnect();
      controls.dispose();
      disposeObject(robot);
      renderer.renderLists.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      canvas.remove();
    },
  };
}
