import { describe, expect, it } from "vitest";
import routes from "./routes";

describe("P17 schema authoring route", () => {
  it("is not registered for any role", () => {
    expect(routes).toEqual([]);
  });
});
