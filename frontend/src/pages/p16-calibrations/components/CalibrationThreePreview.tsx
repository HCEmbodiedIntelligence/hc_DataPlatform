import { Alert, Spin } from "antd";
import { useEffect, useRef, useState } from "react";
import type { BufferGeometry, Material, WebGLRenderer } from "three";
import type { CalibrationDocument } from "../../../features/calibrations/api";

type PreviewState = "loading" | "ready" | "unsupported" | "error";

export function CalibrationThreePreview({
  document,
}: Readonly<{ document: CalibrationDocument }>) {
  const host = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<PreviewState>("loading");

  useEffect(() => {
    const target = host.current;
    if (!target) return undefined;
    let disposed = false;
    let disposeScene = () => undefined;
    setState("loading");

    void import("three")
      .then((THREE) => {
        if (disposed) return;
        const width = Math.max(target.clientWidth, 280);
        const height = Math.max(target.clientHeight, 260);
        let renderer: WebGLRenderer;
        try {
          renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        } catch {
          setState("unsupported");
          return;
        }
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.setSize(width, height, false);
        renderer.setClearColor(0xf8fbff, 1);
        target.replaceChildren(renderer.domElement);

        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(
          42,
          width / height,
          0.01,
          100,
        );
        camera.position.set(1.35, 1.1, 1.5);
        camera.lookAt(0, 0, 0);
        scene.add(new THREE.AmbientLight(0xffffff, 1.7));
        const worldAxes = new THREE.AxesHelper(0.3);
        scene.add(worldAxes);
        const lines: BufferGeometry[] = [];
        const materials: Material[] = [];

        for (const transform of document.frame_transforms) {
          const [x, y, z] = transform.translation_m;
          const frame = new THREE.AxesHelper(0.18);
          frame.position.set(x, y, z);
          frame.quaternion.fromArray(transform.quaternion_xyzw);
          scene.add(frame);

          const geometry = new THREE.BufferGeometry().setFromPoints([
            new THREE.Vector3(0, 0, 0),
            new THREE.Vector3(x, y, z),
          ]);
          const material = new THREE.LineBasicMaterial({ color: 0x2563eb });
          lines.push(geometry);
          materials.push(material);
          scene.add(new THREE.Line(geometry, material));
        }
        renderer.render(scene, camera);
        const resize = () => {
          const nextWidth = Math.max(target.clientWidth, 280);
          const nextHeight = Math.max(target.clientHeight, 260);
          camera.aspect = nextWidth / nextHeight;
          camera.updateProjectionMatrix();
          renderer.setSize(nextWidth, nextHeight, false);
          renderer.render(scene, camera);
        };
        const observer =
          typeof ResizeObserver === "undefined"
            ? null
            : new ResizeObserver(resize);
        observer?.observe(target);
        disposeScene = () => {
          observer?.disconnect();
          for (const geometry of lines) geometry.dispose();
          for (const material of materials) material.dispose();
          renderer.dispose();
          if (renderer.domElement.parentNode === target)
            renderer.domElement.remove();
        };
        setState("ready");
      })
      .catch(() => {
        if (!disposed) setState("error");
      });

    return () => {
      disposed = true;
      disposeScene();
    };
  }, [document]);

  if (state === "unsupported") {
    return (
      <Alert
        type="warning"
        showIcon
        message="当前浏览器无法初始化 WebGL 标定预览"
        description="可继续查看下方的服务端 Frame 和数值变换；没有以示例模型替代真实内容。"
      />
    );
  }
  if (state === "error") {
    return (
      <Alert
        type="error"
        showIcon
        message="标定预览初始化失败"
        description="请重新加载真实版本内容；发布与校验状态未受浏览器预览影响。"
      />
    );
  }
  return (
    <div
      aria-busy={state === "loading"}
      aria-label="基于真实标定变换的三维坐标预览"
      style={{ minHeight: 260, position: "relative" }}
    >
      <div ref={host} style={{ minHeight: 260 }} />
      {state === "loading" ? (
        <div
          style={{
            inset: 0,
            position: "absolute",
            placeItems: "center",
            display: "grid",
          }}
        >
          <Spin description="正在加载真实坐标变换…" />
        </div>
      ) : null}
    </div>
  );
}
