import { afterEach, describe, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import {
  buildRuntimeJointAngleStream,
  buildRuntimeJointFrameSource,
} from "./joint-angle-stream";
import {
  createVisualAnnotationBundle,
  visualAnnotationScope,
} from "./testing/annotation-fixture";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));

const requestMock = vi.mocked(request);

afterEach(() => vi.clearAllMocks());

function jointBundle() {
  const fixture = createVisualAnnotationBundle({
    mode: "annotation",
    cameraCount: 1,
  });
  return {
    ...fixture,
    datasetVersion: { ...fixture.datasetVersion, frequency_hz: 15 },
    manifest: {
      ...fixture.manifest!,
      topics: [
        {
          name: "/robot/joint_states",
          required: true,
          schema_name: "sensor_msgs/msg/JointState",
          message_encoding: "cdr",
        },
      ],
    },
  };
}

describe("P08 joint-angle Lance stream", () => {
  it("discovers a legacy joint column once and projects it in subsequent chunks", async () => {
    const bundle = jointBundle();
    requestMock.mockImplementation(async (options) => {
      const startStep = Number(options.query?.startStep);
      const endStep = Number(options.query?.endStep);
      const selected = options.query?.columns;
      const includeJoint =
        !selected ||
        (Array.isArray(selected) && selected.includes("legacy_joint_angles"));
      return {
        schema_version: "1",
        project_id: bundle.task.project_id,
        dataset_id: bundle.task.dataset_id,
        dataset_version: bundle.task.base_lance_version,
        rollout_id: bundle.task.rollout_id,
        start_step: startStep,
        end_step: endStep,
        steps: [
          {
            schema_version: "1",
            rollout_id: bundle.task.rollout_id,
            step_index: startStep,
            timestamp_ns: "0",
            modalities: includeJoint ? { legacy_joint_angles: [0.1, 0.2] } : {},
            source_timestamps_ns: {},
            time_error_ns: {},
            valid: {},
            repeated: {},
            sample_valid: true,
          },
        ],
      } as never;
    });
    const source = buildRuntimeJointAngleStream({
      bundle,
      scope: visualAnnotationScope,
    })!.windowSource!;
    const signal = new AbortController().signal;
    const first = await source.loadWindow(
      { startNs: "0", endNs: "4000000000", lod: 1 },
      signal,
    );
    expect(first.values).toEqual([[0.1, 0.2]]);
    expect(requestMock).toHaveBeenCalledTimes(2);
    await source.loadWindow(
      { startNs: "4000000000", endNs: "8000000000", lod: 0 },
      signal,
    );
    expect(requestMock).toHaveBeenCalledTimes(3);
    expect(requestMock.mock.calls[2]?.[0].query?.columns).toEqual([
      "legacy_joint_angles",
    ]);
  });

  it("reads the fixed task window and resolves a real joint vector without synthesizing values", async () => {
    const bundle = jointBundle();
    requestMock.mockResolvedValue({
      schema_version: "1",
      project_id: bundle.task.project_id,
      dataset_id: bundle.task.dataset_id,
      dataset_version: bundle.task.base_lance_version,
      rollout_id: bundle.task.rollout_id,
      start_step: 0,
      end_step: 60,
      steps: [
        {
          schema_version: "1",
          rollout_id: bundle.task.rollout_id,
          step_index: 0,
          timestamp_ns: "9007199254740990",
          modalities: { "joint.position": [0.1, -0.2, 0.3] },
          source_timestamps_ns: { "joint.position": [] },
          time_error_ns: { "joint.position": 0 },
          valid: { "joint.position": true },
          repeated: { "joint.position": false },
          sample_valid: true,
        },
        {
          schema_version: "1",
          rollout_id: bundle.task.rollout_id,
          step_index: 2,
          timestamp_ns: "9007199254807656",
          modalities: { "joint.position": null },
          source_timestamps_ns: { "joint.position": [] },
          time_error_ns: { "joint.position": null },
          valid: { "joint.position": false },
          repeated: { "joint.position": false },
          sample_valid: false,
        },
        {
          schema_version: "1",
          rollout_id: bundle.task.rollout_id,
          step_index: 1,
          timestamp_ns: "9007199254774323",
          modalities: { "joint.position": [0.12, -0.18, 0.28] },
          source_timestamps_ns: { "joint.position": [] },
          time_error_ns: { "joint.position": 0 },
          valid: { "joint.position": true },
          repeated: { "joint.position": false },
          sample_valid: true,
        },
      ],
    } as never);

    const stream = buildRuntimeJointAngleStream({
      bundle,
      scope: visualAnnotationScope,
    });
    const payload = await stream?.windowSource?.loadWindow(
      { startNs: "0", endNs: "2000000000", lod: 1 },
      new AbortController().signal,
    );

    expect(payload).toMatchObject({
      timestampsNs: ["0", "66666666"],
      values: [
        [0.1, -0.2, 0.3],
        [0.12, -0.18, 0.28],
      ],
      series: [
        { displayName: "J1", unit: "rad" },
        { displayName: "J2", unit: "rad" },
        { displayName: "J3", unit: "rad" },
      ],
    });
    expect(requestMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "GET",
        path: `/projects/${bundle.task.project_id}/datasets/${bundle.task.dataset_id}/rollouts/${bundle.task.rollout_id}/steps`,
        query: {
          startStep: 0,
          endStep: 60,
          version: bundle.task.base_lance_version,
          columns: expect.arrayContaining([
            "/robot/joint_states",
            "joint.position",
          ]),
        },
      }),
    );
  });

  it("stays unavailable when the Manifest does not declare a joint Topic", () => {
    expect(
      buildRuntimeJointAngleStream({
        bundle: createVisualAnnotationBundle({ cameraCount: 1 }),
        scope: visualAnnotationScope,
      }),
    ).toBeNull();
  });

  it("exposes the nearest named joint frame for URDF playback", async () => {
    const bundle = jointBundle();
    requestMock.mockResolvedValue({
      schema_version: "1",
      project_id: bundle.task.project_id,
      dataset_id: bundle.task.dataset_id,
      dataset_version: bundle.task.base_lance_version,
      rollout_id: bundle.task.rollout_id,
      start_step: 0,
      end_step: 60,
      steps: [
        {
          schema_version: "1",
          rollout_id: bundle.task.rollout_id,
          step_index: 0,
          timestamp_ns: "0",
          modalities: {
            "/robot/joint_states": JSON.stringify({
              names: ["shoulder", "elbow"],
              positions: [0.25, -0.5],
            }),
          },
          source_timestamps_ns: { "/robot/joint_states": [] },
          time_error_ns: { "/robot/joint_states": 0 },
          valid: { "/robot/joint_states": true },
          repeated: { "/robot/joint_states": false },
          sample_valid: true,
        },
      ],
    } as never);
    const stream = buildRuntimeJointAngleStream({
      bundle,
      scope: visualAnnotationScope,
    });
    const source = buildRuntimeJointFrameSource(stream);

    await expect(
      source?.sampleAt("0", new AbortController().signal),
    ).resolves.toEqual({ shoulder: 0.25, elbow: -0.5 });
    await expect(
      source?.sampleAt("1000000000", new AbortController().signal),
    ).resolves.toEqual({ shoulder: 0.25, elbow: -0.5 });
    expect(requestMock).toHaveBeenCalledTimes(1);
    expect(requestMock).toHaveBeenCalledWith(
      expect.objectContaining({
        query: {
          startStep: 0,
          endStep: 60,
          version: bundle.task.base_lance_version,
          columns: expect.arrayContaining([
            "/robot/joint_states",
            "joint.position",
          ]),
        },
      }),
    );
  });

  it("coalesces frame consumers without aborting the shared Lance chunk", async () => {
    const bundle = jointBundle();
    let resolveWindow!: (value: unknown) => void;
    requestMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveWindow = resolve;
        }) as never,
    );
    const stream = buildRuntimeJointAngleStream({
      bundle,
      scope: visualAnnotationScope,
    });
    const source = buildRuntimeJointFrameSource(stream);
    const firstController = new AbortController();
    const firstResult = Promise.resolve(
      source?.sampleAt("0", firstController.signal),
    ).then(
      () => null,
      (cause: unknown) => cause,
    );

    firstController.abort();
    const second = source?.sampleAt("1000000000", new AbortController().signal);
    expect(requestMock).toHaveBeenCalledTimes(1);
    expect(
      (requestMock.mock.calls[0]?.[0].signal as AbortSignal | undefined)
        ?.aborted,
    ).toBe(false);

    resolveWindow({
      schema_version: "1",
      project_id: bundle.task.project_id,
      dataset_id: bundle.task.dataset_id,
      dataset_version: bundle.task.base_lance_version,
      rollout_id: bundle.task.rollout_id,
      start_step: 0,
      end_step: 60,
      steps: [
        {
          schema_version: "1",
          rollout_id: bundle.task.rollout_id,
          step_index: 15,
          timestamp_ns: "1000000000",
          modalities: {
            "/robot/joint_states": {
              names: ["shoulder", "elbow"],
              positions: [0.5, -0.75],
            },
          },
          source_timestamps_ns: { "/robot/joint_states": [] },
          time_error_ns: { "/robot/joint_states": 0 },
          valid: { "/robot/joint_states": true },
          repeated: { "/robot/joint_states": false },
          sample_valid: true,
        },
      ],
    });

    expect((await firstResult) as { name?: string }).toMatchObject({
      name: "AbortError",
    });
    await expect(second).resolves.toEqual({ shoulder: 0.5, elbow: -0.75 });
  });

  it("backs off a failed chunk instead of retrying on every clock tick", async () => {
    const bundle = jointBundle();
    requestMock.mockRejectedValue(new Error("temporary object-store failure"));
    const source = buildRuntimeJointFrameSource(
      buildRuntimeJointAngleStream({
        bundle,
        scope: visualAnnotationScope,
      }),
    );

    await expect(
      Promise.resolve(source?.sampleAt("0", new AbortController().signal)),
    ).rejects.toThrow("temporary object-store failure");
    await expect(
      Promise.resolve(
        source?.sampleAt("100000000", new AbortController().signal),
      ),
    ).rejects.toThrow("temporary object-store failure");
    expect(requestMock).toHaveBeenCalledTimes(1);
  });
});
