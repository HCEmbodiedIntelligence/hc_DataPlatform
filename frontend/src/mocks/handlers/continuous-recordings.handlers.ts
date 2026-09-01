import { http, HttpResponse } from "msw";
import type { components } from "../../shared/api/generated/platform";

type ContinuousRecording = components["schemas"]["ContinuousRecording"];
type RecordingSliceRevision = components["schemas"]["RecordingSliceRevision"];
type RecordingSensorWindow = components["schemas"]["RecordingSensorWindow"];

const root =
  "*/api/v1/projects/:projectId/regions/:regionCode/continuous-recordings";

function recording(
  projectId: string,
  regionCode: string,
  input: Readonly<{
    id: string;
    status: ContinuousRecording["status"];
    revision: number;
    finalizedRevision?: number;
  }>,
): ContinuousRecording {
  return {
    schema_version: "continuous-recording/v2",
    scope: {
      organization_id: "org_fx_01",
      project_id: projectId,
      region_code: regionCode,
    },
    recording_id: input.id,
    recording_upload_id: "11111111-1111-4111-8111-111111111111",
    upload_session_id: null,
    rollout_id: `rollout-${input.id}`,
    data_package_id: `package-${input.id}`,
    collection_task_id: "collection-task-fx-01",
    collection_job_id: "collection-job-fx-01",
    robot_id: "robot_fx_01",
    device_id: "device-fx-01",
    capture_started_at: "2026-08-31T01:00:00Z",
    capture_ended_at: "2026-08-31T01:01:30Z",
    duration_ns: "90000000000",
    source_sha256: "a".repeat(64),
    manifest_fingerprint: "b".repeat(64),
    video_asset_count: 2,
    status: input.status,
    current_revision: input.revision,
    finalized_revision: input.finalizedRevision ?? null,
    etag: `"${input.id}:v${input.revision + 1}"`,
    created_at: "2026-08-31T01:02:00Z",
    updated_at: "2026-08-31T01:05:00Z",
  };
}

function recordings(projectId: string, regionCode: string) {
  return [
    recording(projectId, regionCode, {
      id: "recording_pending_fx_01",
      status: "READY_FOR_SLICING",
      revision: 0,
    }),
    recording(projectId, regionCode, {
      id: "recording_review_fx_02",
      status: "READY_FOR_SLICING",
      revision: 1,
    }),
    recording(projectId, regionCode, {
      id: "recording_sliced_fx_03",
      status: "SLICED",
      revision: 2,
      finalizedRevision: 2,
    }),
  ];
}

function draft(recording: ContinuousRecording): RecordingSliceRevision | null {
  if (recording.current_revision === 0) return null;
  return {
    schema_version: "recording-slice-revision/v1",
    scope: recording.scope,
    recording_id: recording.recording_id,
    revision: recording.current_revision,
    status: recording.status === "SLICED" ? "FINALIZED" : "DRAFT",
    authoring_mode: "HUMAN",
    created_by: "fixture-operator",
    slices: [
      {
        schema_version: "recording-episode-slice/v1",
        scope: recording.scope,
        recording_id: recording.recording_id,
        revision: recording.current_revision,
        ordinal: 0,
        episode_id: "episode_0001",
        start_offset_ns: "5000000000",
        end_offset_ns: "28000000000",
        started_at: "2026-08-31T01:00:05Z",
        ended_at: "2026-08-31T01:00:28Z",
        source_sha256: recording.source_sha256,
        source_upload_session_id: null,
        source_recording_upload_id: recording.recording_upload_id,
        title: "抓取并放置",
        task_label: "pick-and-place",
        notes: "检查抓取结束边界。",
      },
    ],
  };
}

export default [
  http.get(root, ({ params }) => {
    const items = recordings(
      String(params.projectId),
      String(params.regionCode),
    );
    return HttpResponse.json({ items, total: items.length });
  }),
  http.get(`${root}/:recordingId/video-sources`, ({ params }) => {
    const recordingId = String(params.recordingId);
    return HttpResponse.json({
      sources: ["front", "wrist"].map((cameraId, index) => ({
        schema_version: "recording-video-source/v1" as const,
        recording_id: recordingId,
        asset_id:
          index === 0
            ? "22222222-2222-4222-8222-222222222222"
            : "33333333-3333-4333-8333-333333333333",
        camera_id: cameraId,
        media_type: "video/mp4",
        source_url: `/fixtures/recordings/${recordingId}/${cameraId}.mp4`,
        duration_ns: "90000000000",
        fps: 30,
        codec: "h264",
        expires_at: "2026-09-01T23:59:59Z",
        byte_range_supported: true,
        materialization: "ORIGINAL_RECORDING" as const,
      })),
    });
  }),
  http.get(`${root}/:recordingId/sensor-window`, ({ params, request }) => {
    const url = new URL(request.url);
    const startOffsetNs = url.searchParams.get("start_offset_ns") ?? "0";
    const endOffsetNs = url.searchParams.get("end_offset_ns") ?? "4000000000";
    const start = Number(startOffsetNs);
    const end = Number(endOffsetNs);
    const stepNs = 100_000_000;
    const samples: RecordingSensorWindow["samples"] = [];
    for (let offset = start; offset < end; offset += stepNs) {
      const seconds = offset / 1_000_000_000;
      samples.push({
        offset_ns: String(offset),
        source_timestamp_ns: (
          1_788_138_000_000_000_000n + BigInt(offset)
        ).toString(),
        value: {
          name: ["shoulder", "elbow", "wrist"],
          position: [
            Math.sin(seconds) * 0.35,
            Math.cos(seconds * 0.8) * 0.5,
            Math.sin(seconds * 1.4) * 0.2,
          ],
        },
      });
    }
    return HttpResponse.json<RecordingSensorWindow>({
      schema_version: "recording-sensor-window/v1",
      recording_id: String(params.recordingId),
      topic: "/robot/joint_states",
      start_offset_ns: startOffsetNs,
      end_offset_ns: endOffsetNs,
      samples,
      truncated: false,
    });
  }),
  http.get(`${root}/:recordingId/episodes`, () =>
    HttpResponse.json({ items: [], total: 0 }),
  ),
  http.get(`${root}/:recordingId`, ({ params }) => {
    const item = recordings(
      String(params.projectId),
      String(params.regionCode),
    ).find(
      (candidate) => candidate.recording_id === String(params.recordingId),
    );
    return item
      ? HttpResponse.json({ data: item, current_slice_revision: draft(item) })
      : HttpResponse.json({ detail: "Recording not found" }, { status: 404 });
  }),
];
