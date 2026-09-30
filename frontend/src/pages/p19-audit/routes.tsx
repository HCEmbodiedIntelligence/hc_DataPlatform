import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";

export const routes: PageRoute[] = [
  {
    path: "/settings/audit",
    requiredCapabilities: ["audit.read"],
    navigation: {
      label: "审计日志",
      groupId: "security",
      order: 20,
      visibility: "authorized",
    },
    lazy: async () => ({ Component: (await import("./page")).AuditPage }),
  },
];

export const page = definePage({ id: "P19", enabled: true, routes: routes });
export default routes;
