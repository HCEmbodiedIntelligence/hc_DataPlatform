import type { RouteObject } from "react-router-dom";
import { registerPageRoutes } from "../../shared/routing/route-registry";

type CleaningWorkbenchRoute = RouteObject & {
  readonly navigationOwnerPageId: "P08";
  readonly navigationOwnerGroupId: "annotation";
  readonly requiredCapabilities: readonly ["cleaning.read"];
  readonly hiddenFromNavigation: true;
};

export const routes: CleaningWorkbenchRoute[] = [
  {
    path: "/manual/drafts/:draftId",
    navigationOwnerPageId: "P08",
    navigationOwnerGroupId: "annotation",
    requiredCapabilities: ["cleaning.read"],
    hiddenFromNavigation: true,
    lazy: async () => ({
      Component: (await import("./page")).default,
    }),
  },
];

registerPageRoutes("P11", routes);
export default routes;
