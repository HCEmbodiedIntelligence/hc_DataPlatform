import { describe, expect, it } from "vitest";

import { robotsQueryCodec } from "./query-codec";

describe("robotsQueryCodec", () => {
  it("round-trips model asset search and selection", () => {
    const params = robotsQueryCodec.build({
      q: "星舟",
      modelId: "model-1",
      limit: 20,
    });

    expect(robotsQueryCodec.parse(params)).toMatchObject({
      q: "星舟",
      modelId: "model-1",
    });
  });

  it("ignores legacy robot connectivity filters", () => {
    expect(
      robotsQueryCodec.parse(
        new URLSearchParams(
          "lifecycleStatus=UNKNOWN&connectivityState=UNSTABLE",
        ),
      ),
    ).toEqual({ limit: 20 });
  });
});
