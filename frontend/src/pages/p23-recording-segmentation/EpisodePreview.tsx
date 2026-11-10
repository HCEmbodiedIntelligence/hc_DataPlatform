import { useMemo, useState } from "react";
import {
  CameraViewToolbar,
  DataVisualizationWorkbench,
  selectCameraStreams,
  type StreamDescriptor,
} from "../../features/viewer";
import { usePlaybackClock } from "../../features/viewer/use-playback-clock";
import { VideoPreviewModal } from "../../features/viewer/VideoPreviewModal";
import type { EditableSlice } from "./model";

/** Uses the annotation player, bounded to the selected Episode's original time range. */
export function EpisodePreview({
  slice,
  streams,
  onClose,
}: {
  readonly slice: EditableSlice;
  readonly streams: readonly StreamDescriptor[];
  readonly onClose: () => void;
}) {
  const [cameraView, setCameraView] = useState("__quad__");
  const clock = usePlaybackClock(String(slice.startNs), String(slice.endNs), slice.episodeId);
  const cameraStreams = useMemo(
    () => selectCameraStreams(streams, cameraView, clock),
    [streams, cameraView, clock],
  );
  return (
    <VideoPreviewModal
      open
      title={`查看 ${slice.title || slice.episodeId}`}
      onCancel={onClose}
    >
      <p>播放此 Episode 选定的录制区间。</p>
      <DataVisualizationWorkbench
        layout="preview"
        showNavigation={false}
        adapter={{
          id: `episode-preview-${slice.episodeId}`,
          mode: "episode-slicing",
          title: "视频预览",
          readOnly: true,
          clock,
          cameraStreams,
          collectionItems: [],
          findings: [],
          timelineTracks: [],
          actions: [],
        }}
        slots={{
          mediaHeader: () => (
            <CameraViewToolbar
              view={cameraView}
              cameras={streams.map((stream) => ({
                id: stream.id,
                label: stream.displayName,
              }))}
              visibleStreams={cameraStreams}
              onViewChange={setCameraView}
            />
          ),
        }}
      />
    </VideoPreviewModal>
  );
}
