import type { RouteObject } from "react-router-dom";
import { registerPageRoutes } from "../../shared/routing/route-registry";

const browserMockEnabled = import.meta.env.VITE_MOCK_MODE === "browser";

type CleaningDraftsRoute = RouteObject & {
  readonly navigationOwnerPageId: "P08";
  readonly navigationOwnerGroupId: "annotation";
  readonly requiredCapabilities: readonly [];
  readonly hiddenFromNavigation: true;
};

export const routes: CleaningDraftsRoute[] = [
  {
    path: "/manual/drafts",
    navigationOwnerPageId: "P08",
    navigationOwnerGroupId: "annotation",
    requiredCapabilities: [],
    hiddenFromNavigation: true,
    lazy: async () => {
      if (browserMockEnabled) {
        const module = await import("./page");
        return { Component: module.CleaningDraftsPage };
      }

      const module = await import("../../app/shell/RouteCompatibility");
      return { Component: module.LegacyCleaningDraftsRedirect };
    },
  },
];

registerPageRoutes("P10", routes);
export default routes;
