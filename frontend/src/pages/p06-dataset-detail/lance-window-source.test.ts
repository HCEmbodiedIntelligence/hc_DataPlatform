import { afterEach, describe, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import {
  createDatasetLanceWindowSource,
  type EpisodeDataBinding,
} from "./lance-window-source";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));

const requestMock = vi.mocked(request);
const scope = {
  organizationId: "org-p06",
  projectId: "project-p06",
  regionCode: "region-p06",
};
const vectorBinding: EpisodeDataBinding = {
  rollout_id: "rollout-p06",
  lance_version: 7,
  modality_key: "joint.position",
  value_kind: "VECTOR",
  start_step: 10,
  end_step: 110,
};

function stepWindow(
  overrides: Record<string, unknown> = {},
  modalityKey: string = vectorBinding.modality_key,
  modalityValues: readonly unknown[] = [
    [1, 2],
    [3, 4],
  ],
) {
  return {
    schema_version: "1",
    project_id: scope.projectId,
    dataset_id: "dataset_p06fixture",
    dataset_version: vectorBinding.lance_version,
    rollout_id: vectorBinding.rollout_id,
    start_step: 30,
    end_step: 50,
    steps: modalityValues.map((value, index) => ({
      schema_version: "1",
      rollout_id: vectorBinding.rollout_id,
      step_index: 30 + index,
      timestamp_ns: `${9_007_199_254_740_990n + BigInt(index)}`,
      modalities: { [modalityKey]: value },
      source_timestamps_ns: { [modalityKey]: [] },
      time_error_ns: { [modalityKey]: 0 },
      valid: { [modalityKey]: true },
      repeated: { [modalityKey]: false },
      sample_valid: true,
    })),
    ...overrides,
  };
}

function source(binding: EpisodeDataBinding = vectorBinding) {
  return createDatasetLanceWindowSource({
    scope,
    datasetId: "dataset_p06fixture",
    streamStartNs: "0",
    streamEndNs: "1000",
    binding,
  });
}

afterEach(() => vi.clearAllMocks());

describe("P06 immutable Lance window source", () => {
  it("maps the shared nanosecond timeline to a bounded fixed step interval and decodes real vectors", async () => {
    requestMock.mockResolvedValue(stepWindow() as never);

    await expect(
      source().loadWindow(
        { startNs: "200", endNs: "400", lod: 1 },
        new AbortController().signal,
      ),
    ).resolves.toMatchObject({
      timestampsNs: ["9007199254740990", "9007199254740991"],
      values: [
        [1, 2],
        [3, 4],
      ],
    });
    expect(requestMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "GET",
        path: "/projects/project-p06/datasets/dataset_p06fixture/rollouts/rollout-p06/steps",
        scope,
        cache: "no-store",
        query: { startStep: 30, endStep: 50, version: 7 },
      }),
    );
  });

  it("fails closed when a response crosses the immutable rollout/version/window identity", async () => {
    requestMock.mockResolvedValue(stepWindow({ dataset_version: 8 }) as never);

    await expect(
      source().loadWindow(
        { startNs: "200", endNs: "400", lod: 1 },
        new AbortController().signal,
      ),
    ).rejects.toMatchObject({ problemCode: "P06_WINDOW_IDENTITY_MISMATCH" });
  });

  it("decodes the declared pointcloud and event formats without treating arbitrary payloads as values", async () => {
    const pointBinding: EpisodeDataBinding = {
      ...vectorBinding,
      modality_key: "lidar.points",
      value_kind: "POINTCLOUD_XYZ",
    };
    requestMock.mockResolvedValue(
      stepWindow({ start_step: 30, end_step: 50 }, pointBinding.modality_key, [
        [0, 0, 0, 1, 1, 1],
      ]) as never,
    );
    await expect(
      source(pointBinding).loadWindow(
        { startNs: "200", endNs: "400", lod: 1 },
        new AbortController().signal,
      ),
    ).resolves.toMatchObject({
      pointFrames: [
        {
          timestampNs: "9007199254740990",
          points: expect.any(Float32Array),
        },
      ],
    });

    const eventBinding: EpisodeDataBinding = {
      ...vectorBinding,
      modality_key: "task.event",
      value_kind: "EVENT",
    };
    requestMock.mockResolvedValue(
      stepWindow({ start_step: 30, end_step: 50 }, eventBinding.modality_key, [
        { label: "抓取" },
      ]) as never,
    );
    await expect(
      source(eventBinding).loadWindow(
        { startNs: "200", endNs: "400", lod: 1 },
        new AbortController().signal,
      ),
    ).resolves.toMatchObject({
      events: [{ timestampNs: "9007199254740990", label: "抓取" }],
    });
  });

  it("rejects malformed, unavailable and undeclared sample values rather than silently synthesizing a curve", async () => {
    requestMock.mockResolvedValue(
      stepWindow({}, vectorBinding.modality_key, ["not-a-vector"]) as never,
    );
    await expect(
      source().loadWindow(
        { startNs: "200", endNs: "400", lod: 1 },
        new AbortController().signal,
      ),
    ).rejects.toMatchObject({ problemCode: "P06_NUMERIC_VALUE_INVALID" });
  });
});
