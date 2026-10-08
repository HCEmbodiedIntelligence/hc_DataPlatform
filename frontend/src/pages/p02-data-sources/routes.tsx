import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";

export const routes: PageRoute[] = [
  {
    path: "/ingest/sources",
    requiredCapabilities: ["ingest_source.read"],
    navigation: {
      label: "数据源",
      groupId: "ingest",
      order: 20,
      visibility: "discoverable",
    },
    lazy: async () => ({ Component: (await import("./page")).default }),
  },
];

export const page = definePage({ id: "P02", enabled: true, routes: routes });
export default routes;
