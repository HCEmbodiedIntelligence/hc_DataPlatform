import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";
export { registerPageRoutes } from "../../shared/routing/route-registry";

export const p07Routes: PageRoute[] = [
  {
    path: "/datasets/:datasetId/versions/:versionId",
    requiredCapabilities: ["dataset_version.read"],
    navigationOwnerPageId: "P05",
    lazy: async () => ({
      Component: (await import("./page")).VersionDetailPage,
    }),
  },
];

export const page = definePage({ id: "P07", enabled: true, routes: p07Routes });
export default p07Routes;
