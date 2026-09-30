import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";

export const routes: PageRoute[] = [
  {
    path: "/dashboard",
    requiredCapabilities: [],
    navigation: {
      label: "工作台",
      groupId: "dashboard",
      order: 10,
      visibility: "discoverable",
    },
    lazy: async () => ({ Component: (await import("./page")).DashboardPage }),
  },
];

export const page = definePage({ id: "P01", enabled: true, routes: routes });
export default routes;
