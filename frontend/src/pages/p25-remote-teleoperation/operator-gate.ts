import type { DesktopInput } from "./desktop-session";

export function createOperatorGate(
  pc: RTCPeerConnection,
  session: string,
  profile: string,
  life: AbortSignal,
  inputs: () => { valid: boolean; released: boolean },
  report: DesktopInput["operator"],
) {
  const events = pc.createDataChannel("hc-operator.v1");
  let compatible = false,
    enabled = false,
    pending = "",
    deadline = 0;
  let message = "仅预览：等待机器人确认控制协议",
    previous = "";
  const send = (type: string, id: string) => {
    if (events.readyState === "open")
      events.send(
        JSON.stringify({
          version: 1,
          session_id: session,
          type,
          request_id: id,
          input_profile: profile,
        }),
      );
  };
  const ready = () =>
    !life.aborted &&
    compatible &&
    events.readyState === "open" &&
    inputs().valid;
  const disable = () => {
    enabled = false;
    pending = "";
    send("disable", crypto.randomUUID());
    message = "输入已停用";
  };
  const controls = {
    enable() {
      if (!ready() || enabled || pending) return;
      if (!inputs().released) {
        message = "请松开左右握把后再使能";
        return;
      }
      pending = crypto.randomUUID();
      deadline = performance.now() + 3000;
      message = "等待机器人使能回执";
      send("enable", pending);
    },
    disable,
  };
  const pause = () => {
    if (pending || enabled) disable();
  };
  const tick = () => {
    if (!ready() || (pending && performance.now() >= deadline)) pause();
    const canEnable = ready() && !pending;
    const next = JSON.stringify([canEnable, enabled, message]);
    if (previous !== next && !life.aborted) {
      previous = next;
      report(controls, canEnable, enabled, message);
    }
    return enabled && ready();
  };
  events.onopen = () => send("hello", crypto.randomUUID());
  events.onmessage = ({ data }) => {
    if (life.aborted || typeof data !== "string" || data.length > 4096) return;
    let r;
    try {
      r = JSON.parse(data);
    } catch {
      return;
    }
    if (!r || r.version !== 1 || r.session_id !== session) return;
    if (r.type === "ready" && r.input_profile === profile) {
      if (!compatible) message = "已就绪，确认设备和画面后可使能";
      compatible = true;
    } else if (r.type === "disabled") {
      pending = "";
      enabled = false;
      message = "机器人已停用";
    } else if (r.type === "enabled" && pending && r.request_id === pending) {
      if (ready() && inputs().released && performance.now() < deadline) {
        pending = "";
        enabled = true;
        message = "机器人已使能";
      } else disable();
    }
    tick();
  };
  events.onclose = () => {
    pending = "";
    enabled = false;
    compatible = false;
    message = "控制回执通道关闭";
    tick();
  };
  life.addEventListener(
    "abort",
    () => {
      enabled = false;
      pending = "";
      events.close();
      report(null, false, false, "");
    },
    { once: true },
  );
  return { tick, pause, controls };
}
