import type { RouteObject } from "react-router-dom";
import { registerPageRoutes } from "../../shared/routing/route-registry";

type CleaningRouteObject = RouteObject & {
  readonly navigationOwnerPageId: "P09";
  readonly navigationOwnerGroupId: "manual";
  readonly requiredCapabilities: readonly string[];
};

export const routes: CleaningRouteObject[] = [
  {
    path: "/manual/issues",
    navigationOwnerPageId: "P09",
    navigationOwnerGroupId: "manual",
    requiredCapabilities: ["manual_issue.read"],
    lazy: async () => {
      const module = await import("./page");
      return { Component: module.ManualIssuesPage };
    },
  },
  {
    path: "/manual/issues/raw-diagnostic/:uploadId",
    navigationOwnerPageId: "P09",
    navigationOwnerGroupId: "manual",
    requiredCapabilities: ["manual_issue.read", "upload.read"],
    lazy: async () => ({
      Component: (await import("../p04-upload-detail/formal-page")).default,
    }),
  },
];

registerPageRoutes("P09", routes);
export default routes;
