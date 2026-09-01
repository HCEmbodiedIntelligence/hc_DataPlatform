import type { RouteObject } from "react-router-dom";
import { registerPageRoutes } from "../../shared/routing/route-registry";

export const routes: RouteObject[] = [
  { path: "/settings/robots", lazy: () => import("./page") },
  { path: "/settings/robot-instances", lazy: () => import("./instances-page") },
];
registerPageRoutes("P15", routes);
export default routes;
