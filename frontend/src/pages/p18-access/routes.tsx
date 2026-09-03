import type { RouteObject } from "react-router-dom";
import { registerPageRoutes } from "../../shared/routing/route-registry";

type AccessRoute = RouteObject & {
  readonly requiredCapabilities?: readonly string[];
};

export const routes: AccessRoute[] = [
  {
    path: "/settings/access",
    lazy: () => import("./page"),
    // P18 is an OR boundary: exact project access.read OR global platform.account.read.
    // The page owns that split and keeps both query families disabled without either grant.
    requiredCapabilities: [],
  },
];

registerPageRoutes("P18", routes);
export default routes;
