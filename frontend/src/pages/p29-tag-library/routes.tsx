import { definePage } from "../../shared/routing/page-descriptor";

export const routes = [
  {
    path: "/settings/annotation-tags",
    requiredCapabilities: ["robot.read"] as const,
    navigation: {
      label: "标签库",
      groupId: "governance" as const,
      order: 37,
      visibility: "authorized" as const,
    },
    lazy: () => import("./page"),
  },
];
export const page = definePage({ id: "P29", enabled: true, routes });
export default routes;
