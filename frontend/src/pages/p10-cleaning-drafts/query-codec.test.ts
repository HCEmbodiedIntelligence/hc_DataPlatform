import { describe, expect, it } from "vitest";
import { cleaningDraftsQueryCodec } from "./query-codec";

describe("P10 cleaning-draft query codec", () => {
  it("keeps an actual durable RUNNING commit filter and returned scope", () => {
    expect(
      cleaningDraftsQueryCodec.parse(
        "scope=returned&status=active&commitStatus=RUNNING&limit=20",
      ),
    ).toMatchObject({
      scope: "returned",
      status: "active",
      commitStatus: ["RUNNING"],
      limit: 20,
    });
  });
});
