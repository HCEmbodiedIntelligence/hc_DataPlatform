import type { RouteObject } from "react-router-dom";
import { registerPageRoutes } from "../../shared/routing/route-registry";

export type PlatformOperationsRouteObject = RouteObject & {
  readonly navigationOwnerPageId: "P22";
  readonly navigationOwnerGroupId: "security";
  readonly requiredCapabilities: readonly string[];
};

export const p22Routes: PlatformOperationsRouteObject[] = [
  {
    path: "/settings/platform-operations",
    navigationOwnerPageId: "P22",
    navigationOwnerGroupId: "security",
    requiredCapabilities: ["platform.operations.read"],
    lazy: async () => ({
      Component: (await import("./page")).PlatformOperationsPage,
    }),
  },
];

registerPageRoutes("P22", p22Routes);
export default p22Routes;
