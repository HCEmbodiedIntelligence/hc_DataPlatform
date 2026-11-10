import { describe, expect, it } from "vitest";

import { robotModelsQueryCodec } from "./query-codec";

describe("robotModelsQueryCodec", () => {
  it("round-trips model asset search and selection", () => {
    const params = robotModelsQueryCodec.build({
      q: "星舟",
      modelId: "model-1",
      limit: 20,
    });

    expect(robotModelsQueryCodec.parse(params)).toMatchObject({
      q: "星舟",
      modelId: "model-1",
    });
  });

  it("ignores legacy robot connectivity filters", () => {
    expect(
      robotModelsQueryCodec.parse(
        new URLSearchParams(
          "lifecycleStatus=UNKNOWN&connectivityState=UNSTABLE",
        ),
      ),
    ).toEqual(robotModelsQueryCodec.parse(new URLSearchParams()));
  });
});
