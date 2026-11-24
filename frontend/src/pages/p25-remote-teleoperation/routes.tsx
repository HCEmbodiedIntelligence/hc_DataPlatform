import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";

const routes: PageRoute[] = [
  {
    path: "/teleoperation",
    requiredCapabilities: ["robot.read"],
    navigation: {
      label: "远程遥操作",
      groupId: "ingest",
      order: 15,
      visibility: "discoverable",
    },
    lazy: () => import("./page"),
  },
];

export const page = definePage({ id: "P25", enabled: true, routes });
export default routes;
