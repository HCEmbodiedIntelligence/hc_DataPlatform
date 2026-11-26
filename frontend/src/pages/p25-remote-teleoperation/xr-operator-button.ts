import * as THREE from "three";

export function attachOperatorButton(
  xr: XRSession,
  space: XRReferenceSpace,
  screen: THREE.Mesh,
  operator: { activate(): void; enabled(): boolean },
  life: AbortSignal,
) {
  const canvas = document.createElement("canvas");
  canvas.width = 384;
  canvas.height = 76;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("无法显示头显操作按钮");
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const material = new THREE.MeshBasicMaterial({
    map: texture,
    toneMapped: false,
  });
  const geometry = new THREE.PlaneGeometry(0.45, 0.12);
  const button = new THREE.Mesh(geometry, material);
  button.position.set(0, -0.59, 0.004);
  screen.add(button);
  let previous = "";
  button.onBeforeRender = () => {
    const label = operator.enabled() ? "停用遥操作" : "使能遥操作";
    if (label === previous) return;
    previous = label;
    ctx.fillStyle = operator.enabled() ? "#872a26" : "#194685";
    ctx.fillRect(0, 0, 384, 76);
    ctx.fillStyle = "#ffffff";
    ctx.font = "32px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, 192, 38);
    texture.needsUpdate = true;
  };
  const ray = new THREE.Raycaster();
  const rotation = new THREE.Quaternion();
  const select = (event: XRInputSourceEvent) => {
    if (!screen.visible || xr.visibilityState !== "visible") return;
    const aim = event.frame.getPose(event.inputSource.targetRaySpace, space);
    if (!aim) return;
    const { position: p, orientation: q } = aim.transform;
    ray.ray.origin.set(p.x, p.y, p.z);
    ray.ray.direction
      .set(0, 0, -1)
      .applyQuaternion(rotation.set(q.x, q.y, q.z, q.w));
    if (ray.intersectObject(button, false).length) operator.activate();
  };
  xr.addEventListener("select", select);
  life.addEventListener(
    "abort",
    () => {
      xr.removeEventListener("select", select);
      screen.remove(button);
      geometry.dispose();
      material.dispose();
      texture.dispose();
    },
    { once: true },
  );
}
