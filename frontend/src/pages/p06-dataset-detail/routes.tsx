import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";
export { registerPageRoutes } from "../../shared/routing/route-registry";
export type DatasetRouteObject = PageRoute;

export const p06Routes: PageRoute[] = [
  {
    path: "/datasets/:datasetId",
    requiredCapabilities: ["dataset.read"],
    navigationOwnerPageId: "P05",
    lazy: async () => ({
      Component: (await import("./page")).DatasetDetailPage,
    }),
  },
  {
    path: "/datasets/:datasetId/versions/:versionId/episodes/:episodeId/view",
    requiredCapabilities: ["episode.read"],
    navigationOwnerPageId: "P05",
    lazy: async () => ({
      Component: (await import("./ViewerShell")).EpisodeViewerShell,
    }),
  },
];

export const page = definePage({ id: "P06", enabled: true, routes: p06Routes });
export default p06Routes;
