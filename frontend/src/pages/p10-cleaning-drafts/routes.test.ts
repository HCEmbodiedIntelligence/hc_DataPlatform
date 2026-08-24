import { describe, expect, it } from "vitest";
import { LegacyCleaningDraftsRedirect } from "../../app/shell/RouteCompatibility";
import { routes } from "./routes";

describe("P10 cleaning-draft route", () => {
  it("redirects the legacy list to P08 revisions and stays P08-navigation-owned", async () => {
    const [route] = routes;

    expect(route).toMatchObject({
      path: "/manual/drafts",
      navigationOwnerPageId: "P08",
      navigationOwnerGroupId: "annotation",
      hiddenFromNavigation: true,
    });
    expect(route?.lazy).toBeTypeOf("function");

    if (typeof route?.lazy !== "function")
      throw new Error("P10 route must be lazy");
    const resolved = await route.lazy();
    expect(resolved.Component).toBe(LegacyCleaningDraftsRedirect);
  });
});
