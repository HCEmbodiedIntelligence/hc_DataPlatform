import { useEffect, useMemo, useState } from "react";
import {
  CameraViewToolbar,
  createPlaybackClock,
  DataVisualizationWorkbench,
  selectCameraStreams,
  type StreamDescriptor,
} from "../../features/viewer";
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
  const clock = useMemo(
    () =>
      createPlaybackClock({
        startNs: String(slice.startNs),
        endNs: String(slice.endNs),
      }),
    [slice.startNs, slice.endNs],
  );
  useEffect(() => () => clock.dispose(), [clock]);
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
