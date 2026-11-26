import { expect, it, vi } from "vitest";
import { createOperatorGate } from "./operator-gate";
import { freshSample, type LocalState } from "./workstation";

it("requires matching profile, manual request and matching live acknowledgment", () => {
  const packets: Record<string, string>[] = [];
  const events = {
    readyState: "open",
    send: (raw: string) => packets.push(JSON.parse(raw)),
    close: vi.fn(),
    onmessage: undefined as unknown as (m: { data: string }) => void,
  };
  const pc = {
    createDataChannel: () => events,
  } as unknown as RTCPeerConnection;
  let valid = true;
  const gate = createOperatorGate(
    pc,
    "session",
    "webxr",
    new AbortController().signal,
    () => ({ valid, released: true }),
    vi.fn(),
  );
  const reply = (msg: object) =>
    events.onmessage({
      data: JSON.stringify({ version: 1, session_id: "session", ...msg }),
    });
  expect(gate.tick()).toBe(false);
  gate.controls.enable();
  expect(packets).toHaveLength(0);
  reply({ type: "ready", input_profile: "webxr" });
  gate.controls.enable();
  const request = packets[0]!;
  expect(request.type).toBe("enable");
  reply({ type: "enabled", request_id: "wrong" });
  expect(gate.tick()).toBe(false);
  reply({ type: "enabled", request_id: request.request_id });
  expect(gate.tick()).toBe(true);
  valid = false;
  expect(gate.tick()).toBe(false);
  expect(packets.at(-1)?.type).toBe("disable");
  valid = true;
  reply({ type: "enabled", request_id: request.request_id });
  expect(gate.tick()).toBe(false);
});

it("drops frames buffered between the receiver and the browser", () => {
  const frame = { sequence: 17 };
  const state = { sent_ms: 1000, age_ms: 50, frame } as LocalState;
  expect(freshSample(state, 1050)).toBe(frame);
  expect(freshSample(state, 1600)).toBeNull();
  expect(freshSample(state, 500)).toBeNull();
});
