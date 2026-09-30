import { describe, expect, it } from "vitest";
import { useShellStore } from "../scope/shell-store";
import { makeQueryKey } from "./query-keys";
import { makeScopedQueryKey } from "./scoped-query-keys";

describe("explicit cache identities", () => {
  it("constructs immutable scoped keys without consulting active selection", () => {
    const a = { organizationId: "a", projectId: "p", regionCode: "r" };
    useShellStore
      .getState()
      .setScope({ organizationId: "b", projectId: "p", regionCode: "r" });
    expect(makeScopedQueryKey(a, "domain", "resource", "id", "v1")).toEqual([
      "domain",
      "a/p/r",
      "resource",
      "id",
      "v1",
    ]);
    expect(
      Object.isFrozen(makeScopedQueryKey(a, "domain", "resource", "id")),
    ).toBe(true);
    expect(makeQueryKey("domain", "resource", "id")[1]).toBe("b/p/r");
  });
  it("isolates equal resource identifiers in different organizations", () => {
    expect(
      makeScopedQueryKey(
        { organizationId: "a" },
        "robots",
        "bootstrap",
        "same-id",
      ),
    ).not.toEqual(
      makeScopedQueryKey(
        { organizationId: "b" },
        "robots",
        "bootstrap",
        "same-id",
      ),
    );
  });
  it.each([new Date(), () => 1, { signedUrl: "https://objects.test/secret" }])(
    "retains sensitive and unstable key value checks: %j",
    (identity) => {
      expect(() =>
        makeScopedQueryKey(
          { organizationId: "a" },
          "domain",
          "resource",
          identity,
        ),
      ).toThrow(TypeError);
    },
  );
});
