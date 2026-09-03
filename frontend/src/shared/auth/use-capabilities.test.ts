import { describe, expect, it } from "vitest";
import { CANONICAL_CAPABILITIES } from "../../entities/capability";
import { expandGrantedCapabilities } from "./use-capabilities";

describe("expandGrantedCapabilities", () => {
  it("expands platform.admin to every current business capability", () => {
    const expanded = expandGrantedCapabilities(["platform.admin"]);

    expect(expanded.has("platform.admin")).toBe(true);
    expect(
      CANONICAL_CAPABILITIES.every((capability) => expanded.has(capability)),
    ).toBe(true);
  });

  it("keeps exact capabilities isolated", () => {
    expect(
      expandGrantedCapabilities(["upload.read"]).has("upload.manage"),
    ).toBe(false);
    expect(
      expandGrantedCapabilities(["upload.manage"]).has("access.manage"),
    ).toBe(false);
  });
});
