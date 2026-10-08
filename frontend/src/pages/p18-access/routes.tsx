import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";
export type AccessRoute = PageRoute;
// P18 owns its OR boundary: project access.read OR global platform.account.read.
// Both query families remain disabled until their corresponding grant is available.

export const routes: PageRoute[] = [
  {
    path: "/settings/access",
    requiredCapabilities: [],
    navigation: {
      label: "账户与权限",
      groupId: "security",
      order: 10,
      visibility: "authorized",
      capability: "access.read",
    },
    lazy: () => import("./page"),
  },
];

export const page = definePage({ id: "P18", enabled: true, routes: routes });
export default routes;
