import type {
  StreamDescriptor,
  ViewerStreamModality,
  ViewerWindowSource,
} from "../../viewer";
import { bufferJointWindows } from "../../viewer/buffered-joint-window-source";
import { getShellState } from "../../../shared/scope/shell-store";
import { createSessionScopeLease } from "../../../shared/api/session-scope-lease";
import {
  authorizeLogicalMedia,
  fetchLogicalSamples,
  type LogicalSelection,
  type LogicalSnapshot,
  type LogicalStream,
} from "../api/logical-data";
import {
  decodeTemporalValues,
  stepWindowForTime,
  type StepValueBinding,
} from "./step-window";

const modalities: Readonly<Record<string, ViewerStreamModality>> = {
  VIDEO: "rgb",
  RGB: "rgb",
  RGB_VIDEO: "rgb",
  DEPTH: "depth",
  POINTCLOUD: "pointcloud",
  JOINT_STATE: "joint_state",
  ACTION: "action",
  FORCE: "force",
  POSE: "pose",
  IMU: "imu",
  TACTILE: "tactile",
  EVENT: "event",
};
function supportedValue(
  kind: string | null | undefined,
): kind is StepValueBinding["value_kind"] {
  return (
    kind === "SCALAR" ||
    kind === "VECTOR" ||
    kind === "POINTCLOUD_XYZ" ||
    kind === "EVENT"
  );
}

function windowSource(
  selection: LogicalSelection,
  view: LogicalSnapshot,
  stream: LogicalStream,
  valueKind: StepValueBinding["value_kind"],
): ViewerWindowSource {
  return {
    async loadWindow(window, signal) {
      const bounds = stepWindowForTime(window, {
        streamStartNs: stream.start_ns!,
        streamEndNs: stream.end_ns!,
        binding: { start_step: 0, end_step: stream.sample_count },
      });
      const lease = createSessionScopeLease(selection.scope, signal);
      try {
        const samples = [];
        for (
          let start = bounds.startStep;
          start < bounds.endStep;
          start += 4096
        ) {
          lease.signal.throwIfAborted();
          samples.push(
            ...(await fetchLogicalSamples(
              selection,
              view,
              stream,
              start,
              Math.min(start + 4096, bounds.endStep),
              lease.signal,
            )),
          );
        }
        lease.signal.throwIfAborted();
        return decodeTemporalValues(
          samples.map((s) => ({
            timestampNs: s.timestamp_ns!,
            value: s.value,
            valid: s.valid,
          })),
          valueKind,
        );
      } finally {
        lease.release();
      }
    },
  };
}

/** Production viewer consumes only the frozen logical contract, independent of storage. */
export function adaptLogicalViewerStreams(
  selection: LogicalSelection,
  view: LogicalSnapshot,
): readonly StreamDescriptor[] {
  const session = getShellState();
  // Check before cache access as well as transport: a retained viewer must not
  // expose already buffered samples after logout, revocation or reauthorization.
  const assertAuthorization = () => {
    const current = getShellState();
    if (
      current.sessionRevision !== session.sessionRevision ||
      current.capabilityRevision !== session.capabilityRevision
    )
      throw new DOMException(
        "Logical viewer authorization changed",
        "AbortError",
      );
  };
  const leaseFor = (signal: AbortSignal) => {
    assertAuthorization();
    return createSessionScopeLease(selection.scope, signal);
  };
  return view.snapshot.streams.flatMap((stream): StreamDescriptor[] => {
    // Independent samples need a sample navigator; never invent timestamps for the timeline.
    if (!stream.temporal || stream.start_ns == null || stream.end_ns == null)
      return [];
    const modality =
      modalities[stream.kind.toUpperCase()] ?? stream.kind.toLowerCase();
    const numeric = supportedValue(stream.value_kind);
    const media =
      stream.value_kind == null && (modality === "rgb" || modality === "depth");
    const available = stream.sample_count > 0;
    const source =
      numeric && available
        ? windowSource(
            selection,
            view,
            stream,
            stream.value_kind as StepValueBinding["value_kind"],
          )
        : null;
    const authorize = async (signal: AbortSignal) => {
      const lease = leaseFor(signal);
      try {
        lease.signal.throwIfAborted();
        const grant = await authorizeLogicalMedia(
          selection,
          view,
          stream,
          lease.signal,
        );
        lease.signal.throwIfAborted();
        assertAuthorization();
        return {
          url: grant.media_url,
          expiresAt: grant.expires_at,
          kind: grant.kind === "video" ? "rgb-video" : "depth-preview",
          mediaStartSeconds: grant.media_start_seconds,
          mediaEndSeconds:
            grant.media_start_seconds +
            grant.sample_count / grant.sample_rate_hz,
        };
      } finally {
        lease.release();
      }
    };
    const buffered =
      source && modality === "joint_state"
        ? bufferJointWindows(source, stream.start_ns, stream.end_ns)
        : source;
    return [
      {
        id: stream.stream_id,
        canonicalPath: stream.name,
        displayName: stream.name,
        modality,
        schema: {
          id: `hc.${stream.kind.toLowerCase()}`,
          version: "logical-v1",
        },
        startNs: stream.start_ns,
        endNs: stream.end_ns,
        availability: !available
          ? "missing"
          : numeric || media
            ? "ready"
            : "unsupported",
        accessibleSummary: !available
          ? "固定快照中没有可读取的样本。"
          : modality === "depth"
            ? "深度预览按固定快照授权；颜色用于查看，测量请读取物理值，完整包保留深度及有效掩码。"
            : numeric || media
              ? "来自当前固定逻辑快照，显示时按需读取。"
              : "当前查看器尚不支持此数据流的展示方式，原始数据仍保留。",
        ...(source
          ? {
              windowSource: {
                async loadWindow(window, signal) {
                  const lease = leaseFor(signal);
                  try {
                    lease.signal.throwIfAborted();
                    const payload = await buffered!.loadWindow(
                      window,
                      lease.signal,
                    );
                    lease.signal.throwIfAborted();
                    assertAuthorization();
                    return payload;
                  } finally {
                    lease.release();
                  }
                },
              },
            }
          : {}),
        ...(media && available
          ? { mediaSource: { authorize, refresh: authorize } }
          : {}),
      },
    ];
  });
}

export function selectEpisodeJointStream(
  streams: readonly StreamDescriptor[],
): StreamDescriptor | null {
  const joints = streams.filter((stream) => stream.modality === "joint_state");
  return (
    joints.find((stream) =>
      /(^|[/_.-])joint([/_\s.-]|$)/iu.test(stream.canonicalPath),
    ) ??
    joints[0] ??
    null
  );
}
