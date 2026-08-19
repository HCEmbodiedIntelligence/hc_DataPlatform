import { describe, expect, it } from "vitest";
import { dashboardQueryCodec } from "./query-codec";

describe("dashboardQueryCodec", () => {
  it("round-trips a valid custom half-open range in the URL", () => {
    const value = {
      range: "custom" as const,
      from: "2026-08-01T00:00:00+08:00",
      to: "2026-08-02T00:00:00+08:00",
    };
    const params = dashboardQueryCodec.build(value);

    expect(params.get("range")).toBe("custom");
    expect(dashboardQueryCodec.parse(params)).toEqual({
      range: "custom",
      from: "2026-07-31T16:00:00.000Z",
      to: "2026-08-01T16:00:00.000Z",
    });
  });

  it("fails closed to 24 hours for an invalid or reversed custom range", () => {
    expect(
      dashboardQueryCodec.parse(
        new URLSearchParams("range=custom&from=2026-08-02T00:00:00Z&to=bad"),
      ),
    ).toEqual({ range: "24h" });
    expect(
      dashboardQueryCodec.parse(
        new URLSearchParams(
          "range=custom&from=2026-08-02T00:00:00Z&to=2026-08-01T00:00:00Z",
        ),
      ),
    ).toEqual({ range: "24h" });
  });
});
