vi.mock("./xr-operator-button", () => ({ attachOperatorButton: vi.fn() }));
import { expect, it, vi } from "vitest";
import { showInHeadset } from "./vr-view";

const view = vi.hoisted(() => ({
  draw: undefined as ((time: number, frame: XRFrame) => void) | undefined,
  dispose: vi.fn(),
}));
vi.mock("three", async (original) => ({
  ...(await original<typeof import("three")>()),
  WebGLRenderer: class {
    xr = {
      setSession: vi.fn(),
      setReferenceSpaceType: vi.fn(),
      getReferenceSpace: () => ({}),
    };
    render = vi.fn();
    dispose = view.dispose;
    setAnimationLoop(callback: typeof view.draw) {
      view.draw = callback;
    }
  },
}));

it("sends fresh tracked input in the active session and drops stale or buffered samples", async () => {
  let videoFrame: (() => void) | undefined;
  const video = {
    requestVideoFrameCallback: (fn: () => void) => {
      videoFrame = fn;
      return 1;
    },
    cancelVideoFrameCallback: vi.fn(),
    videoWidth: 640,
    videoHeight: 480,
  };
  const xr = {
    visibilityState: "visible",
    inputSources: [
      {
        handedness: "right",
        profiles: ["test-grip"],
        gripSpace: {},
        gamepad: { buttons: [{ value: 0.5 }], axes: [0.1] },
      },
    ],
  };
  const channel = { readyState: "open", bufferedAmount: 0, send: vi.fn() };
  const controller = new AbortController();
  const pose = {
    transform: {
      position: { x: 1, y: 2, z: 3 },
      orientation: { x: 0, y: 0, z: 0, w: 1 },
    },
  };
  const frame = {
    getViewerPose: () => pose,
    getPose: () => pose,
  } as unknown as XRFrame;
  const status = vi.fn();
  const now = vi.spyOn(performance, "now").mockReturnValue(20);
  try {
    await showInHeadset(
      xr as unknown as XRSession,
      video as unknown as HTMLVideoElement,
      channel as unknown as RTCDataChannel,
      "session-a",
      controller.signal,
      status,
      { sample: () => true, activate: () => {}, enabled: () => true },
    );
    const draw = () => view.draw?.(0, frame);
    draw();
    expect(channel.send).not.toHaveBeenCalled();
    videoFrame?.();
    draw();
    expect(JSON.parse(channel.send.mock.calls[0]![0])).toEqual({
      version: 1,
      session_id: "session-a",
      seq: 1,
      time_ms: 20,
      head: { position: [1, 2, 3], orientation: [0, 0, 0, 1] },
      controllers: [
        {
          hand: "right",
          profile: "test-grip",
          buttons: [0.5],
          axes: [0.1],
          grip: { position: [1, 2, 3], orientation: [0, 0, 0, 1] },
        },
      ],
    });
    expect(status).toHaveBeenLastCalledWith({
      video: true,
      control: true,
      inputs: ["right · test-grip"],
    });
    channel.bufferedAmount = 12;
    draw();
    channel.bufferedAmount = 0;
    xr.visibilityState = "hidden";
    draw();
    xr.visibilityState = "visible";
    now.mockReturnValue(600);
    draw();
    expect(channel.send).toHaveBeenCalledTimes(1);
    expect(status).toHaveBeenLastCalledWith({
      video: false,
      control: true,
      inputs: ["right · test-grip"],
    });
  } finally {
    controller.abort();
    now.mockRestore();
  }
  expect(view.dispose).toHaveBeenCalled();
});
