import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";
export type DataExportRouteObject = PageRoute;

export const p21Routes: PageRoute[] = [
  {
    path: "/exports",
    requiredCapabilities: [
      "export.read",
      "dataset.read",
      "dataset_version.read",
    ],
    navigation: {
      label: "数据导出",
      groupId: "production",
      order: 40,
      visibility: "discoverable",
    },
    lazy: async () => ({ Component: (await import("./page")).DataExportPage }),
  },
];

export const page = definePage({ id: "P21", enabled: true, routes: p21Routes });
export default p21Routes;
