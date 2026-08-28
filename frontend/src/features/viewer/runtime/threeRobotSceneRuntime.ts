import {
  AmbientLight,
  Box3,
  Color,
  DirectionalLight,
  GridHelper,
  Group,
  LoadingManager,
  Mesh,
  MeshStandardMaterial,
  PCFSoftShadowMap,
  PerspectiveCamera,
  PlaneGeometry,
  Quaternion,
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

type JointDrivenRobot = Object3D & {
  setJointValue(name: string, value: number): void;
};

function normalizedAssetPath(value: string): string {
  let path = value.split(/[?#]/u, 1)[0] ?? value;
  try {
    path = new URL(path, 'https://robot-assets.invalid/').pathname;
  } catch {
    // Keep non-URL loader identifiers available for suffix matching.
  }
  try {
    path = decodeURIComponent(path);
  } catch {
    // A malformed escape must not prevent the original URL from loading.
  }
  return path.replaceAll('\\', '/').replace(/^\.?\//u, '');
}

export function createRobotAssetUrlResolver(
  urdfPath: string | undefined,
  assetUrls: Readonly<Record<string, string>> | undefined,
): (requestedUrl: string) => string {
  if (!assetUrls || Object.keys(assetUrls).length === 0)
    return (requestedUrl) => requestedUrl;

  const urdfDirectory = normalizedAssetPath(urdfPath ?? '')
    .split('/')
    .slice(0, -1)
    .join('/');
  const signedUrls = new Set(Object.values(assetUrls));
  const aliases = new Map<string, string | null>();
  const addAlias = (alias: string, url: string) => {
    const normalized = normalizedAssetPath(alias);
    if (!normalized) return;
    if (!aliases.has(normalized)) {
      aliases.set(normalized, url);
      return;
    }
    if (aliases.get(normalized) !== url) aliases.set(normalized, null);
  };

  for (const [assetPath, url] of Object.entries(assetUrls)) {
    const normalized = normalizedAssetPath(assetPath);
    addAlias(normalized, url);
    if (urdfDirectory && normalized.startsWith(`${urdfDirectory}/`))
      addAlias(normalized.slice(urdfDirectory.length + 1), url);
  }
  const candidates = [...aliases.entries()]
    .filter((entry): entry is [string, string] => entry[1] !== null)
    .sort(([left], [right]) => right.length - left.length);

  return (requestedUrl) => {
    if (signedUrls.has(requestedUrl)) return requestedUrl;
    const requestedPath = normalizedAssetPath(requestedUrl);
    const match = candidates.find(
      ([alias]) =>
        requestedPath === alias || requestedPath.endsWith(`/${alias}`),
    );
    return match?.[1] ?? requestedUrl;
  };
}

export function applyRobotJointFrame(
  robot: Pick<JointDrivenRobot, 'setJointValue'>,
  jointFrame: Readonly<Record<string, number>>,
  jointMapping: Readonly<Record<string, string>> = {},
): void {
  for (const [sourceJoint, value] of Object.entries(jointFrame)) {
    if (!Number.isFinite(value)) continue;
    robot.setJointValue(jointMapping[sourceJoint] ?? sourceJoint, value);
  }
}

export interface RobotGroundPlacement {
  readonly size: number;
  readonly z: number;
}

export function calculateRobotGroundPlacement(
  bounds: Box3,
): RobotGroundPlacement {
  const modelSize = bounds.getSize(new Vector3());
  const extent = Math.max(modelSize.x, modelSize.y, modelSize.z, 0.75);
  return {
    size: Math.max(3, extent * 3.2),
    z: bounds.min.z,
  };
}

export function groundRobotAtZ(
  robot: Object3D,
  groundZ: number,
  rootBaseZ = robot.position.z,
): number {
  robot.position.z = rootBaseZ;
  robot.updateMatrixWorld(true);
  const currentBounds = new Box3().setFromObject(robot);
  if (!Number.isFinite(groundZ) || currentBounds.isEmpty()) return 0;
  const correction = groundZ - currentBounds.min.z;
  robot.position.z = rootBaseZ + correction;
  robot.updateMatrixWorld(true);
  return correction;
}

export interface RobotGroundPlaneAnchor {
  readonly name: string;
  readonly normalLocal: Vector3;
  readonly object: Object3D;
  readonly pointLocal: Vector3;
}

function objectHierarchyName(object: Object3D, root: Object3D): string {
  const names: string[] = [];
  let current: Object3D | null = object;
  while (current) {
    if (current.name) names.push(current.name);
    if (current === root) break;
    current = current.parent;
  }
  return names.join('/');
}

function belongsToCollisionGeometry(object: Object3D, root: Object3D): boolean {
  let current: Object3D | null = object;
  while (current) {
    const candidate = current as Object3D & { isURDFCollider?: boolean };
    if (
      candidate.isURDFCollider ||
      candidate.userData.isCollision === true ||
      candidate.userData.isCollisionGeom === true
    ) return true;
    if (current === root) break;
    current = current.parent;
  }
  return false;
}

export function createRobotGroundPlaneAnchors(
  robot: Object3D,
  bounds: Box3,
): readonly RobotGroundPlaneAnchor[] {
  const modelSize = bounds.getSize(new Vector3());
  const extent = Math.max(modelSize.x, modelSize.y, modelSize.z, 0.75);
  const contactTolerance = Math.max(0.01, extent * 0.02);
  const candidates: Array<{
    readonly anchor: RobotGroundPlaneAnchor;
    readonly footprint: number;
    readonly minZ: number;
  }> = [];

  robot.updateMatrixWorld(true);
  robot.traverse((object) => {
    if (!(object instanceof Mesh) || !object.visible) return;
    if (belongsToCollisionGeometry(object, robot)) return;
    const hierarchyName = objectHierarchyName(object, robot);
    if (!/(?:foot|sole|toe|ankle)/iu.test(hierarchyName)) return;

    const candidateBounds = new Box3().setFromObject(object);
    if (
      candidateBounds.isEmpty() ||
      candidateBounds.min.z > bounds.min.z + contactTolerance
    ) return;
    const candidateSize = candidateBounds.getSize(new Vector3());
    const pointWorld = candidateBounds.getCenter(new Vector3());
    pointWorld.z = candidateBounds.min.z;
    const worldQuaternion = object.getWorldQuaternion(new Quaternion());
    candidates.push({
      anchor: {
        name: hierarchyName,
        normalLocal: new Vector3(0, 0, 1)
          .applyQuaternion(worldQuaternion.invert())
          .normalize(),
        object,
        pointLocal: object.worldToLocal(pointWorld.clone()),
      },
      footprint: candidateSize.x * candidateSize.y,
      minZ: candidateBounds.min.z,
    });
  });

  return candidates
    .sort(
      (left, right) =>
        left.minZ - right.minZ || right.footprint - left.footprint,
    )
    .slice(0, 4)
    .map(({ anchor }) => anchor);
}

export interface RobotGroundPlaneStabilization {
  readonly anchorName: string;
  readonly offsetZ: number;
  readonly tiltRadians: number;
}

export function stabilizeRobotOnGroundPlane(
  robot: Object3D,
  anchors: readonly RobotGroundPlaneAnchor[],
  groundZ: number,
  rootBasePosition: Vector3,
  rootBaseQuaternion: Quaternion,
): RobotGroundPlaneStabilization | null {
  if (!Number.isFinite(groundZ) || anchors.length === 0) return null;
  robot.position.copy(rootBasePosition);
  robot.quaternion.copy(rootBaseQuaternion);
  robot.updateMatrixWorld(true);

  const worldUp = new Vector3(0, 0, 1);
  const states = anchors.map((anchor) => {
    const worldQuaternion = anchor.object.getWorldQuaternion(new Quaternion());
    return {
      anchor,
      normalWorld: anchor.normalLocal
        .clone()
        .applyQuaternion(worldQuaternion)
        .normalize(),
      pointWorld: anchor.object.localToWorld(anchor.pointLocal.clone()),
    };
  });
  const support = states.reduce((lowest, candidate) =>
    candidate.pointWorld.z < lowest.pointWorld.z ? candidate : lowest,
  );
  const tiltCorrection = new Quaternion().setFromUnitVectors(
    support.normalWorld,
    worldUp,
  );
  robot.quaternion.premultiply(tiltCorrection).normalize();
  robot.updateMatrixWorld(true);

  const lowestContactZ = Math.min(
    ...anchors.map((anchor) =>
      anchor.object.localToWorld(anchor.pointLocal.clone()).z,
    ),
  );
  const offsetZ = groundZ - lowestContactZ;
  robot.position.z += offsetZ;
  robot.updateMatrixWorld(true);

  return {
    anchorName: support.anchor.name,
    offsetZ,
    tiltRadians:
      2 * Math.acos(Math.min(1, Math.abs(tiltCorrection.w))),
  };
}

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
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = PCFSoftShadowMap;
  const canvas = renderer.domElement;
  const scene = new Scene();
  scene.background = new Color(assets.background ?? '#101820');
  const camera = new PerspectiveCamera(45, 1, 0.01, 1_000);
  camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera, canvas);
  // Demand rendering keeps an idle/hidden viewer from consuming a permanent
  // animation frame. OrbitControls emits `change` while the user interacts.
  controls.enableDamping = false;
  scene.add(new AmbientLight(0xffffff, 1.2));
  const keyLight = new DirectionalLight(0xffffff, 2.2);
  keyLight.position.set(3, 5, 4);
  keyLight.castShadow = true;
  keyLight.shadow.mapSize.set(2_048, 2_048);
  keyLight.shadow.bias = -0.0001;
  keyLight.shadow.normalBias = 0.01;
  scene.add(keyLight);

  const manager = new LoadingManager();
  let failedAssetCount = 0;
  const assetLoadComplete = new Promise<void>((resolve) => {
    manager.onLoad = resolve;
    manager.onError = () => {
      failedAssetCount += 1;
    };
  });
  manager.setURLModifier(
    createRobotAssetUrlResolver(assets.urdfPath, assets.assetUrls),
  );
  const loader = new URDFLoader(manager);
  loader.fetchOptions = { signal, credentials: 'same-origin' };
  if (assets.packages) loader.packages = assets.packages;
  else if (assets.assetUrls && Object.keys(assets.assetUrls).length > 0)
    loader.packages = (packageName) =>
      `https://robot-assets.invalid/${encodeURIComponent(packageName)}`;
  let robot: JointDrivenRobot | null = null;
  try {
    robot = await loader.loadAsync(assets.urdfUrl) as JointDrivenRobot;
    await assetLoadComplete;
    if (failedAssetCount > 0)
      throw new Error('One or more robot model dependencies failed to load');
  } catch (error) {
    if (robot) disposeObject(robot);
    controls.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
    throw error;
  }
  if (!robot) throw new Error('URDF loader did not return a robot model');
  if (signal.aborted) {
    disposeObject(robot);
    controls.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
    throw abortError();
  }
  scene.add(robot);

  const rootBasePosition = robot.position.clone();
  const rootBaseQuaternion = robot.quaternion.clone();
  const bounds = new Box3().setFromObject(robot);
  const center = bounds.getCenter(new Vector3());
  const size = bounds.getSize(new Vector3());
  const extent = Math.max(size.x, size.y, size.z, 0.75);
  const groundPlacement = calculateRobotGroundPlacement(bounds);
  // The first contact plane is the world anchor. It never follows later joint
  // poses; instead the robot root receives the inverse vertical correction.
  const fixedGroundZ = groundPlacement.z;
  const groundPlaneAnchors = createRobotGroundPlaneAnchors(robot, bounds);
  canvas.dataset.groundingMode = groundPlaneAnchors.length > 0
    ? 'fixed-initial-contact-plane'
    : 'fixed-initial-contact-height';
  canvas.dataset.groundAnchorZ = String(fixedGroundZ);
  canvas.dataset.groundContactCount = String(groundPlaneAnchors.length);
  const ground = new Group();
  ground.name = 'robot-ground';
  const groundSurface = new Mesh(
    new PlaneGeometry(groundPlacement.size, groundPlacement.size),
    new MeshStandardMaterial({
      color: 0x172a33,
      metalness: 0,
      opacity: 0.94,
      roughness: 1,
      transparent: true,
    }),
  );
  groundSurface.name = 'robot-ground-surface';
  groundSurface.position.z = fixedGroundZ;
  groundSurface.receiveShadow = true;
  ground.add(groundSurface);

  const groundGrid = new GridHelper(
    groundPlacement.size,
    24,
    0x78909b,
    0x324851,
  );
  groundGrid.name = 'robot-ground-grid';
  groundGrid.rotation.x = Math.PI / 2;
  groundGrid.position.z = fixedGroundZ + Math.max(0.0005, extent * 0.001);
  const gridMaterials = Array.isArray(groundGrid.material)
    ? groundGrid.material
    : [groundGrid.material];
  gridMaterials.forEach((material) => {
    material.depthWrite = false;
    material.opacity = 0.62;
    material.transparent = true;
  });
  ground.add(groundGrid);
  scene.add(ground);

  robot.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    object.castShadow = true;
    object.receiveShadow = true;
  });
  keyLight.target.position.set(center.x, center.y, fixedGroundZ);
  scene.add(keyLight.target);
  const shadowRange = groundPlacement.size * 0.55;
  keyLight.shadow.camera.left = -shadowRange;
  keyLight.shadow.camera.right = shadowRange;
  keyLight.shadow.camera.top = shadowRange;
  keyLight.shadow.camera.bottom = -shadowRange;
  keyLight.shadow.camera.near = 0.1;
  keyLight.shadow.camera.far = Math.max(20, extent * 12);
  keyLight.shadow.camera.updateProjectionMatrix();

  const distance = extent * 1.65;
  camera.position.set(center.x + distance, center.y - distance, center.z + distance * 0.72);
  camera.lookAt(center);
  controls.target.copy(center);
  controls.update();

  let disposed = false;
  let frame: number | null = null;
  let jointRequestGeneration = 0;
  let jointRequest: AbortController | null = null;
  const render = () => {
    frame = null;
    if (!disposed) renderer.render(scene, camera);
  };
  const scheduleRender = () => {
    if (disposed || frame !== null) return;
    frame = requestAnimationFrame(render);
  };
  const resize = () => {
    const width = Math.max(1, host.clientWidth);
    const height = Math.max(1, host.clientHeight);
    renderer.setPixelRatio(Math.min(globalThis.devicePixelRatio || 1, 2));
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    scheduleRender();
  };
  const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(resize);
  observer?.observe(host);
  resize();

  controls.addEventListener('change', scheduleRender);
  scheduleRender();

  return {
    canvas,
    applyTime(ns) {
      canvas.dataset.timeNs = ns;
      if (assets.jointFrameSource) {
        jointRequest?.abort();
        const request = new AbortController();
        jointRequest = request;
        const generation = ++jointRequestGeneration;
        Promise.resolve(assets.jointFrameSource.sampleAt(ns, request.signal))
          .then((jointFrame) => {
            if (disposed || request.signal.aborted || generation !== jointRequestGeneration)
              return;
            applyRobotJointFrame(robot, jointFrame, assets.jointMapping);
            const stabilization = stabilizeRobotOnGroundPlane(
              robot,
              groundPlaneAnchors,
              fixedGroundZ,
              rootBasePosition,
              rootBaseQuaternion,
            );
            const groundingOffset = stabilization?.offsetZ ?? groundRobotAtZ(
              robot,
              fixedGroundZ,
              rootBasePosition.z,
            );
            canvas.dataset.groundingOffsetZ = String(groundingOffset);
            canvas.dataset.groundingTiltRadians = String(
              stabilization?.tiltRadians ?? 0,
            );
            if (stabilization)
              canvas.dataset.groundContactAnchor = stabilization.anchorName;
            canvas.dataset.jointFrameNs = ns;
            scheduleRender();
          })
          .catch(() => undefined);
      }
      scheduleRender();
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
      jointRequest?.abort();
      if (frame !== null) cancelAnimationFrame(frame);
      observer?.disconnect();
      controls.removeEventListener('change', scheduleRender);
      controls.dispose();
      disposeObject(robot);
      disposeObject(ground);
      renderer.renderLists.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      canvas.remove();
    },
  };
}
