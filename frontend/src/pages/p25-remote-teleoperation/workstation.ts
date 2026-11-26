const base = "http://127.0.0.1:47653";
export class PairingRequired extends Error {}

export const ticketKey = "hc.teleop.ticket";
const grantKey = "hc.teleop.grant";

type Hand = "left" | "right";
type Pose = { position: number[]; quaternion: number[] };
export type Sample = {
  protocol_version: number;
  sequence: number;
  tracking: Record<Hand | "head", boolean>;
  poses: Record<Hand | "head", Pose>;
  inputs: Record<
    Hand,
    {
      trigger: number;
      grip: number;
      primary_axis: number[];
      secondary_axis: number[];
      held_mask: number;
      pressed_mask: number;
      released_mask: number;
    }
  >;
};
export type LocalState = {
  version: number;
  workstation: string;
  peer: string;
  error: string;
  age_ms: number | null;
  sent_ms: number;
  frame: Sample | null;
};

let pairing: { ticket: string; pending: Promise<string> } | undefined;

function redeem(ticket: string) {
  if (pairing?.ticket === ticket) return pairing.pending;
  const pending = (async () => {
    const response = await fetch(base + "/pair", {
      method: "POST",
      headers: { Authorization: "Bearer " + ticket },
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok)
      throw new PairingRequired("配对码已失效，请从客户端复制新码。");
    const { grant } = await response.json();
    if (typeof grant !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(grant))
      throw new Error("客户端版本不兼容");
    return grant;
  })().finally(() => {
    if (pairing?.ticket === ticket) pairing = undefined;
  });
  pairing = { ticket, pending };
  return pending;
}

export async function readWorkstation(
  scope: string,
  signal: AbortSignal,
  update: (state: LocalState) => void,
) {
  const ticket = sessionStorage.getItem(ticketKey);
  const saved = JSON.parse(sessionStorage.getItem(grantKey) || "null") as {
    scope: string;
    grant: string;
  } | null;
  let grant = saved?.scope === scope ? saved.grant : "";
  if (ticket) {
    grant = await redeem(ticket);
    signal.throwIfAborted();
    sessionStorage.removeItem(ticketKey);
    sessionStorage.setItem(grantKey, JSON.stringify({ scope, grant }));
  }
  if (!grant)
    throw new PairingRequired("请从本机客户端打开网页，或粘贴配对码。");
  const response = await fetch(base + "/stream", {
    headers: { Authorization: "Bearer " + grant },
    credentials: "omit",
    cache: "no-store",
    redirect: "error",
    signal,
  });
  if (response.status === 409)
    throw new Error("另一标签页正在读取本机输入，等待它释放连接。");
  if (!response.ok || !response.body) {
    if (response.status === 401) sessionStorage.removeItem(grantKey);
    throw new PairingRequired("本机连接已失效，请重新配对。");
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let pending = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) throw new Error("客户端已断开，请检查并重新连接。");
      pending += value;
      if (pending.length > 1024 * 1024) throw new Error("客户端发送的数据过大");
      const last = pending.lastIndexOf("\n");
      if (last < 0) continue;
      // Only expose the newest complete frame from a buffered network read.
      const first = pending.lastIndexOf("\n", last - 1);
      const state = JSON.parse(pending.slice(first + 1, last)) as LocalState;
      pending = pending.slice(last + 1);
      if (state.version !== 1 || !Number.isFinite(state.sent_ms))
        throw new Error("不支持的客户端协议");
      if (Math.abs(Date.now() - state.sent_ms) > 500) state.frame = null;
      update(state);
    }
  } finally {
    reader.releaseLock();
  }
}

export function freshSample(
  state: LocalState | null,
  now = Date.now(),
): Sample | null {
  if (!state?.frame || state.age_ms === null) return null;
  const transport = now - state.sent_ms;
  return transport >= -25 && Math.max(0, transport) + state.age_ms < 350
    ? state.frame
    : null;
}
