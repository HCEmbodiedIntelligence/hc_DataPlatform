import type {
  StreamDescriptor,
  ViewerStreamModality,
} from "../../features/viewer";
import type { EpisodeRevisionWire } from "../../features/datasets/api/wire-schemas";
import { createDatasetLanceWindowSource } from "./lance-window-source";
import { createDatasetPreviewMediaSource } from "./preview-media-source";

function modality(kind: string): ViewerStreamModality {
  const byContractKind: Readonly<Record<string, ViewerStreamModality>> = {
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
  return byContractKind[kind.trim().toUpperCase()] ?? "other";
}

/**
 * Adapt only server-projected collection-stream lineage into Viewer inputs.
 *
 * A missing binding remains visibly missing: this adapter never derives a
 * rollout ID from an episode ID or guesses an object URL from a channel name.
 */
export function adaptP06ViewerStreams(
  revision: EpisodeRevisionWire,
  datasetId: string,
): readonly StreamDescriptor[] {
  return revision.streams.map((stream) => {
    const streamModality = modality(stream.kind);
    const previewBinding = stream.preview_binding;
    const dataBinding = stream.data_binding;
    const previewModality =
      streamModality === "rgb" || streamModality === "depth"
        ? streamModality
        : null;
    const previewReady =
      previewModality !== null &&
      previewBinding !== null &&
      previewBinding !== undefined;
    const dataReady =
      previewModality === null &&
      streamModality !== "other" &&
      dataBinding !== null &&
      dataBinding !== undefined;
    return {
      id: stream.episode_stream_id,
      canonicalPath: stream.channel_path,
      displayName: stream.channel_path,
      modality: streamModality,
      schema: {
        id: `hc.${stream.kind.toLowerCase()}`,
        version: "contract-v1",
      },
      startNs: stream.t_start_ns,
      endNs: stream.t_end_ns,
      availability: previewReady || dataReady ? "ready" : "missing",
      accessibleSummary: previewReady
        ? `${stream.channel_path} 是固定 Lance 版本中的受权相机流；播放授权会在面板可见时按需签发。`
        : dataReady
          ? `${stream.channel_path} 是固定 Lance 版本中的受权数据流；可视化数据会在面板可见时按需读取。`
          : previewModality
            ? `${stream.channel_path} 没有可用的固定预览绑定，因此不会伪造浏览器视频。`
            : `${stream.channel_path} 尚未提供与固定 Lance 数据对应的可视化绑定。`,
      ...(previewReady
        ? {
            mediaSource: createDatasetPreviewMediaSource({
              scope: {
                organizationId: revision.scope.organization_id,
                projectId: revision.scope.project_id,
                regionCode: revision.scope.region_code,
              },
              datasetId,
              binding: previewBinding,
              modality: previewModality,
            }),
          }
        : {}),
      ...(dataReady
        ? {
            windowSource: createDatasetLanceWindowSource({
              scope: {
                organizationId: revision.scope.organization_id,
                projectId: revision.scope.project_id,
                regionCode: revision.scope.region_code,
              },
              datasetId,
              streamStartNs: stream.t_start_ns,
              streamEndNs: stream.t_end_ns,
              binding: dataBinding,
            }),
          }
        : {}),
    } satisfies StreamDescriptor;
  });
}
