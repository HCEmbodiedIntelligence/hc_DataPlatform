import type {
  StreamDescriptor,
  ViewerTimelineTrack,
} from "../../features/viewer";
import type { RecordingVideoSource } from "./api";
import type { EditableSlice } from "./model";

function authorizedMedia(source: RecordingVideoSource) {
  return {
    url: source.source_url,
    expiresAt: source.expires_at,
    kind: source.modality === "depth" ? "depth-preview" : "rgb-video",
  };
}

export function buildRecordingCameraStreams(input: {
  readonly sources: readonly RecordingVideoSource[];
  readonly refreshSource: (
    assetId: string,
    signal: AbortSignal,
  ) => Promise<RecordingVideoSource>;
}): readonly StreamDescriptor[] {
  return input.sources.map((source) => ({
    id: source.asset_id,
    canonicalPath: `recording/${source.recording_id}/camera/${source.camera_id}`,
    displayName: source.camera_id,
    modality: source.modality ?? "rgb",
    semanticRole: "camera",
    schema: {
      id: source.media_type,
      version: source.schema_version,
      encoding: source.codec,
    },
    rateHz: source.fps,
    startNs: "0",
    endNs: source.duration_ns,
    availability: source.preview_status === "UNAVAILABLE" ? "missing" : "ready",
    accessibleSummary:
      source.preview_status === "UNAVAILABLE"
        ? "深度预览尚未生成，原始深度仍保留。"
        : source.modality === "depth"
          ? `${source.camera_id}，RealSense 官方动态着色预览；颜色随帧内分布变化，不代表固定距离。无效深度显示为黑色。`
          : `${source.camera_id} RGB 录制，${source.fps} fps，${source.codec}`,
    mediaSource:
      source.preview_status === "UNAVAILABLE"
        ? undefined
        : {
            authorize: async () => authorizedMedia(source),
            refresh: async (signal) =>
              authorizedMedia(
                await input.refreshSource(source.asset_id, signal),
              ),
          },
  }));
}

export function buildEpisodeTimelineTracks(
  slices: readonly EditableSlice[],
): readonly ViewerTimelineTrack[] {
  return [
    {
      id: "episodes",
      label: "Episode",
      segments: slices.map((slice) => ({
        id: slice.episodeId,
        label: slice.title || slice.episodeId,
        startNs: String(slice.startNs),
        endNs: String(slice.endNs),
        activatePlayback: true,
        tone: "action" as const,
      })),
    },
  ];
}
