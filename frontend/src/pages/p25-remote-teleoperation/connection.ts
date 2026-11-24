import { request, type RequestMethod } from "../../shared/api/http-client";
import type { components } from "../../shared/api/generated/platform";
import type { DeviceStatus } from "./vr-view";

type Schema = components["schemas"];
export type OnlineRobot = Schema["RobotPresence"];

function api<T>(
  org: string,
  suffix: string,
  method: RequestMethod,
  body?: unknown,
  signal?: AbortSignal,
) {
  return request<T>({
    method,
    scopeMode: "organization",
    scope: { organizationId: org },
    path: `/organizations/${encodeURIComponent(org)}/teleoperation/robots${suffix}`,
    body,
    signal,
    cache: "no-store",
  });
}

export function onlineRobots(org: string, signal: AbortSignal) {
  return api<OnlineRobot[]>(org, "", "GET", undefined, signal);
}

function pause(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    signal.throwIfAborted();
    const aborted = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", aborted);
      resolve();
    }, ms);
    signal.addEventListener("abort", aborted, { once: true });
  });
}

export async function connectVR(
  org: string,
  robot: string,
  signal: AbortSignal,
  report: (phase: string) => void,
  devices: (status: DeviceStatus) => void,
) {
  const xrSystem = navigator.xr;
  if (!xrSystem) throw new Error("浏览器无法访问本机 VR");
  // Keep the user activation for the immersive permission prompt.
  const xr = await xrSystem.requestSession("immersive-vr");
  if (signal.aborted) {
    await xr.end();
    return;
  }
  const life = new AbortController();
  const active = AbortSignal.any([signal, life.signal]);
  const root = `/${encodeURIComponent(robot)}/sessions`;
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  let pc: RTCPeerConnection | undefined;
  let id: string | undefined;
  const end = () => life.abort();
  xr.addEventListener("end", end, { once: true });
  const closePeer = () => {
    pc?.close();
    video.pause();
    video.srcObject = null;
    void xr.end().catch(() => {});
  };
  active.addEventListener("abort", closePeer, { once: true });
  try {
    report("正在建立连接");
    const assigned = await api<Schema["Assignment"]>(
      org,
      root,
      "POST",
      undefined,
      AbortSignal.any([active, AbortSignal.timeout(6000)]),
    );
    if (!assigned.session_id) throw new Error("平台未分配连接会话");
    id = assigned.session_id;
    active.throwIfAborted();
    const path = `${root}/${id}/sync`;
    pc = new RTCPeerConnection({
      iceServers: (assigned.ice_servers ?? []).map((s) => ({
        urls: s.urls,
        username: s.username ?? undefined,
        credential: s.credential ?? undefined,
      })),
    });
    pc.addTransceiver("video", { direction: "recvonly" });
    const channel = pc.createDataChannel("vr-control.v1", {
      ordered: false,
      maxRetransmits: 0,
    });
    const fail = (message: string) => life.abort(new Error(message));
    channel.onclose = () => fail("控制通道已断开");
    channel.onerror = () => fail("控制通道异常");
    pc.onconnectionstatechange = () => {
      if (
        pc?.connectionState === "failed" ||
        pc?.connectionState === "disconnected"
      )
        fail("机器人网络连接已中断");
    };
    pc.ontrack = ({ track }) => {
      if (track.kind !== "video") return;
      track.onended = () => fail("机器人视频轨已结束");
      video.srcObject = new MediaStream([track]);
      void video.play().catch(() => fail("无法播放机器人视频"));
    };
    const { showInHeadset } = await import("./vr-view");
    await showInHeadset(xr, video, channel, id, active, devices);
    active.throwIfAborted();
    await pc.setLocalDescription(await pc.createOffer());
    const gathering = AbortSignal.any([active, AbortSignal.timeout(9000)]);
    while (pc.iceGatheringState !== "complete") await pause(80, gathering);
    let offer = pc.localDescription?.sdp;
    if (!offer) throw new Error("本机未能生成 WebRTC 描述");
    const deadline = performance.now() + 30000;
    while (!active.aborted) {
      const state = await api<Schema["OperatorState"]>(
        org,
        path,
        "POST",
        offer ? { offer } : {},
        AbortSignal.any([active, AbortSignal.timeout(6000)]),
      );
      active.throwIfAborted();
      offer = undefined;
      if (state.answer && !pc.remoteDescription) {
        await pc.setRemoteDescription({ type: "answer", sdp: state.answer });
      }
      const connected =
        pc.connectionState === "connected" && channel.readyState === "open";
      report(connected ? "已连接" : "等待机器人建立通道");
      if (!connected && performance.now() > deadline)
        throw new Error("机器人连接超时，请检查网络和 ICE 配置");
      await pause(connected ? 3000 : 750, active);
    }
    active.throwIfAborted();
  } catch (error) {
    if (!active.aborted || active.reason?.name !== "AbortError")
      throw active.aborted ? active.reason : error;
  } finally {
    xr.removeEventListener("end", end);
    life.abort();
    if (id) void api(org, `${root}/${id}`, "DELETE").catch(() => {});
  }
}
