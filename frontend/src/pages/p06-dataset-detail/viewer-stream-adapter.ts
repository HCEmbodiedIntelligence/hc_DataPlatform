import { bufferJointWindows } from "../../features/viewer/buffered-joint-window-source";
import type {
  StreamDescriptor,
  ViewerStreamModality,
} from "../../features/viewer";
import type { EpisodeRevisionWire } from "../../features/datasets/api/wire-schemas";
import { createDatasetLanceWindowSource } from "./lance-window-source";
import { createDatasetAlignedMediaSource } from "./aligned-media-source";

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
    const mediaBinding = stream.aligned_media_binding;
    const dataBinding = stream.data_binding;
    const mediaModality =
      streamModality === "rgb" || streamModality === "depth"
        ? streamModality
        : null;
    const mediaReady =
      mediaModality !== null &&
      mediaBinding !== null &&
      mediaBinding !== undefined;
    const dataReady =
      mediaModality === null &&
      streamModality !== "other" &&
      dataBinding !== null &&
      dataBinding !== undefined;
    const windowSource = dataReady
      ? createDatasetLanceWindowSource({
          scope: {
            organizationId: revision.scope.organization_id,
            projectId: revision.scope.project_id,
            regionCode: revision.scope.region_code,
          },
          datasetId,
          streamStartNs: stream.t_start_ns,
          streamEndNs: stream.t_end_ns,
          binding: dataBinding,
        })
      : undefined;
    return {
      id: stream.episode_stream_id,
      canonicalPath: stream.channel_path,
      displayName: mediaBinding?.camera_id ?? stream.channel_path,
      modality: streamModality,
      schema: {
        id: `hc.${stream.kind.toLowerCase()}`,
        version: "contract-v1",
      },
      startNs: stream.t_start_ns,
      endNs: stream.t_end_ns,
      availability: mediaReady || dataReady ? "ready" : "missing",
      accessibleSummary: mediaReady
        ? `${stream.channel_path} 是该 Dataset 版本的就绪视频；面板可见时只签发短期读取权限。`
        : dataReady
          ? `${stream.channel_path} 是固定 Lance 版本中的受权数据流；可视化数据会在面板可见时按需读取。`
          : mediaModality
            ? `${stream.channel_path} 尚无就绪视频，因此不会在页面打开时创建媒体任务。`
            : `${stream.channel_path} 尚未提供与固定 Lance 数据对应的可视化绑定。`,
      ...(mediaReady
        ? {
            mediaSource: createDatasetAlignedMediaSource({
              scope: {
                organizationId: revision.scope.organization_id,
                projectId: revision.scope.project_id,
                regionCode: revision.scope.region_code,
              },
              datasetId,
              binding: mediaBinding,
              modality: mediaModality,
            }),
          }
        : {}),
      ...(windowSource
        ? {
            windowSource:
              streamModality === "joint_state"
                ? bufferJointWindows(
                    windowSource,
                    stream.t_start_ns,
                    stream.t_end_ns,
                  )
                : windowSource,
          }
        : {}),
    } satisfies StreamDescriptor;
  });
}

/** Prefer the explicit joint-angle topic, as in the annotation workbench. */
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
