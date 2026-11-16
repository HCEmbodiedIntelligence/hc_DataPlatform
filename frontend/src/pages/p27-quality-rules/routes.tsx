import { definePage } from "../../shared/routing/page-descriptor";

export const routes = [
  {
    path: "/settings/quality-rules",
    requiredCapabilities: ["robot.read"] as const,
    navigation: {
      label: "质检规则",
      groupId: "governance" as const,
      order: 36,
      visibility: "authorized" as const,
    },
    lazy: () => import("./page"),
  },
];
export const page = definePage({ id: "P27", enabled: true, routes });
export default routes;
