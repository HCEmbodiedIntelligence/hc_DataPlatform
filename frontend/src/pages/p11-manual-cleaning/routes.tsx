import type { RouteObject } from "react-router-dom";
import { registerPageRoutes } from "../../shared/routing/route-registry";

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
    lazy: async () => ({
      Component: (await import("../../app/shell/RouteCompatibility"))
        .LegacyCleaningWorkbenchRedirect,
    }),
  },
];

registerPageRoutes("P11", routes);
export default routes;
