import type { RouteObject } from "react-router-dom";
import { registerPageRoutes } from "../../shared/routing/route-registry";

type CleaningDraftsRoute = RouteObject & {
  readonly navigationOwnerPageId: "P08";
  readonly navigationOwnerGroupId: "annotation";
  readonly requiredCapabilities: readonly ["cleaning.read"];
  readonly hiddenFromNavigation: true;
};

export const routes: CleaningDraftsRoute[] = [
  {
    path: "/manual/drafts",
    navigationOwnerPageId: "P08",
    navigationOwnerGroupId: "annotation",
    requiredCapabilities: ["cleaning.read"],
    hiddenFromNavigation: true,
    lazy: async () => ({
      Component: (await import("./page")).default,
    }),
  },
];

registerPageRoutes("P10", routes);
export default routes;
