import {
  definePage,
  type PageRoute,
} from "../../shared/routing/page-descriptor";

export const routes: PageRoute[] = [
  {
    path: "/storage/lifecycle",
    requiredCapabilities: ["storage.lifecycle.read"],
    navigation: {
      label: "生命周期",
      groupId: "governance",
      order: 20,
      visibility: "authorized",
    },
    lazy: async () => ({ Component: (await import("./page")).LifecyclePage }),
  },
];

export const page = definePage({ id: "P13", enabled: true, routes: routes });
export default routes;
