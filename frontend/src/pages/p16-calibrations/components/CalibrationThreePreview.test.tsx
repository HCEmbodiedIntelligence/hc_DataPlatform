// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CalibrationThreePreview } from "./CalibrationThreePreview";

const { rendererDispose, quaternionFromArray, positionSet } = vi.hoisted(
  () => ({
    rendererDispose: vi.fn(),
    quaternionFromArray: vi.fn(),
    positionSet: vi.fn(),
  }),
);

vi.mock("three", () => {
  class Scene {
    add() {}
  }
  class WebGLRenderer {
    domElement = document.createElement("canvas");
    setPixelRatio() {}
    setSize() {}
    setClearColor() {}
    render() {}
    dispose = rendererDispose;
  }
  class PerspectiveCamera {
    aspect = 1;
    position = { set: positionSet };
    constructor(..._args: unknown[]) {}
    lookAt() {}
    updateProjectionMatrix() {}
  }
  class AmbientLight {
    constructor(..._args: unknown[]) {}
  }
  class AxesHelper {
    position = { set: positionSet };
    quaternion = { fromArray: quaternionFromArray };
    constructor(..._args: unknown[]) {}
  }
  class BufferGeometry {
    setFromPoints() {
      return this;
    }
    dispose() {}
  }
  class Vector3 {
    constructor(..._args: unknown[]) {}
  }
  class LineBasicMaterial {
    constructor(..._args: unknown[]) {}
    dispose() {}
  }
  class Line {
    constructor(..._args: unknown[]) {}
  }
  return {
    Scene,
    WebGLRenderer,
    PerspectiveCamera,
    AmbientLight,
    AxesHelper,
    BufferGeometry,
    Vector3,
    LineBasicMaterial,
    Line,
  };
});

beforeEach(() => {
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: class ResizeObserver {
      observe() {}
      disconnect() {}
    },
  });
  rendererDispose.mockClear();
  quaternionFromArray.mockClear();
  positionSet.mockClear();
});

afterEach(() => cleanup());

describe("CalibrationThreePreview", () => {
  it("uses stored transform values and disposes WebGL resources on unmount", async () => {
    const view = render(
      <CalibrationThreePreview
        document={{
          frame_transforms: [
            {
              parent_frame: "base_link",
              child_frame: "camera_front",
              translation_m: [0.12, 0, 0.42],
              quaternion_xyzw: [0, 0, 0, 1],
              covariance: null,
            },
          ],
          camera_intrinsics: [],
        }}
      />,
    );
    await waitFor(() =>
      expect(screen.queryByText("正在加载真实坐标变换…")).toBeNull(),
    );
    expect(quaternionFromArray).toHaveBeenCalledWith([0, 0, 0, 1]);
    expect(positionSet).toHaveBeenCalledWith(0.12, 0, 0.42);
    view.unmount();
    expect(rendererDispose).toHaveBeenCalledOnce();
  });
});
