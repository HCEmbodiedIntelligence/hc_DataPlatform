import { describe, expect, it } from "vitest";
import { registerPageRoutes, safeReturnTo } from "./route-registry";

describe("safeReturnTo", () => {
  it("keeps query and hash for the formal upload and Raw diagnostic routes", () => {
    expect(safeReturnTo("/ingest/uploads/new?source=browser#manifest")).toBe(
      "/ingest/uploads/new?source=browser#manifest",
    );
    expect(
      safeReturnTo("/ingest/uploads/session-42?tab=quality#finding-3"),
    ).toBe("/ingest/uploads/session-42?tab=quality#finding-3");
  });

  it("accepts a registered dashboard deep link and rejects external targets", () => {
    registerPageRoutes("P01", ["/dashboard/pending/:pendingId"]);
    expect(
      safeReturnTo("/dashboard/pending/qc-42?returnTo=%2Fdashboard#detail"),
    ).toBe("/dashboard/pending/qc-42?returnTo=%2Fdashboard#detail");
    expect(safeReturnTo("//attacker.invalid/dashboard")).toBeNull();
    expect(safeReturnTo("https://attacker.invalid/dashboard")).toBeNull();
  });

  it("recognizes the canonical platform operations route", () => {
    expect(safeReturnTo("/settings/platform-operations#logs")).toBe(
      "/settings/platform-operations#logs",
    );
  });
});
