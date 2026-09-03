import type { RouteObject } from "react-router-dom";
import { registerPageRoutes } from "../../shared/routing/route-registry";

// Schema contracts are registered by robot/configuration packages. The former
// authoring screen intentionally has no UI route for any role.
export const routes: RouteObject[] = [];

registerPageRoutes("P17", routes);
export default routes;
