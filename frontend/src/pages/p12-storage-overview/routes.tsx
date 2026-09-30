import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";

export const routes: PageRoute[] = [
  {
    path: "/storage/overview",
    requiredCapabilities: ["storage.overview.read"],
    navigation: {
      label: "存储容量",
      groupId: "governance",
      order: 10,
      visibility: "authorized",
    },
    lazy: async () => ({
      Component: (await import("./page")).StorageOverviewPage,
    }),
  },
];

export const page = definePage({ id: "P12", enabled: true, routes: routes });
export default routes;
