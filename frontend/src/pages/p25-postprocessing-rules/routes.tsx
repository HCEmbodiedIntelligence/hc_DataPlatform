import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";

export const routes: PageRoute[] = [
  {
    path: "/settings/postprocessing-rules",
    requiredCapabilities: ["robot.read"],
    navigation: {
      label: "数据后处理规则",
      groupId: "governance",
      order: 36,
      visibility: "authorized",
    },
    lazy: () => import("./page"),
  },
];

export const page = definePage({ id: "P25", enabled: true, routes });
export default routes;
