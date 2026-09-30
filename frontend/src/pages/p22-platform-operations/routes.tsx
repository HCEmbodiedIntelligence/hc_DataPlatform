import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";
export type PlatformOperationsRouteObject = PageRoute;

export const p22Routes: PageRoute[] = [
  {
    path: "/settings/platform-operations",
    requiredCapabilities: ["platform.operations.read"],
    navigation: {
      label: "平台设置",
      groupId: "security",
      order: 30,
      visibility: "authorized",
    },
    lazy: async () => ({
      Component: (await import("./page")).PlatformOperationsPage,
    }),
  },
];

export const page = definePage({ id: "P22", enabled: true, routes: p22Routes });
export default p22Routes;
