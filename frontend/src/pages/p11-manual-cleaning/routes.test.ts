import { describe, expect, it } from "vitest";
import { LegacyCleaningWorkbenchRedirect } from "../../app/shell/RouteCompatibility";
import { routes } from "./routes";

describe("P11 workbench route", () => {
  it("redirects its legacy deep link to P08 revisions and stays P08-navigation-owned", async () => {
    const [route] = routes;

    expect(route).toMatchObject({
      path: "/manual/drafts/:draftId",
      navigationOwnerPageId: "P08",
      navigationOwnerGroupId: "annotation",
      hiddenFromNavigation: true,
    });
    expect(route?.lazy).toBeTypeOf("function");

    if (typeof route?.lazy !== "function")
      throw new Error("P11 route must be lazy");
    const resolved = await route.lazy();
    expect(resolved.Component).toBe(LegacyCleaningWorkbenchRedirect);
  });
});
