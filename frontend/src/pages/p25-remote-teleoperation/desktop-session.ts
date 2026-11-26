import { createOperatorGate } from "./operator-gate";
import type { DeviceStatus } from "./vr-view";
import { freshSample, type LocalState, type Sample } from "./workstation";

export interface OperatorControls {
  enable(): void;
  disable(): void;
}
export interface DesktopInput {
  video: HTMLVideoElement;
  latest(): LocalState | null;
  operator(
    controls: OperatorControls | null,
    ready: boolean,
    enabled: boolean,
    text: string,
  ): void;
}

export function asControlSample(frame: Sample, session: string, seq: number) {
  const pose = (key: keyof Sample["poses"]) => ({
    position: frame.poses[key].position,
    orientation: frame.poses[key].quaternion,
  });
  return {
    version: 1,
    session_id: session,
    seq,
    time_ms: performance.now(),
    head: pose("head"),
    controllers: (["left", "right"] as const).map((hand) => {
      const input = frame.inputs[hand];
      return {
        hand,
        profile: "hc-pico-v2",
        grip: pose(hand),
        buttons: [
          input.trigger,
          input.grip,
          0,
          (input.held_mask >> 5) & 1,
          input.held_mask & 1,
          (input.held_mask >> 1) & 1,
        ],
        axes: [...input.secondary_axis, ...input.primary_axis],
      };
    }),
  };
}

export function startDesktop(
  pc: RTCPeerConnection,
  motion: RTCDataChannel,
  session: string,
  life: AbortSignal,
  input: DesktopInput,
  status: (info: DeviceStatus) => void,
) {
  const video = input.video;
  let lastVideo = -Infinity,
    seq = 0,
    lastInput = -1;
  let lastReport = "";
  const newFrame = () => {
    lastVideo = performance.now();
    callback = video.requestVideoFrameCallback(newFrame);
  };
  let callback = video.requestVideoFrameCallback(newFrame);
  const sample = () => {
    const state = freshSample(input.latest());
    return !document.hidden &&
      performance.now() - lastVideo < 500 &&
      motion.readyState === "open" &&
      state?.protocol_version === 2 &&
      state.tracking.head &&
      state.tracking.left &&
      state.tracking.right
      ? state
      : null;
  };
  const gate = createOperatorGate(
    pc,
    session,
    "hc-pico-v2",
    life,
    () => {
      const f = sample();
      return {
        valid: !!f,
        released:
          !!f && f.inputs.left.grip <= 0.1 && f.inputs.right.grip <= 0.1,
      };
    },
    input.operator,
  );
  const visibility = () => {
    if (document.hidden) gate.pause();
  };
  document.addEventListener("visibilitychange", visibility);
  const tick = () => {
    const frame = sample(),
      fresh = performance.now() - lastVideo < 500,
      open = motion.readyState === "open";
    if (
      gate.tick() &&
      frame &&
      motion.bufferedAmount === 0 &&
      lastInput !== frame.sequence
    ) {
      lastInput = frame.sequence;
      motion.send(JSON.stringify(asControlSample(frame, session, ++seq)));
    }
    const flags = JSON.stringify([fresh, open]);
    if (flags !== lastReport) {
      lastReport = flags;
      status({ video: fresh, control: open, inputs: [] });
    }
  };
  const timer = window.setInterval(tick, 16);
  life.addEventListener(
    "abort",
    () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", visibility);
      video.cancelVideoFrameCallback(callback);
    },
    { once: true },
  );
}
