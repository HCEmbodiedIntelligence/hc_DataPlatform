import type { RouteObject } from "react-router-dom";
import { registerPageRoutes } from "../../shared/routing/route-registry";

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
    lazy: async () => ({
      Component: (await import("../../app/shell/RouteCompatibility"))
        .LegacyCleaningDraftsRedirect,
    }),
  },
];

registerPageRoutes("P10", routes);
export default routes;
