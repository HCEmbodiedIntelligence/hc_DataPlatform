import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";

export const routes: PageRoute[] = [
  {
    path: "/settings/robot-models",
    requiredCapabilities: ["robot_model.read"],
    navigation: {
      label: "机器人模型",
      groupId: "governance",
      order: 30,
      visibility: "authorized",
    },
    lazy: () => import("./page"),
  },
];

export const page = definePage({ id: "P14", enabled: true, routes: routes });
export default routes;
