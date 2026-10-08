import type {
  ResolvedViewerComposition,
  StreamDescriptor,
  ViewerPanelSpec,
} from "./types";
import { defaultViewerRendererCatalog } from "./builtin-renderers";
import type { ViewerRendererCatalog } from "./renderer-catalog";

function availabilityState(stream: StreamDescriptor): ViewerPanelSpec["state"] {
  if (stream.availability === "media-preparing") return "pending";
  if (stream.availability === "partial") return "partial";
  if (stream.availability === "unsupported") return "unsupported";
  if (stream.availability === "missing") return "missing";
  return "ready";
}

export function resolveViewerComposition(
  streams: readonly StreamDescriptor[],
  catalog: ViewerRendererCatalog = defaultViewerRendererCatalog,
): ResolvedViewerComposition {
  const panels: ViewerPanelSpec[] = [];
  const inputOrder = new Map(
    streams.map((stream, index) => [stream.id, index] as const),
  );
  const jointGroups: ResolvedViewerComposition["jointGroups"][number][] = [];
  const diagnostics: ResolvedViewerComposition["diagnostics"][number][] = [];

  for (const stream of streams) {
    const selected = catalog.select(stream);
    const registration = selected.registration;
    const descriptor = registration?.descriptor;
    let hasDetailedJointAxes = false;
    if (descriptor?.jointAxes) {
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

    const kind =
      registration?.implementation.kind === "builtin"
        ? registration.implementation.panelKind
        : registration
          ? "extension"
          : "unsupported";
    if (kind === "time-series" && hasDetailedJointAxes) continue;
    if (
      selected.reason === "ambiguous" ||
      selected.reason === "missing-input"
    ) {
      diagnostics.push({
        code:
          selected.reason === "ambiguous"
            ? "RENDERER_SELECTION_AMBIGUOUS"
            : "RENDERER_INPUT_UNAVAILABLE",
        streamId: stream.id,
        message:
          selected.reason === "ambiguous"
            ? "多个可视化能力具有相同优先级；此面板已停用，请明确能力优先级。"
            : "此可视化能力所需的数据源不可用；其他面板仍可使用。",
      });
    }
    panels.push({
      panelId: `stream:${stream.id}`,
      kind,
      renderer: descriptor
        ? { id: descriptor.id, version: descriptor.version }
        : undefined,
      streamIds: [stream.id],
      title: stream.displayName,
      priority: descriptor?.priority ?? 90,
      minWidthPx: descriptor?.minWidthPx ?? 320,
      aspectRatio: descriptor?.aspectRatio,
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
    (inputOrder.get(a.streamIds[0] ?? "") ?? Number.MAX_SAFE_INTEGER) -
      (inputOrder.get(b.streamIds[0] ?? "") ?? Number.MAX_SAFE_INTEGER) ||
    a.streamIds.join(":").localeCompare(b.streamIds.join(":"));
  panels.sort(stable);
  jointGroups.sort((a, b) => a.streamId.localeCompare(b.streamId));
  return { panels, jointGroups, diagnostics };
}
