import { describe, expect, it } from "vitest";

import { robotsQueryCodec } from "./query-codec";

describe("robotsQueryCodec", () => {
  it("round-trips the server-supported lifecycle and connectivity filters", () => {
    const params = robotsQueryCodec.build({
      q: "星舟",
      lifecycleStatus: "MAINTENANCE",
      connectivityState: "DEGRADED",
      tab: "overview",
      limit: 20,
    });

    expect(robotsQueryCodec.parse(params)).toMatchObject({
      q: "星舟",
      lifecycleStatus: "MAINTENANCE",
      connectivityState: "DEGRADED",
    });
  });

  it("drops unrecognized filter values instead of sending an invalid query", () => {
    expect(
      robotsQueryCodec.parse(
        new URLSearchParams(
          "lifecycleStatus=UNKNOWN&connectivityState=UNSTABLE",
        ),
      ),
    ).toEqual({ tab: "overview", limit: 20 });
  });
});
