import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";
export { registerPageRoutes } from "../../shared/routing/route-registry";

export const p05Routes: PageRoute[] = [
  {
    path: "/datasets",
    requiredCapabilities: ["dataset.read"],
    navigation: {
      label: "数据集",
      groupId: "production",
      order: 20,
      visibility: "discoverable",
    },
    lazy: async () => ({ Component: (await import("./page")).DatasetsPage }),
  },
];

export const page = definePage({ id: "P05", enabled: true, routes: p05Routes });
export default p05Routes;
