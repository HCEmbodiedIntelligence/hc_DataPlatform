import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";
export type CollectionTaskRouteObject = PageRoute;

export const collectionTaskRoutes: PageRoute[] = [
  {
    path: "/collection-tasks",
    requiredCapabilities: ["upload.read"],
    navigation: {
      label: "采集任务",
      groupId: "ingest",
      order: 10,
      visibility: "discoverable",
    },
    lazy: async () => ({ Component: (await import("./page")).default }),
  },
];

export const page = definePage({
  id: "P20",
  enabled: true,
  routes: collectionTaskRoutes,
});
export default collectionTaskRoutes;
