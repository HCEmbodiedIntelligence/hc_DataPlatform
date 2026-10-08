import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";

export const routes: PageRoute[] = [
  {
    path: "/settings/robots",
    requiredCapabilities: ["robot.read"],
    navigationOwnerPageId: "P14",
    lazy: () => import("./page"),
  },
  {
    path: "/settings/robot-instances",
    requiredCapabilities: ["robot.read"],
    navigation: {
      label: "机器人实例",
      groupId: "governance",
      order: 40,
      visibility: "authorized",
    },
    lazy: () => import("./instances-page"),
  },
];

export const page = definePage({ id: "P15", enabled: true, routes: routes });
export default routes;
