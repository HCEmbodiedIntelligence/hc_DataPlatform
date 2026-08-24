import type {
  ResolvedViewerComposition,
  StreamDescriptor,
  ViewerPanelKind,
  ViewerPanelSpec,
} from "./types";

const panelKinds: Readonly<Record<string, ViewerPanelKind>> = {
  rgb: "video",
  depth: "depth",
  pointcloud: "pointcloud-preview",
  joint_state: "time-series",
  action: "time-series",
  force: "time-series",
  pose: "time-series",
  imu: "time-series",
  tactile: "time-series",
  event: "event-track",
  other: "unsupported",
};

const priorities: Readonly<Record<ViewerPanelKind, number>> = {
  video: 10,
  depth: 20,
  "pointcloud-preview": 30,
  "time-series": 40,
  "event-track": 50,
  unsupported: 90,
};

function availabilityState(stream: StreamDescriptor): ViewerPanelSpec["state"] {
  if (stream.availability === "preview-generating") return "pending";
  if (stream.availability === "partial") return "partial";
  if (stream.availability === "unsupported") return "unsupported";
  if (stream.availability === "missing") return "missing";
  return "ready";
}

export function resolveViewerComposition(
  streams: readonly StreamDescriptor[],
): ResolvedViewerComposition {
  const panels: ViewerPanelSpec[] = [];
  const jointGroups: ResolvedViewerComposition["jointGroups"][number][] = [];
  const diagnostics: ResolvedViewerComposition["diagnostics"][number][] = [];

  for (const stream of streams) {
    let hasDetailedJointAxes = false;
    if (stream.modality === "joint_state") {
      const axes = stream.schema.axes;
      const shapeWidth = stream.schema.shape?.[0];
      const indexes = axes?.map((axis) => axis.sampleIndex) ?? [];
      const ids = axes?.map((axis) => axis.axisId) ?? [];
      const invalid =
        !axes?.length ||
        (shapeWidth !== undefined && shapeWidth !== axes.length) ||
        new Set(indexes).size !== indexes.length ||
        new Set(ids).size !== ids.length ||
        indexes.some(
          (index) =>
            !Number.isInteger(index) || index < 0 || index >= axes.length,
        );
      if (invalid) {
        diagnostics.push({
          code: "JOINT_AXIS_CONTRACT_MISMATCH",
          streamId: stream.id,
          message:
            "关节轴身份、shape 或 sample index 不一致；轴卡片已安全停用，保留原始时序可视化。",
        });
      } else {
        jointGroups.push({
          streamId: stream.id,
          title: stream.displayName,
          axes,
        });
        hasDetailedJointAxes = true;
      }
    }

    const kind = panelKinds[stream.modality] ?? "unsupported";
    if (kind === "time-series" && hasDetailedJointAxes) continue;
    panels.push({
      panelId: `stream:${stream.id}`,
      kind,
      streamIds: [stream.id],
      title: stream.displayName,
      priority: priorities[kind],
      minWidthPx: kind === "event-track" || kind === "time-series" ? 420 : 320,
      aspectRatio:
        kind === "video" || kind === "depth" || kind === "pointcloud-preview"
          ? "16 / 9"
          : undefined,
      state: kind === "unsupported" ? "unsupported" : availabilityState(stream),
    });
  }

  const stable = <
    T extends {
      readonly streamIds: readonly string[];
      readonly priority: number;
    },
  >(
    a: T,
    b: T,
  ) =>
    a.priority - b.priority ||
    a.streamIds.join(":").localeCompare(b.streamIds.join(":"));
  panels.sort(stable);
  jointGroups.sort((a, b) => a.streamId.localeCompare(b.streamId));
  return { panels, jointGroups, diagnostics };
}
