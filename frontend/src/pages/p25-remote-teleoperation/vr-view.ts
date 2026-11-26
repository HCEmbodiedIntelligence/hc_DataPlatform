import * as THREE from "three";

export interface DeviceStatus {
  video: boolean;
  control: boolean;
  inputs: string[];
}

function pose(value: XRRigidTransform) {
  const { position: p, orientation: q } = value;
  return { position: [p.x, p.y, p.z], orientation: [q.x, q.y, q.z, q.w] };
}

export async function showInHeadset(
  xr: XRSession,
  video: HTMLVideoElement,
  channel: RTCDataChannel,
  sessionId: string,
  signal: AbortSignal,
  status: (update: DeviceStatus) => void,
  operator: {
    sample(valid: boolean, released: boolean): boolean;
    activate(): void;
    enabled(): boolean;
  },
) {
  signal.throwIfAborted();
  const renderer = new THREE.WebGLRenderer({ antialias: false, alpha: false });
  renderer.xr.enabled = true;
  renderer.xr.setReferenceSpaceType("local");
  const camera = new THREE.PerspectiveCamera();
  const scene = new THREE.Scene();
  const texture = new THREE.VideoTexture(video);
  texture.colorSpace = THREE.SRGBColorSpace;
  const geometry = new THREE.PlaneGeometry(1, 1);
  const material = new THREE.MeshBasicMaterial({
    map: texture,
    toneMapped: false,
  });
  const screen = new THREE.Mesh(geometry, material);
  scene.add(screen);

  let seq = 0;
  let lastFrame = -Infinity;
  let callback = 0;
  let lastReport = "";
  const frameArrived = () => {
    lastFrame = performance.now();
    callback = video.requestVideoFrameCallback(frameArrived);
  };
  let disposed = false;
  function dispose() {
    if (disposed) return;
    disposed = true;
    video.cancelVideoFrameCallback(callback);
    renderer.setAnimationLoop(null);
    geometry.dispose();
    material.dispose();
    texture.dispose();
    renderer.dispose();
  }
  signal.addEventListener("abort", dispose, { once: true });
  try {
    await renderer.xr.setSession(xr);
    signal.throwIfAborted();
    const space = renderer.xr.getReferenceSpace();
    if (!space) throw new Error("无法建立头显的本地坐标系");
    const { attachOperatorButton } = await import("./xr-operator-button");
    signal.throwIfAborted();
    attachOperatorButton(xr, space, screen, operator, signal);
    callback = video.requestVideoFrameCallback(frameArrived);
    renderer.setAnimationLoop((_time, frame) => {
      if (!frame || signal.aborted) return;
      const viewer = frame.getViewerPose(space);
      const sources = Array.from(xr.inputSources);
      const fresh = performance.now() - lastFrame < 500;
      const open = channel.readyState === "open";
      const inputs = sources.map((s) =>
        [s.handedness, s.profiles[0] || s.targetRayMode].join(" · "),
      );
      const report = JSON.stringify([fresh, open, inputs]);
      if (report !== lastReport) {
        lastReport = report;
        status({ video: fresh, control: open, inputs });
      }

      const hands = sources.filter(
        (source) =>
          (source.handedness === "left" || source.handedness === "right") &&
          source.gripSpace &&
          frame.getPose(source.gripSpace, space),
      );
      const both =
        hands.some((s) => s.handedness === "left") &&
        hands.some((s) => s.handedness === "right");
      const permitted = operator.sample(
        !!viewer && fresh && open && both && xr.visibilityState === "visible",
        both &&
          hands.every(
            (source) => (source.gamepad?.buttons[1]?.value ?? 1) <= 0.1,
          ),
      );
      screen.visible = !!viewer && fresh;
      if (viewer) {
        const { position, orientation } = viewer.transform;
        screen.position.set(position.x, position.y, position.z);
        screen.quaternion.set(
          orientation.x,
          orientation.y,
          orientation.z,
          orientation.w,
        );
        screen.translateZ(-1.6);
        screen.scale.set(
          2.2,
          (2.2 * (video.videoHeight || 9)) / (video.videoWidth || 16),
          1,
        );
        if (
          permitted &&
          fresh &&
          open &&
          xr.visibilityState === "visible" &&
          channel.bufferedAmount === 0
        ) {
          const controllers = sources.flatMap((source) => {
            const grip =
              source.gripSpace && frame.getPose(source.gripSpace, space);
            return grip
              ? [
                  {
                    hand: source.handedness,
                    grip: pose(grip.transform),
                    buttons: source.gamepad?.buttons.map((b) => b.value) ?? [],
                    axes: Array.from(source.gamepad?.axes ?? []),
                    profile: source.profiles[0] ?? null,
                  },
                ]
              : [];
          });
          channel.send(
            JSON.stringify({
              version: 1,
              session_id: sessionId,
              seq: ++seq,
              time_ms: performance.now(),
              head: pose(viewer.transform),
              controllers,
            }),
          );
        }
      }
      renderer.render(scene, camera);
    });
  } catch (error) {
    dispose();
    throw error;
  }
}
