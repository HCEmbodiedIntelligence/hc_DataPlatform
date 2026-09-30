import { beforeEach, describe, expect, it } from "vitest";
import {
  registerPageRoutes,
  replaceRegisteredPageRoutes,
  safeReturnTo,
} from "./route-registry";

beforeEach(() => {
  registerPageRoutes("test-routes", [
    "/ingest/uploads/new",
    "/ingest/uploads/:uploadId",
    "/settings/platform-operations",
  ]);
});

describe("safeReturnTo", () => {
  it("removes disabled paths when the application replaces its catalog", () => {
    replaceRegisteredPageRoutes([{ pageId: "example", routes: ["/example"] }]);
    expect(safeReturnTo("/example?tab=one#detail")).toBe(
      "/example?tab=one#detail",
    );
    expect(safeReturnTo("/settings/platform-operations")).toBeNull();
    replaceRegisteredPageRoutes([]);
    expect(safeReturnTo("/example")).toBeNull();
  });

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
