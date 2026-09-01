import { describe, expect, it, vi } from "vitest";
import { buildJointFrameSource } from "../../features/viewer";
import type { RecordingGateway, RecordingScope } from "./api";
import { buildRecordingJointAngleStream } from "./raw-joint-angle-stream";

const scope: RecordingScope = {
  organizationId: "org-test",
  projectId: "project-test",
  regionCode: "cn-test",
};

describe("raw recording joint-angle stream", () => {
  it("reads original MCAP windows and drives the shared robot frame source", async () => {
    const sensorWindow = vi.fn(
      async (
        _scope: RecordingScope,
        recordingId: string,
        query: { readonly startOffsetNs: string; readonly endOffsetNs: string },
      ) => ({
        schema_version: "recording-sensor-window/v1" as const,
        recording_id: recordingId,
        topic: "/robot/joint_states",
        start_offset_ns: query.startOffsetNs,
        end_offset_ns: query.endOffsetNs,
        samples: [
          {
            offset_ns: "1000000000",
            source_timestamp_ns: "1788138001000000000",
            value: {
              name: ["shoulder", "elbow"],
              position: [0.25, -0.5],
            },
          },
          {
            offset_ns: "2000000000",
            source_timestamp_ns: "1788138002000000000",
            value: {
              name: ["shoulder", "elbow"],
              position: [0.5, -1],
            },
          },
        ],
        truncated: false,
      }),
    );
    const gateway = { sensorWindow } as unknown as RecordingGateway;
    const stream = buildRecordingJointAngleStream({
      gateway,
      scope,
      recordingId: "recording-test",
      durationNs: "90000000000",
    });

    const payload = await stream.windowSource?.loadWindow(
      { startNs: "0", endNs: "4000000000", lod: 0 },
      new AbortController().signal,
    );
    expect(payload).toMatchObject({
      timestampsNs: ["1000000000", "2000000000"],
      values: [
        [0.25, -0.5],
        [0.5, -1],
      ],
      series: [
        { displayName: "shoulder", unit: "rad" },
        { displayName: "elbow", unit: "rad" },
      ],
    });

    const frameSource = buildJointFrameSource(stream);
    await expect(
      frameSource?.sampleAt("1900000000", new AbortController().signal),
    ).resolves.toEqual({ shoulder: 0.5, elbow: -1 });
    expect(sensorWindow).toHaveBeenLastCalledWith(
      scope,
      "recording-test",
      {
        startOffsetNs: "0",
        endOffsetNs: "4000000000",
        maximumSamples: 10_000,
      },
      expect.any(AbortSignal),
    );
  });
});
