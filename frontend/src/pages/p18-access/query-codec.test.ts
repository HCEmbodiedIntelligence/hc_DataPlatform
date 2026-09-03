import { describe, expect, it } from "vitest";
import { accessQueryCodec } from "./query-codec";

describe("P18 access query codec", () => {
  it("parses shareable tab, filter, paging and drawer state", () => {
    expect(
      accessQueryCodec.parse(
        new URLSearchParams([
          ["tab", "capability-requests"],
          ["q", " publish "],
          ["status", "APPROVED"],
          ["accountState", "DISABLED"],
          ["accountRole", "PLATFORM_ADMIN"],
          ["order", "oldest"],
          ["page", "3"],
          ["pageSize", "20"],
          ["requestId", "request-42"],
          ["drawer", "open"],
        ]),
      ),
    ).toEqual({
      tab: "capability-requests",
      q: "publish",
      status: "APPROVED",
      accountState: "DISABLED",
      accountRole: "PLATFORM_ADMIN",
      order: "oldest",
      page: 3,
      pageSize: 20,
      requestId: "request-42",
      drawer: "open",
    });
  });

  it("fails invalid values back to safe defaults", () => {
    expect(
      accessQueryCodec.parse(
        "tab=registration-approval&status=ROOT&page=-1&pageSize=500",
      ),
    ).toEqual({
      tab: "membership-requests",
      status: "ALL",
      accountState: "ALL",
      accountRole: "ALL",
      order: "recent",
      page: 1,
      pageSize: 10,
      drawer: "closed",
    });
  });

  it("serializes only non-default state", () => {
    expect(
      String(
        accessQueryCodec.build({
          tab: "capability-requests",
          status: "REVOKED",
          accountState: "ACTIVE",
          accountRole: "USER",
          page: 2,
          drawer: "open",
        }),
      ),
    ).toBe(
      "tab=capability-requests&status=REVOKED&accountState=ACTIVE&accountRole=USER&page=2&drawer=open",
    );
  });

  it("keeps a request ID closed unless the deep link explicitly opens the drawer", () => {
    expect(accessQueryCodec.parse("requestId=request-42")).toMatchObject({
      requestId: "request-42",
      drawer: "closed",
    });
    expect(String(accessQueryCodec.build(accessQueryCodec.parse("")))).toBe("");
  });
});
