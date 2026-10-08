import type { RouteObject } from "react-router-dom";
import type { PageCatalog } from "../../shared/routing/page-descriptor";
import { RouteCapabilityGuard } from "./RouteCapabilityGuard";

export function guardedPageRoutes(catalog: PageCatalog): RouteObject[] {
  return catalog.routes.map((route) => ({
    element: (
      <RouteCapabilityGuard requiredCapabilities={route.requiredCapabilities} />
    ),
    children: [route],
  }));
}
