import { describe, expect, it } from "vitest";

import { pageDataSchemasQueryCodec } from "./query-codec";

describe("P17 schema list query codec", () => {
  it("round-trips declared filters and exactly one cursor direction", () => {
    const query = pageDataSchemasQueryCodec.build({
      tab: "registry",
      detailTab: "fields",
      q: "front",
      status: "PUBLISHED",
      logicalType: "IMAGE",
      after: "signed-after",
      limit: 50,
    });

    expect(query.toString()).toContain("status=PUBLISHED");
    expect(query.toString()).toContain("logicalType=IMAGE");
    expect(pageDataSchemasQueryCodec.parse(query)).toMatchObject({
      q: "front",
      status: "PUBLISHED",
      logicalType: "IMAGE",
      after: "signed-after",
      limit: 50,
    });
  });

  it("fails closed to a cursor-free query when both cursor directions appear", () => {
    expect(
      pageDataSchemasQueryCodec.parse(
        new URLSearchParams("after=next&before=previous&status=UNKNOWN"),
      ),
    ).toEqual({ tab: "registry", detailTab: "fields", limit: 20 });
  });
});
