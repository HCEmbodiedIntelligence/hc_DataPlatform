import type { RouteObject } from "react-router-dom";
import { registerPageRoutes } from "../../shared/routing/route-registry";

const browserMockEnabled = import.meta.env.VITE_MOCK_MODE === "browser";

type CleaningWorkbenchRoute = RouteObject & {
  readonly navigationOwnerPageId: "P08";
  readonly navigationOwnerGroupId: "annotation";
  readonly requiredCapabilities: readonly [];
  readonly hiddenFromNavigation: true;
};

export const routes: CleaningWorkbenchRoute[] = [
  {
    path: "/manual/drafts/:draftId",
    navigationOwnerPageId: "P08",
    navigationOwnerGroupId: "annotation",
    requiredCapabilities: [],
    hiddenFromNavigation: true,
    lazy: async () => {
      if (browserMockEnabled) {
        const module = await import("./page");
        return { Component: module.ManualCleaningWorkbenchPage };
      }

      const module = await import("../../app/shell/RouteCompatibility");
      return { Component: module.LegacyCleaningWorkbenchRedirect };
    },
  },
];

registerPageRoutes("P11", routes);
export default routes;
