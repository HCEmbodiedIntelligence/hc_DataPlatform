import type { ViewerTimelineTrack } from "../../features/viewer";
import type { EditableSlice } from "./model";
export { buildRecordingCameraStreams } from "../../features/viewer/recording-camera-streams";

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
