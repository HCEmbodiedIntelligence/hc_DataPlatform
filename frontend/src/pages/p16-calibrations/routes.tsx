import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";

export const routes: PageRoute[] = [
  {
    path: "/settings/calibrations",
    requiredCapabilities: ["calibration.read"],
    navigation: {
      label: "标定管理",
      groupId: "governance",
      order: 50,
      visibility: "authorized",
    },
    lazy: () => import("./page"),
  },
];

export const page = definePage({ id: "P16", enabled: false, routes: routes });
export default routes;
