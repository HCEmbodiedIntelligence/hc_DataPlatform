import { describe, expect, it } from "vitest";
import {
  episodeStreamWireSchema,
  type EpisodeRevisionWire,
} from "../../features/datasets/api/wire-schemas";
import { adaptP06ViewerStreams } from "./viewer-stream-adapter";

function revision(
  streams: EpisodeRevisionWire["streams"],
): EpisodeRevisionWire {
  return {
    scope: {
      organization_id: "org-p06",
      project_id: "project-p06",
      region_code: "region-p06",
    },
    dataset_id: "dataset_p06fixture",
    version_id: "version_p06fixture",
    episode_id: "episode_p06fixture",
    revision_id: "revision_p06fixture",
    ordinal: 0,
    content_sha256: "a".repeat(64),
    started_at_ns: "100",
    duration_ns: "1000",
    streams,
  };
}

describe("P06 viewer stream adapter", () => {
  it("uses only a server-projected camera binding to expose a lazy authorized MP4 source", () => {
    const [stream] = adaptP06ViewerStreams(
      revision([
        {
          episode_stream_id: "stream_p06camera",
          channel_path: "/camera/front/image_raw",
          kind: "RGB_VIDEO",
          t_start_ns: "100",
          t_end_ns: "1100",
          aligned_media_binding: {
            rollout_id: "rollout-p06",
            dataset_version: 7,
            artifact_id: "artifact-p06",
            camera_id: "front-rgb",
            fps: 30,
            start_step: 0,
            end_step: 30,
          },
          data_binding: null,
        },
      ]),
      "dataset_p06fixture",
    );

    expect(stream).toMatchObject({
      modality: "rgb",
      availability: "ready",
      canonicalPath: "/camera/front/image_raw",
    });
    expect(stream?.mediaSource).toBeDefined();
  });

  it("does not invent a rollout or a browser video when a camera has no immutable binding", () => {
    const [stream] = adaptP06ViewerStreams(
      revision([
        {
          episode_stream_id: "stream_p06unbound",
          channel_path: "/camera/rear/image_raw",
          kind: "VIDEO",
          t_start_ns: "100",
          t_end_ns: "1100",
          aligned_media_binding: null,
          data_binding: null,
        },
      ]),
      "dataset_p06fixture",
    );

    expect(stream).toMatchObject({ modality: "rgb", availability: "missing" });
    expect(stream?.mediaSource).toBeUndefined();
    expect(stream?.accessibleSummary).toContain("不会在页面打开时创建媒体任务");
  });

  it("marks an unbound non-camera modality as missing rather than claiming its format is unsupported", () => {
    const [stream] = adaptP06ViewerStreams(
      revision([
        {
          episode_stream_id: "stream_p06force",
          channel_path: "/force/wrench",
          kind: "FORCE",
          t_start_ns: "100",
          t_end_ns: "1100",
          aligned_media_binding: null,
          data_binding: null,
        },
      ]),
      "dataset_p06fixture",
    );

    expect(stream).toMatchObject({
      modality: "force",
      availability: "missing",
    });
    expect(stream?.accessibleSummary).toContain("可视化绑定");
  });

  it("uses only a server-projected non-camera binding to expose a lazy real Lance window source", () => {
    const [stream] = adaptP06ViewerStreams(
      revision([
        {
          episode_stream_id: "stream_p06joint",
          channel_path: "/joint_states/position",
          kind: "JOINT_STATE",
          t_start_ns: "100",
          t_end_ns: "1100",
          aligned_media_binding: null,
          data_binding: {
            rollout_id: "rollout-p06",
            lance_version: 7,
            modality_key: "joint.position",
            value_kind: "VECTOR",
            start_step: 0,
            end_step: 30,
          },
        },
      ]),
      "dataset_p06fixture",
    );

    expect(stream).toMatchObject({
      modality: "joint_state",
      availability: "ready",
    });
    expect(stream?.windowSource).toBeDefined();
    expect(stream?.mediaSource).toBeUndefined();
    expect(stream?.accessibleSummary).toContain("固定 Lance 版本");
  });

  it("fails closed on a malformed non-camera media binding before it can reach the viewer", () => {
    expect(
      episodeStreamWireSchema.safeParse({
        episode_stream_id: "stream_p06bad",
        channel_path: "/force/wrench",
        kind: "FORCE",
        t_start_ns: "100",
        t_end_ns: "1100",
        aligned_media_binding: {
          rollout_id: "rollout-p06",
          dataset_version: 7,
          artifact_id: "artifact-p06",
          camera_id: "front-rgb",
          fps: 30,
          start_step: 0,
          end_step: 30,
        },
      }),
    ).toMatchObject({ success: false });
  });

  it("fails closed on a camera data binding or an incompatible declared pointcloud decoder", () => {
    const base = {
      episode_stream_id: "stream_p06bad_data",
      channel_path: "/camera/front/image_raw",
      kind: "RGB_VIDEO",
      t_start_ns: "100",
      t_end_ns: "1100",
      aligned_media_binding: null,
      data_binding: {
        rollout_id: "rollout-p06",
        lance_version: 7,
        modality_key: "joint.position",
        value_kind: "VECTOR",
        start_step: 0,
        end_step: 30,
      },
    };
    expect(episodeStreamWireSchema.safeParse(base)).toMatchObject({
      success: false,
    });
    expect(
      episodeStreamWireSchema.safeParse({
        ...base,
        episode_stream_id: "stream_p06bad_points",
        channel_path: "/lidar/points",
        kind: "POINTCLOUD",
        data_binding: { ...base.data_binding, value_kind: "VECTOR" },
      }),
    ).toMatchObject({ success: false });
  });
});
