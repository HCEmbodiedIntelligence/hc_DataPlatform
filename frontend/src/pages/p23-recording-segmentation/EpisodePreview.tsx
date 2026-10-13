import { useEffect, useMemo, useState } from "react";
import { Checkbox } from "antd";
import {
  createPlaybackClock,
  DataVisualizationWorkbench,
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
  const [showDepth, setShowDepth] = useState(false);
  const clock = useMemo(
    () =>
      createPlaybackClock({
        startNs: String(slice.startNs),
        endNs: String(slice.endNs),
      }),
    [slice.startNs, slice.endNs],
  );
  useEffect(() => () => clock.dispose(), [clock]);
  const depth = streams.filter((stream) => stream.modality === "depth");
  return (
    <VideoPreviewModal
      open
      title={`查看 ${slice.title || slice.episodeId}`}
      onCancel={onClose}
    >
      <p>播放此 Episode 选定的录制区间。</p>
      <Checkbox
        checked={showDepth}
        disabled={!depth.length}
        onChange={(event) => setShowDepth(event.target.checked)}
      >
        显示深度视频（{depth.length} 路）
      </Checkbox>
      <DataVisualizationWorkbench
        layout="preview"
        showNavigation={false}
        adapter={{
          id: `episode-preview-${slice.episodeId}`,
          mode: "episode-slicing",
          title: "RGB 视频",
          readOnly: true,
          clock,
          cameraStreams: streams.filter(
            (stream) => stream.modality !== "depth",
          ),
          collectionItems: [],
          findings: [],
          timelineTracks: [],
          actions: [],
        }}
      />
      {showDepth && (
        <DataVisualizationWorkbench
          layout="preview"
          showNavigation={false}
          adapter={{
            id: `episode-depth-${slice.episodeId}`,
            mode: "episode-slicing",
            title: "深度视频",
            readOnly: true,
            clock,
            cameraStreams: depth,
            collectionItems: [],
            findings: [],
            timelineTracks: [],
            actions: [],
          }}
        />
      )}
    </VideoPreviewModal>
  );
}
