import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";
export const routes: PageRoute[] = [
  {
    path: "/settings/capture-schemes",
    requiredCapabilities: ["robot.read"],
    navigation: {
      label: "采集方案",
      groupId: "governance",
      order: 35,
      visibility: "authorized",
    },
    lazy: () => import("./page"),
  },
  {
    path: "/settings/capture-schemes/new",
    requiredCapabilities: ["robot.read"],
    lazy: () => import("./page"),
  },
  {
    path: "/settings/capture-schemes/:schemeId",
    requiredCapabilities: ["robot.read"],
    lazy: () => import("./page"),
  },
];
export const page = definePage({ id: "P24", enabled: true, routes });
export default routes;
