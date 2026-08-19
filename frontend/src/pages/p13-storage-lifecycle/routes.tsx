import type { RouteObject } from "react-router-dom";
import { registerPageRoutes } from "../../shared/routing/route-registry";

export const routes: RouteObject[] = [
  {
    path: "/storage/lifecycle",
    lazy: async () => {
      const module = await import("../p12-storage-overview/page");
      return { Component: module.StorageOverviewPage };
    },
  },
];

registerPageRoutes("P13", routes);
export default routes;
