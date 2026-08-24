import { describe, expect, it } from "vitest";
import { resolveViewerComposition } from "./ViewerCompositionResolver";
import type { StreamDescriptor } from "./types";

function jointStream(
  overrides: Partial<StreamDescriptor> = {},
): StreamDescriptor {
  return {
    id: "joint-stream",
    canonicalPath: "/joint_states/position",
    displayName: "关节位置",
    modality: "joint_state",
    schema: { id: "hc.joint_state", version: "contract-v1" },
    startNs: "0",
    endNs: "1000",
    availability: "ready",
    ...overrides,
  };
}

describe("ViewerCompositionResolver joint-state fallback", () => {
  it("keeps a real generic time-series panel when immutable data exists but axis metadata is absent", () => {
    const composition = resolveViewerComposition([jointStream()]);

    expect(composition.panels).toEqual([
      expect.objectContaining({
        kind: "time-series",
        state: "ready",
        streamIds: ["joint-stream"],
      }),
    ]);
    expect(composition.diagnostics).toEqual([
      expect.objectContaining({ code: "JOINT_AXIS_CONTRACT_MISMATCH" }),
    ]);
    expect(composition.jointGroups).toEqual([]);
  });

  it("uses detailed axis cards only when their contract is internally consistent", () => {
    const composition = resolveViewerComposition([
      jointStream({
        schema: {
          id: "hc.joint_state",
          version: "contract-v1",
          shape: [2],
          axes: [
            {
              axisId: "joint-1",
              sourceName: "joint_1",
              displayName: "J1",
              sampleIndex: 0,
              unit: "rad",
              mappingStatus: "mapped",
            },
            {
              axisId: "joint-2",
              sourceName: "joint_2",
              displayName: "J2",
              sampleIndex: 1,
              unit: "rad",
              mappingStatus: "mapped",
            },
          ],
        },
      }),
    ]);

    expect(composition.panels).toEqual([]);
    expect(composition.jointGroups).toHaveLength(1);
    expect(composition.diagnostics).toEqual([]);
  });
});
