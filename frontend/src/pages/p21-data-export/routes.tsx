import type { RouteObject } from "react-router-dom";
import { registerPageRoutes } from "../../shared/routing/route-registry";

export type DataExportRouteObject = RouteObject & {
  readonly navigationOwnerPageId: "P21";
  readonly navigationOwnerGroupId: "production";
  readonly requiredCapabilities: readonly string[];
};

export const p21Routes: DataExportRouteObject[] = [
  {
    path: "/exports",
    navigationOwnerPageId: "P21",
    navigationOwnerGroupId: "production",
    requiredCapabilities: [
      "export.read",
      "dataset.read",
      "dataset_version.read",
    ],
    lazy: async () => ({ Component: (await import("./page")).DataExportPage }),
  },
];

registerPageRoutes("P21", p21Routes);
export default p21Routes;
