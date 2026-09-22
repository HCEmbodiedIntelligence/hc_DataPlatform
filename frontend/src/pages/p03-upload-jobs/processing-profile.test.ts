import { describe, expect, it } from "vitest";
import { supportsLeRobotProcessing } from "./processing-profile";

describe("native processing profile selection", () => {
  it.each([2, 16])("accepts a named %i-axis source", (size) => {
    const feature = {
      dtype: "float32",
      shape: [size],
      names: Array.from({ length: size }, (_, i) => `axis-${i}`),
    };
    expect(
      supportsLeRobotProcessing({
        robot_type: size === 16 ? "openarmx" : "generic",
        features: {
          "observation.state": feature,
          action: feature,
        },
      }),
    ).toBe(true);
  });
  it("keeps G1 and rejects incomplete or ambiguous generic vectors", () => {
    expect(supportsLeRobotProcessing({ robot_type: "unitree_g1" })).toBe(true);
    expect(supportsLeRobotProcessing({ robot_type: "openarmx" })).toBe(false);
    const feature = { dtype: "float32", shape: [2], names: ["same", "same"] };
    expect(
      supportsLeRobotProcessing({
        features: { action: feature, "observation.state": feature },
      }),
    ).toBe(false);
  });
});
